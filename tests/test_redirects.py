"""Redirect semantics for the crawler (issue #28): every hop re-checked
against robots and site scope, results keyed by the final URL, hostile
path characters neutralized in page ids, BFS paced like fetch_pages.

Offline: all HTTP goes through httpx.MockTransport; robots comes from stubs.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from site_kg import ingest, manifest

REAL_ASYNC_CLIENT = httpx.AsyncClient


class Allow:
    def can_fetch(self, ua, url):
        return True


class DenyPrivate:
    def can_fetch(self, ua, url):
        return "/private" not in url


def _patch_http(monkeypatch, handler):
    """Inject a MockTransport into every AsyncClient the crawler builds."""
    def factory(**kw):
        kw.pop("follow_redirects", None)
        return REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw)
    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _patch_robots(monkeypatch, rp):
    async def fake(base):
        return rp
    monkeypatch.setattr(ingest, "_robots_async", fake)


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html"})


def _redir(loc: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": loc})


# ---- fetch_pages: hops are re-checked, never delegated to httpx -----------
def test_redirect_into_robots_denied_path_is_refused(monkeypatch):
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/latest/":
            return _redir("/private/x")
        if request.url.path == "/private/x":
            return _html("<html><body>secret</body></html>")
        return httpx.Response(404)

    _patch_http(monkeypatch, handler)
    _patch_robots(monkeypatch, DenyPrivate())

    async def run():
        pages, stats = await ingest.fetch_pages(
            ["http://t.local/latest/"], "http://t.local/", respect_robots=True)
        return pages, stats

    pages, stats = asyncio.run(run())
    assert pages == {}
    assert stats["robots_blocked"] == 1
    assert "/private/x" not in asked  # the hop is refused BEFORE the request


def test_offsite_redirect_is_refused_without_fetching_the_target(monkeypatch):
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.host == "t.local":
            return _redir("http://evil.local/x")
        return _html("<html><body>gotcha</body></html>")

    _patch_http(monkeypatch, handler)
    _patch_robots(monkeypatch, Allow())

    async def run():
        return await ingest.fetch_pages(
            ["http://t.local/a"], "http://t.local/", respect_robots=True)

    pages, stats = asyncio.run(run())
    assert pages == {}
    assert stats["offsite_redirect"] == 1
    assert not any("evil.local" in u for u in asked)


def test_final_url_keys_the_page_and_resolves_relative_links(monkeypatch):
    body = '<html><body><a href="child/">next</a></body></html>'
    streamed = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/docs/latest":
            return _redir("/docs/v2/guide/")
        assert request.url.path == "/docs/v2/guide/"
        return _html(body)

    _patch_http(monkeypatch, handler)
    _patch_robots(monkeypatch, Allow())

    async def run():
        return await ingest.fetch_pages(
            ["http://t.local/docs/latest"], "http://t.local/docs/",
            respect_robots=True,
            on_page=lambda u, p, r: streamed.append((u, p is not None, r)))

    pages, _stats = asyncio.run(run())
    assert list(pages) == ["http://t.local/docs/v2/guide/"]  # keyed by the final URL
    # the streamed node uses the final URL too: the live viewer's node_added id
    # must match the id the built graph will carry (no ghost nodes)
    assert streamed == [("http://t.local/docs/v2/guide/", True, None)]
    doc = ingest.to_markdown(pages["http://t.local/docs/v2/guide/"],
                             "http://t.local/docs/v2/guide/", "http://t.local/docs/")
    assert doc["id"] == "v2_guide"          # id derived from the final path
    assert doc["links"] == ["v2_guide_child"]  # relative link resolved from it too


def test_two_urls_redirecting_to_one_target_merge(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/a", "/b"):
            return _redir("/t")
        assert request.url.path == "/t"
        return _html("<html><body>t</body></html>")

    _patch_http(monkeypatch, handler)
    _patch_robots(monkeypatch, Allow())

    async def run():
        return await ingest.fetch_pages(
            ["http://t.local/a", "http://t.local/b"], "http://t.local/",
            respect_robots=True)

    pages, stats = asyncio.run(run())
    assert list(pages) == ["http://t.local/t"]
    assert stats["redirect_merged"] == 1


def test_redirect_loop_is_capped(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/loop"
        return _redir("/loop")

    _patch_http(monkeypatch, handler)
    _patch_robots(monkeypatch, Allow())

    async def run():
        return await ingest.fetch_pages(
            ["http://t.local/loop"], "http://t.local/", respect_robots=True)

    pages, stats = asyncio.run(run())
    assert pages == {}
    assert stats["errors"]["too_many_hops"] == 1


# ---- bfs: same hop rules, final URLs, politeness ---------------------------
def test_bfs_paces_its_fetches(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/docs/":
            return _html('<html><body><a href="child/">c</a></body></html>')
        assert request.url.path == "/docs/child/"
        return _html("<html><body>child</body></html>")

    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(manifest, "_sleep", fake_sleep)
    _patch_robots(monkeypatch, Allow())

    async def run():
        stats = {"errors": {}}
        client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))
        async with client:
            return await manifest.bfs(client, "http://t.local/docs/", 10, 2,
                                      stats, delay=0.2)

    urls = asyncio.run(run())
    assert urls == ["http://t.local/docs/", "http://t.local/docs/child/"]
    assert sleeps == [0.2, 0.2]  # one pause per settled fetch, like fetch_pages


def test_bfs_keys_pages_by_final_url(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/docs/latest":
            return _redir("/docs/v2/")
        assert request.url.path == "/docs/v2/"
        return _html('<html><body><a href="kid/">k</a></body></html>')

    _patch_robots(monkeypatch, Allow())

    async def run():
        stats = {"errors": {}}
        client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))
        async with client:
            found = {}
            urls = await manifest.bfs(client, "http://t.local/docs/latest", 10, 1,
                                      stats, on_page_found=lambda u, h: found.update({u: h}),
                                      delay=0.0)
            return urls, found

    urls, found = asyncio.run(run())
    assert urls == ["http://t.local/docs/v2/"]
    assert "http://t.local/docs/v2/" in found  # the streamed node uses the final url
    assert "http://t.local/docs/latest" not in found


# ---- page_id: hostile paths stay inside the corpus dir ---------------------
@pytest.mark.parametrize("url, base, expected", [
    # hostile: backslash / colon / star are path-hostile on Windows
    ("https://s.io/x/a\\b", "https://s.io/x/", "a_5Cb"),
    ("https://s.io/x/a:b*c", "https://s.io/x/", "a_3Ab_2Ac"),
    ("https://s.io/x/..%2F..%2Fetc", "https://s.io/x/", "_252F-_252Fetc"),
    ("https://s.io/x/..", "https://s.io/x/", "index"),
    # benign shapes unchanged, dots preserved for versioned ids
    ("https://s.io/x/v1.2/guide", "https://s.io/x/", "v1.2_guide"),
    ("https://s.io/x/concepts/authentication/", "https://s.io/x/",
     "concepts_authentication"),
    ("https://s.io/x/Package_management", "https://s.io/x/", "Package_management"),
    ("https://s.io/x/", "https://s.io/x/", "index"),
])
def test_page_id_neutralizes_path_hostile_characters(url, base, expected):
    assert manifest.page_id(base, url) == expected


def test_page_id_keeps_distinct_paths_distinct():
    # percent-encoding before the charset fold: exotic slugs must not collide
    # onto one id and silently overwrite each other's corpus file
    base = "https://s.io/x/"
    ids = {manifest.page_id(base, u) for u in (
        "https://s.io/x/a:b",     # -> a_3Ab
        "https://s.io/x/a/b",     # -> a_b (slash fold, the long-standing scheme)
        "https://s.io/x/a..b",    # -> a-b (dot-runs fold to '-')
        "https://s.io/x/.hidden", # -> _hidden (edge dots fold to one '_')
        "https://s.io/x/hidden",  # -> hidden
    )}
    assert len(ids) == 5


def test_bfs_crawls_a_redirect_target_once_even_if_also_linked(monkeypatch):
    # the seed links BOTH the alias and its target: the alias settles first and
    # marks the target URL, so the direct queue entry must not fetch it again
    fetches = {"v2": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/docs/":
            return _html('<html><body><a href="latest/">alias</a>'
                         '<a href="v2/">direct</a></body></html>')
        if request.url.path == "/docs/latest/":
            return _redir("/docs/v2/")
        assert request.url.path == "/docs/v2/"
        fetches["v2"] += 1
        return _html("<html><body>v2</body></html>")

    _patch_robots(monkeypatch, Allow())

    async def run():
        stats = {"errors": {}}
        client = REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))
        async with client:
            return await manifest.bfs(client, "http://t.local/docs/", 10, 2,
                                      stats, delay=0.0)

    urls = asyncio.run(run())
    assert urls == ["http://t.local/docs/", "http://t.local/docs/v2/"]
    assert fetches["v2"] == 1
