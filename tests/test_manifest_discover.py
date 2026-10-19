"""BFS discovery reports progress and failures instead of staying silent."""
from __future__ import annotations

import asyncio
import functools
import http.server
import threading
from pathlib import Path

from site_kg import manifest


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


def _page(title: str, links: list[str]) -> str:
    anchors = " ".join(f'<a href="{href}">link</a>' for href in links)
    return (f"<html><head><title>{title}</title></head><body><main>"
            f"<h1>{title}</h1>{anchors}</main></body></html>")


def test_bfs_reports_discovery_progress_and_failures(tmp_path):
    url, srv = _serve(tmp_path, {
        "index.html": _page("Index", ["alpha.html", "beta.html", "missing.html"]),
        "alpha.html": _page("Alpha", ["index.html"]),
        "beta.html": _page("Beta", ["alpha.html"]),
    })
    calls: list[tuple[int, int, int]] = []
    try:
        urls, source, stats = asyncio.run(manifest.page_inventory(
            url, max_pages=10,
            on_discover=lambda found, depth, failed: calls.append(
                (found, depth, failed))))
    finally:
        srv.shutdown()

    assert source == "bfs"
    assert len(urls) == 3  # index + alpha + beta; missing.html is a 404
    assert calls, "on_discover must fire during BFS"
    assert all(isinstance(n, int) for call in calls for n in call)
    found = [call[0] for call in calls]
    assert found == sorted(found), "found count never steps backwards"
    assert found[-1] == len(urls)
    assert calls[-1][2] >= 1, "the 404 link is counted as a failed fetch"
    assert stats["discovered"] == len(urls)
    assert stats["failed"] >= 1
    assert stats["errors"].get("status:404") == 1


def test_page_inventory_without_callback_keeps_old_contract(tmp_path):
    """The optional callback must not change the return shape or default path."""
    url, srv = _serve(tmp_path, {"index.html": _page("Index", ["alpha.html"]),
                                 "alpha.html": _page("Alpha", [])})
    try:
        urls, source, stats = asyncio.run(
            manifest.page_inventory(url, max_pages=10))
    finally:
        srv.shutdown()
    assert source == "bfs"
    assert len(urls) == 2
    assert isinstance(stats, dict)
    assert stats["failed"] == 0
    assert stats["discovered"] == 2
