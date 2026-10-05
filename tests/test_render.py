"""JS-shell detection and Playwright fallback tests. The SPA fixture runs a
loopback http.server -- no external network."""
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
        urls, _src = await manifest.page_inventory(url, max_pages=10)
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


def test_viewer_file_is_self_contained(tmp_path: Path):
    import json

    from site_kg.viewer import render_viewer
    g = {"nodes": [{"i": "a", "c": "c1", "t": "A", "u": "http://x/a", "g": 1},
                   {"i": "b", "c": "c2", "t": "B", "u": "http://x/b", "g": 0}],
         "edges": [{"s": "a", "t": "b", "k": "ref"}]}
    out = render_viewer(g, {"url": "http://x/", "docs": 2, "verdict": "READY"},
                        tmp_path / "viewer.html")
    s = out.read_text(encoding="utf-8")
    assert "<canvas" in s and "const G = " in s
    data = s.split("const G = ", 1)[1].split(";", 1)[0]
    assert json.loads(data)["nodes"][0]["i"] == "a"
    head = s.split("</head>", 1)[0]
    assert "<script src" not in head and "<link" not in head  # no external resources
