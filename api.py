"""
api.py — multi-site HTTP layer for Compass.

Sites can be added three ways:
  1. Pre-committed in sites/<site_id>/  (loaded at startup)
  2. POST /register  (manual upload, admin-only)
  3. POST /auto-register  (lightweight crawl on demand, called by widget)

The auto-register path uses requests + BS4 (no Playwright) so it fits
Render's 512MB. Won't handle JS-rendered sites, but works for the
majority of server-rendered pages.

Usage:
    uvicorn api:app --reload
"""

from dotenv import load_dotenv
load_dotenv()
import hashlib
import os
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq

from retrieval import load_all_sites, embed_site, site_dir, SITES_DIR, model
from answer import answer
from indexer import index_site

MAX_INGEST_BYTES = 3_000_000   # ~3MB rendered HTML cap (Render is 512MB)

from indexer import chunk_rendered

STATE = {"sites": {}, "llm": None}

# prevents two visitors from triggering parallel crawls of the same site
_indexing_locks = {}
_lock_guard = threading.Lock()


def normalize_site_id(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = s.split("//")[-1]
    s = s.split("/")[0]
    s = s.split(":")[0]
    if s.startswith("www."):
        s = s[4:]
    return s


@asynccontextmanager
async def lifespan(app: FastAPI):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("Set GROQ_API_KEY before starting the server.")
    print("warming embedder + loading sites...")
    model()
    STATE["sites"] = load_all_sites()
    STATE["llm"] = Groq(api_key=key)
    print(f"ready. {len(STATE['sites'])} site(s): {list(STATE['sites'])}")
    yield
    STATE["sites"].clear()


app = FastAPI(title="Compass API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


# ---------- models ----------

class QueryIn(BaseModel):
    query: str
    site_id: str = "openlake.in"


class QueryOut(BaseModel):
    found: bool
    url: str | None = None
    selector: str | None = None
    heading: str | None = None
    explanation: str | None = None
    confidence: float | None = None
    reason: str | None = None


class RegisterIn(BaseModel):
    site_id: str
    chunks: list[dict]


class RegisterOut(BaseModel):
    site_id: str
    chunks: int
    status: str


class AutoRegisterIn(BaseModel):
    site_id: str


class AutoRegisterOut(BaseModel):
    site_id: str
    chunks: int
    status: str
    message: str


# ---------- endpoints ----------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "sites": {sid: len(c) for sid, (c, _) in STATE["sites"].items()},
    }


@app.get("/sites")
def sites():
    return {"sites": list(STATE["sites"].keys())}


@app.post("/query", response_model=QueryOut)
def query(body: QueryIn):
    sid = normalize_site_id(body.site_id)
    site = STATE["sites"].get(sid)
    if site is None:
        return {"found": False,
                "reason": f"site '{sid}' is not registered with Compass",
                "url": None, "selector": None, "heading": None,
                "explanation": None, "confidence": None}
    chunks, vecs = site
    return answer(body.query, chunks, vecs, STATE["llm"])


@app.post("/register", response_model=RegisterOut)
def register(body: RegisterIn, x_admin_token: str = Header(default="")):
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected or x_admin_token != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")

    sid = normalize_site_id(body.site_id)
    if not sid:
        raise HTTPException(status_code=400, detail="site_id required")
    if not body.chunks:
        raise HTTPException(status_code=400, detail="no chunks supplied")

    required = {"url", "heading", "selector", "content", "level", "page_title"}
    missing = required - set(body.chunks[0].keys())
    if missing:
        raise HTTPException(status_code=400,
                            detail=f"chunks missing fields: {sorted(missing)}")

    d = site_dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "chunks.json").write_text(
        json.dumps(body.chunks, indent=2, ensure_ascii=False), encoding="utf-8")

    chunks, vecs = embed_site(sid)
    STATE["sites"][sid] = (chunks, vecs)
    return {"site_id": sid, "chunks": len(chunks), "status": "indexed"}


