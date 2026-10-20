"""Page inventory for a docs site: Sphinx objects.inv -> sitemap.xml -> BFS."""
from __future__ import annotations

import re
import zlib
from urllib.parse import urljoin, urlparse

import httpx

INV_LINE = re.compile(r"^(.+?)\s+(\S+):(\S+)\s+(-?\d+)\s+(\S+)\s+(.*)$")
UA = {"User-Agent": "site-kg/0.1 (+https://github.com/CallMeHFK/site-kg)"}


def _base_prefix(base: str) -> str:
    """Directory scope of the seed URL: '/uv/' -> '/uv/', '/docs/installation' -> '/docs/'.
    Crawling and id derivation stay inside this prefix."""
    p = urlparse(base).path
    return p if p.endswith("/") else p.rsplit("/", 1)[0] + "/"


def same_site(base: str, url: str) -> bool:
    b, u = urlparse(base), urlparse(url)
    if b.netloc.lower() != u.netloc.lower():
        return False
    pre = _base_prefix(base)
    return True if pre == "/" else u.path.startswith(pre)


def page_id(base: str, url: str) -> str:
    """Relative path -> stable id. Handles trailing-slash and extension-less
    pretty URLs (mkdocs / Docusaurus / VitePress / MediaWiki), not just *.html."""
    p = urlparse(url).path
    pre = _base_prefix(base)
    if pre != "/" and p.startswith(pre):
        p = p[len(pre):]
    p = p.strip("/")
    p = re.sub(r"\.(html?|php|aspx?)$", "", p, flags=re.I)
    return p.replace("/", "_") or "index"


async def from_objects_inv(client: httpx.AsyncClient, base: str) -> list[str] | None:
    try:
        r = await client.get(urljoin(base, "objects.inv"))
        if r.status_code != 200 or b"Sphinx inventory" not in r.content[:200]:
            return None
        blob = r.content.split(b"\n", 4)[4]
        pages = []
        for line in zlib.decompress(blob).decode("utf-8", "replace").splitlines():
            m = INV_LINE.match(line)
            if not m:
                continue
            name, _domain, role, _prio, uri, _disp = m.groups()
            if role != "doc":
                continue
            uri = uri[:-1] + name if uri.endswith("$") else uri
            url = urljoin(base, uri)
            if url.endswith((".html", "/")) or "." not in urlparse(url).path.rsplit("/", 1)[-1]:
                pages.append(url)
        return sorted(set(pages)) or None
    except Exception:
        return None


async def from_sitemap(client: httpx.AsyncClient, base: str) -> list[str] | None:
    """Handles both flat sitemaps and sitemap *index* files (one level of recursion)."""
    root = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
    for cand in (urljoin(base, "sitemap.xml"), f"{root}/sitemap.xml"):
        try:
            r = await client.get(cand)
            if r.status_code != 200 or "<loc>" not in r.text:
                continue
            urls = re.findall(r"<loc>([^<]+)</loc>", r.text)
            child_indexes = [u for u in urls if u.endswith(".xml")]
            page_urls = [u for u in urls if not u.endswith(".xml") and same_site(base, u)]
            if page_urls:
                return sorted(set(page_urls))
            for child in child_indexes[:20]:
                try:
                    rc = await client.get(child)
                    if rc.status_code == 200:
                        page_urls += [u for u in re.findall(r"<loc>([^<]+)</loc>", rc.text)
                                      if not u.endswith(".xml") and same_site(base, u)]
                except Exception:
                    continue
                if len(page_urls) >= 2000:
                    break
            return sorted(set(page_urls)) or None
        except Exception:
            continue
    return None


CONTENT_RE = re.compile(r"\.(png|jpe?g|gif|svg|css|js|ico|pdf|zip|tar|woff2?)$", re.I)


async def from_doxygen_navtree(client: httpx.AsyncClient, base: str) -> list[str] | None:
    """Doxygen sites are JS shells: the page inventory lives in navtreeindex0.js
    (URLMap keys). Anchors are stripped and files deduped to page level."""
    urls, found = [], False
    for n in range(0, 40):
        name = "navtreeindex0.js" if n == 0 else f"navtreeindex{n}.js"
        try:
            r = await client.get(urljoin(base, name))
        except Exception:
            break
        if r.status_code != 200:
            break
        found = True
        for key in re.findall(r'"([^"#]+\.html)', r.text):
            urls.append(urljoin(base, key))
    return sorted(set(urls)) if found else None


