"""Inspection report: JSON + self-contained HTML (map overview, defect table, snapshots)."""
from __future__ import annotations

import base64
import html
import json
import time
from pathlib import Path


def _map_svg(m, route, found, truth) -> str:
    x0, y0, x1, y1 = m.bounding_box(3.0)
    W, H = x1 - x0, y1 - y0
    sc = 900 / max(W, 1)
    px = lambda x: (x - x0) * sc          # noqa: E731
    py = lambda y: (y1 - y) * sc          # noqa: E731
    out = [f'<svg viewBox="0 0 {W * sc:.0f} {H * sc:.0f}" xmlns="http://www.w3.org/2000/svg" style="background:#10151c;width:100%;max-width:900px">']
    rset = {frozenset((route[i], route[i + 1])) for i in range(len(route) - 1)}
    for e in m.edges.values():
        a, b = m.nodes[e.a].pos, m.nodes[e.b].pos
        col = "#4a90d9" if frozenset((e.a, e.b)) in rset else "#445"
        out.append(f'<line x1="{px(a[0]):.1f}" y1="{py(a[1]):.1f}" x2="{px(b[0]):.1f}" y2="{py(b[1]):.1f}" stroke="{col}" '
                   f'stroke-width="{max(3, e.section.width * sc * 0.6):.1f}" stroke-linecap="round" opacity="0.8"/>')
    for n in m.nodes.values():
        out.append(f'<circle cx="{px(n.pos[0]):.1f}" cy="{py(n.pos[1]):.1f}" r="{m.chambers[n.id].apothem * sc * 0.8:.1f}" fill="#222c3a" stroke="#556"/>')
        out.append(f'<text x="{px(n.pos[0]):.1f}" y="{py(n.pos[1]) + 4:.1f}" fill="#aab" font-size="11" text-anchor="middle">{html.escape(n.id)}</text>')
    for g in m.goals.values():
        gx, gy = m.nodes[g.node].pos
        out.append(f'<circle cx="{px(gx):.1f}" cy="{py(gy):.1f}" r="6" fill="{g.color}" stroke="#fff"/>')
    from ..core.mapspec import SewerMap  # noqa: F401
    for d in truth:
        x, y, _ = m.world_point(d["edge"], d["s"], 0, 0)
        out.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="5" fill="none" stroke="#7d7" stroke-width="1.5"/>')
    for d in found:
        x, y, _ = m.world_point(d.edge, d.s, 0, 0)
        col = "#f55" if d.cls == "crack" else "#39f"
        out.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="3.5" fill="{col}"/>')
    out.append("</svg>")
    return "".join(out)


def write_report(base: Path, m, profile, route, found, truth, evaluation, detector_name, mission, log) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    d = base / f"{stamp}_{m.name}_{profile.name}"
    (d / "snapshots").mkdir(parents=True, exist_ok=True)
    rows = []
    for t in sorted(found, key=lambda t: (t.edge, t.s)):
        if t.snapshot:
            (d / "snapshots" / f"defect_{t.id:02d}.jpg").write_bytes(t.snapshot)
        rows.append(t.to_dict())
    (d / "report.json").write_text(json.dumps({
        "map": m.name, "rover": profile.to_dict(), "detector": detector_name, "mission": mission, "route": route,
        "evaluation": evaluation, "defects": rows, "truth": truth, "log": log}, indent=1))
    tr = ""
    for t in sorted(found, key=lambda t: (t.edge, t.s)):
        img = ""
        if t.snapshot:
            img = f'<img src="data:image/jpeg;base64,{base64.b64encode(t.snapshot).decode()}" width="220">'
        tr += (f"<tr><td>{t.id}</td><td>{t.cls}</td><td>{html.escape(t.edge)}</td><td>{t.s:.1f} m</td><td>{t.angle:.0f}&deg;</td>"
               f"<td>{t.size_m[0] * 100:.0f} x {t.size_m[1] * 100:.0f} cm</td><td>{t.conf:.2f}</td><td>{t.hits}</td><td>{img}</td></tr>")
    ev = evaluation or {}
    (d / "report.html").write_text(f"""<!doctype html><meta charset="utf-8"><title>Inspection report - {html.escape(m.name)}</title>
<style>body{{font:14px system-ui;background:#0e1319;color:#dde;margin:24px}}table{{border-collapse:collapse}}td,th{{border:1px solid #334;padding:4px 8px;vertical-align:top}}
th{{background:#1a2230}}h1,h2{{font-weight:600}}.k{{display:inline-block;margin:0 18px 8px 0}}.k b{{display:block;font-size:22px}}</style>
<h1>Inspection report: {html.escape(m.name)}</h1>
<p>Rover <b>{html.escape(profile.name)}</b> ({profile.drive}, {profile.length * 100:.0f}x{profile.width * 100:.0f} cm, max {profile.max_speed} m/s) &middot;
detector <b>{html.escape(detector_name)}</b> &middot; route {' &rarr; '.join(route)}</p>
<div><span class="k"><b>{len(found)}</b>defects found</span><span class="k"><b>{len(truth)}</b>known (simulator truth)</span>
<span class="k"><b>{ev.get('recall', 0):.0%}</b>recall</span><span class="k"><b>{ev.get('precision', 0):.0%}</b>precision</span>
<span class="k"><b>{ev.get('false_positive', 0)}</b>false alarms</span></div>
<h2>Map</h2>{_map_svg(m, route, found, truth)}<p style="color:#99a">filled dots = found defects (red crack, blue leak); green rings = ground truth</p>
<h2>Defects</h2><table><tr><th>#</th><th>type</th><th>pipe</th><th>at</th><th>angle</th><th>size</th><th>conf</th><th>views</th><th>best view</th></tr>{tr}</table>
""", encoding="utf-8")
    return d
