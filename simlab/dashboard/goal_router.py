"""HTTP API of the Goal tab: describe a job, get a checked machine, watch it, stress-test it, get the build sheet.
Include it in a FastAPI app *before* any catch-all `/api/{kind}/...` routes."""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..goals import all_goals, get_goal, match, propose
from ..goals.assembly import make_sheet, render as render_sheet
from ..goals.design import part_names
from ..hazards import load_catalogue, run_sweep, write_report
from ..hazards.synth import synthesize
from ..llm import LLMError, OpenRouter, settings as llm_settings
from ..llm.client import gated_models
from ..llm.planner import goal_for, run_plan
from ..parts import Build, build_to_vehicle, load_inventory

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]

# Parts you can tick in one go. What is listed was reported or measured; anything unknown stays out (the sheet will ask you to buy it).
KITS = {"my_rover": {"label": "My ESP32 rover kit (ZK-2WD chassis, ESP32 DevKit, ESP32-CAM, DRV8833, 4xAA)",
                     "owned": {"esp32_devkit": 1, "esp32cam_gc2145_board": 1, "tt_motor_6v": 2, "wheel_65mm_tt": 2, "drv8833_module_pw": 1,
                               "aa_nimh_2000": 4}}}


class MatchReq(BaseModel):
    text: str = ""


class ProposeReq(BaseModel):
    goal: str
    answers: dict[str, int] = {}
    owned: dict[str, int] | None = None
    kit: str | None = None


class BuildReq(BaseModel):
    goal: str
    build: dict
    answers: dict[str, int] = {}
    owned: dict[str, int] | None = None
    episodes: int = 4
    plan_id: str | None = None   # a plan the AI made and the server validated: its stress plan is used


def _goal(gid: str):
    try:
        return get_goal(gid)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))


def _owned(req_owned, kit=None) -> dict:
    if kit:
        if kit not in KITS:
            raise HTTPException(404, f"unknown kit {kit!r}")
        return dict(KITS[kit]["owned"])
    return dict(req_owned) if req_owned is not None else load_inventory()


@router.get("/api/goal/goals")
def goals():
    return {"goals": [g.to_dict() for g in all_goals().values()], "kits": [{"id": k, "label": v["label"]} for k, v in KITS.items()]}


@router.post("/api/goal/match")
def match_text(req: MatchReq):
    hits = match(req.text)
    goals_ = all_goals()
    return {"matches": [{**h, "card": goals_[h["goal"]].card} for h in hits]}


@router.post("/api/goal/propose")
def propose_machine(req: ProposeReq):
    _goal(req.goal)
    out = propose(req.goal, req.answers, owned=_owned(req.owned, req.kit))
    out["owned_used"] = _owned(req.owned, req.kit)
    return out


@router.post("/api/goal/sheet")
def build_sheet(req: BuildReq):
    plan = PLANS.get(req.plan_id) if req.plan_id else None
    goal = goal_for(plan) if plan is not None else _goal(req.goal)
    b, issues = Build.parse(req.build)
    if b is None:
        raise HTTPException(422, "that is not a valid build: " + "; ".join(f"{i.field}: {i.msg}" for i in issues[:3]))
    sheet = make_sheet({"build": req.build}, goal.to_dict(), _owned(req.owned))
    name = f"{time.strftime('%Y%m%d_%H%M%S')}_sheet_{goal.id}_{uuid.uuid4().hex[:6]}"      # the suffix: two sheets in one second must not share a folder
    out = ROOT / "runs" / "reports" / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "sheet.html").write_text(render_sheet(sheet), encoding="utf-8")
    return {"sheet": sheet, "url": f"/reports/{name}/sheet.html"}


# ---------------------------------------------------------------- stress test as a background job
JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _sweep_thread(job: dict, goal, build: dict, answers: dict, episodes: int, plan: dict | None = None):
    try:
        spec, _ = build_to_vehicle(Build.from_dict(build))
        if spec is None:
            raise ValueError("that build does not make a valid machine")
        ctx = {"max_accel": float(spec.params.get("max_accel", 0.5)), "thrusters": int(build.get("drive", {}).get("count", 1))}

        def progress(done, total, label):
            job.update(done=done, total=total, label=label)
        if plan is not None:
            cat, asked = synthesize(plan, goal.platform)
        else:
            cat, asked = load_catalogue(goal.hazards), goal.params(answers)
        result = run_sweep(spec, cat, ctx, asked, episodes, progress, job["cancel"])
        name = write_report(result, goal.id, goal.title, {"name": build.get("name", ""), "parts": part_names(build)})
        job.update(state="stopped" if result["partial"] else "done", result=result, report=f"/reports/{name}/report.html")
    except Exception as e:                                       # shown on the screen, never lost in a thread
        job.update(state="error", error=f"{type(e).__name__}: {e}")


