# inspect.py
from pathlib import Path
from bs4 import BeautifulSoup

for f in sorted(Path("pages").glob("*.html")):
    soup = BeautifulSoup(f.read_text(encoding="utf-8"), "html.parser")
    heads = soup.find_all(["h1", "h2", "h3"])
    print(f"\n=== {f.name}  ({len(heads)} headings)")
    for h in heads[:12]:
        hid = h.get("id") or "—"
        print(f"  <{h.name} id={hid}> {h.get_text(strip=True)[:60]}")