async def bfs(client: httpx.AsyncClient, base: str, max_pages: int, max_depth: int,
              stats: dict, on_discover=None, on_page_found=None,
              respect_robots: bool = True) -> list[str]:
    """Same-site BFS over <a href>. Pages that fetch as client-side shells are
    re-rendered headlessly so their links are discoverable. Renderer failures
    degrade to the static HTML and are counted in stats.

    ``on_discover(found, depth, failed)`` fires after every settled page and
    after a batch of links is queued, so a caller can show progress instead of
    sitting silent for the whole crawl. ``failed`` counts fetches that errored
    or returned non-HTML, rather than swallowing them. ``on_page_found(url,
    html)`` fires for each page that settles with HTML, so the caller can add
    the node while the crawl is still running. Honours robots.txt unless
    ``respect_robots=False`` (only for sites you are authorized to test)."""
    from .ingest import _robots_async
    from .render import ambient_proxy, internal_links, is_js_shell, try_render_urls

    rp = await _robots_async(base) if respect_robots else None
    failed = 0

    def notify(found: int, depth: int) -> None:
        stats["failed"] = failed
        stats["discovered"] = found
        if on_discover is not None:
            on_discover(found, depth, failed)

    seen, queue, out = {base}, [(base, 0)], []
    while queue and len(out) < max_pages:
        url, depth = queue.pop(0)
        if rp and not rp.can_fetch(UA["User-Agent"], url):
            stats["robots_blocked"] = stats.get("robots_blocked", 0) + 1
            continue
        try:
            r = await client.get(url, follow_redirects=True)
        except Exception as exc:
            failed += 1
            stats["errors"][type(exc).__name__] = \
                stats["errors"].get(type(exc).__name__, 0) + 1
            notify(len(out), depth)
            continue
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            failed += 1
            key = f"status:{r.status_code}"
            stats["errors"][key] = stats["errors"].get(key, 0) + 1
            notify(len(out), depth)
            continue
        html = r.text
        if is_js_shell(html):
            rendered, err = await try_render_urls([url], proxy=ambient_proxy(url))
            if err:
                # renderer is an optional extra; shell links stay undiscovered
                stats["js_render_skipped"] = stats.get("js_render_skipped", 0) + 1
                stats["errors"][err] = stats["errors"].get(err, 0) + 1
            else:
                html = rendered.get(url, html)
        out.append(url)
        if on_page_found is not None:
            on_page_found(url, html)
        notify(len(out), depth)
        if depth >= max_depth:
            continue
        queued = len(queue)
        for u in internal_links(html, url, base):
            if u not in seen:
                seen.add(u)
                queue.append((u, depth + 1))
        if len(queue) != queued:
            notify(len(out), depth)
    stats["failed"] = failed
    stats["discovered"] = len(out)
    return sorted(out)


async def page_inventory(base: str, max_pages: int = 500, max_depth: int = 4,
                         dev: bool = False, on_discover=None,
                         on_page_found=None) -> tuple[list[str], str, dict]:
    """Returns (urls, source, stats) where source is objects_inv|navtree|sitemap|bfs.
    stats carries renderer-degradation counters and ``failed``/``discovered``.

    ``on_discover(found, depth, failed)`` is optional; without it behaviour is
    unchanged. Fast inventory sources still report once so the caller sees the
    page count before fetching starts. ``on_page_found(url, html)`` is only
    available on the BFS path, which already has each page's HTML in hand.
    ``dev=True`` (authorized testing only) skips the BFS robots.txt check."""
    stats: dict = {"errors": {}, "failed": 0, "discovered": 0}

    def report(urls: list[str]) -> None:
        stats["discovered"] = len(urls)
        if on_discover is not None:
            on_discover(len(urls), 0, stats["failed"])

    async with httpx.AsyncClient(headers=UA, timeout=30, follow_redirects=True) as client:
        inv = await from_objects_inv(client, base)
        if inv:
            urls = inv[:max_pages]
            report(urls)
            return urls, "objects_inv", stats
        nav = await from_doxygen_navtree(client, base)
        if nav:
            urls = nav[:max_pages]
            report(urls)
            return urls, "navtree", stats
        sm = await from_sitemap(client, base)
        if sm:
            urls = sm[:max_pages]
            report(urls)
            return urls, "sitemap", stats
        urls = await bfs(client, base, max_pages, max_depth, stats,
                         on_discover=on_discover, on_page_found=on_page_found,
                         respect_robots=not dev)
        return urls, "bfs", stats
