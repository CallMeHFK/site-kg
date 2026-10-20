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
| `similar_to` | corpus | prose ≥ 220 chars | sparse tf-idf cosine over prose + title tokens: same topic, no explicit link |
| `sibling_of` | corpus | 1–12 dir mates | same source directory / URL folder cohort |
| `supersedes` | corpus | version in id/URL | 2.0 supersedes 1.4 of the same page family |
| `shares_heading` | corpus | ≥ 3 headings | document-skeleton overlap: ≥ 0.35 Jaccard over normalized heading sets |
| `shares_term` | corpus | ≥ 2 curated terms | pages share salient writer-marked terms: inline code spans, headings, declared symbols |
| `duplicate_of` | corpus | frontmatter sha256 | identical content; exact-hash tier, chained not cliqued |
| `coupled_with` | corpus | evidence links | bibliographic coupling: both pages cite the same targets (RA-weighted) |
| `co_cited_with` | corpus | evidence links | co-citation: the same sources cite both pages — relates two stubs that never link out |
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
- a code file gets `mentions_symbol` and, if its directory holds ≤ 13 files
  (1–12 dir-mates), `sibling_of`;
- a page whose id or URL carries a version (`release-v2`) gets `supersedes` if
  another node shares its base name;
- a page with ≥ 3 headings gets `shares_heading`: API references from the same
  generator share a section skeleton (Installation / Configuration / …) that
  prose similarity alone under-detects. Boilerplate headings — shared by more
  than a corpus-fraction ceiling — are dropped before pairing;
- a page with ≥ 2 curated terms (inline code spans, headings, declared
  symbols) gets `shares_term`: two pages anchored on the same backticked API
  names relate even when their prose styles differ — exactly the vocabulary
  `_prose()` discards for the cosine pass;
- stub pages still participate through the corpus-level dimensions: identical
  frontmatter sha256 chains them (`duplicate_of`), and the evidence graph's
  own neighbourhoods relate them (`coupled_with`/`co_cited_with`) without any
  per-node content;
- LLM/embedding dimensions need both the model and enough prose.

Three dimensions are **corpus-level** rather than per-node gated:
`duplicate_of` (hash evidence), `coupled_with` and `co_cited_with`
(graph-structure evidence). Their input is the evidence graph or the hash
inventory, not per-node content, so the per-node selector does not apply.
`co_cited_with` deliberately excludes `contains` hub edges — a README listing
its directory is not a citation.

Modes (`--relations` on the CLI, `relations=` on `ingest_url`/`ingest_local`,
`SITE_KG_RELATIONS` in the environment):

| mode | behaviour |
|---|---|
| `off` | no derived dimensions — the pre-feature behaviour |
| `auto` | keyless dimensions, per-node heuristic selection (default) |
| `llm` | `auto` + an LLM router that *narrows* each node's set, + entity/embedding dimensions |
| `all` | every derived dimension enabled; per-node content gating still applies (ablation / debugging) |
| `similar_to,supersedes` | explicit subset |

In `llm` mode the router can only remove: its picks are intersected with the
heuristic set, so a hallucinated "this stub is lexically similar" cannot
manufacture an edge the content cannot support. Narrowing a node to zero
dimensions is honored. An edge is admitted when *either* endpoint selected
the dimension, so removing a dimension from a pair means narrowing both nodes.

## Invariants

1. **Derived edges never replace evidence.** A pair already linked by `ref` or
   `imports` is never re-labelled with a derived kind.
2. **Content is a signal, not a hairball.** Keyless content dimensions run even
   when `ref + imports == 0` — content is the only signal such a corpus has —
   but the verdict keeps reporting `NOT-READY` ("no link structure"), and a
   stub corpus with **no shared evidence of any kind** (no prose, headings,
   cohorts, versions, curated terms, and no shared content hash) still gates
   to zero edges, the property
   `tests/test_relations.py::test_graph_never_invents_edges_for_a_stub_corpus`
   pins down. One deliberate exception: stubs whose sha256 is *identical* do
   get `duplicate_of` chains — hash equality is real corpus-level evidence,
   not an invented edge (`test_identical_stubs_chain_but_stay_not_ready`).
3. **Fail open.** No LLM key, no embedding model, or a gateway error ⇒ the
   dimension is skipped with a line in `report.relationNotes`; the keyless
   dimensions still run.
4. **Bounded fan-out.** `similar_to`: each node nominates at most 5 neighbours
   above cosine 0.20 over a 15-term signature (terms with df outside 2 and a
   corpus-fraction ceiling are ignored); `sibling_of`
   only for directories of 2–13 files; `mentions_symbol` ≤ 5 per file;
   `shares_heading` ≤ 5 per node above Jaccard 0.35 (headings shared by more
   than the relative df ceiling are treated as boilerplate); `shares_term` ≤ 5
   per node above ≥ 2 shared curated terms; `duplicate_of` ≤ 2 per node
   (chained, never cliqued: a 400-copy page would fan out 79 800 edges, the
   chain keeps incident degree at 2); `coupled_with`/`co_cited_with` ≤ 5 per
   node above a resource-allocation score of 0.25 (a shared neighbour contributes
   1/deg(z), and a page with > 24 out-links is a table of contents: excluded
   from cohort *membership*). The guard is asymmetric by design: an
   over-24-out hub never becomes a candidate itself, but a source with ≤ 24
   out-links still contributes its RA-damped weight when it is the shared
   neighbour linking two other pages.
