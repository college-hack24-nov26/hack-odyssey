"""Runs training in a separate process and reads its progress back from the run directory (no shared memory).

A training run is a subprocess (`python -m simlab.train`) so the dashboard stays responsive, a crash cannot take it down,
and the run keeps going if the page is closed. State lives entirely in runs/train/<id>/ (see simlab.train.rl).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs" / "train"
RUN_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z_.-]*")       # starts alphanumeric: never "." or ".."
STALE_AFTER = 45.0           # s without a progress update, with no known process: treat a "running" run as dead


def _read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


class JobManager:
    """One manager per kind of job: reinforcement-learning runs (default) or detector pipelines."""

    def __init__(self, runs_dir: Path = RUNS, module: str = "simlab.train", args: tuple = (), model_file: str = "model_best.zip",
                 tag: str | None = None):
        self.dir = Path(runs_dir)
        self.module, self.args, self.model_file, self.tag = module, tuple(args), model_file, tag
        self.procs: dict[str, subprocess.Popen] = {}

    # ---------------------------------------------------------------- lifecycle
    def start(self, cfg: dict) -> str:
        """cfg must carry fully resolved 'vehicle' and 'task' dicts so the run is self-contained."""
        algo = self.tag or cfg.get("algo", "ppo")
        run_id = time.strftime("%Y%m%d_%H%M%S") + f"_{cfg['vehicle']['name']}_{cfg['task']['name']}_{algo}"
        out = self.dir / run_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "request.json").write_text(json.dumps(cfg), encoding="utf-8")
        (out / "progress.json").write_text(json.dumps({"status": "starting", "steps": 0, "total_steps": cfg.get("total_steps", 0),
                                                       "vehicle": cfg["vehicle"]["name"], "task": cfg["task"]["name"],
                                                       "algo": algo, "kind": cfg["vehicle"]["kind"],
                                                       "started": time.time()}), encoding="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        with open(out / "train.log", "w", encoding="utf-8") as log:
            self.procs[run_id] = subprocess.Popen(
                [sys.executable, "-m", self.module, *self.args, "--config", str(out / "request.json"), "--out", str(out)],
                cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        return run_id

    def _path(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id or ""):
            raise KeyError(f"bad run id {run_id!r}")
        p = self.dir / run_id
        if not p.is_dir() or p.resolve().parent != self.dir.resolve():       # also refuses links that point elsewhere
            raise KeyError(f"no such run {run_id!r}")
        return p

    def stop(self, run_id: str) -> None:
        p = self._path(run_id)
        (p / "stop.flag").write_text("stop", encoding="utf-8")
        proc = self.procs.get(run_id)
        if proc and proc.poll() is None:                      # give it a moment to finish cleanly, then insist
            try:
                proc.wait(timeout=25)
            except subprocess.TimeoutExpired:
                proc.terminate()

    def delete(self, run_id: str) -> None:
        p = self._path(run_id)
        if self.status(run_id) in ("starting", "running", "evaluating"):
            raise ValueError("stop the run before deleting it")
        proc = self.procs.get(run_id)
        if proc is not None and proc.poll() is None:              # "done" is written just before the process exits
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                raise ValueError("the run is still shutting down; try again in a moment") from None
        for attempt in range(5):                                  # Windows keeps a file locked briefly after its owner exits
            try:
                shutil.rmtree(p)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.4)
        self.procs.pop(run_id, None)

    def stop_all(self) -> None:
        for run_id, proc in list(self.procs.items()):
            if proc.poll() is None:
                (self.dir / run_id / "stop.flag").write_text("stop", encoding="utf-8")
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.terminate()

    # ---------------------------------------------------------------- state
    def status(self, run_id: str) -> str:
        p = self._path(run_id)
        prog = _read(p / "progress.json", {})
        st = prog.get("status", "unknown")
        proc = self.procs.get(run_id)
        if st in ("starting", "running", "evaluating"):
            if proc is not None and proc.poll() is not None:
                return "crashed"
            if proc is None:
                try:
                    stale = time.time() - (p / "progress.json").stat().st_mtime > STALE_AFTER
                except OSError:
                    stale = True
                if stale:
                    return "crashed"
        return st

    def summary(self, run_id: str) -> dict:
        p = self._path(run_id)
        prog = _read(p / "progress.json", {})
        final = _read(p / "final_eval.json")
        return {"id": run_id, **{k: prog.get(k) for k in ("vehicle", "task", "algo", "kind", "steps", "total_steps", "fps", "best_eval",
                                                          "last_eval", "train_success", "started", "finished", "error", "stage", "classes")},
                "status": self.status(run_id), "has_model": (p / self.model_file).exists(),
                "final": {k: {"success_rate": v["success_rate"], "success_ci95": v["success_ci95"]} for k, v in final.items()} if final else None}

    def list(self) -> list[dict]:
        if not self.dir.exists():
            return []
        return [self.summary(d.name) for d in sorted(self.dir.iterdir(), reverse=True) if d.is_dir() and RUN_ID.fullmatch(d.name)]

    def details(self, run_id: str) -> dict:
        p = self._path(run_id)
        rows = []
        try:
            for line in (p / "metrics.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass                                      # a half-written last line
        except OSError:
            pass
        cfg = _read(p / "config.json", {})
        log = ""
        try:
            log = "\n".join((p / "train.log").read_text(encoding="utf-8", errors="replace").splitlines()[-25:])
        except OSError:
            pass
        slim = {k: v for k, v in cfg.items() if k not in ("vehicle", "task")}
        model = p / self.model_file
        return {**self.summary(run_id), "config": slim, "vehicle_spec": cfg.get("vehicle"), "task_spec": cfg.get("task"),
                "train": [r for r in rows if r.get("kind") == "train"], "eval": [r for r in rows if r.get("kind") == "eval"],
                "baseline": _read(p / "baseline.json"), "final_eval": _read(p / "final_eval.json"), "log": log,
                "model": str(model.parent if model.name == "detector.json" else model) if model.exists() else None,
                "detector_eval": _read(p / "eval.json"), "dataset": (_read(p / "progress.json", {}) or {}).get("dataset"),
                "previews": [n for n in ("dataset_preview.jpg", "eval_preview.jpg") if (p / n).exists()]}

    def models(self) -> list[dict]:
        out = []
        for s in self.list():
            if s["has_model"]:
                path = self.dir / s["id"] / self.model_file
                ev = _read(self.dir / s["id"] / "eval.json") or {}
                out.append({"id": s["id"], "vehicle": s["vehicle"], "task": s["task"], "kind": s["kind"], "algo": s["algo"],
                            "best_eval": s["best_eval"], "classes": s["classes"], "mean_f1": ev.get("mean_f1"),
                            "path": str(path.parent if path.name == "detector.json" else path)})
        return out

    def file(self, run_id: str, name: str) -> Path:
        """A previewable artefact of a run (whitelisted names only)."""
        if name not in ("dataset_preview.jpg", "eval_preview.jpg"):
            raise KeyError(name)
        p = self._path(run_id) / name
        if not p.exists():
            raise KeyError(name)
        return p
