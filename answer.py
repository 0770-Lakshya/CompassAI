"""
The answer layer.

Takes a query + one site's chunks/vectors, runs hybrid search, passes the
top chunk to an LLM with a grounded prompt, returns structured JSON or a
refusal. Site-aware: chunks/vecs are passed in per request by the API.

Usage:
    from answer import answer
"""

import os
import json

from retrieval import search

LLM_MODEL = "llama-3.3-70b-versatile"
CONFIDENCE_FLOOR = 0.35

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


def answer(query, chunks, vecs, llm):
    results = search(query, chunks, vecs, k=3)
    if not results:
        return {"found": False, "reason": "no chunks for this site", "score": 0.0}

    top, score = results[0]

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
        temperature=0.1,
        max_tokens=200,
    )

    parsed = json.loads(resp.choices[0].message.content)

    if not parsed.get("found"):
        return {"found": False,
                "reason": "the top chunk did not address the question",
                "score": score}

    return {
        "found": True,
        "url": top["url"],
        "selector": top["selector"],
        "heading": top["heading"],
        "explanation": parsed["explanation"],
        "confidence": score,
    }