"""Offline regression tests -- no network. Run: pytest -q"""
from pathlib import Path

import pytest

from site_kg import graph, ingest, manifest


def test_page_id_pretty_urls():
    base = "https://docs.astral.sh/uv/"
    assert manifest.page_id(base, "https://docs.astral.sh/uv/") == "index"
    assert manifest.page_id(base, "https://docs.astral.sh/uv/concepts/") == "concepts"
    assert manifest.page_id(base, "https://docs.astral.sh/uv/concepts/authentication/") == "concepts_authentication"
    # extension-less within a file seed's directory
    b2 = "https://vuejs.org/guide/introduction.html"
    assert manifest.page_id(b2, "https://vuejs.org/guide/essentials/reactivity.html") == "essentials_reactivity"
    assert manifest.page_id(b2, "https://vuejs.org/guide/introduction") == "introduction"
    # mediawiki
    b3 = "https://wiki.archlinux.org/title/Main_page"
    assert manifest.page_id(b3, "https://wiki.archlinux.org/title/Package_management") == "Package_management"


def test_same_site_scopes_to_seed_dir():
    base = "https://docusaurus.io/docs/installation"
    assert manifest.same_site(base, "https://docusaurus.io/docs/cli")
    assert not manifest.same_site(base, "https://docusaurus.io/blog/post")
    assert not manifest.same_site(base, "https://other.io/docs/x")


def test_internal_links_catch_pretty_urls():
    from bs4 import BeautifulSoup
    html = '<a href="/uv/concepts/">c</a><a href="https://docs.astral.sh/uv/help/">h</a>' \
           '<a href="#frag">f</a><a href="mailto:x@y">m</a><a href="https://ext.example/x">e</a>'
    soup = BeautifulSoup(html, "html.parser")
    links = ingest._internal_links(soup, "https://docs.astral.sh/uv/", "https://docs.astral.sh/uv/", "index")
    assert links == ["concepts", "help"]


def test_graph_ready_and_not_ready_gates(tmp_path: Path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "one.md").write_text("---\ntitle: One\n---\n\nprose only, zero links")
    (a / "two.md").write_text("---\ntitle: Two\n---\n\nmore prose")
    r = graph.build_graph(a)["report"]
    assert r["verdict"] == "NOT-READY" and r["edges"] == 0

    b = tmp_path / "b"
    b.mkdir()
    (b / "one.md").write_text("---\ntitle: One\n---\n\nsee [two](two.md)")
    (b / "two.md").write_text("---\ntitle: Two\n---\n\nback to [one](one.md)")
    built = graph.build_graph(b)
    assert built["report"]["verdict"] == "READY"
    assert built["report"]["edgeTypes"].get("ref", 0) >= 2


def test_search_scores_rare_terms_higher(tmp_path: Path):
    c = tmp_path / "c"
    c.mkdir()
    (c / "x.md").write_text("---\ntitle: X\n---\n\nkernel driver kernel driver common")
    (c / "y.md").write_text("---\ntitle: Y\n---\n\ncommon unrelated text here too")
    # df ratio cap needs enough docs before a term counts as "common"
    for n in range(3):
        (c / f"filler{n}.md").write_text(f"---\ntitle: F{n}\n---\n\nordinary filler prose {n}")
    built = graph.build_graph(c)
    hits = graph.search_index(built["search"], "kernel driver", 5)
    assert hits and hits[0]["id"] == "x"


def test_page_id_stable_for_classic_sphinx_sites():
    # regression guard: ids of the originally verified sites must not drift
    base = "https://docs.example-vendor.com/docs/drive/drive-os/7.0.3/public/drive-os-linux-sdk/index.html"
    assert manifest.page_id(base, base) == "index"
    assert manifest.page_id(base, "https://docs.example-vendor.com/docs/drive/drive-os/7.0.3/public/drive-os-linux-sdk/core-concepts/IST.html") == "core-concepts_IST"
    b2 = "https://docs.pipewire.org/page_api.html"
    assert manifest.page_id(b2, "https://docs.pipewire.org/page_modules.html") == "page_modules"


def test_sitemap_index_child_pages_are_flattened():
    import asyncio, httpx

    index = '<?xml version="1.0"?><sitemapindex><sitemap><loc>https://s.example/docs-sitemap.xml</loc></sitemap></sitemapindex>'
    child = ('<?xml version="1.0"?><urlset>'
             '<url><loc>https://s.example/docs/a/</loc></url>'
             '<url><loc>https://s.example/docs/b/</loc></url></urlset>')

    class FakeResp:
        def __init__(self, text): self.text, self.status_code = text, 200

    class FakeClient:
        async def get(self, url):
            return FakeResp(child if "docs-sitemap" in url else index)

    urls = asyncio.run(manifest.from_sitemap(FakeClient(), "https://s.example/docs/"))
    assert urls == ["https://s.example/docs/a/", "https://s.example/docs/b/"]


def test_robots_blocked_is_surfaced_not_silent():
    # unit level: fetch_pages must report robots_blocked rather than returning {} quietly
    import asyncio

    async def run():
        orig = ingest._robots

        class Deny:
            def can_fetch(self, ua, url):
                return False

        ingest._robots = lambda base: Deny()
        try:
            pages, stats = await ingest.fetch_pages(["https://example.invalid/a/"], "https://example.invalid/a/",
                                                    respect_robots=True)
        finally:
            ingest._robots = orig
        assert pages == {}
        assert stats["robots_blocked"] == 1

    asyncio.run(run())
