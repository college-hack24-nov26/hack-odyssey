"""Match a typed sentence to a goal by keywords. No language model: the same text always gives the same answer, offline."""
from __future__ import annotations

import re

from .catalogue import all_goals


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", str(text).lower())


MIN_HITS = 2          # one stray word ("water" in "a plant monitor ... water") is not a ready-made job; two different words are


def match(text: str) -> list[dict]:
    """Goals ranked by how many different words of the text hit their keywords (a phrase of 2+ goals is possible). A goal needs
    MIN_HITS different words, so one word shared with an unrelated job never picks it. Empty when nothing matches, so the caller
    asks the user instead of guessing. `score` counts different words of the text (a word that hits "inspect" and "inspection"
    is one hit); `matched` lists the keywords."""
    words = set(_words(text))
    if not words:
        return []
    scored = []
    for g in all_goals().values():
        hits, covered = set(), set()
        for k in g.keywords:
            for w in words:
                if w == k or (len(k) > 4 and k in w):
                    hits.add(k)
                    covered.add(w)
        if len(covered) >= MIN_HITS:
            scored.append({"goal": g.id, "title": g.title, "score": len(covered), "matched": sorted(hits)})
    scored.sort(key=lambda s: (-s["score"], s["goal"]))
    return scored
