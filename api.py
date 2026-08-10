"""
api.py — multi-site HTTP layer for Compass.

Loads every registered site's index at startup. Each /query carries a
site_id so only that site's chunks are searched. New sites are added via
POST /register (protected by ADMIN_TOKEN).

Crawling does NOT happen here — Playwright needs more memory than a free
Render instance has. The crawl runs wherever there's room (your machine
via register_site.py today, a dedicated crawl service later) and pushes
the finished chunks to /register. Swapping to on-demand crawling later
means changing who calls this endpoint, not the endpoint itself.

Usage:
    uvicorn api:app --reload
"""

from dotenv import load_dotenv
load_dotenv()

import os
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq

from retrieval import load_all_sites, embed_site, site_dir, SITES_DIR, model
from answer import answer

STATE = {"sites": {}, "llm": None}


def normalize_site_id(raw: str) -> str:
    """
    Hostnames vary: Example.COM, www.example.com, example.com:8080 are all
    the same site to a visitor. Normalize so a site registered once matches
    however the visitor arrived.
    """
    s = (raw or "").strip().lower()
    s = s.split("//")[-1]          # tolerate a full URL being passed
    s = s.split("/")[0]            # drop any path
    s = s.split(":")[0]            # drop port
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
    site_id: str


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
                "reason": f"site '{sid}' is not registered with Compass"}
    chunks, vecs = site
    return answer(body.query, chunks, vecs, STATE["llm"])


@app.post("/register", response_model=RegisterOut)
def register(body: RegisterIn, x_admin_token: str = Header(default="")):
    """
    Add or replace a site's index. Expects already-crawled chunks, each with
    url / page_title / heading / level / selector / content.
    """
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

    # embed now and put it straight into the live registry — no restart needed
    chunks, vecs = embed_site(sid)
    STATE["sites"][sid] = (chunks, vecs)

    return {"site_id": sid, "chunks": len(chunks), "status": "indexed"}