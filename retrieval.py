"""
Hybrid retrieval: semantic (fastembed / ONNX) + lexical (rapidfuzz).
Multi-site aware: each registered site has its own index under sites/<site_id>/.

Layout:
    sites/
      openlake.in/
        chunks.json
        embeddings.npy
      example.com/
        chunks.json
        embeddings.npy

The embedding model is shared across all sites (embeddings are model-specific
but site-independent). Only the chunks + vectors differ per site.

Usage from api.py:
    from retrieval import load_all_sites, load_site, search, model
"""

import json
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from rapidfuzz import fuzz
from fastembed import TextEmbedding

SITES_DIR = Path("sites")
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"   # 384-dim, ONNX

W_SEMANTIC = 0.65
W_LEXICAL = 0.35
LEVEL_BONUS = {"h1": 0.08, "h2": 0.0, "h3": -0.02}

FILLER = {"where", "is", "are", "the", "all", "of", "a", "an", "to",
          "me", "take", "show", "find", "i", "want", "can", "how",
          "do", "on", "in", "at", "page", "please", "what"}

_model = None


def model():
    """Load the ONNX embedder once, lazily, shared across all sites."""
    global _model
    if _model is None:
        _model = TextEmbedding(model_name=EMBED_MODEL)
    return _model


def _encode(texts):
    vecs = np.array(list(model().embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.clip(norms, 1e-8, None)


def embed_query(query):
    return _encode([query])[0]


# ---------- text builders (shared) ----------

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


# ---------- per-site loading ----------

def site_dir(site_id):
    return SITES_DIR / site_id


def embed_site(site_id):
    """(Re)build embeddings.npy for one site from its chunks.json."""
    d = site_dir(site_id)
    chunks = json.loads((d / "chunks.json").read_text(encoding="utf-8"))
    print(f"[{site_id}] embedding {len(chunks)} chunks...")
    vecs = _encode([build_text(c) for c in chunks])
    np.save(d / "embeddings.npy", vecs)
    return chunks, vecs


def load_site(site_id, reembed=False):
    """Load one site's chunks + vectors. Embeds if missing or reembed=True."""
    d = site_dir(site_id)
    chunks = json.loads((d / "chunks.json").read_text(encoding="utf-8"))
    cache = d / "embeddings.npy"
    if cache.exists() and not reembed:
        vecs = np.load(cache)
        if len(vecs) == len(chunks):
            return chunks, vecs
    return embed_site(site_id)


def load_all_sites():
    """Load every site under sites/ into {site_id: (chunks, vecs)}.
    Called once at API startup."""
    registry = {}
    if not SITES_DIR.exists():
        return registry
    for d in sorted(SITES_DIR.iterdir()):
        if d.is_dir() and (d / "chunks.json").exists():
            try:
                registry[d.name] = load_site(d.name)
                print(f"loaded site '{d.name}' ({len(registry[d.name][0])} chunks)")
            except Exception as e:
                print(f"skip site '{d.name}': {e}")
    return registry


# ---------- search (per-site) ----------

def search(query, chunks, vecs, k=3, debug=False):
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