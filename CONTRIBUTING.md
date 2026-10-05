# Contributing to site-kg

Thanks for your interest! This document covers the dev setup, the conventions this
codebase follows, and the rules for submitting contributions.

## Legal

By submitting a contribution you agree that it is licensed under the project's
[MIT License](LICENSE), the same as the rest of the codebase.

## Development setup

Prerequisites: Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/CallMeHFK/site-kg.git
cd site-kg
uv sync --all-extras          # .venv with runtime + dev (pytest, ruff) + crawl extra
```

Run the same checks as CI:

```bash
python -m pytest -q           # offline; never hits the network
ruff check .
```

Run the MCP server locally:

```bash
python -m site_kg.cli serve --transport http --port 8766
```

## Repository layout

```
site_kg/manifest.py    page inventory: objects.inv -> navtreeindex -> sitemap -> BFS
site_kg/ingest.py      bounded crawl, robots compliance, markdown corpus + link sidecar
site_kg/render.py      JS-shell detection and Playwright fallback
site_kg/graph.py       link-derived edges, READY/NOT-READY gate, inverted index
site_kg/semantic.py    optional LLM layer (embed -> rerank -> cited answer)
site_kg/mcp_server.py  FastMCP tools/resources, stdio + streamable-http
tests/                 offline pytest; loopback fixtures only
```

## Conventions

- Edges come from the crawled corpus only. Never invent a link to make a graph
  look connected; a zero-edge site gets verdict NOT-READY and the reason.
- Detection first, browser second: static fetch always runs before Playwright.
- Robots.txt is honored by default; `--no-robots` exists only for sites you own
  or are authorized to crawl.

## Tests

- Everything offline. No real API keys, no live sites: use loopback fixtures
  (`http.server.ThreadingHTTPServer` bound to 127.0.0.1) and fake clients.
- Dummy values in tests must be obviously fake (`"k"`, `"file-key"`), never
  realistic-looking strings.
- Every bug fix ships with a regression test that fails without the fix.

## Commit & PR rules

- Commit messages: imperative summary line; explain the *why* in the body, and
  quote the measured before/after numbers when behavior changed.
- Secrets never enter code, config samples, docs, tests, or commit messages.
  Runtime state (`data/`, `.env`, `.venv/`) stays gitignored.
