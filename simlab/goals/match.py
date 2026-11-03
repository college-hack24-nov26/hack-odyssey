"""Match a typed sentence to a goal by keywords. No language model: the same text always gives the same answer, offline."""
from __future__ import annotations

import re

from .catalogue import all_goals


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", str(text).lower())


def match(text: str) -> list[dict]:
    """Goals ranked by how many of their keywords the text uses (a phrase of 2+ goals is possible). Empty when nothing matches,
    so the caller asks the user instead of guessing."""
    words = _words(text)
    if not words:
        return []
    joined = " ".join(words)
    scored = []
    for g in all_goals().values():
        hits = sorted({k for k in g.keywords if k in words or (len(k) > 4 and k in joined)})
        if hits:
            scored.append({"goal": g.id, "title": g.title, "score": len(hits), "matched": hits})
    scored.sort(key=lambda s: (-s["score"], s["goal"]))
    return scored
