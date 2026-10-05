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


async def bfs(client: httpx.AsyncClient, base: str, max_pages: int, max_depth: int) -> list[str]:
    """Same-site BFS over <a href>. For JS shells this under-discovers; the CLI
    warns and suggests installing the crawl extra (Crawl4AI)."""
    from bs4 import BeautifulSoup

    seen, queue, out = {base}, [(base, 0)], []
    while queue and len(out) < max_pages:
        url, depth = queue.pop(0)
        try:
            r = await client.get(url, follow_redirects=True)
        except Exception:
            continue
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            continue
        out.append(url)
        if depth >= max_depth:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href]"):
            u = urljoin(url, a["href"].split("#")[0])
            if not u or CONTENT_RE.search(urlparse(u).path):
                continue
            if u not in seen and same_site(base, u):
                seen.add(u)
                queue.append((u, depth + 1))
    return sorted(out)


async def page_inventory(base: str, max_pages: int = 500, max_depth: int = 4) -> tuple[list[str], str]:
    """Returns (urls, source) where source is objects_inv|navtree|sitemap|bfs."""
    async with httpx.AsyncClient(headers=UA, timeout=30, follow_redirects=True) as client:
        inv = await from_objects_inv(client, base)
        if inv:
            return inv[:max_pages], "objects_inv"
        nav = await from_doxygen_navtree(client, base)
        if nav:
            return nav[:max_pages], "navtree"
        sm = await from_sitemap(client, base)
        if sm:
            return sm[:max_pages], "sitemap"
        urls = await bfs(client, base, max_pages, max_depth)
        return urls, "bfs"