5. **Bounded cost.** Above 8,000 docs the quadratic passes (`similar_to`,
   `mentions_symbol`) are skipped with a note; `mentions_symbol` has a second,
   tighter limit — above 4,000 code files the symbol scan returns nothing
   (silently, so the note is the only way to notice). The LLM entity budget
   defaults to 120 pages (`SITE_KG_RELATION_ENTITY_PAGES`), the router pages
   budget to 120 (`SITE_KG_RELATION_ROUTE_PAGES`), and the embedding budget to
   300 (`SITE_KG_RELATION_EMBED_PAGES`). The remaining pair passes are
   sub-quadratic by construction: inverted indices with relative df windows
   and per-term posting truncation (64 for the cosine pass, 64 for headings,
   40 for terms).

## Measured

Re-measured 2026-10-20 with the relative-df window and the six new dimensions
(current code, `--relations auto`). Both corpora are offline and reproducible
by path; the 2026-10-14 figures (28 docs / 132 evidence edges) came from
earlier snapshots of this repo and a different SDK slice, so they are replaced
here rather than compared against.

**This repo, dogfooded** (`ingest-local .`): 58 docs (11 markdown + 47 code)
→ 93 `imports` + 5 `ref` + 3 `contains` = 101 evidence edges → +36
`mentions_symbol`, +36 `similar_to`, +80 `sibling_of`, +19 `coupled_with`,
+13 `co_cited_with`, +2 `shares_term`. Verdict READY, ~4 s end-to-end.
`mentions_symbol` stays small on purpose: cross-module symbol use in this
codebase mostly coincides with an `imports` edge, and invariant 1 drops the
duplicates — the 36 are the residue (a symbol used without an import).

**A 2,000-file DriveOS SDK slice** (`sdk-src/sdk`, `--max-files 2000`):
2,000 docs (220 doc + 1,780 code) → 1,280 `imports` + 297 `contains` +
1 `ref` = 1,578 evidence edges → +3,430 `similar_to`, +915 `sibling_of`,
+765 `mentions_symbol`, +305 `coupled_with`, +246 `co_cited_with`,
+169 `shares_heading`, +157 `shares_term`, +8 `duplicate_of`. 3.5 s
end-to-end. Sampled pairs are the ones a reader would want and no link
statement provides: `build_fs.py` ↔ `copytarget.py`; NVIDIA SIPL headers that
co-cite the same includers (`IMX390_CUST1_V2_CustomData.h` ↔
`NvSIPLCommon.hpp` — both are included by the same sources); byte-identical
`orin`/`thor` copytarget manifests as `duplicate_of`.

Effect of the df-window change on that same corpus and file set (pre-change
code at `48227b5` vs current): `similar_to` 2,570 → 3,430 (+33% — the
pure-ratio ceiling admits terms the old absolute `df > 25` cap amputated,
while posting truncation keeps the pass bounded), `mentions_symbol` 760 → 765,
`sibling_of` 1,042 → 915. The `sibling_of` drop is claim order, not fewer
cohorts: `similar_to`/`coupled_with`/`co_cited_with` are claimed first and
re-label part of the sibling inventory — the pair survives, its label
changes.

## Research update (2026-10-20)

Two parallel literature surveys (graph-structural and text/NLP angles) drove a
second pass over the keyless layer. Citations verified against live search;
the full reports live out-of-band, the applied subset:

- **Relative df windows** replace the absolute caps (`df > 25` / `df > 20`).
  An absolute ceiling amputates genuinely topical terms exactly where the
  corpus is rich — at n = 20 000 the old `similar_to` layer silently
  collapsed. The ceiling is now `max(floor, 0.35·n)`, matching the
  search index's `DF_RATIO_CAP` semantics, plus weight-ordered posting
  truncation to keep the pair pass bounded once the ceiling stops capping it.
  (Cf. the search index's own ratio cap; Bayardo–Ma–Srikant WWW 2007 for
  threshold-aware posting pruning.)
- **`coupled_with` / `co_cited_with`** — bibliographic coupling (Kessler
  1963) and co-citation (Small 1973) over the evidence graph with a
  resource-allocation weight (Zhou–Lü–Zhang 2009), hub guards for
  table-of-contents pages, and deterministic group truncation.
  Liben-Nowell–Kleinberg 2007: proximity indices are good rankers, weak
  classifiers — hence the per-nominator cap.
- **`shares_term`** — writer-curated vocabulary (inline code spans, headings,
  declared symbols) with salience weights (YAKE: Campos et al. 2020; entity
  salience features: Dunietz–Gillick EACL 2014) and an idf-sum floor.
- **`duplicate_of`** — exact-hash tier over frontmatter sha256, chained in
  id order (Manku–Jain–Das Sarma WWW 2007's near-duplicate engineering, tier
  0; SimHash banding is the follow-up if fuzzy tiers are ever needed).
- **`mentions_symbol` ranking** — mention count plus a title bonus, with a
  `(-score, symbol)` tie-break replacing `most_common`'s scan-order fallback
  (a permuted doc list could permute which symbols made the cap).
- **`_dir_key` fix** — `file://` source URLs count as directory keys; local
  markdown corpora silently got zero `sibling_of` edges before.

Deliberately deferred (measured proposals, not rejections): BM25 rescoring of
`similar_to` (threshold retuning needed), SimHash fuzzy near-duplicate tiers,
heading-path alignment, witness-family corroboration, inbound-anchor-text
sidecars.
