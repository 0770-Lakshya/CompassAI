"""
Hybrid retrieval: semantic (Voyage API) + lexical (rapidfuzz).

Swapped from local sentence-transformers to Voyage hosted embeddings so
the server needs no torch and almost no RAM. The lexical half and all
scoring (filler stripping, h1 bonus, weighting) are unchanged.

Env:
    VOYAGE_API_KEY   from dashboard.voyageai.com

Usage from answer.py / api.py:
    from retrieval import load, search, embed_query
"""

import os
import json
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from rapidfuzz import fuzz
import voyageai

CHUNKS = Path("chunks.json")
CACHE = Path("embeddings.npy")
EMBED_MODEL = "voyage-3-lite"        # 512-dim, fast, free-tier friendly

W_SEMANTIC = 0.65
W_LEXICAL = 0.35
LEVEL_BONUS = {"h1": 0.08, "h2": 0.0, "h3": -0.02}

FILLER = {"where", "is", "are", "the", "all", "of", "a", "an", "to",
          "me", "take", "show", "find", "i", "want", "can", "how",
          "do", "on", "in", "at", "page", "please", "what"}

_client = None


def client():
    """Lazy Voyage client so importing this module doesn't require the key."""
    global _client
    if _client is None:
        key = os.environ.get("VOYAGE_API_KEY")
        if not key:
            raise RuntimeError("Set VOYAGE_API_KEY (dashboard.voyageai.com).")
        _client = voyageai.Client(api_key=key)
    return _client


def strip_filler(query):
    words = [w for w in query.lower().split() if w not in FILLER]
    return " ".join(words) or query.lower()


def slug_words(url):
    path = urlparse(url).path.strip("/")
    if not path:
        return "home homepage main index"
    return path.replace("-", " ").replace("_", " ").replace("/", " ")


def build_text(c):
    return (f"{slug_words(c['url'])} | {c['page_title']} | "
            f"{c['heading']} | {c['heading']} | {c['content']}")


def lexical_target(c):
    return f"{slug_words(c['url'])} {c['page_title']} {c['heading']}"


def _embed(texts, input_type):
    """Call Voyage; returns a normalized numpy array (rows = texts)."""
    resp = client().embed(texts, model=EMBED_MODEL, input_type=input_type)
    vecs = np.array(resp.embeddings, dtype=np.float32)
    # normalize so dot product == cosine similarity
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-8, None)


def embed_query(query):
    return _embed([query], input_type="query")[0]


def load(force_reembed=False):
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))

    if CACHE.exists() and not force_reembed:
        vecs = np.load(CACHE)
        if len(vecs) == len(chunks):
            return chunks, None, vecs

    print(f"embedding {len(chunks)} chunks via Voyage...")
    texts = [build_text(c) for c in chunks]
    out = []
    for i in range(0, len(texts), 128):
        out.append(_embed(texts[i:i + 128], input_type="document"))
    vecs = np.vstack(out)
    np.save(CACHE, vecs)
    return chunks, None, vecs


def search(query, chunks, embedder, vecs, k=3, debug=False):
    # embedder arg kept for signature compatibility; unused now
    q = embed_query(query)
    sem = vecs @ q

    lean = strip_filler(query)
    lex = np.array([
        fuzz.token_set_ratio(lean, lexical_target(c).lower()) / 100.0
        for c in chunks
    ])

    bonus = np.array([LEVEL_BONUS.get(c["level"], 0.0) for c in chunks])
    combined = W_SEMANTIC * sem + W_LEXICAL * lex + bonus

    top = np.argsort(-combined)[:k]
    out = []
    for i in top:
        out.append((chunks[i], float(combined[i])))
        if debug:
            print(f"    [{combined[i]:.3f}] sem={sem[i]:.3f} lex={lex[i]:.3f}  "
                  f"{chunks[i]['heading'][:40]}  ({chunks[i]['url']})")
    return out