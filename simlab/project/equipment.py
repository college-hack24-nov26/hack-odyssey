"""The equipment list: the blueprint's parts, each with an inventory state (have / buy / swap), the cost of what is left to buy, and the comparison
"using what I have" vs "best".

* `rows()` takes the blueprint's bill of materials (simlab.goals.assembly.bill) and what the user owns, and applies the user's decisions.
  Without a decision a part is "have" when the owned count covers it, else "buy". A decision "have" is the user's word and is trusted; "swap" names a
  different library part of the same kind that replaces it.
* Prices are the user's (the parts library has no price field): a part with no price is listed as unpriced and left out of the total, never guessed.
* `compare()` is "best" (the blueprint exactly as designed, buy whatever is missing) against "using what I have": every part you would have to buy is
  tried against the owned parts of the same kind, each swap is applied with simlab.parts.recommend.apply_patch and checked by the Builder's evaluator,
  and kept only if it adds no new error. What the swap costs you in warnings is reported, not hidden.
"""
from __future__ import annotations

import copy

from ..goals.assembly import bill
from ..parts import evaluate
from ..parts.library import all_parts, get_part, parts_by_kind
from ..parts.recommend import apply_patch, make_patch

STATES = ("have", "buy", "swap")


class EquipmentError(ValueError):
    pass


def _price(prices: dict, name: str):
    v = (prices or {}).get(name)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def rows(build: dict, owned: dict, decisions: dict | None = None, prices: dict | None = None) -> list[dict]:
    """One row per part of the blueprint: qty, have, state, what to buy, price and line cost (None when unpriced)."""
    owned, decisions, prices = owned or {}, decisions or {}, prices or {}
    out = []
    for r in bill(build, owned):
        d = decisions.get(r["name"]) or {}
        auto = "have" if r["have"] >= r["qty"] else "buy"
        state = d.get("state") if d.get("state") in STATES else auto
        swap_to = d.get("swap_to") if state == "swap" else None
        if state == "swap" and swap_to not in all_parts():
            state, swap_to = auto, None                                     # a swap target that no longer exists falls back to the automatic state
        buy_name = swap_to or r["name"]
        if state == "have":
            to_buy = 0
        else:
            to_buy = max(0, r["qty"] - int(owned.get(buy_name, 0)))          # for a swap: how many of the replacement you still lack
        price = _price(prices, buy_name)
        out.append({"name": r["name"], "title": r["title"], "kind": r["kind"], "qty": r["qty"], "owned": int(owned.get(r["name"], 0)), "state": state,
                    "auto_state": auto, "swap_to": swap_to, "buy_name": buy_name, "buy_qty": to_buy,
                    "price": price, "line_cost": None if price is None else round(price * to_buy, 2),
                    "claimed": state == "have" and r["have"] < r["qty"], "confidence": r["confidence"], "source": r["source"]})
    return out


def totals(rs: list[dict]) -> dict:
    """What is left to buy: the cost of the priced rows, and the rows that still need a price."""
    buy = [r for r in rs if r["buy_qty"] > 0]
    unpriced = sorted({r["buy_name"] for r in buy if r["price"] is None})
    return {"cost": round(sum(r["line_cost"] for r in buy if r["line_cost"] is not None), 2), "unpriced": unpriced, "complete": not unpriced,
            "to_buy": sum(r["buy_qty"] for r in buy), "parts_to_buy": len(buy), "have": sum(1 for r in rs if r["state"] == "have"),
            "swaps": sum(1 for r in rs if r["state"] == "swap")}


def check_decision(build: dict, part: str, state: str, swap_to: str | None) -> None:
    """Raise EquipmentError unless this is a sensible decision for a part of the blueprint."""
    names = {r["name"]: r for r in bill(build, {})}
    if part not in names:
        raise EquipmentError(f"{part!r} is not a part of this blueprint")
    if state not in STATES:
        raise EquipmentError(f"state must be one of {', '.join(STATES)}")
    if state == "swap":
        try:
            new = get_part(swap_to or "")
        except KeyError:
            raise EquipmentError(f"swap needs a library part of the same kind as {part} (swap_to {swap_to!r} is not one)")
        if new.kind != names[part]["kind"] or new.name == part:
            raise EquipmentError(f"{swap_to} is not a different part of the same kind ({names[part]['kind']}) as {part}")


