"""
Step 2: Turn crawled HTML into navigable chunks.

Each chunk = one heading + the text under it, PLUS a CSS selector
that points at that heading in the live page. The selector is what
lets the widget scroll to and highlight the exact spot.

Usage:
    python chunker.py

Input:   pages/*.html  +  pages/index.json   (from crawler.py)
Output:  chunks.json
"""

import json
from pathlib import Path

from bs4 import BeautifulSoup

PAGES = Path("pages")
OUT = Path("chunks.json")

HEADINGS = ["h1", "h2", "h3"]
CHROME = ["nav", "header", "footer", "aside"]   # not real content
MIN_CHARS = 20                                   # drop near-empty chunks
MAX_CHARS = 1500                                 # truncate runaway sections


def in_chrome(tag):
    """True if this heading lives inside a nav/header/footer."""
    for parent in tag.parents:
        if parent.name in CHROME:
            return True
        # common class-based chrome on template sites
        classes = " ".join(parent.get("class", [])).lower()
        if any(w in classes for w in ("navbar", "nav-", "footer", "menu")):
            return True
    return False


def css_path(tag):
    """
    Build a unique CSS selector for a tag with no id.

    Walks up the tree collecting tag + :nth-of-type(n) at each level,
    which produces something the browser can resolve exactly.
    e.g. "main > section:nth-of-type(3) > h2:nth-of-type(1)"
    """
    if tag.get("id"):
        return f"#{tag['id']}"

    parts = []
    node = tag
    while node is not None and node.name != "[document]":
        parent = node.parent
        if parent is None or parent.name == "[document]":
            parts.append(node.name)
            break
        # position among siblings of the same tag name
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


def level(tag):
    return int(tag.name[1])


def text_until_next_heading(heading):
    out = []
    my_level = level(heading)
    own = set(id(d) for d in heading.descendants)   # skip heading's own children
    for el in heading.find_all_next():
        if id(el) in own:
            continue
        if el.name in HEADINGS and level(el) <= my_level:
            break
        if el.name in ("p", "li", "td", "span", "a", "div"):
            t = el.find(string=True, recursive=False)
            if t and t.strip():
                out.append(t.strip())
        if sum(len(x) for x in out) > MAX_CHARS:
            break
    return " ".join(out)[:MAX_CHARS]

def chunk_page(html, url, title):
    soup = BeautifulSoup(html, "html.parser")

    # strip things that never contain answerable content
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    chunks = []
    for h in soup.find_all(HEADINGS):
        if in_chrome(h):
            continue

        heading_text = h.get_text(" ", strip=True)
        if not heading_text:
            continue

        body = text_until_next_heading(h)
        content = f"{heading_text}. {body}".strip()

        if len(content) < MIN_CHARS:
            # keep bare headings only if they look navigable (h1/h2)
            if h.name == "h3":
                continue

        chunks.append({
            "url": url,
            "page_title": title,
            "heading": heading_text,
            "level": h.name,
            "selector": css_path(h),
            "content": content,
        })
    return chunks


def main():
    index = json.loads((PAGES / "index.json").read_text(encoding="utf-8"))

    all_chunks = []
    for page in index:
        html = (PAGES / page["file"]).read_text(encoding="utf-8")
        cs = chunk_page(html, page["url"], page["title"])
        all_chunks.extend(cs)
        print(f"{len(cs):3d} chunks  <-  {page['url']}")

    for i, c in enumerate(all_chunks):
        c["id"] = i

    OUT.write_text(json.dumps(all_chunks, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nTotal {len(all_chunks)} chunks -> {OUT}")

    print("\n--- sample ---")
    for c in all_chunks[:3]:
        print(f"\n[{c['id']}] {c['heading']}  ({c['level']})")
        print(f"    url:      {c['url']}")
        print(f"    selector: {c['selector']}")
        print(f"    content:  {c['content'][:120]}...")


if __name__ == "__main__":
    main()