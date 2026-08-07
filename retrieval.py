"""
Hybrid retrieval: semantic (fastembed / ONNX) + lexical (rapidfuzz).

Uses fastembed's ONNX build of all-MiniLM-L6-v2 — same model, same 384-dim
vectors as before, but NO torch, so it fits comfortably in Render's 512MB
free tier. No API key, no rate limit, no billing. The lexical half and all
scoring (filler stripping, h1 bonus, weighting) are unchanged.

Usage from answer.py / api.py:
    from retrieval import load, search
"""

import json
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from rapidfuzz import fuzz
from fastembed import TextEmbedding

CHUNKS = Path("chunks.json")
CACHE = Path("embeddings.npy")
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"   # 384-dim, ONNX, ~90MB

W_SEMANTIC = 0.65
W_LEXICAL = 0.35
LEVEL_BONUS = {"h1": 0.08, "h2": 0.0, "h3": -0.02}

FILLER = {"where", "is", "are", "the", "all", "of", "a", "an", "to",
          "me", "take", "show", "find", "i", "want", "can", "how",
          "do", "on", "in", "at", "page", "please", "what"}

_model = None


def model():
    """Load the ONNX embedder once, lazily, kept in memory."""
    global _model
    if _model is None:
        _model = TextEmbedding(model_name=EMBED_MODEL)
    return _model


def _encode(texts):
    """fastembed returns a generator of un-normalized vectors; normalize
    so a dot product equals cosine similarity."""
    vecs = np.array(list(model().embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-8, None)


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


def embed_query(query):
    return _encode([query])[0]


def load(force_reembed=False):
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))

    if CACHE.exists() and not force_reembed:
        vecs = np.load(CACHE)
        if len(vecs) == len(chunks):
            return chunks, None, vecs

    print(f"embedding {len(chunks)} chunks locally (ONNX)...")
    texts = [build_text(c) for c in chunks]
    vecs = _encode(texts)
    np.save(CACHE, vecs)
    return chunks, None, vecs


def search(query, chunks, embedder, vecs, k=3, debug=False):
    # embedder arg kept for signature compatibility; unused
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