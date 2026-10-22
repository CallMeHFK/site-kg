"""Live ingestion streams per-page nodes before edges and reports skips."""
from __future__ import annotations

import asyncio
import functools
import http.server
import threading
import time
from pathlib import Path

import site_kg.live as livemod
from site_kg import store
from site_kg.live import LiveHub

PROSE = "streaming prose about ingestion and graph construction " * 8


def _serve(tmp_path: Path, files: dict[str, str]) -> tuple[str, object]:
    site = tmp_path / "site"
    site.mkdir()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    for name, content in files.items():
        (site / name).write_text(content.replace("{base}", base))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return base + "/index.html", srv


def _pages() -> dict[str, str]:
    def page(title: str, links: list[str]) -> str:
        anchors = " ".join(f'<a href="{href}">link</a>' for href in links)
        return (f"<html><head><title>{title}</title></head><body><main>"
                f"<h1>{title}</h1><p>{PROSE}</p>{anchors}</main></body></html>")

    locs = "".join(f"<url><loc>{{base}}/{name}</loc></url>" for name in
                   ("index.html", "alpha.html", "beta.html", "missing.html"))
    return {
        "sitemap.xml": f'<?xml version="1.0"?><urlset>{locs}</urlset>',
        "index.html": page("Index", ["alpha.html", "beta.html", "missing.html"]),
        "alpha.html": page("Alpha", ["index.html"]),
        "beta.html": page("Beta", ["alpha.html"]),
    }