@app.post("/auto-register", response_model=AutoRegisterOut)
def auto_register(body: AutoRegisterIn):
    """
    Lightweight auto-indexing: crawl with requests + BS4 (no Playwright),
    chunk, embed, and make the site queryable — all in one request.
    Called by the widget when it lands on an unregistered site.
    """
    sid = normalize_site_id(body.site_id)
    if not sid:
        raise HTTPException(status_code=400, detail="site_id required")

    # already registered? skip
    if sid in STATE["sites"]:
        chunks, _ = STATE["sites"][sid]
        return {"site_id": sid, "chunks": len(chunks),
                "status": "already_indexed",
                "message": "Site is already indexed."}

    # dedup: if another request is already indexing this site, wait for it
    with _lock_guard:
        if sid not in _indexing_locks:
            _indexing_locks[sid] = threading.Lock()
        lock = _indexing_locks[sid]

    if not lock.acquire(blocking=True, timeout=120):
        raise HTTPException(status_code=503,
                            detail="indexing in progress, try again shortly")

    try:
        # double-check after acquiring lock (another thread may have finished)
        if sid in STATE["sites"]:
            chunks, _ = STATE["sites"][sid]
            return {"site_id": sid, "chunks": len(chunks),
                    "status": "already_indexed",
                    "message": "Site was indexed while waiting."}

        print(f"[auto-register] crawling {sid}...")
        raw_chunks = index_site(f"https://{sid}")

        if not raw_chunks:
            # try http if https failed
            raw_chunks = index_site(f"http://{sid}")

        if not raw_chunks:
            return {"site_id": sid, "chunks": 0,
                    "status": "failed",
                    "message": "Could not crawl the site or no content found. "
                               "The site may require JavaScript to render."}

        # save to disk so it persists across restarts
        d = site_dir(sid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "chunks.json").write_text(
            json.dumps(raw_chunks, indent=2, ensure_ascii=False),
            encoding="utf-8")

        # embed and load into memory
        chunks, vecs = embed_site(sid)
        STATE["sites"][sid] = (chunks, vecs)

        js_note = ""
        if len(raw_chunks) < 5:
            js_note = (" Note: very few sections found — if your site uses "
                       "JavaScript rendering, some content may be missing.")

        print(f"[auto-register] {sid} indexed: {len(chunks)} chunks")
        return {"site_id": sid, "chunks": len(chunks),
                "status": "indexed",
                "message": f"Successfully indexed {len(chunks)} sections.{js_note}"}
    finally:
        lock.release()
        with _lock_guard:
            _indexing_locks.pop(sid, None)


class IngestIn(BaseModel):
    site_id: str
    url: str
    title: str | None = ""
    html: str


class IngestOut(BaseModel):
    site_id: str
    chunks: int
    status: str
    message: str


def _get_site_lock(sid):
    with _lock_guard:
        return _indexing_locks.setdefault(sid, threading.Lock())


def _read_json(sid, name, default):
    p = site_dir(sid) / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default

def _origin_ok(origin: str, sid: str) -> bool:
    """
    Anti-poisoning guard. Allow when: no Origin header (non-browser),
    local dev (localhost/127.0.0.1/file://->"null"), or origin host
    equals the site or is a subdomain of it. Not a hard boundary
    (Origin is forgeable off-browser) but blocks casual browser poisoning.
    """
    if not origin:
        return True
    o = normalize_site_id(origin)
    if o in ("localhost", "127.0.0.1", "null", ""):
        return True
    return o == sid or o.endswith("." + sid)


@app.post("/ingest", response_model=IngestOut)
def ingest(body: IngestIn, origin: str = Header(default="")):
    sid = normalize_site_id(body.site_id)
    if not _origin_ok(origin, sid):
        raise HTTPException(status_code=403, detail="origin/site_id mismatch")

    # Anti-poisoning: the browser stamps Origin on the cross-origin POST.
    # Require it to match the site being written to. NOT a hard boundary
    # (curl can forge Origin), but it blocks browser-based / casual poisoning.

    if len(body.html.encode("utf-8")) > MAX_INGEST_BYTES:
        raise HTTPException(status_code=413, detail="page too large")

    url = body.url or f"https://{sid}"
    page_hash = hashlib.sha256(body.html.encode("utf-8")).hexdigest()

    lock = _get_site_lock(sid)
    if not lock.acquire(blocking=True, timeout=60):
        raise HTTPException(status_code=503, detail="busy, retry shortly")
    try:
        meta = _read_json(sid, "meta.json", {"hashes": {}})
        if meta["hashes"].get(url) == page_hash:
            cur = STATE["sites"].get(sid)
            return {"site_id": sid, "chunks": len(cur[0]) if cur else 0,
                    "status": "unchanged",
                    "message": "Page already indexed at this version."}

        page_chunks = chunk_rendered(body.html, url, body.title or "")
        if not page_chunks:
            return {"site_id": sid, "chunks": 0, "status": "empty",
                    "message": "No indexable content on this page."}

        # merge: replace this URL's chunks, keep the rest
        merged = [c for c in _read_json(sid, "chunks.json", [])
                  if c.get("url") != url]
        merged.extend(page_chunks)
        for i, c in enumerate(merged):
            c["id"] = i

        d = site_dir(sid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "chunks.json").write_text(
            json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        meta["hashes"][url] = page_hash
        (d / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        chunks, vecs = embed_site(sid)
        STATE["sites"][sid] = (chunks, vecs)
        print(f"[ingest] {sid} {url}: +{len(page_chunks)}, {len(chunks)} total")
        return {"site_id": sid, "chunks": len(chunks), "status": "indexed",
                "message": f"Indexed this page ({len(page_chunks)} sections)."}
    finally:
        lock.release()