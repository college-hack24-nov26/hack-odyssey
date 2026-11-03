"""HTTP API of the chat page: describe a job, get a draft, test it, watch it. Include it in a FastAPI app *before* any catch-all `/api/{kind}/...` and `/ws/{kind}` routes.

Transport is plain HTTP long-poll (no SSE): `POST /api/chat/{id}/send` answers 202 at once, and `GET /api/chat/{id}/events?after=<seq>&wait=<s>` returns as soon as there is
anything newer than `seq`. The live 3D view keeps using /api/lab/scene, /api/lab/state and /ws/lab. The wire format is written down in docs/CHAT_PROTOCOL.md.
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..chat.chat import MAX_WAIT
from ..chat.orchestrator import Engine
from ..chat.store import Store
from ..llm import settings as llm_settings

router = APIRouter()
STATIC = __import__("pathlib").Path(__file__).parent / "static"
MAX_TEXT = 2000

_engine: Engine | None = None
_lock = threading.Lock()


def engine() -> Engine:
    """The one engine of this server (created on first use, so importing the module costs nothing)."""
    global _engine
    with _lock:
        if _engine is None:
            _engine = Engine()
        return _engine


def set_engine(e: Engine | None) -> None:
    """Tests install an engine with fakes (a temp folder, a fake runner); None goes back to the default on next use."""
    global _engine
    with _lock:
        if _engine is not None and _engine is not e:
            _engine.close()
        _engine = e


class SendReq(BaseModel):
    text: str | None = None
    action: dict | None = None
    client_msg_id: str | None = None
    label: str | None = None             # what a button's own message should say in the transcript


def _chat(cid: str):
    if not Store.valid_id(cid):
        raise HTTPException(404, "no such chat")
    try:
        return engine().get(cid)
    except KeyError:
        raise HTTPException(404, "no such chat")


@router.get("/chat")
def chat_page():
    return FileResponse(STATIC / "chat.html", headers={"Cache-Control": "no-store"})


@router.get("/api/chat/capabilities")
def capabilities():
    return engine().capabilities()


@router.post("/api/chat/new")
def new_chat():
    chat = engine().new_chat()
    return {"id": chat.id, "seq": chat.seq, "title": chat.title}


@router.get("/api/chat/latest")
def latest():
    """The chat to open when the page loads: the most recently updated one (with its events, so the page can draw it at once), or {"id": null}."""
    chat = engine().latest()
    if chat is None:
        return {"id": None}
    first = chat.events_after(0, 0.0)
    return {"id": chat.id, "title": chat.title, "seq": chat.seq, **first}


@router.get("/api/chat/{cid}/events")
def events(cid: str, after: int = Query(0, ge=0), wait: float = Query(0.0, ge=0.0, le=MAX_WAIT)):
    """Long-poll. {"events": [...], "last": N, "more": bool, "reset": bool?}. `more` means the page was cut: ask again at once with after=last. An empty list after `wait`
    seconds means nothing happened; ask again. `reset` means `after` is ahead of this chat (the server restarted without its log): start again from 0."""
    chat = _chat(cid)
    return chat.events_after(after, wait)


@router.post("/api/chat/{cid}/send", status_code=202)
def send(cid: str, req: SendReq):
    chat = _chat(cid)
    text = (req.text or "").strip() if req.text is not None else None
    if not text and req.action is None:
        raise HTTPException(422, "say something or press a button")
    if text and len(text) > MAX_TEXT:
        raise HTTPException(422, f"that message is too long (at most {MAX_TEXT} characters)")
    if req.action is not None and (not isinstance(req.action.get("type"), str) or len(req.action["type"]) > 40):
        raise HTTPException(422, "an action needs a type")
    try:
        return engine().send(chat, text=text or None, action=req.action if not text else None, client_msg_id=(req.client_msg_id or None) and req.client_msg_id[:64],
                             label=(req.label or "")[:120] or None)
    except Exception as e:                                           # never a bare 500 with a traceback: the page shows this sentence
        raise HTTPException(500, llm_settings.scrub(f"{type(e).__name__}: {e}")[:300])


@router.get("/api/chat/{cid}/state")
def state(cid: str):
    return engine().state(_chat(cid))
