"""
api.py — HTTP layer for Compass.

Wraps the existing retrieval + answer pipeline in a FastAPI server so the
browser widget can call it. The embedder and chunks load ONCE at startup
(not per request), which is the whole point of a server vs the CLI.

Usage:
    pip install fastapi uvicorn
    setx GROQ_API_KEY "gsk_..."      (then reopen terminal)
    uvicorn api:app --reload

Then test without a browser:
    curl -X POST http://localhost:8000/query ^
         -H "Content-Type: application/json" ^
         -d "{\"query\": \"where are the projects\"}"
"""

from dotenv import load_dotenv
load_dotenv()

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq

from retrieval import load, search
from answer import answer   # reuse the exact logic the CLI uses

# --- loaded once at startup, held in memory for the server's lifetime ---
STATE = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("Set GROQ_API_KEY before starting the server.")
    print("loading model + chunks (once)...")
    chunks, embedder, vecs = load()          # no force_reembed in production
    STATE["chunks"] = chunks
    STATE["embedder"] = embedder
    STATE["vecs"] = vecs
    STATE["llm"] = Groq(api_key=key)
    print(f"ready. {len(chunks)} chunks in memory.")
    yield
    STATE.clear()


app = FastAPI(title="Compass API", lifespan=lifespan)

# CORS: the widget runs on the customer's domain and calls this API from
# a different origin. Without this, the browser blocks every request.
# "*" is fine for local dev; in production, restrict to registered sites.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


class QueryIn(BaseModel):
    query: str


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
    return {"status": "ok", "chunks": len(STATE.get("chunks", []))}


@app.post("/query", response_model=QueryOut)
def query(body: QueryIn):
    result = answer(
        body.query,
        STATE["chunks"],
        STATE["embedder"],
        STATE["vecs"],
        STATE["llm"],
    )
    return result