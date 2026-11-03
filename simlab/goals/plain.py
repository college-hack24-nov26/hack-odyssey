"""Plain-language lines for the Goal screen: one traffic-light verdict and short sentences instead of rule codes."""
from __future__ import annotations

import re


def finding_line(f: dict) -> str:
    """'Title. First sentence of the detail.' without rule ids or part-number noise."""
    detail = re.split(r"(?<=[.!?])\s", f.get("detail", "").strip())[0] if f.get("detail") else ""
    title = f["title"].rstrip(".")
    return f"{title}. {detail}" if detail and detail.rstrip(".") != title else f"{title}."


def verdict(checks: list[dict], findings: list[dict], goal) -> dict:
    """green = meets every requirement with nothing to warn about; amber = meets them, with things to watch; red = misses one."""
    unmet = [c for c in checks if not c["ok"]]
    errors = [f for f in findings if f["severity"] == "error"]
    warns = [f for f in findings if f["severity"] == "warn"]
    if unmet or errors:
        reasons = [f"{c['text']}: {c['detail']}" for c in unmet] + [finding_line(f) for f in errors]
        return {"light": "red", "headline": "Does not meet the goal yet.", "reasons": reasons[:4]}
    if warns:
        return {"light": "amber", "headline": f"Should do the job, with {len(warns)} thing{'s' if len(warns) > 1 else ''} to watch.",
                "reasons": [finding_line(f) for f in warns[:3]]}
    return {"light": "green", "headline": "Should do the job on paper.", "reasons": [c["text"] + ": " + c["detail"] for c in checks[:3]]}
