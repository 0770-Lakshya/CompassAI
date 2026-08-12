"""
indexer.py — lightweight crawl + chunk in one pass, inline.

Runs inside the API process using requests + BeautifulSoup (no Playwright,
no subprocess). Designed to fit Render's 512MB. Won't handle JS-rendered
content, but works for the majority of simple/server-rendered sites.

Usage from api.py:
    from indexer import index_site
    chunks = index_site("https://example.com")
"""

import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser
import hashlib
import requests
from bs4 import BeautifulSoup

MIN_HEADING_CHUNKS = 3       # below this, fall back to text windows
FB_WINDOW = 1200
FB_OVERLAP = 150

MAX_DEPTH = 3
MAX_PAGES = 100
DELAY = 0.3
TIMEOUT = 12
UA = "CompassAI/0.1 (auto-indexer)"

HEADINGS = ["h1", "h2", "h3"]
CHROME_TAGS = ["nav", "header", "footer", "aside"]
MIN_CHARS = 20
MAX_CHARS = 1500


# ---------- crawler ----------

def _clean_url(url):
    return urldefrag(url)[0].rstrip("/")


def _same_domain(url, root_netloc):
    return urlparse(url).netloc == root_netloc


def _is_html_url(url):
    path = urlparse(url).path.lower()
    bad = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
           ".zip", ".mp4", ".mp3", ".css", ".js", ".ico", ".woff", ".woff2")
    return not path.endswith(bad)


def _load_robots(root):
    rp = RobotFileParser()
    rp.set_url(urljoin(root, "/robots.txt"))
    try:
        rp.read()
    except Exception:
        return None
    return rp


def _crawl(root):
    """Fetch pages breadth-first, return list of {url, title, html}."""
    root = _clean_url(root)
    root_netloc = urlparse(root).netloc
    rp = _load_robots(root)

    session = requests.Session()
    session.headers["User-Agent"] = UA

    seen = {root}
    queue = deque([(root, 0)])
    pages = []

    while queue and len(pages) < MAX_PAGES:
        url, depth = queue.popleft()

        if rp and not rp.can_fetch(UA, url):
            continue

        try:
            r = session.get(url, timeout=TIMEOUT)
        except requests.exceptions.SSLError:
            try:
                r = session.get(url, timeout=TIMEOUT, verify=False)
            except Exception:
                continue
        except Exception:
            continue

        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "text/html" not in ctype:
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""

        pages.append({"url": url, "title": title, "html": r.text})

        if depth < MAX_DEPTH:
            for a in soup.find_all("a", href=True):
                nxt = _clean_url(urljoin(url, a["href"]))
                if (nxt not in seen
                        and _same_domain(nxt, root_netloc)
                        and _is_html_url(nxt)
                        and nxt.startswith("http")):
                    seen.add(nxt)
                    queue.append((nxt, depth + 1))

        time.sleep(DELAY)

    return pages


# ---------- chunker ----------

def _in_chrome(tag):
    for parent in tag.parents:
        if parent.name in CHROME_TAGS:
            return True
        classes = " ".join(parent.get("class", [])).lower()
        if any(w in classes for w in ("navbar", "nav-", "footer", "menu")):
            return True
    return False


def _css_path(tag):
    if tag.get("id"):
        return f"#{tag['id']}"
    parts = []
    node = tag
    while node is not None and node.name != "[document]":
        parent = node.parent
        if parent is None or parent.name == "[document]":
            parts.append(node.name)
            break
        siblings = [s for s in parent.find_all(node.name, recursive=False)]
        if len(siblings) > 1:
            idx = siblings.index(node) + 1
            parts.append(f"{node.name}:nth-of-type({idx})")
        else:
            parts.append(node.name)
        if parent.get("id"):
            parts.append(f"#{parent['id']}")
            break
        node = parent
    return " > ".join(reversed(parts))


def _level(tag):
    return int(tag.name[1])


def _text_until_next_heading(heading):
    out = []
    my_level = _level(heading)
    own = set(id(d) for d in heading.descendants)
    for el in heading.find_all_next():
        if id(el) in own:
            continue
        if el.name in HEADINGS and _level(el) <= my_level:
            break
        if el.name in ("p", "li", "td", "span", "a", "div"):
            t = el.find(string=True, recursive=False)
            if t and t.strip():
                out.append(t.strip())
        if sum(len(x) for x in out) > MAX_CHARS:
            break
    return " ".join(out)[:MAX_CHARS]


def _chunk_page(html, url, title):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    chunks = []
    for h in soup.find_all(HEADINGS):
        if _in_chrome(h):
            continue
        heading_text = h.get_text(" ", strip=True)
        if not heading_text:
            continue
        body = _text_until_next_heading(h)
        content = f"{heading_text}. {body}".strip()
        if len(content) < MIN_CHARS and h.name == "h3":
            continue

        chunks.append({
            "url": url,
            "page_title": title,
            "heading": heading_text,
            "level": h.name,
            "selector": _css_path(h),
            "content": content,
        })
    return chunks


# ---------- public API ----------

def index_site(root_url):
    """
    Crawl a site and return a list of chunks ready for embedding.
    Lightweight: requests + BS4 only, no Playwright, no disk I/O.
    Returns [] if the site can't be reached or has no content.
    """
    if not root_url.startswith("http"):
        root_url = "https://" + root_url

    pages = _crawl(root_url)
    if not pages:
        return []

    all_chunks = []
    for page in pages:
        cs = _chunk_page(page["html"], page["url"], page["title"])
        all_chunks.extend(cs)

    for i, c in enumerate(all_chunks):
        c["id"] = i

    return all_chunks


def chunk_html(html, url, title):
    """Public wrapper: heading-based chunking of one rendered page."""
    return _chunk_page(html, url, title)


def _visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"] + CHROME_TAGS):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def _chunk_text_fallback(text, url, title):
    """Overlapping text windows for heading-less SPA pages."""
    if len(text) < MIN_CHARS:
        return []
    out, start, n = [], 0, len(text)
    while start < n:
        end = min(start + FB_WINDOW, n)
        window = text[start:end]
        if end < n:
            cut = window.rfind(". ")
            if cut > FB_WINDOW // 2:
                window = window[:cut + 1]
                end = start + len(window)
        out.append({
            "url": url, "page_title": title,
            "heading": title or url, "level": "p",
            "selector": "",                       # no element anchor → page-level
            "content": window.strip(),
        })
        if end >= n:
            break
        start = end - FB_OVERLAP
    return out


def chunk_rendered(html, url, title):
    """Widget-supplied rendered HTML: prefer headings, fall back to text."""
    heading_chunks = _chunk_page(html, url, title)
    if len(heading_chunks) >= MIN_HEADING_CHUNKS:
        return heading_chunks
    fb = _chunk_text_fallback(_visible_text(html), url, title)
    return heading_chunks if len(heading_chunks) >= len(fb) else fb