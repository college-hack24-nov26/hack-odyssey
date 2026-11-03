"""Does this build meet what a task asks of the hardware? (T01-T06, M04, pipe fit)

Each requirement becomes a line of the Builder's checklist ("ok", "warn", "fail", "info") and, when it is not met, a finding
with a fix hint the recommender can turn into part suggestions.
"""
from __future__ import annotations

from ..tasks.base import Requirement
from .build import Build
from .derive.common import Derived
from .derive.ground import CAPACITY_DERATE
from .rules import Finding

SENSOR_PART_KIND = {"camera": "sensor_camera", "rangefinder": "sensor_range", "imu": "sensor_imu", "pose": "sensor_gps"}
SENSOR_NAME = {"camera": "a camera", "rangefinder": "a range sensor", "imu": "an IMU", "pose": "a position source (GPS)"}


def check(build: Build, dv: Derived, reqs: list[Requirement]) -> tuple[list[dict], list[Finding]]:
    res, nums = build.resolve(), dv.numbers
    items: list[dict] = []
    finds: list[Finding] = []

    def item(req: Requirement, status: str, detail: str = "") -> None:
        items.append({"kind": req.kind, "title": req.title, "status": status, "detail": detail})

    def find(*a, **kw) -> None:
        finds.append(Finding(*a, scope="task", **kw))

    for r in reqs:
        p = r.params
        if r.kind == "sensor":
            kind = SENSOR_PART_KIND[p["type"]]
            have = [part for _, part in res.sensors if part.kind == kind]
            constraints = {}
            if p["type"] == "camera":
                constraints = {"fps_max": [p.get("min_fps"), None], "width_px": [p.get("min_width"), None]}
            if have:
                bad = []
                if p["type"] == "camera":
                    best = max(have, key=lambda x: x.params["fps_max"] * x.params["width_px"])
                    if best.params["fps_max"] < p.get("min_fps", 0):
                        bad.append(f"{best.params['fps_max']:g} fps (needs {p['min_fps']:g})")
                    if best.params["width_px"] < p.get("min_width", 0):
                        bad.append(f"{best.params['width_px']} px wide (needs {p['min_width']})")
                if bad:
                    item(r, "warn", "your camera gives " + ", ".join(bad))
                    find("T02", "warn", "The camera is below what this task needs", "Your camera gives " + ", ".join(bad) + ".",
                         {"action": "add_part", "kind": kind, "constraints": constraints, "why": "a faster or higher-resolution camera"})
                else:
                    item(r, "ok", "your machine has one")
                continue
            sev = "info" if p.get("optional") else ("warn" if p.get("ideal") else "error")
            lead = "The simulator gives the task an ideal one so the run works, but real hardware needs it. " if p.get("ideal") else ""
            item(r, {"info": "info", "warn": "warn", "error": "fail"}[sev], lead + "your machine has none")
            find("T01", sev, f"No {SENSOR_NAME[p['type']].split(' ', 1)[1]} for this task", lead + f"The task needs {r.title}; your build has none.",
                 {"action": "add_part", "kind": kind, "constraints": constraints, "why": f"add {SENSOR_NAME[p['type']]}"})
        elif r.kind == "light":
            if res.lamp:
                item(r, "ok", "your machine has a lamp")
            else:
                item(r, "warn", "no lamp")
                find("T03", "warn", "No lamp for a dark environment", "The detector needs to see: add a headlamp (and tilt the camera up a little).",
                     {"action": "add_part", "kind": "lamp", "constraints": {}, "why": "a headlamp"})
        elif r.kind == "endurance":
            need_min = p["seconds"] / 60.0
            run_min = nums["runtime_h"] * 60.0
            if run_min >= need_min:
                item(r, "ok", f"runs about {run_min:.0f} min, needs {need_min:.1f} min")
            else:
                derate = build.calibration.get("battery_derate", CAPACITY_DERATE)
                need_mah = p["seconds"] / 3600.0 * nums["i_cruise"] / derate * 1000.0
                have_mah = nums["pack"].q_mah
                item(r, "warn", f"runs about {run_min:.1f} min, needs {need_min:.1f} min")
                find("T05", "warn", "The battery will not last the whole task",
                     f"About {run_min:.1f} min of cruise against {need_min:.1f} min needed: add about {max(need_mah - have_mah, 0):.0f} mAh "
                     f"({need_mah:.0f} mAh in all).",
                     {"action": "change_part", "kind": "battery", "constraints": {"capacity_mah": [need_mah / build.power["parallel"], None]}, "why": f"cells of about {need_mah / build.power['parallel']:.0f} mAh"})
        elif r.kind == "speed":
            v = dv.params.get("max_speed", nums.get("v_top", 0.0))
            if v >= p["min_mps"]:
                item(r, "ok", f"top speed {v:.2f} m/s")
            else:
                item(r, "warn", f"top speed {v:.2f} m/s")
                find("T06", "warn", "Too slow for this task", f"Needs at least {p['min_mps']:g} m/s; the build reaches {v:.2f} m/s.")
        elif r.kind == "slope":
            if nums["climb_deg"] >= p["deg"]:
                item(r, "ok", f"climbs {nums['climb_deg']:.0f} degrees")
            else:
                item(r, "warn", f"climbs {nums['climb_deg']:.0f} degrees")
                find("M04", "warn", "Cannot climb the slopes of this task", f"Needs {p['deg']:g} degrees; the build manages {nums['climb_deg']:.0f}.")
        elif r.kind == "fit":
            bad = [e for e, f in p["fits"].items() if not f["fits"]]
            no_turn = [e for e, f in p["fits"].items() if f["fits"] and not f["can_turn"]]
            if not p["start_ok"]:
                why = next(iter(f["reason"] for e, f in p["fits"].items() if e in p["start_edges"] and not f["fits"]), "")
                item(r, "fail", "does not fit any pipe leaving the start chamber")
                find("T04", "error", "The vehicle does not fit the pipes", f"It cannot enter any pipe leaving the start. {why}",
                     {"action": "change_value", "kind": "chassis", "constraints": {}, "why": "a smaller chassis (width and height)"})
            elif bad:
                item(r, "warn", f"does not fit {len(bad)} of {len(p['fits'])} pipes: {', '.join(bad)}")
                find("T04", "warn", "Some pipes are too small", f"Pipes {', '.join(bad)} are too small: an inspect-everything mission would skip them.")
            else:
                item(r, "ok", "fits every pipe" + (f" (cannot turn in {', '.join(no_turn)})" if no_turn else ""))
        else:
            item(r, "info", "not checked")
    return items, finds
