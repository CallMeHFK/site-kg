"""Dev/test crawl mode. Opt-in, for sites you own or are authorized to test:
robots.txt and politeness delays are skipped so authorized deep crawls are fast.
Offline: no network -- fetches against .invalid hosts only prove control flow."""
from __future__ import annotations

import asyncio
import sys

from site_kg import cli, ingest, manifest, mcp_server, store


class Deny:
    def can_fetch(self, ua, url):
        return False

    def read(self):
        return None


# ---- fetch_pages: dev forces robots off + no delay -------------------------
def test_fetch_pages_dev_mode_skips_robots_and_fetches_anyway(monkeypatch):
    monkeypatch.setattr(ingest, "_robots", lambda base: Deny())

    async def run():
        _pages, stats = await ingest.fetch_pages(
            ["http://127.0.0.1:9/a/"], "http://127.0.0.1:9/a/", dev=True)
        assert stats.get("dev") is True
        assert stats["robots_blocked"] == 0          # robots never consulted
        assert stats["errors"]                        # the fetch itself was attempted

    asyncio.run(run())


def test_fetch_pages_dev_flag_beats_respect_robots_true(monkeypatch):
    monkeypatch.setattr(ingest, "_robots", lambda base: Deny())

    async def run():
        _pages, stats = await ingest.fetch_pages(
            ["http://127.0.0.1:9/a/"], "http://127.0.0.1:9/a/",
            respect_robots=True, dev=True)
        assert stats["robots_blocked"] == 0

    asyncio.run(run())


def test_fetch_pages_without_dev_still_blocks(monkeypatch):
    monkeypatch.setattr(ingest, "_robots", lambda base: Deny())

    async def run():
        pages, stats = await ingest.fetch_pages(
            ["http://127.0.0.1:9/a/"], "http://127.0.0.1:9/a/",
            respect_robots=True)
        assert pages == {}
        assert stats["robots_blocked"] == 1

    asyncio.run(run())


# ---- discovery: BFS honours robots unless dev ------------------------------
class _Resp:
    status_code = 200
    text = '<html><body><a href="/b/">b</a></body></html>'

    def __init__(self) -> None:
        self.headers = {"content-type": "text/html"}


class _Client:
    async def get(self, url, **_kw):
        return _Resp()


def test_bfs_respects_robots_by_default(monkeypatch):
    monkeypatch.setattr(ingest, "_robots", lambda base: Deny())
    stats: dict = {"errors": {}}
    urls = asyncio.run(manifest.bfs(_Client(), "https://x.example/", 10, 3, stats))
    assert urls == []
    assert stats["robots_blocked"] == 1          # the seed was refused


def test_bfs_dev_mode_crawls_past_robots(monkeypatch):
    monkeypatch.setattr(ingest, "_robots", lambda base: Deny())
    stats: dict = {"errors": {}}
    urls = asyncio.run(manifest.bfs(_Client(), "https://x.example/", 10, 3, stats,
                                    respect_robots=False))
    assert urls == ["https://x.example/", "https://x.example/b/"]
    assert stats.get("robots_blocked", 0) == 0


def test_page_inventory_passes_dev_to_bfs(monkeypatch):
    seen = {}

    async def none(client, base):
        return None

    async def fake_bfs(client, base, max_pages, max_depth, stats, respect_robots=True):
        seen["respect_robots"] = respect_robots
        return ["https://x.example/"]

    # stub the higher-priority discovery sources so no real network is touched
    monkeypatch.setattr(manifest, "from_objects_inv", none)
    monkeypatch.setattr(manifest, "from_doxygen_navtree", none)
    monkeypatch.setattr(manifest, "from_sitemap", none)
    monkeypatch.setattr(manifest, "bfs", fake_bfs)
    asyncio.run(manifest.page_inventory("https://x.example/zzz", dev=True))
    assert seen["respect_robots"] is False
    asyncio.run(manifest.page_inventory("https://x.example/zzz"))
    assert seen["respect_robots"] is True


# ---- CLI: --dev-crawl threads through with deep defaults -------------------
def test_cli_dev_crawl_threads_through(monkeypatch):
    captured: dict = {}

    async def fake_ingest_url(url, **kw):
        captured.update(kw)
        return {}

    monkeypatch.setattr(mcp_server, "ingest_url", fake_ingest_url)
    monkeypatch.setattr(sys, "argv",
                        ["site-kg", "ingest", "https://x.example/", "--dev-crawl"])
    cli.main()
    assert captured["dev"] is True
    assert captured["max_pages"] == 2000 and captured["max_depth"] == 8

    monkeypatch.setattr(sys, "argv", ["site-kg", "ingest", "https://x.example/"])
    cli.main()
    assert captured["dev"] is False
    assert captured["max_pages"] == 200 and captured["max_depth"] == 4

    # an explicit cap beats the dev default
    monkeypatch.setattr(sys, "argv",
                        ["site-kg", "ingest", "https://x.example/",
                         "--dev-crawl", "--max-pages", "50"])
    cli.main()
    assert captured["max_pages"] == 50 and captured["max_depth"] == 8


def test_cli_live_dev_crawl_flag(monkeypatch):
    from site_kg import cli, live
    captured: dict = {}

    def fake_serve_live(site_id, **kw):
        captured.update(kw)

        class _H:
            url = "http://127.0.0.1:0"

            def close(self):
                pass

        return _H()

    monkeypatch.setattr(live, "serve_live", fake_serve_live)
    # cli.main() parks in _wait_live() until Ctrl-C; stub it out
    monkeypatch.setattr(cli, "_wait_live", lambda handle: handle.close())
    monkeypatch.setattr(sys, "argv",
                        ["site-kg", "live", "https://x.example/", "--scene", "planets",
                         "--dev-crawl"])
    cli.main()
    assert captured["dev_crawl"] is True


# ---- MCP: dev reaches both crawl phases ------------------------------------
def test_mcp_ingest_url_dev_reaches_inventory_and_fetch(tmp_path, monkeypatch):
    captured: dict = {}

    async def fake_inventory(url, max_pages=500, max_depth=4, dev=False):
        captured["inv"] = {"dev": dev, "max_pages": max_pages}
        return ["https://x.example/a/"], "bfs", {"errors": {}}

    async def fake_fetch(urls, base, concurrency=4, delay=0.2, respect_robots=True,
                         on_page=None, dev=False):
        captured["fetch"] = {"dev": dev, "respect_robots": respect_robots}
        return {"https://x.example/a/": "<html><body><main>hi</main></body></html>"}, \
            {"robots_blocked": 0, "errors": {}, "skipped": 0}

    monkeypatch.setattr(manifest, "page_inventory", fake_inventory)
    monkeypatch.setattr(ingest, "fetch_pages", fake_fetch)
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    import asyncio as aio
    out = aio.run(mcp_server.ingest_url("https://x.example/", dev=True))
    assert out["ok"] is True
    assert captured["inv"] == {"dev": True, "max_pages": 200}
    assert captured["fetch"]["dev"] is True
    assert captured["fetch"]["respect_robots"] is False


def test_mcp_empty_discovery_reports_robots_block(tmp_path, monkeypatch):
    # bfs-side robots blocking used to surface as an opaque "no pages discovered"
    async def fake_inventory(url, max_pages=500, max_depth=4, dev=False):
        return [], "bfs", {"errors": {}, "robots_blocked": 3}

    monkeypatch.setattr(manifest, "page_inventory", fake_inventory)
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    out = asyncio.run(mcp_server.ingest_url("https://x.example/"))
    assert out["ok"] is False
    assert "robots" in out["error"] and out["robots_blocked"] == 3
