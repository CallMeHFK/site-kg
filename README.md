# site-kg

[![ci](https://github.com/CallMeHFK/site-kg/actions/workflows/ci.yml/badge.svg)](https://github.com/CallMeHFK/site-kg/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

Give it a website URL; it crawls the site, builds a knowledge graph, and serves it to AI agents over MCP.

中文文档：[README.zh-CN.md](README.zh-CN.md)

## What it does

```
URL ──► manifest probe ──► crawl ──► markdown corpus ──► graph.json + search.json ──► MCP server
        (objects.inv →          (httpx+bs4+html2text,   (link-derived edges,          (search / get_page /
         sitemap.xml → BFS)      robots.txt, rate-limited)  inverted index, verdict)     neighbors / site_stats)
```

- **Manifest probe** — Sphinx sites are enumerated from `objects.inv` (the authoritative page list), Doxygen sites from `navtreeindex0.js` (their `index.html` is a JS shell with no static links), then `sitemap.xml`, then same-site BFS. No guessing from local filenames.
- **Edges come from the corpus only.** If a site has no cross-references the verdict is `NOT-READY` and the graph layer says so instead of drawing a decorative hairball. Never invents links.
- **No LLM required** for the structural layer: search, page bodies and graph traversal all work keyless. The optional semantic layer (entity/relation extraction for Q&A) plugs in cognee when `LLM_API_KEY` is set.
- **Wheels, not rewrites**: MCP via the official `mcp` SDK; optional JS-heavy crawling via Crawl4AI; optional semantic layer via cognee. Only the glue (manifest → corpus → graph → MCP) is ours.

## Quickstart

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[crawl]"

# ingest a site (bounded)
python -m site_kg.cli ingest https://example.org/docs/index.html --max-pages 200

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
| `ingest_url(url, max_pages, max_depth)` | crawl + build graph; returns `site_id` and `verdict` |
| `list_sites()` | ingested sites with verdicts |
| `site_stats(site_id)` | doc/edge counts, edge types, top hubs |
| `search(site_id, query)` | idf-weighted full-text search |
| `get_page(site_id, page_id)` | full markdown body + source URL |
| `neighbors(site_id, page_id, depth)` | subgraph expansion (multi-hop context) |
| `ask(site_id, question)` | LLM Q&A with citations (needs LLM config) |
| `render_site(site_id)` | self-contained **3D** Three.js graph viewer (human inspection): degree-sized glowing nodes, hover tooltip, click-to-focus neighbourhood, chapters aggregate view (click a sphere to isolate), instant search with focus, live status bar (nodes/edges/view%/FPS) |

Resource: `site://<site_id>/graph` — the full graph JSON.

## Verified

Measured on the embedded vendor 7.0.3 docs site (static Sphinx): 40-page bounded ingest → 40 docs, 32 `ref` edges, 1709 indexed terms, verdict `READY`; all seven MCP tools exercised over real streamable-HTTP sessions (2026-10-06). The same origin site proves raw websites — not just wiki exports — carry enough link structure to graph.

`ask` verified against an on-prem vLLM gateway (qwen3.5-122b chat + bge-m3 embed + bge-reranker-v2-m3): an English question returns a cited step-by-step answer; a Chinese cross-lingual question works through the embedding fallback when keyword recall fails on the ASCII index.

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

- **The ingest says `blocked by robots.txt`.** The site disallows this crawler's user agent. Ask the owner, or pass `respect_robots=false` only if you are authorized to crawl it.
- **It found only 1-2 pages.** Your seed URL is probably a section leaf; re-ingest with the docs root (the `seed_hint` field says so). If the site is a client-rendered SPA, the Playwright fallback handles shell pages automatically.
- **Do I need an LLM?** No for crawl/search/graph/neighbors. Only `ask` needs `SITE_KG_LLM_BASE` + `SITE_KG_LLM_KEY` (any OpenAI-compatible gateway; vLLM/new-api verified).
- **NOT-READY verdict.** The site has no internal cross-references — it is a directory tree, not a graph. You still get full-text search and page bodies; graph traversal is honestly disabled instead of drawing noise.

## Deployment


Local-first. A sudo-free systemd user unit:

```ini
# ~/.config/systemd/user/site-kg.service
[Service]
WorkingDirectory=%h/.qwenpaw/workspaces/default/site-kg
ExecStart=%h/.qwenpaw/workspaces/default/site-kg/.venv/bin/python -m site_kg.cli serve --transport http --port 8766
Restart=on-failure
[Install]
WantedBy=default.target
```

## Roadmap

- Semantic enrichment (optional): cognee `cognify` over the crawled corpus when `LLM_API_KEY` is present — entity/relation edges alongside the structural ones.
- Crawl4AI deep-crawl for JS sites that defeat the Playwright shell-fallback (infinite scroll, heavy anti-bot), via the `.[crawl]` extra.
- ~~Static graph visualization~~ done: `render_site` emits a self-contained 3D Three.js viewer per site (bundle embedded, works offline; seeded layout computed at render time).

## License

MIT. Crawled content remains the property of its respective owners.
