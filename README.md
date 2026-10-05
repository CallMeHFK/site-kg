# site-kg

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

Resource: `site://<site_id>/graph` — the full graph JSON.

## Verified

Measured on the embedded vendor 7.0.3 docs site (static Sphinx): 40-page bounded ingest → 40 docs, 32 `ref` edges, 1709 indexed terms, verdict `READY`; all seven MCP tools exercised over real streamable-HTTP sessions (2026-10-06). The same origin site proves raw websites — not just wiki exports — carry enough link structure to graph.

`ask` verified against an on-prem vLLM gateway (qwen3.5-122b chat + bge-m3 embed + bge-reranker-v2-m3): an English question returns a cited step-by-step answer; a Chinese cross-lingual question works through the embedding fallback when keyword recall fails on the ASCII index.

Second-site check on the Doxygen API reference (`drive-os-linux-sdk-api-ref`, 300-page bounded ingest): the first attempt correctly reported NOT-READY (JS shell => BFS discovered 1 page); after the navtree manifest source landed, 301 docs / 527 `ref` edges, verdict READY, and `ask` returned a correct cited answer across header/source pages.

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
- Crawl4AI deep-crawl backend for JS-rendered sites (dependency already optional as `.[crawl]`).
- Static graph visualization reuse from [an earlier internal prototype](https://github.com/CallMeHFK/an earlier internal prototype).

## License

MIT. Crawled content remains the property of its respective owners.
