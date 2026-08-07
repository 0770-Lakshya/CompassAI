# check_links.py
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

index = json.loads(Path("pages/index.json").read_text(encoding="utf-8"))
crawled = {p["url"].rstrip("/") for p in index}

found = {}
for page in index:
    html = Path("pages") / page["file"]
    soup = BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser")
    for a in soup.find_all("a", href=True):
        raw = a["href"]
        full = urljoin(page["url"], raw)
        found.setdefault(full, raw)

print(f"crawled: {len(crawled)} pages")
print(f"unique links seen: {len(found)}\n")

print("--- NOT crawled ---")
for full, raw in sorted(found.items()):
    if full.rstrip("/") not in crawled:
        netloc = urlparse(full).netloc
        print(f"  raw={raw:45s}  ->  {full}   [{netloc}]")