# ---------------------------------------------------------------- "using what I have" vs "best"
def _replace(build: dict, old: str, new_part) -> dict | None:
    """`build` with part `old` replaced by `new_part`, or None when this kind of part cannot be swapped one for one."""
    b = copy.deepcopy(build)
    if new_part.kind.startswith("sensor_"):
        hit = False
        for s in b.get("sensors", []):
            if s.get("part") == old:
                s["part"], hit = new_part.name, True
        return b if hit else None
    if new_part.kind == "lamp":
        if b.get("lamp", {}).get("part") != old:
            return None
        b["lamp"]["part"] = new_part.name
        return b
    patch = make_patch(b, new_part)
    return apply_patch(b, patch) if patch and "set" in patch else None


def _score(build: dict):
    r = evaluate(build, None, suggest=False)
    if r["vehicle"] is None:
        return None
    errs = {f["id"] for f in r["findings"] if f["severity"] == "error"}
    warns = [f for f in r["findings"] if f["severity"] == "warn"]
    return {"errors": errs, "warns": len(warns), "warn_ids": sorted({f["id"] for f in warns}), "ok": r["ok"]}


def compare(build: dict, owned: dict, prices: dict | None = None) -> dict:
    """{"best": {...}, "using_what_i_have": {...}, "saved": money or None, "swaps": [...], "still_to_buy": [...]}. Both sides carry rows, totals and the
    Builder's check of that machine, so the page can show the cost against what you give up."""
    owned, prices = owned or {}, prices or {}
    base_rows = rows(build, owned, None, prices)
    base_score = _score(build)
    cur, swaps = copy.deepcopy(build), []
    for r in base_rows:
        if r["buy_qty"] == 0:
            continue                                                           # already owned: nothing to replace
        candidates = [p for p in parts_by_kind().get(r["kind"], []) if p.name != r["name"] and int(owned.get(p.name, 0)) >= r["qty"]]
        best = None
        before = _score(cur)
        for p in candidates:
            trial = _replace(cur, r["name"], p)
            sc = _score(trial) if trial is not None else None
            if sc is None or (before is not None and sc["errors"] - before["errors"]) or (before is None and not sc["ok"]):
                continue                                                       # unusable, or it adds a new error
            key = (len(sc["errors"]), sc["warns"], p.name)
            if best is None or key < best[0]:
                best = (key, p, trial, sc)
        if best is not None:
            _, p, trial, sc = best
            swaps.append({"part": r["name"], "kind": r["kind"], "swap_to": p.name, "title": p.title, "qty": r["qty"], "confidence": p.confidence,
                          "warnings_before": before["warns"] if before else None, "warnings_after": sc["warns"],
                          "new_warnings": sorted(set(sc["warn_ids"]) - set(before["warn_ids"])) if before else sc["warn_ids"]})
            cur = trial
    have_decisions = {s["part"]: {"state": "swap", "swap_to": s["swap_to"]} for s in swaps}
    have_rows = rows(build, owned, have_decisions, prices)
    have_score = _score(cur)
    best_t, have_t = totals(base_rows), totals(have_rows)
    both_priced = best_t["complete"] and have_t["complete"]
    return {"best": {"rows": base_rows, "totals": best_t, "check": _public(base_score)},
            "using_what_i_have": {"rows": have_rows, "totals": have_t, "check": _public(have_score), "build": cur},
            "swaps": swaps,
            "still_to_buy": [r["name"] for r in have_rows if r["buy_qty"] > 0],
            "saved": round(best_t["cost"] - have_t["cost"], 2) if both_priced else None,
            "note": ("" if both_priced else "Some parts have no price yet, so the saving is not computed: add the prices of " +
                     ", ".join(sorted(set(best_t["unpriced"]) | set(have_t["unpriced"]))) + ".")}


def _public(score: dict | None) -> dict | None:
    return None if score is None else {"ok": score["ok"], "errors": len(score["errors"]), "warnings": score["warns"], "warning_ids": score["warn_ids"]}
