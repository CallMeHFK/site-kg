"""JS-shell detection and Playwright fallback tests. The SPA fixture runs a
loopback http.server -- no external network."""
import math
import re
from pathlib import Path

import pytest

from site_kg.render import better_than_static, is_js_shell

SHELL = (
    '<html><head><title>x</title></head><body><div id="app"></div>'
    '<script>document.getElementById("app").innerHTML=`<p>real text</p>`</script></body></html>'
)
CONTENT = ("<html><body><main><h1>Alpha</h1><p>" + "real prose " * 40 +
           '</p><a href="b.html">next</a></main></body></html>')


def test_shell_detection():
    assert is_js_shell(SHELL)
    assert not is_js_shell(CONTENT)


def test_adoption_is_comparative_not_thresholded():
    # a rendered DOM with little text still beats a shell
    rendered = "<html><body><div id=\"app\"><p>" + "x " * 90 + "</p></div></body></html>"
    assert better_than_static(rendered, SHELL)
    assert not better_than_static(rendered, CONTENT)


SPA = {
    "index.html": '''<!doctype html><html><head><title>SPA shell</title></head>
<body><div id="app"></div><script>
document.getElementById("app").innerHTML = `<main><h1>Alpha module</h1>
<p>The alpha module computes flux capacitance through the beta channel.</p>
<a href="page2.html">Beta module</a></main>`;
</script></body></html>''',
    "page2.html": '''<!doctype html><html><head><title>Beta module</title></head>
<body><main><h1>Beta module</h1><p>The beta channel carries the flux signal.
Return to the <a href="index.html">alpha module</a>.</p></main></body></html>''',
}


def test_csr_spa_full_pipeline(tmp_path: Path):
    """Negative control: static fetch of index is a shell. Positive: the same
    pipeline discovers + renders the SPA and builds a real graph."""
    pytest.importorskip("playwright.async_api")
    import asyncio
    import functools
    import http.server
    import threading

    from site_kg import graph, ingest, manifest

    site = tmp_path / "site"
    site.mkdir()
    for n, c in SPA.items():
        (site / n).write_text(c)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"

    async def run():
        urls, _src, _inv_stats = await manifest.page_inventory(url, max_pages=10)
        pages, stats = await ingest.fetch_pages(urls, url, respect_robots=False)
        corpus = tmp_path / "corpus"
        ingest.write_corpus(pages, url, corpus)
        built = graph.build_graph(corpus)
        return urls, stats, built

    urls, stats, built = asyncio.run(run())
    srv.shutdown()
    assert len(urls) == 2
    assert stats.get("js_rendered", 0) >= 1
    assert built["report"]["verdict"] == "READY"
    assert built["report"]["edges"] >= 2
    assert "flux capacitance" in built["docs"]["index"]["body"]


def _serve(tmp_path: Path, files: dict[str, str]) -> tuple[str, object]:
    """Loopback http.server over a fixture dir; returns (base_url, server)."""
    import functools
    import http.server
    import threading

    site = tmp_path / "site"
    site.mkdir()
    for n, c in files.items():
        (site / n).write_text(c)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


def _no_renderer(monkeypatch):
    """Simulate playwright not installed: render_urls raises on import-time use."""
    async def boom(urls, proxy=None, timeout_ms=20000):
        raise ModuleNotFoundError("No module named 'playwright'")
    from site_kg import render
    monkeypatch.setattr(render, "render_urls", boom)


def test_fetch_pages_degrades_when_renderer_missing(tmp_path: Path, monkeypatch):
    """Regression: renderer absent must keep static HTML and record the skip,
    not crash the crawl (was ModuleNotFoundError escaping fetch_pages)."""
    import asyncio

    from site_kg import ingest

    base, srv = _serve(tmp_path, {"shell.html": SHELL})
    _no_renderer(monkeypatch)
    url = f"{base}/shell.html"
    pages, stats = asyncio.run(ingest.fetch_pages([url], url, respect_robots=False))
    srv.shutdown()
    assert url in pages  # static HTML kept
    assert stats["js_render_skipped"] == 1
    assert stats["errors"].get("ModuleNotFoundError") == 1


def test_bfs_records_render_skip_when_renderer_missing(tmp_path: Path, monkeypatch):
    """The BFS inventory probe hits the same renderer; it must degrade and count
    the skip in inventory stats instead of silently passing."""
    import asyncio

    from site_kg import manifest

    base, srv = _serve(tmp_path, {"index.html": SHELL})
    _no_renderer(monkeypatch)
    url = f"{base}/index.html"
    urls, source, stats = asyncio.run(manifest.page_inventory(url, max_pages=5))
    srv.shutdown()
    assert source == "bfs" and urls == [url]  # shell kept, links undiscovered
    assert stats["js_render_skipped"] == 1
    assert stats["errors"].get("ModuleNotFoundError") == 1


