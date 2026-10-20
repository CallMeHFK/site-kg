# site-kg

[![ci](https://github.com/CallMeHFK/site-kg/actions/workflows/ci.yml/badge.svg)](https://github.com/CallMeHFK/site-kg/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

Give it a website URL or a local directory; it builds a knowledge graph from docs **and code**, and serves it to AI agents over MCP.

中文文档：[README.zh-CN.md](README.zh-CN.md)

## What it does

```
URL ──► manifest probe ──► crawl ──► markdown corpus ──► graph.json + search.json ──► MCP server
        (objects.inv →          (httpx+bs4+html2text,   (link-derived edges,          (search / get_page /
         sitemap.xml → BFS)      robots.txt, rate-limited)  inverted index, verdict)     neighbors / site_stats)

local dir ──► walk (.gitignore-aware) ──► same corpus format ──► same graph + MCP
              (md/txt/wiki docs, code files;
               edges: md links, [[wikilinks]], imports, README hubs)
```

- **Manifest probe** — Sphinx sites are enumerated from `objects.inv` (the authoritative page list), Doxygen sites from `navtreeindex0.js` (their `index.html` is a JS shell with no static links), then `sitemap.xml`, then same-site BFS. No guessing from local filenames.
- **Edges come from the corpus only.** If a site has no cross-references the verdict is `NOT-READY` and the graph layer says so instead of drawing a decorative hairball. Never invents links.
- **No LLM required** for the structural layer: search, page bodies and graph traversal all work keyless. The optional `ask` Q&A layer runs on any OpenAI-compatible gateway (`SITE_KG_LLM_BASE` + `SITE_KG_LLM_KEY`).
- **Wheels, not rewrites**: MCP via the official `mcp` SDK; optional JS-heavy crawling via Crawl4AI; optional semantic layer via cognee. Only the glue (manifest → corpus → graph → MCP) is ours.

## Quickstart

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[crawl]"

# ingest a site (bounded)
python -m site_kg.cli ingest https://example.org/docs/index.html --max-pages 200

# or ingest a local directory (docs wiki + code repo)
python -m site_kg.cli ingest-local /path/to/repo-or-wiki

# serve MCP over streamable HTTP (for network clients)
python -m site_kg.cli serve --transport http --port 8766

# or stdio, for agent hosts that launch subprocesses
python -m site_kg.cli serve
```

MCP client config (streamable HTTP):

```json
{"mcpServers": {"site-kg": {"url": "http://127.0.0.1:8766/mcp"}}}
```

## MCP tools

| tool | purpose |
|---|---|
| `ingest_url(url, max_pages, max_depth, relations)` | crawl + build graph; returns `site_id` and `verdict` |
| `ingest_local(path, max_files, respect_gitignore, relations)` | local directory (docs + code) → graph; same outputs |
| `list_sites()` | ingested sites with verdicts |
| `site_stats(site_id)` | doc/edge counts, edge types, top hubs |
| `search(site_id, query)` | idf-weighted full-text search |
| `get_page(site_id, page_id)` | full markdown body + source URL |
| `neighbors(site_id, page_id, depth)` | subgraph expansion (multi-hop context) |
| `ask(site_id, question)` | LLM Q&A with citations (needs LLM config) |
| `render_site(site_id)` | self-contained **3D** Three.js graph viewer (human inspection): degree-sized glowing nodes, hover tooltip, click-to-focus neighbourhood, chapters aggregate view (click a sphere to isolate), instant search with focus, live status bar (nodes/edges/view%/FPS) |
| `render_build(site_id)` | self-contained **WebGPU** build-replay viewer: the graph constructs itself live — nodes born in corpus order onto 3D chapter shells, then evidence edges (ref/imports/contains), then one flying wave per derived relation dimension; every edge shoots out of its source node and binds to the target with a pulse while a HUD counts nodes/edges/per-dimension edges. Speed controls 0.25×–16×, replay/pause/skip. Needs a WebGPU-capable browser |

Resource: `site://<site_id>/graph` — the full graph JSON.

## Local directories (docs + code)

`ingest_local` walks a directory into the same corpus format the crawler produces, so every downstream tool (search/get_page/neighbors/ask/render) works unchanged.

- **Docs** (`.md/.markdown/.mdx/.txt/.wiki/.rst/.adoc`) keep their body. Edges come from relative markdown links (extension-less and root-absolute forms included) and `[[wikilinks]]` (Obsidian/MediaWiki style, case/underscore-insensitive basename match). Unresolvable references are counted in `dropped_refs`, never turned into edges.
- **Code** (37 extensions: py, js/ts, go, rust, c/c++, java/kotlin, ruby, … plus the kernel/build ecosystem — yaml, asm `.S/.s`, perl, awk, linker scripts, makefile fragments, devicetree `.dts/.dtsi`) becomes searchable docs whose body starts with a prose header (path, language, detected top-level symbols) so identifiers hit the inverted index. Well-known extension-less build files (`Makefile`, `Kconfig`, `Kbuild`, `COPYING`, `LICENSE`, `README`, `MAINTAINERS`, …) are ingested by basename with the same treatment. Edges come from real dependency statements: Python `import`/`from…import` (including relative and parenthesized forms), JS/TS `import`/`require`, C/C++ quoted `#include`, Go `import` (go.mod-aware), Rust `mod`/`use crate::`, JVM `import`, Ruby `require_relative`.
- **README/index files are hubs**: `contains` edges to their directory siblings, mirroring the site-side hub semantics.
- **`.gitignore` is honored** (via `pathspec`) plus a builtin noise list (`node_modules`, `__pycache__, lockfiles, *.min.js`, …); >512 KB and binary files are skipped. Every skip is attributed, not silent: `scan_stats` counts `skipped_noise_file` / `skipped_ext` / `skipped_oserror` separately (their sum stays in `skipped_kind`), and NOISE_DIRS pruning reports `pruned_noise_dirs` plus a sample of the pruned paths — a coverage claim you cannot audit is a wrong claim.
- **Page bodies are sharded, not monolithic**: `data/sites/<site_id>/docs/<bucket>.json` holds 1–256 sha-keyed buckets (count scaled to corpus size, recorded as `docBuckets`), so `get_page` reads one small bucket whatever the corpus shape (measured on a 75k-doc kernel tree: reading the single 975 MB `docs.json` took 8.3 s, a sharded cold page read takes ~12 ms; a `drivers/` chapter with 31k files cannot recreate one giant shard). Sites built before sharding keep their `docs.json` and are still read transparently. Re-ingesting replaces the shards and drops the old single file in the same pass.

Env knobs (read per call, so a long-lived MCP server picks up changes without a restart):

| var | effect |
|---|---|
| `SITE_KG_MAX_BYTES` | per-file size cap in bytes (default 512 KiB) |
| `SITE_KG_NOISE_EXEMPT` | comma-separated posix relpath prefixes whose dirs escape NOISE_DIRS pruning (e.g. `drivers/target` — real source trees collide with noise names) |
| `SITE_KG_RELATIONS` | derived relation dimensions, selected per node: `auto` (default) / `llm` / `all` / `off` / comma list — see [Relation dimensions](#relation-dimensions) |
| `SITE_KG_RELATION_ENTITY_PAGES` | LLM entity-extraction budget per ingest (default 120) |
| `SITE_KG_RELATION_EMBED_PAGES` | embedding-similarity budget per ingest (default 300) |
| `SITE_KG_RELATION_ROUTE_PAGES` | LLM router budget per ingest (default 120) |
- **PageRank** runs over the edge list (aider repo-map style) and lands on each node as `r`, so `site_stats`/the viewer can rank structurally central files.

Design choices, after surveying the field (aider, Serena, CocoIndex, gitingest; GraphRAG, LightRAG, cognee, khoj; GraphCoder/CGM papers): the structural layer stays **LLM-free and honest** — every edge traces to an explicit link or import statement. LLM-extracted entity/relation edges (the GraphRAG/LightRAG approach) buy recall at the cost of hallucinated edges and build cost, so they stay in the optional cognee semantic layer. Compiler-grade precision (LSP/SCIP, Serena-style) and tree-sitter symbol graphs are roadmap items as optional extras, not hard dependencies.

Measured (2026-10-09, dogfooding this repo): 22 files (7 docs + 15 code) → 30 `imports` + 2 `ref` + 2 `contains` edges, verdict `READY`; identifier search (`pagerank`, `gitignore`) lands on the right source file.

Measured at corpus scale (2026-10-13, a 115,598-doc / 7-site DriveOS SDK source tree): re-ingest and report parity verified site-by-site; `get_page` on the largest site (75,415 docs) reads one ~3.8 MB shard in ~12 ms where the single-file format needed 8.3 s for the same page. Shard/meta caches are keyed by file mtime+size, so a re-ingest from any process (cli, crawl job, another agent) is served fresh with no invalidation hook; all writes are tmp-file + `os.replace`, and a 4-thread read hammer during repeated re-ingest saw zero torn reads.

## Live viewer

`site-kg live` starts a local, address-first live session: give it the address first; the browser then asks which scene to build in.

```bash
# URL: prints a loopback URL; the launcher is prefilled with the address
python -m site_kg.cli live https://docs.example.com

# skip the launcher with a scene: crawl -> stream -> settle in one view
python -m site_kg.cli live https://docs.example.com --scene neural

# local docs/code directory
python -m site_kg.cli live --local ./docs --scene planets

# replay an ingested site from its stored graph
python -m site_kg.cli live my-site --replay
```

The page streams the build over SSE while the server crawls and lays the graph out incrementally: nodes arrive in corpus order, edges bind as pages are parsed, and derived relation dimensions stay hidden by default and never participate in layout springs, so they cannot distort the picture. Scenes: **Planets** (1) chapters as worlds in orbit, **Neural** (2) a dense field of synapses. `#scene=`, the last-picked scene in localStorage, and the MCP `scene=` argument are presets once a scene has been chosen — they never replace the address-first start.

Limits and fallbacks:

- The scale floor is the measured 75,415-node / 61,615-edge corpus; layout runs on the server (no browser-side force layout at that size). `--max-nodes` trims the preview only.
- Labels are budgeted at most 40 chapter / 12 tick labels, and the scene is merged point/line batches — no per-node objects or DOM labels.
- Without WebGL2 the page falls back to a canvas-2D view capped at 5,000 nodes and points at the offline archive for the full graph. WebGL context loss is restored in place.
- The archive viewer (`site-kg render <site_id>`) is a single offline HTML file (binary payloads embedded by default; `--external-data` writes `sk-*.bin` beside it) and replays the same scenes.

Security: the server binds `127.0.0.1` only, sends no CORS headers, rejects non-loopback `Host` values, refuses path traversal, serves no corpus files, and offers an optional shared token (`--token`). With a token, the printed URL carries `?token=…` and the page forwards it on snapshot, SSE and start calls. The Three.js r186 bundle is committed and pinned; normal development and offline use need no npm — see `tools/build_viewer_bundle.mjs` for the deliberate upgrade path.

## Relation dimensions

Link and import edges are honest but sparse: most pages in most corpora are
unconnected because nobody linked them. `--relations` (CLI), `relations=`
(MCP) or `SITE_KG_RELATIONS` (env) adds **derived** dimensions, chosen **per
node** from that node's own content — not one fixed extractor over everything:

| kind | needs | meaning |
|---|---|---|
| `mentions_symbol` | code | this file mentions an identifier defined in another file |
| `similar_to` | prose ≥ 220 chars | sparse tf-idf cosine over prose + title tokens: same topic, no explicit link |
| `sibling_of` | 1–12 dir mates | same source directory / URL folder cohort |
| `supersedes` | version in id/URL | 2.0 supersedes 1.4 of the same page family |
| `shares_heading` | ≥ 3 headings | document-skeleton overlap (Jaccard ≥ 0.35 over heading sets): same-generator API pages |
| `shares_term` | ≥ 2 curated terms | shared writer-marked vocabulary: inline code spans, headings, declared symbols |
| `duplicate_of` | frontmatter sha256 | identical content (exact-hash tier, chained) |
| `coupled_with` | evidence links | bibliographic coupling: both pages cite the same targets |
| `co_cited_with` | evidence links | co-citation: the same sources cite both pages (relates stubs) |
| `shares_entity` | chat model | pages share ≥ 2 LLM-extracted named entities |
| `semantic_similar` | embed model | embedding cosine over title + body head |

A stub gets no lexical neighbours, a prose page gets no symbol edges, a release
note gets version lineage, a source file gets symbol references. Modes: `auto`
(keyless, default), `llm` (auto + an LLM router that may only *narrow* each
node's set, plus the two model-backed dimensions), `all`, `off`, or an explicit
comma list.

Two invariants keep this from becoming a hairball generator: a pair the corpus
already links is never re-labelled with a derived kind, and everything is
per-node gated and fan-out capped. Content-derived dimensions run **even on a
corpus with zero cross-references** — content is the only signal such a corpus
has — but the verdict still reports `NOT-READY` (no link structure), and a stub
corpus still gates to zero edges. No LLM or embedding model is required; without
one those dimensions are skipped with a note.

Design and the literature it rests on (AutoSchemaKG arXiv:2505.23628, KGGen
arXiv:2502.09956, EDC arXiv:2404.03868, Adaptive-RAG arXiv:2403.14403):
[docs/RELATIONS.md](docs/RELATIONS.md).

Measured on a 2,000-file DriveOS SDK slice: 132 evidence edges → +142
`mentions_symbol`, +3,560 `similar_to`, +427 `sibling_of`, 4.1 s end-to-end.

## Verified

Measured on the embedded vendor 7.0.3 docs site (static Sphinx): 40-page bounded ingest → 40 docs, 32 `ref` edges, 1709 indexed terms, verdict `READY`; all seven MCP tools exercised over real streamable-HTTP sessions (2026-10-06). The same origin site proves raw websites — not just wiki exports — carry enough link structure to graph.

`ask` verified against an on-prem vLLM gateway (qwen3.5-122b chat + bge-m3 embed + bge-reranker-v2-m3): an English question returns a cited step-by-step answer; a Chinese cross-lingual question still answers when keyword recall fails on the ASCII index, because embedding similarity over title + body head is unioned with keyword hits on every query.

Second-site check on the Doxygen API reference (`drive-os-linux-sdk-api-ref`, 300-page bounded ingest): the first attempt correctly reported NOT-READY (JS shell => BFS discovered 1 page); after the navtree manifest source landed, 301 docs / 527 `ref` edges, verdict READY, and `ask` returned a correct cited answer across header/source pages.

## Site-type coverage (measured)

| engine | manifest tier used | result |
|---|---|---|
| Sphinx (embedded vendor guide) | `objects.inv` | READY, 40 docs / 32 edges |
| Doxygen (vendor platform API reference) | `navtreeindex0.js` | READY, 301 docs / 527 edges |
| MkDocs Material (squidfunk) | `sitemap.xml` | READY, 60 docs / 2801 edges |
| Docusaurus (Checkly) | `sitemap.xml` index → child flattening | READY, 60 docs / 460 edges |
| VitePress (Vue guide) | `sitemap.xml` | READY, 50 docs / 2402 edges |
| MediaWiki (Arch Wiki) | same-site BFS | READY, 44 docs / 1201 edges |
| robots-disallowed (docs.astral.sh, docusaurus.io) | n/a | reported `blocked by robots.txt`, never a silent empty graph |

Every pretty-URL shape is handled (trailing slash, extension-less, `/title/X`); a leaf seed that discovers <=2 pages returns a `seed_hint` telling you to seed the docs root.

`ask` uses block-level embedding selection, not head truncation: the same question on the same corpus went from "insufficient information" to a fully cited answer after that fix (the answer paragraph sat mid-page in a long FAQ).

Client-rendered SPAs: pages that fetch as empty framework shells are detected (empty `#app`/`#root` root, or near-zero visible text and no internal links) and re-rendered with headless Chromium (Playwright). Static fetch always runs first — only shell pages pay for a browser. Verified with a local CSR fixture: BFS discovers the JS-injected links, the graph reaches READY, and the JS-rendered body lands in the corpus.

## FAQ

- **The ingest says `blocked by robots.txt`.** The site disallows this crawler's user agent. Ask the owner, or pass `respect_robots=false` only if you are authorized to crawl it. For deep crawls of sites you own or are authorized to test, use `--dev-crawl` (CLI) / `dev=true` (MCP): robots.txt and politeness delays are skipped and the defaults deepen (`--max-pages 2000 --max-depth 8`, concurrency 8).
- **It found only 1-2 pages.** Your seed URL is probably a section leaf; re-ingest with the docs root (the `seed_hint` field says so). If the site is a client-rendered SPA, the Playwright fallback handles shell pages automatically.
- **Do I need an LLM?** No for crawl/search/graph/neighbors. Only `ask` needs `SITE_KG_LLM_BASE` + `SITE_KG_LLM_KEY` (any OpenAI-compatible gateway; vLLM/new-api verified).
- **NOT-READY verdict.** The site has no internal cross-references — it is a directory tree, not a graph. You still get full-text search, page bodies (with extracted headings and description in the frontmatter), and the keyless content dimensions (`similar_to`, `shares_heading`, …) still connect pages that share substance; what you do not get is a link-structure claim.

## Deployment


Local-first. A sudo-free systemd user unit:

```ini
# ~/.config/systemd/user/site-kg.service
[Service]
WorkingDirectory=%h/develop/site-kg
ExecStart=%h/develop/site-kg/.venv/bin/python -m site_kg.cli serve --transport http --port 8766
Restart=on-failure
[Install]
WantedBy=default.target
```

## Roadmap

- Semantic enrichment (optional): cognee `cognify` over the crawled corpus when `SITE_KG_LLM_KEY` is present — entity/relation edges alongside the structural ones.
- ~~Cross-file symbol edges~~ done: `mentions_symbol` (regex layer, keyless). tree-sitter symbol/reference edges (aider-style tags) remain an optional extra for precision.
- Crawl4AI deep-crawl for JS sites that defeat the Playwright shell-fallback (infinite scroll, heavy anti-bot), via the `.[crawl]` extra.
- ~~Static graph visualization~~ done: `render_site` emits a self-contained 3D Three.js viewer per site (bundle embedded, works offline; seeded layout computed at render time). `render_build` adds a WebGPU replay of the construction itself — nodes and per-dimension edge waves animating onto the shells with a live HUD.

## License

MIT. Crawled content remains the property of its respective owners.
