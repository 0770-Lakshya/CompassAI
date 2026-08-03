"""
Step 3: Embed chunks and search them.

Proves the core retrieval works BEFORE any LLM or widget exists.
Type a question -> get back the page URL and the CSS selector to
scroll to. That pair is the whole product.

First run downloads a ~90MB model, then it's offline and free.

Usage:
    pip install sentence-transformers numpy
    python search.py

Input:   chunks.json  (from chunker.py)
Cache:   embeddings.npy
"""

import json
from pathlib import Path

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import json
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

CHUNKS = Path("chunks.json")
CACHE = Path("embeddings.npy")
MODEL = "all-MiniLM-L6-v2"
TOP_K = 3


def build_text(c):
    """
    What actually gets embedded.

    Heading is repeated and page title prepended so that short,
    label-like headings ("Campus-Marketplace") still carry weight
    against longer body text.
    """
    return f"{c['page_title']} | {c['heading']} | {c['heading']} | {c['content']}"


def load():
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))
    model = SentenceTransformer(MODEL)

    if CACHE.exists():
        vecs = np.load(CACHE)
        if len(vecs) == len(chunks):
            print(f"loaded {len(chunks)} cached embeddings")
            return chunks, model, vecs
        print("chunk count changed, re-embedding...")

    print(f"embedding {len(chunks)} chunks...")
    vecs = model.encode(
        [build_text(c) for c in chunks],
        normalize_embeddings=True,      # so dot product == cosine similarity
        show_progress_bar=True,
    )
    np.save(CACHE, vecs)
    return chunks, model, vecs


def search(query, chunks, model, vecs, k=TOP_K):
    q = model.encode([query], normalize_embeddings=True)[0]
    scores = vecs @ q                    # cosine similarity, all at once
    top = np.argsort(-scores)[:k]
    return [(chunks[i], float(scores[i])) for i in top]


def main():
    chunks, model, vecs = load()
    print("\nAsk something (blank line to quit).")
    print("try: where can I find the marketplace project\n")

    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            break

        for c, score in search(q, chunks, model, vecs):
            print(f"\n  [{score:.3f}] {c['heading']}  ({c['level']})")
            print(f"      url:      {c['url']}")
            print(f"      selector: {c['selector']}")
            print(f"      content:  {c['content'][:110]}...")
        print()


if __name__ == "__main__":
    main()