"""JS-shell detection and a Playwright fallback renderer.

Static fetch stays first -- it is cheap and sufficient for SSR/SSG sites. Only
pages that look like a client-side shell get a real browser. Playwright was
chosen over Crawl4AI: already installed, zero extra deps, and the only thing we
lack is rendered HTML, not a second crawl orchestrator.
"""
from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from .manifest import UA, same_site, CONTENT_RE
from urllib.parse import urljoin, urlsplit

STRIP = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>", re.I)
SHELL_ROOTS = re.compile(r'<div id="(app|root|__next|___gatsby)">\s*</div>', re.I)


def text_len(html: str) -> int:
    t = STRIP.sub(" ", html)
    t = re.sub(r"<[^>]+>", " ", t)
    return len(re.sub(r"\s+", " ", t).strip())


def internal_links(html: str, page_url: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
            continue
        target = urljoin(page_url, href.split("#")[0])
        ts = urlsplit(target)
        target = f"{ts.scheme}://{ts.netloc}{ts.path}"
        if CONTENT_RE.search(ts.path) or not same_site(base, target):
            continue
        out.add(target)
    return sorted(out)


def is_js_shell(html: str) -> bool:
    """Render trigger: empty framework root, or near-zero visible text with no
    internal links. Deliberately conservative -- a false positive costs one
    browser render, a false negative loses the page."""
    if SHELL_ROOTS.search(html) and text_len(html) < 800:
        return True
    return text_len(html) < 150 and not internal_links(html, "http://x/", "http://x/")


def better_than_static(rendered: str, static: str) -> bool:
    """Adoption is comparative, not thresholded. Two regimes: a real page
    (static text >= 50 chars) must gain 100+; a true shell (near-zero text)
    needs only a 50-char floor of rendered content."""
    tr, ts = text_len(rendered), text_len(static)
    return (tr >= ts + 100) or (ts < 50 and tr > 50)


async def render_urls(urls: list[str], proxy: str | None = None, timeout_ms: int = 20000) -> dict[str, str]:
    """Render pages with headless Chromium; returns {url: html_after_js}.
    External sites need the ambient proxy because Chromium ignores env vars."""
    from playwright.async_api import async_playwright

    out: dict[str, str] = {}
    launch_kwargs: dict = {"headless": True}
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        try:
            for url in urls:
                page = await browser.new_page(user_agent=UA["User-Agent"])
                try:
                    await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                    out[url] = await page.content()
                except Exception:
                    pass
                finally:
                    await page.close()
        finally:
            await browser.close()
    return out


def ambient_proxy(url: str) -> str | None:
    """Loopback traffic goes direct; everything else may need the proxy."""
    import os
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1"):
        return None
    return os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or None
