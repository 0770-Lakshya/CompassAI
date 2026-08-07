"""
Hybrid retrieval: semantic + lexical.

Semantic search alone fails on:
  - typos ("phylosophy")
  - exact terms that carry the whole query
  - page names that only appear in the URL slug

Lexical (fuzzy string) matching catches those. Semantic catches meaning
when the words don't match at all ("codeforces" -> "canonforces").
Combining both is strictly better than either.

Usage from answer.py:
    from retrieval import load, search
"""

import json
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer

CHUNKS = Path("chunks.json")
CACHE = Path("embeddings.npy")
EMBED_MODEL = "all-MiniLM-L6-v2"

# how much each signal counts
W_SEMANTIC = 0.65
W_LEXICAL = 0.35

LEVEL_BONUS = {"h1": 0.08, "h2": 0.0, "h3": -0.02}

FILLER = {"where", "is", "are", "the", "all", "of", "a", "an", "to",
          "me", "take", "show", "find", "i", "want", "can", "how",
          "do", "on", "in", "at", "page", "please", "what"}


def strip_filler(query):
    """Keep only meaningful words for lexical matching."""
    words = [w for w in query.lower().split() if w not in FILLER]
    return " ".join(words) or query.lower()


def slug_words(url):
    path = urlparse(url).path.strip("/")
    if not path:
        return "home homepage main index"      # root page needs searchable words
    return path.replace("-", " ").replace("_", " ").replace("/", " ")


def build_text(c):
    """
    What gets embedded. Includes the URL slug so that a page's own name
    is searchable even when it appears nowhere in the visible text.
    """
    return (f"{slug_words(c['url'])} | {c['page_title']} | "
            f"{c['heading']} | {c['heading']} | {c['content']}")


def lexical_target(c):
    """The short, high-signal text we fuzzy-match the query against."""
    return f"{slug_words(c['url'])} {c['page_title']} {c['heading']}"


def load(force_reembed=False):
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))
    embedder = SentenceTransformer(EMBED_MODEL)

    if CACHE.exists() and not force_reembed:
        vecs = np.load(CACHE)
        if len(vecs) == len(chunks):
            return chunks, embedder, vecs

    print(f"embedding {len(chunks)} chunks...")
    vecs = embedder.encode([build_text(c) for c in chunks],
                           normalize_embeddings=True,
                           show_progress_bar=True)
    np.save(CACHE, vecs)
    return chunks, embedder, vecs


def search(query, chunks, embedder, vecs, k=3, debug=False):
    q = embedder.encode([query], normalize_embeddings=True)[0]
    sem = vecs @ q

    lean = strip_filler(query)          # <-- filler removed
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