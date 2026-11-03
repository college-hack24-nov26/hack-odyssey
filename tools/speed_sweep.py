"""How fast can a rover go before it starts missing defects?  Drives the same mission at several speeds through the
running dashboard (python -m sewerbot) and scores each run against the planted defects.

    python -m tools.speed_sweep --map straight_t --rover mid_4wd --goal GOAL_EAST --speeds 0.15,0.3,0.5,0.8,1.2
Motion blur (camera exposure) is enabled so the effect of speed on image quality is part of the result.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request


def call(base, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def run_one(base, map_name, rover, goal, speed, blur_ms, detector, timeout=900, seed=5):
    call(base, "/api/sim/start", {
        "map": map_name, "rover": rover, "detector": detector, "mode": "fast",
        "overrides": {"max_speed": speed, "cam_motion_blur_ms": blur_ms},
        "mission": {"mode": "goto", "goal": goal}, "defect_seed": seed, "defect_count": 14})
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(3)
        st = call(base, "/api/sim/state")
        if st["mission_state"] == "finished" and st.get("evaluation"):
            call(base, "/api/sim/stop", {})
            ev = st["evaluation"]
            return {"speed": speed, "wall_s": round(time.time() - t0), "recall": ev["recall"], "precision": ev["precision"],
                    "found": ev["true_positive"], "known": ev["true_positive"] + ev["missed"], "false_alarms": ev["false_positive"]}
        if st["mission_state"].startswith("blocked"):
            call(base, "/api/sim/stop", {})
            return {"speed": speed, "error": st["mission_state"]}
    call(base, "/api/sim/stop", {})
    return {"speed": speed, "error": "timeout"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--map", default="straight_t")
    ap.add_argument("--rover", default="mid_4wd")
    ap.add_argument("--goal", default="GOAL_EAST")
    ap.add_argument("--speeds", default="0.15,0.3,0.5,0.8,1.2")
    ap.add_argument("--blur-ms", type=float, default=60.0)
    ap.add_argument("--detector", default=None)
    a = ap.parse_args()
    det = a.detector or call(a.base, "/api/catalog")["default_detector"]
    print(f"detector {det}, motion blur {a.blur_ms} ms", flush=True)
    for s in (float(x) for x in a.speeds.split(",")):
        print(json.dumps(run_one(a.base, a.map, a.rover, a.goal, s, a.blur_ms, det)), flush=True)


if __name__ == "__main__":
    main()
