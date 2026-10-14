# Multi-dimensional relations

`site_kg/relations.py`. Adds derived edge dimensions on top of the evidence
edges (`ref`, `imports`, `contains`) and, for **each node**, decides which
dimensions apply to it.

## The question this answers

> Can we, when building a node, dynamically choose which relation dimensions to
> use to connect it to other nodes — instead of running one fixed extractor
> over the whole corpus?

Short answer: **yes, and the parts are separately validated; the naive version
(no fixed schema, LLM invents relation types freely) is not affordable.**
There is no single paper titled "per-node relation dimension routing"; what
exists is three results that compose into exactly that:

| result | what it proves | where |
|---|---|---|
| **AutoSchemaKG** | A graph can be built with *no predefined schema at all*. An LLM extracts open-vocabulary triples, then a second pass conceptualizes each entity/event/relation into ≥3 abstract concept phrases; the induced concept set *is* the schema (`G=(V,E,C,φ,ψ)`). Relation naming is chunk-dependent and generative, normalized softly through shared abstract concepts rather than a fixed ID inventory. 92–95% semantic alignment with human-crafted schemas, zero manual intervention; triple F1 up to 96.8; multi-hop QA +12–18% over BM25/Contriever and competitive with HippoRAG2 while using an 8B constructor. | arXiv:2505.23628 |
| **KGGen** | Induced schemas must be **clustered or they explode**. KGGen clusters related entities to cut sparsity; ships the MINE benchmark for text→KG quality. This is the counterweight to open-vocabulary extraction. | arXiv:2502.09956 |
| **EDC (Extract–Define–Canonicalize)** | The hybrid: three stages (open extraction → schema definition → canonicalization) that work *with* a target schema and *without* one (self-generated schema + self-canonicalization). Proves "bounded registry" and "free induction" are two settings of one design, not rival religions. | arXiv:2404.03868 |
| **Adaptive-RAG** | Precedent for the router itself: a small classifier reads the *item* (query complexity) and routes it to no-retrieval / single-hop / multi-hop instead of applying the most expensive strategy to everything. Our selector is the same shape, applied to nodes rather than queries. | arXiv:2403.14403 |
| **Graph structure learning / GraphEdit** | Precedent for treating the edge set as a hypothesis to prune, not as ground truth — which is why derived edges here get thresholds, caps, and per-node gating. | GSL literature, GraphEdit |

What the literature does **not** support: unbounded LLM-invented relation types
at scale. AutoSchemaKG's web-scale run cost 78,400 GPU-hours and its Common
Crawl concept vocabulary reached 31M phrases (ATLAS-CC: 937M nodes / 5.96B
edges). That is the wrong price for "ingest this docs site".

So: **bounded registry + per-node router + optional LLM that may only choose,
never mint, an edge kind.** The LLM sees title + digest + cheap flags, never
whole bodies.

## Dimensions

| kind | source | needs | meaning |
|---|---|---|---|
| `ref` | corpus | — | markdown link / wikilink resolved against the corpus |
| `imports` | corpus | — | code import / include / require |
| `contains` | corpus | — | README / index hub → its directory |
| `mentions_symbol` | corpus | code | this file mentions an identifier **defined** in another file |
| `similar_to` | corpus | prose ≥ 220 chars | sparse tf-idf cosine: same topic, no explicit link |
| `sibling_of` | corpus | 1–12 dir mates | same source directory / URL folder cohort |
| `supersedes` | corpus | version in id/URL | 2.0 supersedes 1.4 of the same page family |
| `shares_entity` | LLM | chat model | pages share ≥ 2 extracted named entities |
| `semantic_similar` | embeddings | embed model | embedding cosine over title + body head |

The first three are evidence: they trace to a statement in the source. The rest
are derived: they trace to a computation over the corpus, and are labelled as
such in `report.dimensions`.

## Selection

`select_dimensions()` reads one node's profile and returns its dimensions:

- a stub (`prose_len < 220`) gets **no** `similar_to` — there is no lexical
  signature to compare, and comparing it produces noise;
- a prose page gets **no** `mentions_symbol` — it is not code;
- a code file gets `mentions_symbol` and, if its directory holds ≤ 12 files,
  `sibling_of`;
- a page whose id or URL carries a version (`release-v2`) gets `supersedes` if
  another node shares its base name;
- LLM/embedding dimensions need both the model and enough prose.

Modes (`--relations` on the CLI, `relations=` on `ingest_url`/`ingest_local`,
`SITE_KG_RELATIONS` in the environment):

| mode | behaviour |
|---|---|
| `off` | no derived dimensions — the pre-feature behaviour |
| `auto` | keyless dimensions, per-node heuristic selection (default) |
| `llm` | `auto` + an LLM router that *narrows* each node's set, + entity/embedding dimensions |
| `all` | every derived dimension on every node (ablation / debugging) |
| `similar_to,supersedes` | explicit subset |

In `llm` mode the router can only remove: its picks are intersected with the
heuristic set, so a hallucinated "this stub is lexically similar" cannot
manufacture an edge the content cannot support.

## Invariants

1. **Derived edges never replace evidence.** A pair already linked by `ref` or
   `imports` is never re-labelled with a derived kind.
2. **No graph where the corpus has none.** Derived dimensions run only when
   `ref + imports > 0`. A corpus with zero cross-references still reports
   `NOT-READY` with zero edges rather than gaining a decorative hairball —
   the property `tests/test_pipeline.py::test_graph_ready_and_not_ready_gates`
   pins down.
3. **Fail open.** No LLM key, no embedding model, or a gateway error ⇒ the
   dimension is skipped with a line in `report.relationNotes`; the keyless
   dimensions still run.
4. **Bounded fan-out.** `similar_to` ≤ 5 per node above cosine 0.20 over a
   15-term signature (terms with df outside 2–25 are ignored); `sibling_of`
   only for directories of 2–13 files; `mentions_symbol` ≤ 5 per file.
5. **Bounded cost.** Above 8,000 docs the quadratic passes (`similar_to`,
   `mentions_symbol`) are skipped with a note; the LLM entity budget defaults
   to 120 pages (`SITE_KG_RELATION_ENTITY_PAGES`) and the embedding budget to
   300 (`SITE_KG_RELATION_EMBED_PAGES`).

## Measured

Dogfooding this repo (`--relations auto`, 2026-10-14): 28 docs, 47 `imports` +
3 `ref` + 3 `contains` evidence edges → +3 `mentions_symbol`, +18 `similar_to`,
+29 `sibling_of`. `mentions_symbol` is deliberately small: cross-module symbol
use in this codebase coincides with an `imports` edge, and duplicates are
dropped by invariant 1 — the 3 are the genuine residue (a symbol used without
an import).

A 2,000-file slice of a DriveOS SDK tree: 132 evidence edges → +142
`mentions_symbol`, +3,560 `similar_to`, +427 `sibling_of`, 4.1 s end-to-end for
the whole ingest. Sampled `similar_to` pairs are the ones a reader would want
and no link statement provides: `…/profile/cpu_assign.mk` (linux-qnx ↔ l4t-qnx
variants), `tegra234-mb1-bct-p3710-0012-a03-…-gpio-default.dtsi` ↔ its `-0020-`
sibling, a `…storage-gos1.dtsi` ↔ its `tegra_virt_storage.yaml` schema. That
corpus has almost no cross-references and is still, structurally, a pile of
files; the derived layer is what makes traversal useful on it.
