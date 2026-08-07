"""
Hybrid retrieval: semantic (local model) + lexical (rapidfuzz).

Uses a local sentence-transformers model so there is NO API key, NO rate
limit, and NO billing. all-MiniLM-L6-v2 is 384-dim and ~90MB — small
enough for Render's free tier at runtime. The lexical half and all
scoring (filler stripping, h1 bonus, weighting) are unchanged.

Usage from answer.py / api.py:
    from retrieval import load, search
"""

import os
# load the model from local cache without phoning home to Hugging Face
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import json
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer

CHUNKS = Path("chunks.json")
CACHE = Path("embeddings.npy")
EMBED_MODEL = "all-MiniLM-L6-v2"     # 384-dim, ~90MB, local & free

W_SEMANTIC = 0.65
W_LEXICAL = 0.35
LEVEL_BONUS = {"h1": 0.08, "h2": 0.0, "h3": -0.02}

FILLER = {"where", "is", "are", "the", "all", "of", "a", "an", "to",
          "me", "take", "show", "find", "i", "want", "can", "how",
          "do", "on", "in", "at", "page", "please", "what"}

_model = None


def model():
    """Load the embedder once, lazily, and keep it in memory."""
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


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
    return model().encode([query], normalize_embeddings=True)[0]


def load(force_reembed=False):
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))

    if CACHE.exists() and not force_reembed:
        vecs = np.load(CACHE)
        if len(vecs) == len(chunks):
            return chunks, None, vecs

    print(f"embedding {len(chunks)} chunks locally...")
    texts = [build_text(c) for c in chunks]
    vecs = model().encode(texts, normalize_embeddings=True,
                          show_progress_bar=True)
    vecs = np.asarray(vecs, dtype=np.float32)
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