@router.post("/api/goal/sweep/start")
def sweep_start(req: BuildReq):
    plan = PLANS.get(req.plan_id) if req.plan_id else None
    if req.plan_id and plan is None:
        raise HTTPException(404, "that plan is no longer in memory: make the plan again")
    goal = goal_for(plan) if plan is not None else _goal(req.goal)
    with _LOCK:
        if any(j["state"] == "running" for j in JOBS.values()):
            raise HTTPException(409, "a stress test is already running: wait for it or stop it")
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "state": "running", "done": 0, "total": 0, "label": "starting", "cancel": threading.Event(), "started": time.time()}
        JOBS[jid] = job
        for old in sorted(JOBS, key=lambda k: JOBS[k]["started"])[:-5]:        # keep the last five
            JOBS.pop(old, None)
    threading.Thread(target=_sweep_thread, args=(job, goal, req.build, req.answers, max(1, min(req.episodes, 20)), plan), daemon=True, name="goal-sweep").start()
    return {"id": jid}


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "cancel"}


@router.get("/api/goal/sweep/status")
def sweep_status(id: str):
    job = JOBS.get(id)
    if not job:
        raise HTTPException(404, "no such stress test")
    return _public(job)


@router.post("/api/goal/sweep/stop")
def sweep_stop(id: str):
    job = JOBS.get(id)
    if not job:
        raise HTTPException(404, "no such stress test")
    job["cancel"].set()
    return {"ok": True}


# ---------------------------------------------------------------- the AI planner
def make_client() -> OpenRouter:
    """Tests replace this with a client that talks to a local fake server."""
    return OpenRouter()


class LLMSettingsReq(BaseModel):
    api_key: str | None = None
    model: str | None = None


class PlanReq(BaseModel):
    text: str
    answers: dict[str, str | float | int] = {}
    kit: str | None = None
    owned: dict[str, int] | None = None
    final: bool = False


PLAN_JOBS: dict[str, dict] = {}
PLANS: dict[str, dict] = {}


@router.get("/api/goal/llm/status")
def llm_status():
    out = llm_settings.public()
    out["skipped_models"] = gated_models()                                  # models OpenRouter refused as "approved apps only" (skipped for hours)
    if out["configured"]:
        try:
            out["usage"] = make_client().usage()
        except LLMError as e:
            out["usage_error"] = str(e)
    return out


@router.post("/api/goal/llm/settings")
def llm_save(req: LLMSettingsReq):
    try:
        llm_settings.save(api_key=req.api_key, model=req.model)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return llm_settings.public()


@router.post("/api/goal/llm/test")
def llm_test():
    """One tiny request, to check the key and the model. It counts as one of the day's free requests."""
    t0 = time.time()
    try:
        c = make_client()
        r = c.chat_with_fallback([{"role": "user", "content": "Reply with the single word OK."}], c.choose_models(llm_settings.get_model()), max_tokens=200, reasoning_effort=None, retries=0)
    except LLMError as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "model": r["model"], "seconds": round(time.time() - t0, 1), "reply": r["text"].strip()[:40]}


def _plan_thread(job: dict, req: PlanReq, owned: dict):
    def emit(kind, data):
        if kind == "phase":
            job["phase"] = data
        elif kind == "text":
            job["chars"], job["tail"] = len(data), data[-700:]
    try:
        res = run_plan(req.text, req.answers or None, owned, client=make_client(), cancel=job["cancel"], emit=emit, final=req.final)
        raw = res.pop("raw_plan", None)
        if raw is not None and res.get("state") == "done":
            pid = uuid.uuid4().hex[:10]
            PLANS[pid] = raw
            res["plan_id"] = pid
            for old in list(PLANS)[:-20]:                                     # keep the last twenty
                PLANS.pop(old, None)
        job.update(state=res["state"], result=res, phase="Done")
    except LLMError as e:
        job.update(state="stopped" if e.kind == "cancelled" else "error", error=str(e))
    except Exception as e:                                                    # shown on the screen, never lost in a thread
        job.update(state="error", error=llm_settings.scrub(f"{type(e).__name__}: {e}"))


@router.post("/api/goal/plan/start")
def plan_start(req: PlanReq):
    if not req.text.strip():
        raise HTTPException(422, "describe the job first")
    owned = _owned(req.owned, req.kit)
    with _LOCK:
        if any(j["state"] == "running" for j in PLAN_JOBS.values()):
            raise HTTPException(409, "a plan is already being made: wait for it or stop it")
        jid = uuid.uuid4().hex[:8]
        job = {"id": jid, "state": "running", "phase": "Starting", "chars": 0, "tail": "", "cancel": threading.Event(), "started": time.time()}
        PLAN_JOBS[jid] = job
        for old in sorted(PLAN_JOBS, key=lambda k: PLAN_JOBS[k]["started"])[:-5]:
            PLAN_JOBS.pop(old, None)
    threading.Thread(target=_plan_thread, args=(job, req, owned), daemon=True, name="goal-plan").start()
    return {"id": jid}


@router.get("/api/goal/plan/status")
def plan_status(id: str):
    job = PLAN_JOBS.get(id)
    if not job:
        raise HTTPException(404, "no such plan")
    out = _public(job)
    out["seconds"] = round(time.time() - job["started"], 1)
    return out


@router.post("/api/goal/plan/stop")
def plan_stop(id: str):
    job = PLAN_JOBS.get(id)
    if not job:
        raise HTTPException(404, "no such plan")
    job["cancel"].set()
    return {"ok": True}