def test_viewer_file_is_self_contained(tmp_path: Path):
    from site_kg import payload
    from site_kg.viewer import render_viewer

    g = {"nodes": [{"i": "a", "c": "c1", "t": "A", "u": "http://x/a", "g": 1},
                   {"i": "b", "c": "c2", "t": "B", "u": "http://x/b", "g": 0}],
         "edges": [{"s": "a", "t": "b", "k": "ref"}]}
    out = render_viewer(g, {"url": "http://x/", "docs": 2, "verdict": "READY"},
                        tmp_path / "viewer.html", scene="neural")
    s = out.read_text(encoding="utf-8")
    for marker in ("SK.LiveGraph", "SK.Renderer", "SK.GpuRenderer", "SK.Camera",
                   "SK.Director", "SK.UI"):
        assert marker in s  # modules assembled into the bundle
    assert "fn vs_node" in s  # the WebGPU backend ships its WGSL, not an import
    assert "fn vs_city" in s  # the city scene rides in the same bundle
    assert '"scene":"neural"' in s
    assert "T3" in s  # vendor bundle embedded
    assert "<script src" not in s and "<link" not in s  # no external resources

    m = re.search(r'<script[^>]*id="sk-payload"[^>]*>([^<]+)</script>', s)
    assert m, "embedded payload missing"
    parts = payload.unpack_archive_payload(m.group(1).strip())
    graph = payload.decode_graph(parts["graph"])
    layout = payload.decode_layout(parts["layout"])
    timeline = payload.decode_timeline(parts["timeline"])
    assert [n["i"] for n in graph["nodes"]] == ["a", "b"]
    assert graph["nodes"][0]["t"] == "A"
    assert len(layout["nodes"]) == 2 and len(timeline["nodes"]) == 2
    assert all(math.isfinite(n[k]) for n in layout["nodes"] for k in ("x", "y", "z"))
    assert max(abs(n[k]) for n in layout["nodes"] for k in ("x", "y", "z")) <= 1.0
    assert timeline["run_id"]


def test_live_viewer_carries_no_baked_stats(tmp_path: Path):
    """Live graphs are empty at page load, so cfg.stats would bake
    '0 pages · 0 edges · verdict ?' into the finale forever. The live
    shell must ship no stats string; the finale reads director status."""
    from site_kg.viewer import render_viewer

    out = render_viewer({"nodes": [], "edges": []}, {"url": "http://x/"},
                        tmp_path / "live.html", scene="neural", mode="live")
    s = out.read_text(encoding="utf-8")
    assert '"stats":""' in s
    assert "0 pages" not in s and "verdict ?" not in s


def test_city_scene_bundles_and_selects(tmp_path: Path):
    """The city is a first-class world: its module ships, its name validates,
    and the shell records the scene so the launcher lands on it."""
    from site_kg.viewer import SCENES, render_viewer

    assert "city" in SCENES
    out = render_viewer({"nodes": [], "edges": []}, {"url": "http://x/"},
                        tmp_path / "city.html", scene="city", mode="live")
    s = out.read_text(encoding="utf-8")
    assert '"scene":"city"' in s
    assert "SK.cityCells" in s and "SK.citySpec" in s
    assert 'sky: "skyline"' in s
    with pytest.raises(ValueError):
        render_viewer({"nodes": [], "edges": []}, {"url": "http://x/"},
                      tmp_path / "bad.html", scene="atlantis")


def test_launcher_art_is_live_only(tmp_path: Path):
    """The poster's captures are base64 JPEGs inlined only for live shells;
    archive shells never show the launcher and must stay lean."""
    from site_kg.viewer import render_viewer

    g = {"nodes": [{"i": "a", "c": "c1", "t": "A", "u": "http://x/a", "g": 1}],
         "edges": []}
    live = render_viewer(g, {"url": "http://x/"}, tmp_path / "live.html",
                         mode="live").read_text(encoding="utf-8")
    archive = render_viewer(g, {"url": "http://x/"}, tmp_path / "archive.html",
                            mode="archive").read_text(encoding="utf-8")
    assert "data:image/jpeg;base64," in live
    assert "data:image/jpeg;base64," not in archive
    assert ('<script type="application/json" id="sk-launch-art">{}</script>'
            in archive)


def test_archive_cfg_carries_real_doc_count(tmp_path: Path):
    """The HUD's pages field is the crawled doc count (meta.docs), not the
    node count after max_nodes trimming; live shells have no doc count."""
    from site_kg.viewer import render_viewer

    g = {"nodes": [{"i": "a", "c": "c1", "t": "A", "u": "http://x/a", "g": 1},
                   {"i": "b", "c": "c2", "t": "B", "u": "http://x/b", "g": 0}],
         "edges": [{"s": "a", "t": "b", "k": "ref"}]}
    archive = render_viewer(g, {"url": "http://x/", "docs": 60},
                            tmp_path / "archive.html", max_nodes=1,
                            mode="archive").read_text(encoding="utf-8")
    live = render_viewer(g, {"url": "http://x/"}, tmp_path / "live.html",
                         mode="live").read_text(encoding="utf-8")
    assert '"docs":60' in archive
    assert '"docs":null' in live
