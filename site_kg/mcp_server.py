"""MCP server: AI-facing access to site knowledge graphs.

Transports: stdio (default, for IDE/agent hosts) or streamable-http (for network clients).
Tools: ingest_url, list_sites, site_stats, search, get_page, neighbors.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import graph as graphmod
from . import ingest as ingestmod
from . import manifest, store

mcp = FastMCP("site-kg", instructions=(
    "Knowledge graphs of crawled websites. Workflow: ingest_url(url) -> search/get_page/neighbors. "
    "A site whose verdict is NOT-READY has no link structure; use search, not graph traversal."
))


@mcp.tool()
async def ingest_url(url: str, max_pages: int = 200, max_depth: int = 4,
                     concurrency: int = 4, respect_robots: bool = True) -> dict:
    """Crawl a website, build its knowledge graph, and register it as a queryable site.

    Returns site_id for later tools plus a graph-quality verdict:
    READY (cross-references found) or NOT-READY (tree without links; graph traversal useless).
    """
    urls, source = await manifest.page_inventory(url, max_pages=max_pages, max_depth=max_depth)
    seed_hint = None
    if len(urls) <= 2:
        seed_hint = ("only the seed page was discovered -- the seed looks like a section leaf. "
                     "Re-ingest with the docs root (e.g. https://site/docs/) "
                     "so scope covers siblings.")
    if not urls:
        return {"ok": False, "error": "no pages discovered", "url": url}
    pages, stats = await ingestmod.fetch_pages(urls, url, concurrency=concurrency,
                                               respect_robots=respect_robots)
    if not pages:
        if stats["robots_blocked"]:
            return {"ok": False, "url": url, "discovered": len(urls), **stats,
                    "error": "blocked by robots.txt: the site disallows this user agent. "
                             "Ask the site owner or pass respect_robots=false only if authorized."}
        return {"ok": False, "error": "all fetches failed", "url": url,
                "discovered": len(urls), **stats}
    site_id = store.site_id_for(url)
    corpus = store.site_dir(site_id) / "corpus"
    ingestmod.write_corpus(pages, url, corpus)
    built = graphmod.build_graph(corpus)
    meta = store.save_site(site_id, url, built)
    return {"ok": True, "site_id": site_id, "inventory_source": source,
            "discovered": len(urls), "fetched": len(pages), "fetch_stats": stats,
            **({"seed_hint": seed_hint} if seed_hint else {}), **meta}


@mcp.tool()
def list_sites() -> list[dict]:
    """List ingested sites with their verdicts and graph sizes."""
    return store.list_sites()


@mcp.tool()
def site_stats(site_id: str) -> dict:
    """Graph health for one site: doc/edge counts, edge types, verdict, top hubs."""
    try:
        g = store.load(site_id, "graph")
        meta = store.load(site_id, "meta")
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    deg = {}
    for e in g["edges"]:
        deg[e["s"]] = deg.get(e["s"], 0) + 1
        deg[e["t"]] = deg.get(e["t"], 0) + 1
    top = sorted(deg.items(), key=lambda kv: -kv[1])[:5]
    keep = ("url", "docs", "edges", "edgeTypes", "verdict", "built_at")
    return {"ok": True, **{k: meta[k] for k in keep}, "top_degree_nodes": top}


@mcp.tool()
def search(site_id: str, query: str, limit: int = 10) -> dict:
    """Full-text search over the site's pages (inverted index, idf-weighted)."""
    try:
        s = store.load(site_id, "search")
        g = store.load(site_id, "graph")
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    titles = {n["i"]: n["t"] for n in g["nodes"]}
    urls = {n["i"]: n["u"] for n in g["nodes"]}
    hits = graphmod.search_index(s, query, limit)
    for h in hits:
        h["title"] = titles.get(h["id"], "")
        h["url"] = urls.get(h["id"], "")
    return {"ok": True, "hits": hits}


@mcp.tool()
def get_page(site_id: str, page_id: str) -> dict:
    """Full markdown body of one page, with title and source URL."""
    try:
        docs = store.load(site_id, "docs")
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    d = docs.get(page_id)
    if not d:
        return {"ok": False, "error": f"unknown page '{page_id}'", "known": len(docs)}
    return {"ok": True, "id": page_id, "title": d["title"], "url": d["url"], "body": d["body"]}


@mcp.tool()
def neighbors(site_id: str, page_id: str, depth: int = 1) -> dict:
    """Subgraph around a page up to `depth` hops (capped at 2).
    Use after search to expand context."""
    depth = min(depth, 2)
    try:
        g = store.load(site_id, "graph")
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    adj: dict[str, set[str]] = {}
    etype: dict[frozenset, str] = {}
    for e in g["edges"]:
        adj.setdefault(e["s"], set()).add(e["t"])
        adj.setdefault(e["t"], set()).add(e["s"])
        etype[frozenset((e["s"], e["t"]))] = e["k"]
    if page_id not in adj and page_id not in {n["i"] for n in g["nodes"]}:
        return {"ok": False, "error": f"unknown page '{page_id}'"}
    frontier, seen = {page_id}, {page_id}
    for _ in range(depth):
        nxt = set()
        for p in frontier:
            nxt |= adj.get(p, set())
        nxt -= seen
        seen |= nxt
        frontier = nxt
    titles = {n["i"]: n["t"] for n in g["nodes"] if n["i"] in seen}
    es = [{"s": e["s"], "t": e["t"], "k": e["k"]} for e in g["edges"]
          if e["s"] in seen and e["t"] in seen]
    return {"ok": True, "center": page_id, "edges": es,
            "nodes": [{"id": i, "title": t} for i, t in titles.items()]}


@mcp.tool()
def render_site(site_id: str) -> dict:
    """Render the graph as a self-contained HTML viewer (human inspection path).
    Returns the file path; AI-facing queries stay on search/ask/neighbors."""
    try:
        g = store.load(site_id, "graph")
        meta = store.load(site_id, "meta")
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    from .viewer import render_viewer
    out = store.site_dir(site_id) / "viewer.html"
    render_viewer(g, meta, out)
    return {"ok": True, "path": str(out), "pages": len(g["nodes"]), "edges": len(g["edges"])}


@mcp.tool()
def ask(site_id: str, question: str, top_k: int = 4) -> dict:
    """LLM Q&A grounded in the site's pages: keyword retrieval + graph-neighborhood
    expansion, then embedding rerank and a cited answer. Requires SITE_KG_LLM_BASE
    and SITE_KG_LLM_KEY in the server environment."""
    try:
        from . import semantic
        search = store.load(site_id, "search")
        docs = store.load(site_id, "docs")
    except KeyError as e:
        return {"ok": False, "error": str(e)}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    from .graph import search_index
    graph_ctx: list[str] = []
    for pid in [h["id"] for h in search_index(search, question, limit=3)]:
        graph_ctx += [n["id"] for n in neighbors(site_id, pid, depth=1).get("nodes", [])]
    try:
        return semantic.answer({"search": search}, docs, question, graph_ctx[:6], top_k)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.resource("site://{site_id}/graph")
def graph_resource(site_id: str) -> str:
    """The full graph.json of a site."""
    import json
    return json.dumps(store.load(site_id, "graph"), ensure_ascii=False)


def serve(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8766) -> None:
    if transport == "http":
        mcp.settings.host = host
        mcp.settings.port = port
        # stateless: clients holding a pre-restart session id must not 404
        mcp.settings.stateless_http = True
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    serve()
