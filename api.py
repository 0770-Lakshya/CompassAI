"""
api.py — multi-site HTTP layer for Compass.

Loads every registered site's index at startup into memory. Each /query
carries a site_id so the API searches only that site's chunks.

Usage:
    uvicorn api:app --reload
"""

from dotenv import load_dotenv
load_dotenv()

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq

from retrieval import load_all_sites, load_site, model
from answer import answer

# in-memory registry: {site_id: (chunks, vecs)}  +  shared llm
STATE = {"sites": {}, "llm": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("Set GROQ_API_KEY before starting the server.")
    print("warming embedder + loading sites...")
    model()                                  # warm the ONNX model once
    STATE["sites"] = load_all_sites()
    STATE["llm"] = Groq(api_key=key)
    print(f"ready. {len(STATE['sites'])} site(s) loaded: {list(STATE['sites'])}")
    yield
    STATE["sites"].clear()


app = FastAPI(title="Compass API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


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


@app.get("/health")
def health():
    return {
        "status": "ok",
        "sites": {sid: len(chunks) for sid, (chunks, _) in STATE["sites"].items()},
    }


@app.get("/sites")
def sites():
    return {"sites": list(STATE["sites"].keys())}


@app.post("/query", response_model=QueryOut)
def query(body: QueryIn):
    site = STATE["sites"].get(body.site_id)
    if site is None:
        # site not indexed — a clean, explicit signal (not a 500)
        return {"found": False,
                "reason": f"site '{body.site_id}' is not registered with Compass"}
    chunks, vecs = site
    return answer(body.query, chunks, vecs, STATE["llm"])