"""
Step 1: Crawl a site, save raw HTML to disk.

Usage:
    python crawler.py https://openlake.iitbhilai.ac.in

Output:
    pages/            one .html file per page
    pages/index.json  url -> filename, title, depth
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

MAX_DEPTH = 3
MAX_PAGES = 200
DELAY = 0.5          # be polite
TIMEOUT = 15
UA = "CrawlerAI/0.1 (student project; contact: you@example.com)"

OUT = Path("pages")


def same_domain(url, root_netloc):
    return urlparse(url).netloc == root_netloc


def is_html_url(url):
    """Cheap pre-filter: skip obvious non-HTML by extension."""
    path = urlparse(url).path.lower()
    bad = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
           ".zip", ".mp4", ".mp3", ".css", ".js", ".ico", ".woff", ".woff2")
    return not path.endswith(bad)


def clean_url(url):
    """Drop #fragments so we don't crawl the same page 10 times."""
    return urldefrag(url)[0].rstrip("/")


def slugify(url):
    """Turn a URL into a safe filename."""
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
        return None          # no robots.txt = crawl allowed
    return rp


def crawl(root):
    root = clean_url(root)
    root_netloc = urlparse(root).netloc
    rp = load_robots(root)

    OUT.mkdir(exist_ok=True)

    session = requests.Session()
    session.headers["User-Agent"] = UA

    seen = {root}
    queue = deque([(root, 0)])
    index = []

    while queue and len(index) < MAX_PAGES:
        url, depth = queue.popleft()

        if rp and not rp.can_fetch(UA, url):
            print(f"[robots] skip {url}")
            continue

        try:
            r = session.get(url, timeout=TIMEOUT)
        except Exception as e:
            print(f"[error] {url} -> {e}")
            continue

        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "text/html" not in ctype:
            print(f"[skip]  {url} ({r.status_code}, {ctype.split(';')[0]})")
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""

        fname = slugify(url)
        (OUT / fname).write_text(r.text, encoding="utf-8")
        index.append({"url": url, "file": fname, "title": title, "depth": depth})
        print(f"[ok {len(index):3d}] d{depth} {title[:45]:45s} {url}")

        if depth < MAX_DEPTH:
            for a in soup.find_all("a", href=True):
                nxt = clean_url(urljoin(url, a["href"]))
                if (nxt not in seen
                        and same_domain(nxt, root_netloc)
                        and is_html_url(nxt)
                        and nxt.startswith("http")):
                    seen.add(nxt)
                    queue.append((nxt, depth + 1))

        time.sleep(DELAY)

    (OUT / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\nDone. {len(index)} pages -> {OUT}/  (see index.json)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python crawler.py <root-url>")
    crawl(sys.argv[1])