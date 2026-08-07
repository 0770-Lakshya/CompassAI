"""
Step 4: The answer layer.

Takes a query, runs semantic search, passes the top chunk to an LLM
with a strict grounded prompt, returns structured JSON or a refusal.

Usage:
    pip install groq
    setx GROQ_API_KEY "gsk_..."    (then reopen terminal)
    python answer.py

Requires chunks.json + embeddings.npy from the previous steps.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import json
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer
from groq import Groq
from retrieval import load, search

CHUNKS = Path("chunks.json")
CACHE = Path("embeddings.npy")
EMBED_MODEL = "all-MiniLM-L6-v2"
LLM_MODEL = "llama-3.3-70b-versatile"   # smart, still free-tier
# LLM_MODEL = "llama-3.1-8b-instant"    # faster, use if you hit rate limits

CONFIDENCE_FLOOR = 0.35   # below this, don't even ask the LLM

# The prompt is the product. Every word here matters.
SYSTEM = """You are a website navigation assistant. You are given ONE section from a website and a visitor's request.

Your job is NOT to answer the question. Your job is to decide whether sending the visitor to this section would help them, and then tell them what they'll find there.

Set found to true if the section is a reasonable destination for this request — including when the visitor is simply asking where something is, or naming a page or topic. The section does not need to contain a complete answer; it only needs to be the right place to go.

Set found to false only if this section is clearly about something unrelated.

Rules:
- Use ONLY the given content. Never use outside knowledge.
- If the content contains a direct answer (a name, date, number), include it in the explanation.
- Otherwise describe what the visitor will see there.
- Keep the explanation under 30 words.

Respond with ONLY a JSON object:
{"found": true or false, "explanation": "..."}"""

USER_TEMPLATE = """SECTION FROM: {url}
HEADING: {heading}

CONTENT:
{content}

USER QUESTION: {query}"""






def answer(query, chunks, embedder, vecs, llm):
    results = search(query, chunks, embedder, vecs, k=3)
    top, score = results[0]

    # Cheap refusal: don't bother the LLM if retrieval is weak
    if score < CONFIDENCE_FLOOR:
        return {
            "found": False,
            "reason": f"no confident match (top score {score:.3f} below floor {CONFIDENCE_FLOOR})",
            "score": score,
        }

    resp = llm.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_TEMPLATE.format(
                url=top["url"],
                heading=top["heading"],
                content=top["content"],
                query=query,
            )},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,        # low = stick to the content, don't get creative
        max_tokens=200,
    )

    parsed = json.loads(resp.choices[0].message.content)

    # LLM says the chunk doesn't answer — trust it
    if not parsed.get("found"):
        return {
            "found": False,
            "reason": "the top chunk did not address the question",
            "score": score,
        }

    return {
        "found": True,
        "url": top["url"],
        "selector": top["selector"],
        "heading": top["heading"],
        "explanation": parsed["explanation"],
        "confidence": score,
    }


def main():
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise SystemExit("Set GROQ_API_KEY. See https://console.groq.com/keys")

    print("loading...")
    chunks, embedder, vecs = load()
    llm = Groq(api_key=key)

    print(f"ready. {len(chunks)} chunks indexed. Ask something (blank to quit).\n")

    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            break

        result = answer(q, chunks, embedder, vecs, llm)
        print()
        if result["found"]:
            print(f"  {result['explanation']}")
            print(f"  → {result['url']}")
            print(f"    selector: {result['selector']}")
            print(f"    (confidence {result['confidence']:.3f})")
        else:
            print(f"  I couldn't find that on this site.")
            print(f"  ({result['reason']})")
        print()


if __name__ == "__main__":
    main()