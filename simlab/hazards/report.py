"""Write a stress-test result as report.json and a self-contained report.html (runs/reports/<stamp>_goal_<name>/)."""
from __future__ import annotations

import html
import json
import re
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = """body{font:15px/1.5 system-ui,sans-serif;background:#12151c;color:#e6e9ef;max-width:920px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:26px 0 8px;color:#9fb4d6}table{border-collapse:collapse;width:100%}
td,th{padding:6px 8px;border-bottom:1px solid #2a3040;text-align:left;vertical-align:top}th{color:#8b95aa;font-weight:600;font-size:13px}
.c{display:inline-block;min-width:44px;text-align:center;border-radius:6px;padding:2px 6px;margin:1px;font-size:13px;color:#06100a}
.ok{background:#3ecf8e}.mid{background:#e8c34a}.bad{background:#ef6a6a}.box{background:#1b2030;border:1px solid #2a3040;border-radius:10px;padding:12px 14px;margin:10px 0}
.warn{border-color:#e8c34a}.muted{color:#8b95aa;font-size:13px}li{margin:3px 0}"""


def _cell(lv: dict, thr: float) -> str:
    cls = "ok" if lv["success"] >= thr else "mid" if lv["success"] >= 0.5 else "bad"
    return f'<span class="c {cls}" title="{lv["n"]} runs">{lv["success"]:.0%}</span>'


def render(result: dict, goal_title: str, machine: dict | None = None) -> str:
    e, thr = html.escape, result["threshold"]
    rows = ""
    for h in result["hazards"]:
        cells = "".join(f'<div class="muted" style="display:inline-block;text-align:center">{lv["value"]:g}<br>{_cell(lv, thr)}</div>' for lv in h["levels"])
        rows += f'<tr><td><b>{e(h["title"])}</b><div class="muted">{e(h["plain"])}</div></td><td>{cells}<div>{e(h["summary"])}</div></td></tr>'
    frows = "".join(f'<tr><td><b>{e(f["title"])}</b><div class="muted">{e(f["plain"])}</div></td><td>{_cell(f, thr)} '
                    f'{"carries on" if f["ok"] else "fails"}'
                    + (f' <span class="muted">({", ".join(f"{k} x{v}" for k, v in f["crashes"].items())})</span>' if f["crashes"] else "") + "</td></tr>"
                    for f in result["faults"])
    weak = result.get("weakest")
    weak_html = (f'<div class="box warn"><b>Weakest point: {e(weak["title"])}.</b> {e(weak["why"])}<br><span class="muted">What may help: {e(weak["fix"])}</span></div>'
                 if weak else '<div class="box">Nothing in the tested list broke the design.</div>')
    machine_html = ""
    if machine:
        machine_html = f'<div class="box"><b>Machine:</b> {e(machine.get("name", ""))} &middot; parts: {e(", ".join(machine.get("parts", [])))}</div>'
    partial = '<div class="box warn">This test was stopped early: some conditions were not run.</div>' if result.get("partial") else ""
    nt = "".join(f"<li>{e(x)}</li>" for x in result["not_tested"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stress test: {e(goal_title)}</title><style>{CSS}</style></head><body>
<h1>How tough is it? {e(goal_title)}</h1>
<div class="muted">{result["episodes"]} runs per condition, the same scenes at every level. A level counts as passed when at least {thr:.0%} of the runs succeed. Tested with {e(result["baseline"])}.</div>
{machine_html}{partial}{weak_html}
<h2>Conditions that get worse step by step</h2><table><tr><th>Hazard</th><th>Success at each level</th></tr>{rows}</table>
<h2>Things that go wrong</h2><table><tr><th>Failure</th><th>Result</th></tr>{frows}</table>
<h2>Not tested</h2><ul>{nt}</ul>
<div class="box warn"><b>Read this first.</b> {e(result["statement"])}</div>
<div class="muted">Made {time.strftime("%Y-%m-%d %H:%M")} in {result["seconds"]} s.</div></body></html>"""


def write_report(result: dict, goal_id: str, goal_title: str, machine: dict | None = None, root: Path | None = None) -> str:
    """Returns the report folder's name (served at /reports/<name>/report.html by the dashboard)."""
    base = (root or ROOT / "runs" / "reports")
    name = f"{time.strftime('%Y%m%d_%H%M%S')}_goal_{re.sub(r'[^a-z0-9_]', '', goal_id.lower())}_{uuid.uuid4().hex[:6]}"      # the suffix: two reports in one second must not share a folder
    out = base / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"goal": goal_id, "machine": machine, "result": result}, indent=1, default=str), encoding="utf-8")
    (out / "report.html").write_text(render(result, goal_title, machine), encoding="utf-8")
    return name