def test_live_ingest_emits_nodes_before_done(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    url, srv = _serve(tmp_path, _pages())
    hub = LiveHub()
    try:
        hub.start(url, "planets")
        deadline = time.monotonic() + 30
        while hub.run["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert hub.run["state"] == "complete", hub.run.get("error")
        sub, events = hub.log.subscribe(0)
        hub.log.unsubscribe(sub)
    finally:
        srv.shutdown()
        hub.close()

    done = next(ev for ev in events if ev["t"] == "ingest_done")
    kinds = [ev["t"] for ev in events]
    nodes = [ev for ev in events if ev["t"] == "node_added"]
    assert len(nodes) == 3
    assert all(ev["seq"] < done["seq"] for ev in nodes)
    edges = [ev for ev in events if ev["t"] == "edge_added"]
    assert edges and all(ev["seq"] < done["seq"] for ev in edges)
    skipped = [ev for ev in events if ev["t"] == "node_skipped"]
    assert any(str(ev.get("reason", "")).startswith("status:404") for ev in skipped)
    assert kinds[-1] == "settled"
    assert events[-1]["seq"] > done["seq"]


def _collect(hub: LiveHub) -> list[dict]:
    sub, events = hub.log.subscribe(0)
    hub.log.unsubscribe(sub)
    return events


def test_nodes_stream_during_fetch_not_after(tmp_path, monkeypatch):
    """The sitemap path fetches after discovery; a node must exist before the
    last page settles, otherwise the scene is empty until the very end."""
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    url, srv = _serve(tmp_path, _pages())
    hub = LiveHub()
    try:
        hub.start(url, "planets")
        assert _wait_state(hub) == "complete", hub.run.get("error")
        events = _collect(hub)
    finally:
        srv.shutdown()
        hub.close()

    nodes = [ev for ev in events if ev["t"] == "node_added"]
    progress = [ev for ev in events if ev["t"] == "progress" and ev.get("pages")]
    assert nodes and progress
    last_fetch = max(ev["seq"] for ev in progress if ev["pages"] >= 3)
    assert nodes[0]["seq"] < last_fetch, "first node must land mid-fetch"
    patches = [ev for ev in events if ev["t"] == "layout_patch"]
    assert patches, "streamed nodes need positions before ingest_done"
    assert patches[0]["seq"] < max(ev["seq"] for ev in nodes)


def test_bfs_streams_nodes_while_discovering(tmp_path, monkeypatch):
    """The BFS inventory already fetched the HTML; emit the node then, not
    after the whole crawl returns its URL list."""
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    url, srv = _serve(tmp_path, _bfs_pages())
    hub = LiveHub()
    try:
        hub.start(url, "planets")
        assert _wait_state(hub) == "complete", hub.run.get("error")
        events = _collect(hub)
    finally:
        srv.shutdown()
        hub.close()

    nodes = [ev for ev in events if ev["t"] == "node_added"]
    final_discover = max(ev["seq"] for ev in events
                         if ev["t"] == "discover" and ev.get("found") == 3)
    assert nodes[0]["seq"] < final_discover


def _wait_state(hub: LiveHub, timeout: float = 15.0) -> str:
    deadline = time.monotonic() + timeout
    while hub.run["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    return str(hub.run["state"])


def _bfs_pages() -> dict[str, str]:
    """No sitemap/objects.inv: forces the BFS inventory path."""
    def page(title: str, links: list[str]) -> str:
        anchors = " ".join(f'<a href="{href}">link</a>' for href in links)
        return (f"<html><head><title>{title}</title></head><body><main>"
                f"<h1>{title}</h1><p>{PROSE}</p>{anchors}</main></body></html>")

    return {
        "index.html": page("Index", ["alpha.html", "beta.html", "missing.html"]),
        "alpha.html": page("Alpha", ["index.html"]),
        "beta.html": page("Beta", ["alpha.html"]),
    }


def test_live_ingest_emits_discover_events(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    url, srv = _serve(tmp_path, _bfs_pages())
    hub = LiveHub()
    try:
        hub.start(url, "planets")
        assert _wait_state(hub) == "complete", hub.run.get("error")
        sub, events = hub.log.subscribe(0)
        hub.log.unsubscribe(sub)
    finally:
        srv.shutdown()
        hub.close()

    discover = [ev for ev in events if ev["t"] == "discover"]
    assert discover, "BFS must stream discover frames"
    for ev in discover:
        assert set(ev) >= {"t", "found", "failed", "depth"}
        assert isinstance(ev["found"], int)
        assert isinstance(ev["failed"], int)
        assert isinstance(ev["depth"], int)
    found = [ev["found"] for ev in discover]
    assert found == sorted(found), "discover counters never regress"
    assert found[-1] == 3
    assert max(ev["failed"] for ev in discover) >= 1


def test_discovery_timeout_emits_error_and_marks_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    monkeypatch.setattr(livemod, "DISCOVERY_TIMEOUT_S", 0.05)

    async def slow_inventory(*args, **kwargs):
        await asyncio.sleep(5)
        return [], "bfs", {}

    monkeypatch.setattr(livemod.manifest, "page_inventory", slow_inventory)
    hub = LiveHub()
    try:
        hub.start("http://127.0.0.1:1/", "planets")
        assert _wait_state(hub) == "failed"
        sub, events = hub.log.subscribe(0)
        hub.log.unsubscribe(sub)
    finally:
        hub.close()

    assert "discovery timed out" in str(hub.run["error"])
    errors = [ev for ev in events if ev["t"] == "error"]
    assert errors and "discovery timed out" in errors[-1]["error"]
    assert errors[-1]["error"] == hub.run["error"]


def test_fetch_timeout_emits_error_and_marks_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "sites")
    monkeypatch.setattr(livemod, "FETCH_TIMEOUT_S", 0.05)

    async def fast_inventory(base, **kwargs):
        callback = kwargs.get("on_discover")
        if callback:
            callback(1, 0, 0)
        return ["http://127.0.0.1:1/index.html"], "bfs", {"errors": {},
                                                          "failed": 0,
                                                          "discovered": 1}

    async def slow_fetch(*args, **kwargs):
        await asyncio.sleep(5)
        return {}, {}

    monkeypatch.setattr(livemod.manifest, "page_inventory", fast_inventory)
    monkeypatch.setattr(livemod.ingestmod, "fetch_pages", slow_fetch)
    hub = LiveHub()
    try:
        hub.start("http://127.0.0.1:1/", "planets")
        assert _wait_state(hub) == "failed"
        sub, events = hub.log.subscribe(0)
        hub.log.unsubscribe(sub)
    finally:
        hub.close()

    assert "fetch timed out" in str(hub.run["error"])
    assert [ev for ev in events if ev["t"] == "discover"]
    errors = [ev for ev in events if ev["t"] == "error"]
    assert errors and "fetch timed out" in errors[-1]["error"]
    assert hub.health()["state"] == "failed"
    assert hub.health()["error"] == hub.run["error"]

