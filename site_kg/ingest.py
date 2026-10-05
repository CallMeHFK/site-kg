"""Crawl pages -> markdown corpus with frontmatter (title/source_url/sha256)."""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
import html2text

from .manifest import UA, page_id, same_site

MAIN_SELECTORS = ["div[role=main]", "main", "article", "div.document div.body", "div.body", "div.contents", "body"]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s#]+\.html?)(#[^)\s]*)?\)", re.I)


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
    return str(soup.body or soup)


def _robots(base: str) -> RobotFileParser:
    rp = RobotFileParser()
    rp.set_url(f"{urlparse(base).scheme}://{urlparse(base).netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        pass
    return rp


async def fetch_pages(urls: list[str], base: str, concurrency: int = 4, delay: float = 0.2,
                      respect_robots: bool = True) -> dict[str, str]:
    rp = _robots(base) if respect_robots else None
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, str] = {}

    async def one(client: httpx.AsyncClient, url: str) -> None:
        if rp and not rp.can_fetch(UA["User-Agent"], url):
            return
        async with sem:
            try:
                r = await client.get(url, follow_redirects=True)
                if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
                    out[url] = r.text
            except Exception:
                pass
            await asyncio.sleep(delay)

    async with httpx.AsyncClient(headers=UA, timeout=30) as client:
        await asyncio.gather(*(one(client, u) for u in urls))
    return out


def to_markdown(html: str, url: str, base: str) -> dict:
    """Returns {id, title, md, links}: md has frontmatter; links are resolved same-site page ids."""
    soup = BeautifulSoup(html, "html.parser")
    title = _title(soup, url.rsplit("/", 1)[-1])
    conv = html2text.HTML2Text()
    conv.body_width = 0
    conv.ignore_images = False
    conv.ignore_emphasis = False
    md_body = conv.handle(_main_html(soup))
    links = set()
    for m in LINK_RE.finditer(md_body):
        target = urljoin(url, m.group(1))
        if same_site(base, target):
            links.add(page_id(base, target))
    pid = page_id(base, url)
    raw = md_body.encode()
    fm = f"---\ntitle: {json_escape(title)}\nsource_url: {url}\nsha256: {hashlib.sha256(raw).hexdigest()}\n---\n\n"
    return {"id": pid, "title": title, "md": fm + md_body, "links": sorted(links - {pid})}


def json_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def write_corpus(pages: dict[str, str], base: str, out_dir: Path) -> list[dict]:
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    docs, link_map = [], {}
    for url, html in sorted(pages.items()):
        d = to_markdown(html, url, base)
        chapter = d["id"].split("_", 1)[0] if "_" in d["id"] else "_root"
        chapter_dir = out_dir / chapter
        chapter_dir.mkdir(exist_ok=True)
        (chapter_dir / f"{d['id']}.md").write_text(d["md"], encoding="utf-8")
        link_map[d["id"]] = d["links"]
        docs.append({k: d[k] for k in ("id", "title", "links")} | {"url": url, "chapter": chapter})
    # sidecar: ids resolved against page URLs at crawl time; markdown hrefs are
    # page-relative and cannot be re-resolved from the corpus tree alone
    (out_dir / "_links.json").write_text(json.dumps(link_map, indent=0, sort_keys=True))
    return docs
