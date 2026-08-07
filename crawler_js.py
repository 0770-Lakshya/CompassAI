"""
crawler_js.py — crawler for JavaScript-rendered sites.

Same output as crawler.py (pages/<netloc>/*.html + index.json) but runs a
real headless browser so client-side nav, dropdowns, and hydrated links
are visible. Use this for React/Next/Vue sites; use crawler.py for plain
server-rendered sites (it's faster).

Usage:
    pip install playwright
    playwright install chromium
    python crawler_js.py https://openlake.in/
"""

import json
import re
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

MAX_DEPTH = 3
MAX_PAGES = 200
WAIT_MS = 2500          # let the page hydrate + dropdowns render
TIMEOUT_MS = 20000
UA = "CrawlerAI/0.1 (student project)"


def clean_url(url):
    return urldefrag(url)[0].rstrip("/")


def same_domain(url, root_netloc):
    return urlparse(url).netloc == root_netloc


def is_html_url(url):
    path = urlparse(url).path.lower()
    bad = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
           ".zip", ".mp4", ".mp3", ".css", ".js", ".ico", ".woff", ".woff2")
    return not path.endswith(bad)


def slugify(url):
    p = urlparse(url)
    slug = (p.path or "/").strip("/").replace("/", "_") or "index"
    if p.query:
        slug += "_" + re.sub(r"\W+", "-", p.query)
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", slug)
    return slug[:120] + ".html"


def load_robots(root):
    rp = RobotFileParser()
    rp.set_url(urljoin(root, "/robots.txt"))
    try:
        rp.read()
    except Exception:
        return None
    return rp


def crawl(root):
    root = clean_url(root)
    root_netloc = urlparse(root).netloc
    rp = load_robots(root)

    out_dir = Path("pages") / root_netloc.replace(":", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    seen = {root}
    queue = deque([(root, 0)])
    index = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)

        while queue and len(index) < MAX_PAGES:
            url, depth = queue.popleft()

            if rp and not rp.can_fetch(UA, url):
                print(f"[robots] skip {url}")
                continue

            try:
                page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            except Exception as e:
                print(f"[error] {url} -> {str(e)[:80]}")
                continue

            # trigger hover menus so their links render into the DOM
            try:
                page.wait_for_timeout(WAIT_MS)
                buttons = page.query_selector_all("button, [aria-haspopup], nav [role='button']")
                for btn in buttons:
                    try:
                        btn.hover(timeout=1000)
                        page.wait_for_timeout(300)   # let the menu render
                    except Exception:
                        pass
            except Exception:
                pass

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            title = soup.title.get_text(strip=True) if soup.title else ""

            fname = slugify(url)
            (out_dir / fname).write_text(html, encoding="utf-8")
            index.append({"url": url, "file": fname, "title": title, "depth": depth})
            print(f"[ok {len(index):3d}] d{depth} {title[:40]:40s} {url}")

            if depth < MAX_DEPTH:
                for a in soup.find_all("a", href=True):
                    nxt = clean_url(urljoin(url, a["href"]))
                    if (nxt not in seen
                            and same_domain(nxt, root_netloc)
                            and is_html_url(nxt)
                            and nxt.startswith("http")):
                        seen.add(nxt)
                        queue.append((nxt, depth + 1))

        browser.close()

    (out_dir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\nDone. {len(index)} pages -> {out_dir}/  (see index.json)")
    print("Note: chunker.py reads pages/index.json — point it at this folder "
          "or copy the files up one level.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python crawler_js.py <root-url>")
    crawl(sys.argv[1])