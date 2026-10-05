"""Crawl pages -> markdown corpus with frontmatter (title/source_url/sha256)."""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
import html2text

from .manifest import UA, page_id, same_site, CONTENT_RE

MAIN_SELECTORS = ["div[role=main]", "main", "article", "div.document div.body", "div.body", "div.contents", "body"]
SKIP_HREF = re.compile(r"^(#|mailto:|javascript:|tel:|data:)")


def _title(soup: BeautifulSoup, fallback: str) -> str:
    # Doxygen puts the real page name in .headertitle .title; its h1/<title> are site boilerplate
    dt = soup.select_one(".headertitle .title, div.headertitle div.title")
    if dt and dt.get_text(strip=True):
        return dt.get_text(" ", strip=True)
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        # Sphinx headerlinks render as a trailing "#"
        return h1.get_text(" ", strip=True).removesuffix("#").strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return fallback


def _main_html(soup: BeautifulSoup) -> str:
    for sel in MAIN_SELECTORS:
        el = soup.select_one(sel)
        if el:
            for bad in el.select("nav, script, style, header, footer, .related, .sphinxsidebar"):
                bad.decompose()
            return str(el)
    body = str(soup.body or soup)
    return body


def _robots(base: str) -> RobotFileParser:
    rp = RobotFileParser()
    rp.set_url(f"{urlparse(base).scheme}://{urlparse(base).netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        pass
    return rp


async def fetch_pages(urls: list[str], base: str, concurrency: int = 4, delay: float = 0.2,
                      respect_robots: bool = True) -> tuple[dict[str, str], dict]:
    """Returns (pages, stats). stats surfaces why pages are missing instead of
    silently producing an empty corpus."""
    rp = _robots(base) if respect_robots else None
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, str] = {}
    stats = {"robots_blocked": 0, "errors": {}, "skipped": 0}

    async def one(client: httpx.AsyncClient, url: str) -> None:
        if rp and not rp.can_fetch(UA["User-Agent"], url):
            stats["robots_blocked"] += 1
            return
        async with sem:
            try:
                r = await client.get(url, follow_redirects=True)
                if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
                    out[url] = r.text
                elif r.status_code == 200:
                    stats["skipped"] += 1
                else:
                    stats["errors"][str(r.status_code)] = stats["errors"].get(str(r.status_code), 0) + 1
            except Exception as e:
                key = type(e).__name__
                stats["errors"][key] = stats["errors"].get(key, 0) + 1
            await asyncio.sleep(delay)

    async with httpx.AsyncClient(headers=UA, timeout=30) as client:
        await asyncio.gather(*(one(client, u) for u in urls))
    # detection first, browser second: only shell pages pay for a JS render
    shells = [u for u, h in out.items() if _is_shell(h)]
    if shells:
        from .render import render_urls, ambient_proxy, better_than_static
        rendered = await render_urls(shells, proxy=ambient_proxy(base))
        stats["js_rendered"] = 0
        for u, h in rendered.items():
            if better_than_static(h, out[u]):
                out[u] = h
                stats["js_rendered"] += 1
    return out, stats


def _is_shell(html: str) -> bool:
    from .render import is_js_shell
    return is_js_shell(html)


def _internal_links(soup: BeautifulSoup, page_url: str, base: str, own_id: str) -> list[str]:
    """Resolve every anchor to a page id. Pretty URLs (trailing slash or no
    extension) are first-class here -- a .html-only rule loses every non-Sphinx site."""
    ids = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href or SKIP_HREF.match(href):
            continue
        clean = urlsplit(href)
        if clean.query or CONTENT_RE.search(clean.path):
            continue
        target = urljoin(page_url, href.split("#")[0])
        ts = urlsplit(target)
        target = f"{ts.scheme}://{ts.netloc}{ts.path}"
        if not same_site(base, target):
            continue
        pid = page_id(base, target)
        if pid and pid != own_id:
            ids.add(pid)
    return sorted(ids)


def to_markdown(html: str, url: str, base: str) -> dict:
    """Returns {id, title, md, links}: md has frontmatter; links are resolved same-site page ids."""
    soup = BeautifulSoup(html, "html.parser")
    title = _title(soup, url.rsplit("/", 1)[-1])
    pid = page_id(base, url)
    links = _internal_links(soup, url, base, pid)
    conv = html2text.HTML2Text()
    conv.body_width = 0
    md_body = conv.handle(_main_html(soup))
    raw = md_body.encode()
    fm = f"---\ntitle: {esc(title)}\nsource_url: {url}\nsha256: {hashlib.sha256(raw).hexdigest()}\n---\n\n"
    return {"id": pid, "title": title, "md": fm + md_body, "links": links}


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def write_corpus(pages: dict[str, str], base: str, out_dir: Path) -> list[dict]:
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    docs, link_map = [], {}
    for url, html in sorted(pages.items()):
        d = to_markdown(html, url, base)
        chapter = d["id"].split("_", 1)[0] if "_" in d["id"] else "_root"
        (out_dir / chapter).mkdir(exist_ok=True)
        (out_dir / chapter / f"{d['id']}.md").write_text(d["md"], encoding="utf-8")
        link_map[d["id"]] = d["links"]
        docs.append({"id": d["id"], "title": d["title"], "links": d["links"],
                     "url": url, "chapter": chapter})
    # sidecar: ids resolved against page URLs at crawl time; markdown hrefs are
    # page-relative and cannot be re-resolved from the corpus tree alone
    (out_dir / "_links.json").write_text(json.dumps(link_map, indent=0, sort_keys=True))
    return docs
