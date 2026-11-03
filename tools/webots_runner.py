"""Find and launch Webots, run scripted headless-ish tests, and analyse the resulting pose logs.

    python -m tools.webots_runner check --map straight_t --rovers micro_2wd,mid_4wd,tracked_crawler,large_6wd
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from .gen_world import ROOT, generate

WEBOTS_EXE_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Webots" / "msys64" / "mingw64" / "bin" / "webots.exe",
    Path("C:/Program Files/Webots/msys64/mingw64/bin/webots.exe"),
]


def find_webots() -> Path:
    env = os.environ.get("WEBOTS_EXE")
    for p in ([Path(env)] if env else []) + WEBOTS_EXE_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError("Webots R2025a not found; install it or set WEBOTS_EXE")


def kill_webots():
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/IM", "webots-bin.exe", "/IM", "webots.exe"],
                       capture_output=True, check=False)


def launch(world: Path, env_extra: dict | None = None, mode: str = "realtime", minimize: bool = False) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(env_extra or {})
    args = [str(find_webots()), f"--mode={mode}"]
    if minimize:
        args.append("--minimize")
    args.append(str(world))
    return subprocess.Popen(args, env=env)


def run_test(world: Path, steps: list[dict], out: Path, timeout: float = 120.0, mode: str = "realtime",
             minimize: bool = False, **extra) -> Path:
    """Run the scripted drive inside Webots (the controller quits the simulation when done)."""
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*"):
        f.unlink()
    spec = out.parent / f"{out.name}_test.json"
    spec.write_text(json.dumps({"steps": steps, "out": str(out).replace("\\", "/"), **extra}))
    kill_webots()
    p = launch(world, {"SEWERBOT_TEST": str(spec)}, mode, minimize)
    t0 = time.time()
    while p.poll() is None and time.time() - t0 < timeout:
        time.sleep(0.5)
    if p.poll() is None:
        kill_webots()
        raise TimeoutError(f"Webots did not finish within {timeout}s")
    return out / "pose.csv"


def load_log(path: Path) -> dict[str, list[float]]:
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return {k: [float(r[k]) for r in rows] for k in rows[0]}


def unwrap(angles: list[float]) -> list[float]:
    out, off, prev = [], 0.0, angles[0]
    for a in angles:
        d = a - prev
        if d > math.pi:
            off -= 2 * math.pi
        elif d < -math.pi:
            off += 2 * math.pi
        out.append(a + off)
        prev = a
    return out


def check_rover(map_path: Path, rover: str, vmax_scale: float = 0.6) -> dict:
    wbt = generate(map_path, rover)
    meta = json.loads(wbt.with_suffix(".json").read_text())
    prof = meta["profile"]
    v = round(prof["max_speed"] * vmax_scale, 3)
    w = round(min(prof["max_yaw_rate"] * 0.6, 0.9), 3)
    steps = [{"v": v, "w": 0.0, "t": 6.0}, {"v": 0.0, "w": w, "t": 3.0}, {"v": 0.0, "w": 0.0, "t": 0.5}]
    log = load_log(run_test(wbt, steps, ROOT / "runs" / f"chk_{rover}", timeout=100))
    t = log["t"]
    i6 = min(range(len(t)), key=lambda i: abs(t[i] - 6.5))
    yaw = unwrap(log["yaw"])
    res = {"rover": rover, "drive": prof["drive"], "cmd_v": v, "cmd_w": w,
           "dist_6s": round(math.hypot(log["x"][i6] - log["x"][0], log["y"][i6] - log["y"][0]), 3),
           "expected_dist": round(v * (6 - 0.5 * v / prof["max_accel"]), 3),
           "lateral_drift": round(max(abs(y - log["y"][0]) for y in log["y"][:i6]), 3),
           "yaw_turn_deg": round(math.degrees(yaw[-1] - yaw[i6]), 1),
           "expected_turn_deg": round(math.degrees(w * 3.0), 1),
           "z_range": (round(min(log["z"][20:]), 4), round(max(log["z"][20:]), 4))}
    return res


def calibrate_yaw(map_path: Path, rover: str, w_cmd: float = 0.5, duration: float = 7.0) -> dict:
    """Spin in place in the start chamber (room to turn) and measure the steady-state yaw rate."""
    wbt = generate(map_path, rover, yaw_gain=1.0)
    log = load_log(run_test(wbt, [{"v": 0.0, "w": w_cmd, "t": duration}], ROOT / "runs" / f"cal_{rover}", timeout=100))
    t, yaw = log["t"], unwrap(log["yaw"])
    i0 = min(range(len(t)), key=lambda i: abs(t[i] - 2.5))
    rate = (yaw[-1] - yaw[i0]) / (t[-1] - t[i0])
    return {"rover": rover, "w_cmd": w_cmd, "measured_rate": round(rate, 4), "yaw_gain": round(rate / w_cmd, 3)}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--map", default="straight_t")
    c.add_argument("--rovers", default="micro_2wd,mid_4wd,tracked_crawler,large_6wd")
    k = sub.add_parser("calibrate")
    k.add_argument("--map", default="straight_t")
    k.add_argument("--rovers", default="micro_2wd,mid_4wd,tracked_crawler,large_6wd")
    a = ap.parse_args()
    if a.cmd == "calibrate":
        for r in a.rovers.split(","):
            print(json.dumps(calibrate_yaw(ROOT / "maps" / f"{a.map}.yaml", r)), flush=True)
    if a.cmd == "check":
        mp = ROOT / "maps" / f"{a.map}.yaml"
        for r in a.rovers.split(","):
            try:
                print(json.dumps(check_rover(mp, r)), flush=True)
            except Exception as e:  # keep going so one broken rover doesn't hide the others
                print(json.dumps({"rover": r, "error": f"{type(e).__name__}: {e}"}), flush=True)


if __name__ == "__main__":
    main()
