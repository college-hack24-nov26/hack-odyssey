"""The project service: every change to a project goes through here, is validated, bumps `updated`, and is saved atomically.

A project holds the job's requirements, the blueprint (the Builder build and its goal), the equipment list with what the user already owns, the sensor
ladder, the named environment, the run records and the stage. Nothing here talks to the hardware or the simulator; it is the record the pages and the
rover loop write to. All of it works offline.
"""
from __future__ import annotations

import json
import time

from ..parts import Build, evaluate
from ..parts.library import all_parts, load_inventory
from . import environment, equipment, sensors, stages
from .store import ProjectStore

RUN_KINDS = ("sim", "real")
LINKS = ("usb", "wifi", "synthetic")                 # how a real run reached the rover; "synthetic" is the labelled simulated rover, not hardware
MAX_NAME = 80
MAX_REQ_TEXT = 2000
MAX_REQ_ITEMS = 30
MAX_RUN_DATA = 20000                                  # characters of JSON a run record may carry
VIEW_RUNS = 20


class ProjectError(ValueError):
    """A plain sentence the page can show."""


def _clean(v, limit: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:limit]


class ProjectService:
    def __init__(self, store: ProjectStore | None = None):
        self.store = store or ProjectStore()

    # ------------------------------------------------------------ create / read
    def create(self, name: str = "", text: str = "", moving: bool = True, owned: dict | None = None) -> dict:
        pid = self.store.new_id()
        now = time.time()
        p = {"id": pid, "name": _clean(name, MAX_NAME) or "Untitled project", "created": now, "updated": now, "moving": bool(moving), "stage": "",
             "requirements": None, "blueprint": None, "owned": self._owned(owned) if owned is not None else load_inventory(), "prices": {}, "currency": "",
             "decisions": {}, "parts_confirmed": False, "sensors": sensors.fresh(), "environment": None, "env_version": 0, "marks": {}}
        if text and text.strip():
            p["requirements"] = self._requirements({"text": text})
        self._save(p)
        return self.view(pid)

    def get(self, pid: str) -> dict:
        """The raw stored project (KeyError when absent). Fields added later are filled in so an old file still loads."""
        p = self.store.load(pid)
        p.setdefault("moving", True)
        p.setdefault("marks", {})
        p.setdefault("decisions", {})
        p.setdefault("prices", {})
        p["sensors"] = sensors.normalise(p.get("sensors"))
        return p

    def list(self) -> list[dict]:
        return self.store.list()

    def delete(self, pid: str) -> None:
        self.store.folder(pid)
        self.store.delete(pid)

    def runs(self, pid: str) -> list[dict]:
        return self.store.load_runs(pid)

    def view(self, pid: str) -> dict:
        """Everything a page needs about the project in one dict."""
        p = self.get(pid)
        runs = self.runs(pid)
        build = (p.get("blueprint") or {}).get("build")
        rs = equipment.rows(build, p["owned"], p["decisions"], p["prices"]) if build else []
        sens = {n: {**v, "in_blueprint": n in sensors.expected(build), "usable_for_safety": v["level"] == "verified"} for n, v in p["sensors"].items()}
        out = {k: p.get(k) for k in ("id", "name", "created", "updated", "moving", "requirements", "blueprint", "owned", "prices", "currency", "decisions",
                                     "parts_confirmed", "environment", "marks")}
        out.update(equipment={"rows": rs, "totals": equipment.totals(rs), "currency": p.get("currency", "")}, sensors=sens, stages=stages.tracker(p, runs),
                   environment_steps=environment.setup_steps(p.get("environment")), runs=runs[-VIEW_RUNS:], run_count=len(runs))
        return out

    # ------------------------------------------------------------ editing
    def _save(self, p: dict) -> None:
        p["updated"] = time.time()
        p["stage"] = stages.tracker(p, self.store.load_runs(p["id"]))["current"] or "done"
        self.store.save(p["id"], p)

    def _edit(self, pid: str, fn) -> dict:
        with self.store._lock:
            p = self.get(pid)
            fn(p)
            self._save(p)
        return self.view(pid)

    def update(self, pid: str, fields: dict) -> dict:
        """Apply the given fields in one save: name, moving, requirements, blueprint, environment, owned, prices, currency, decisions, sensors.
        All fields are checked first, so a bad one changes nothing. A sensor can be set to 'unknown' or 'fitted' here, never to 'verified'."""
        known = {"name", "moving", "requirements", "blueprint", "environment", "owned", "prices", "currency", "decisions", "sensors"}
        extra = sorted(set(fields) - known)
        if extra:
            raise ProjectError(f"unknown field {extra[0]!r}; known: {', '.join(sorted(known))}")

        def apply(p: dict) -> None:
            new: dict = {}                                                         # validate everything first: a bad field must change nothing
            if "name" in fields:
                new["name"] = _clean(fields["name"], MAX_NAME) or p["name"]
            if "moving" in fields:
                if not isinstance(fields["moving"], bool):
                    raise ProjectError("moving must be true or false")
                new["moving"] = fields["moving"]
            if "requirements" in fields:
                new["requirements"] = self._requirements(fields["requirements"])
            if "blueprint" in fields:
                new["blueprint"] = self._blueprint(fields["blueprint"])
            if "environment" in fields:
                new["environment"] = self._environment(fields["environment"])
            if "owned" in fields:
                new["owned"] = self._owned(fields["owned"])
            if "prices" in fields:
                new["prices"] = self._prices(fields["prices"])
            if "currency" in fields:
                new["currency"] = _clean(fields["currency"], 8)
            if "decisions" in fields:
                new["decisions"] = self._decisions(fields["decisions"], (new.get("blueprint") or p.get("blueprint") or {}).get("build"))
            if "sensors" in fields:
                new["sensors"] = self._sensor_updates(fields["sensors"])
            for k in ("name", "moving", "requirements", "currency", "prices"):
                if k in new:
                    p[k] = new[k]
            if "blueprint" in new:
                self._apply_blueprint(p, new["blueprint"])
            if "owned" in new:
                p["owned"], p["parts_confirmed"] = new["owned"], False
            if "decisions" in new:
                p["decisions"], p["parts_confirmed"] = new["decisions"], False
            if "environment" in new:
                p["environment"] = new["environment"]
                if new["environment"] is not None:
                    p["env_version"] += 1
            for name, (level, note) in new.get("sensors", {}).items():
                sensors.set_level(p["sensors"], name, level, note)
        return self._edit(pid, apply)

    # ---- validators (raise ProjectError, never change anything)
    @staticmethod
    def _requirements(raw) -> dict | None:
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = {"text": raw}
        if not isinstance(raw, dict):
            raise ProjectError("requirements must be text or an object with text and items")
        text = _clean(raw.get("text"), MAX_REQ_TEXT)
        items = raw.get("items", [])
        if not isinstance(items, list):
            raise ProjectError("requirement items must be a list")
        items = [_clean(i, 200) for i in items[:MAX_REQ_ITEMS] if _clean(i, 200)]
        return {"text": text, "items": items} if text or items else None

    @staticmethod
    def _blueprint(raw) -> dict:
        if not isinstance(raw, dict) or not isinstance(raw.get("build"), dict):
            raise ProjectError("a blueprint needs a build (the Builder's build as an object) and optionally a goal id")
        b, issues = Build.parse(raw["build"])
        if b is None:
            raise ProjectError("that is not a valid build: " + "; ".join(f"{i.field}: {i.msg}" for i in issues[:3]))
        r = evaluate(b, None, suggest=False)
        errors = sum(f["severity"] == "error" for f in r["findings"])
        check = {"ok": bool(r["ok"]), "errors": errors, "warnings": sum(f["severity"] == "warn" for f in r["findings"]),
                 "headline": [f.get("title") or f.get("id", "") for f in r["findings"] if f["severity"] == "error"][:3]}
        return {"build": b.to_dict(), "goal": _clean(raw.get("goal"), 60), "check": check}

    @staticmethod
    def _apply_blueprint(p: dict, bp: dict) -> None:
        p["blueprint"] = bp
        names = {r["name"] for r in equipment.rows(bp["build"], {}, None, None)}
        p["decisions"] = {k: v for k, v in p["decisions"].items() if k in names}      # a decision about a part that left the build goes with it
        p["parts_confirmed"] = False

    @staticmethod
    def _environment(raw) -> dict | None:
        try:
            return None if raw is None else environment.normalise(raw)
        except environment.EnvError as e:
            raise ProjectError(str(e))

    @staticmethod
    def _owned(raw) -> dict:
        if not isinstance(raw, dict):
            raise ProjectError("owned must be an object {part: count}")
        known, out = all_parts(), {}
        for k, v in raw.items():
            if k not in known:
                raise ProjectError(f"unknown part {k!r} in owned")
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 999:
                raise ProjectError(f"the count of {k} must be a whole number from 0 to 999")
            if v:
                out[str(k)] = v
        return out

    @staticmethod
    def _prices(raw) -> dict:
        if not isinstance(raw, dict):
            raise ProjectError("prices must be an object {part: price}")
        known, out = all_parts(), {}
        for k, v in raw.items():
            if k not in known:
                raise ProjectError(f"unknown part {k!r} in prices")
            if v is None:
                continue                                                            # null clears a price
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1e7:
                raise ProjectError(f"the price of {k} must be a number from 0 up")
            out[str(k)] = float(v)
        return out

    @staticmethod
    def _decisions(raw, build: dict | None) -> dict:
        if not isinstance(raw, dict):
            raise ProjectError("decisions must be an object {part: {state, swap_to}}")
        if not build:
            raise ProjectError("set the blueprint before deciding about its parts")
        out = {}
        for part, d in raw.items():
            if d is None:
                continue                                                            # null removes a decision (back to automatic)
            d = d if isinstance(d, dict) else {"state": d}
            try:
                equipment.check_decision(build, part, d.get("state"), d.get("swap_to"))
            except equipment.EquipmentError as e:
                raise ProjectError(str(e))
            out[part] = {"state": d["state"], **({"swap_to": d["swap_to"]} if d["state"] == "swap" else {})}
        return out

    @staticmethod
    def _sensor_updates(raw) -> dict:
        if not isinstance(raw, dict):
            raise ProjectError("sensors must be an object {sensor: level or {level, note}}")
        out = {}
        for name, v in raw.items():
            v = v if isinstance(v, dict) else {"level": v}
            probe = sensors.fresh()
            try:
                sensors.set_level(probe, name, v.get("level"), v.get("note", ""))            # validates the name and refuses 'verified'
            except sensors.SensorError as e:
                raise ProjectError(str(e))
            out[name] = (v["level"], v.get("note", ""))
        return out

    # ------------------------------------------------------------ the guarded steps
    def confirm_sensor(self, pid: str, name: str, confirm: bool = False, note: str = "") -> dict:
        def apply(p):
            try:
                sensors.confirm(p["sensors"], name, confirm=confirm, note=note)
            except sensors.SensorError as e:
                raise ProjectError(str(e))
        return self._edit(pid, apply)

    def confirm_parts(self, pid: str) -> dict:
        """The user says the equipment list is settled. Needs a blueprint the Builder accepts."""
        def apply(p):
            if not (p.get("blueprint") or {}).get("check", {}).get("ok"):
                raise ProjectError("make a blueprint the Builder accepts before confirming the parts")
            p["parts_confirmed"] = True
        return self._edit(pid, apply)

    def mark_stage(self, pid: str, stage: str, note: str = "") -> dict:
        def apply(p):
            try:
                stages.mark(p, self.store.load_runs(pid), stage, note)
            except stages.StageError as e:
                raise ProjectError(str(e))
        return self._edit(pid, apply)

    def record_run(self, pid: str, kind: str, summary: str = "", ok: bool | None = None, link: str | None = None, data: dict | None = None) -> dict:
        """Append a run record. It stores the environment's name (and version), so a project without a named environment cannot record a run.
        A real run must say how it reached the rover (usb / wifi / synthetic); only a non-synthetic one counts as the test stage."""
        with self.store._lock:
            p = self.get(pid)
            if kind not in RUN_KINDS:
                raise ProjectError(f"kind must be one of {', '.join(RUN_KINDS)}")
            env = p.get("environment")
            if not env:
                raise ProjectError("name the test environment before recording a run: every run is stored with it")
            if kind == "real" and link not in LINKS:
                raise ProjectError(f"a real run must say how it reached the rover: {', '.join(LINKS)}")
            if kind == "sim" and link is not None:
                raise ProjectError("a simulated run has no link")
            if ok is not None and not isinstance(ok, bool):
                raise ProjectError("ok must be true, false or left out")
            data = {} if data is None else data
            try:
                text = json.dumps(data)
            except (TypeError, ValueError):
                raise ProjectError("the run's data must be plain JSON")
            if not isinstance(data, dict) or len(text) > MAX_RUN_DATA:
                raise ProjectError(f"the run's data must be an object of at most {MAX_RUN_DATA} characters")
            run = {"id": self.store.new_id(), "kind": kind, "at": time.time(), "environment": env["name"], "env_version": p["env_version"],
                   "summary": _clean(summary, 300), "ok": ok, "link": link, "data": data}
            self.store.append_run(pid, run)
            self._save(p)
        return run

    # ------------------------------------------------------------ equipment
    def compare(self, pid: str) -> dict:
        p = self.get(pid)
        build = (p.get("blueprint") or {}).get("build")
        if not build:
            raise ProjectError("set the blueprint first")
        return equipment.compare(build, p["owned"], p["prices"])
