"""
register_site.py — crawl a site locally and push it to the Compass API.

Runs on YOUR machine (or any box with memory to spare), so Playwright's
headless browser never has to fit inside the API's 512MB. The API only
receives the finished chunks.

Usage:
    # full pipeline: crawl -> chunk -> upload
    python register_site.py https://example.com

    # skip the crawl, upload an existing chunks.json
    python register_site.py https://example.com --chunks chunks.json

    # target a deployed API instead of localhost
    python register_site.py https://example.com --api https://compassai-vkoe.onrender.com

Needs ADMIN_TOKEN in .env (must match the API's ADMIN_TOKEN).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
import os

load_dotenv()


def normalize_site_id(raw):
    s = (raw or "").strip().lower()
    s = s.split("//")[-1]
    s = s.split("/")[0]
    s = s.split(":")[0]
    if s.startswith("www."):
        s = s[4:]
    return s


def crawl_and_chunk(url):
    """Run the existing crawler + chunker as separate steps."""
    print(f"\n[1/3] crawling {url} ...")
    r = subprocess.run([sys.executable, "crawler_js.py", url])
    if r.returncode != 0:
        sys.exit("crawl failed")

    netloc = urlparse(url if "//" in url else "https://" + url).netloc
    src = Path("pages") / netloc.replace(":", "_")
    if not (src / "index.json").exists():
        sys.exit(f"no index.json in {src} — did the crawl find anything?")

    # chunker.py reads pages/index.json, so stage this crawl there
    print(f"[2/3] chunking {src} ...")
    dest = Path("pages")
    for f in src.iterdir():
        (dest / f.name).write_bytes(f.read_bytes())

    r = subprocess.run([sys.executable, "chunker.py"])
    if r.returncode != 0:
        sys.exit("chunking failed")

    return Path("chunks.json")


def upload(api, site_id, chunks_path, token):
    chunks = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
    print(f"[3/3] uploading {len(chunks)} chunks as '{site_id}' -> {api}")

    resp = requests.post(
        api.rstrip("/") + "/register",
        json={"site_id": site_id, "chunks": chunks},
        headers={"X-Admin-Token": token},
        timeout=300,
    )
    if resp.status_code != 200:
        sys.exit(f"register failed [{resp.status_code}]: {resp.text[:300]}")

    print("done:", resp.json())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url", help="site to register, e.g. https://example.com")
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--chunks", help="skip crawling, upload this chunks.json")
    args = p.parse_args()

    token = os.environ.get("ADMIN_TOKEN")
    if not token:
        sys.exit("Set ADMIN_TOKEN in .env (must match the API's).")

    site_id = normalize_site_id(args.url)
    chunks_path = Path(args.chunks) if args.chunks else crawl_and_chunk(args.url)
    upload(args.api, site_id, chunks_path, token)


if __name__ == "__main__":
    main()