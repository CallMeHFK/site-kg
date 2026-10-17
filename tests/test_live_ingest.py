"""Live ingestion streams per-page nodes before edges and reports skips."""
from __future__ import annotations

import functools
import http.server
import threading
import time
from pathlib import Path

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
