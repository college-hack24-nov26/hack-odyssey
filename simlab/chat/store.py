"""Chats on disk: runs/chats/<id>/events.jsonl (one JSON event per line, appended as it happens) and session.json (the engine's state, replaced atomically).

A reload restores a chat by replaying its events (the page's cards) and loading session.json (the engine's state: the plan, the test list, the results). The id is 12 hex
characters, so it can never name a path outside the folder. Windows can hold a file for a moment (a scanner, an editor), so the replace is retried.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ID_RE = re.compile(r"^[0-9a-f]{12}$")


def default_root() -> Path:
    return Path(os.environ.get("SIMLAB_CHATS", ROOT / "runs" / "chats"))


def atomic_write(path: Path, text: str, retries: int = 8) -> None:
    """Write `text` to a temp file next to `path`, then replace `path` with it. A crash in between leaves the old file (and a stray temp file, ignored)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex[:6]}")
    tmp.write_text(text, encoding="utf-8")
    try:
        for i in range(retries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:                       # Windows: another process has the file open for a moment
                if i == retries - 1:
                    raise
                time.sleep(0.02 * (i + 1))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else default_root()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ ids and paths
    @staticmethod
    def valid_id(cid: str) -> bool:
        return isinstance(cid, str) and bool(ID_RE.match(cid))

    def new_id(self) -> str:
        while True:
            cid = uuid.uuid4().hex[:12]
            if not (self.root / cid).exists():
                return cid

    def folder(self, cid: str) -> Path:
        if not self.valid_id(cid):
            raise KeyError(f"no chat {cid!r}")
        return self.root / cid

    # ------------------------------------------------------------ write
    def append_event(self, cid: str, ev: dict) -> None:
        d = self.folder(cid)
        line = json.dumps(ev, separators=(",", ":"), default=str) + "\n"
        with self._lock:
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "events.jsonl", "a", encoding="utf-8") as f:
                f.write(line)

    def save_session(self, cid: str, session: dict) -> None:
        atomic_write(self.folder(cid) / "session.json", json.dumps(session, separators=(",", ":"), default=str))

    # ------------------------------------------------------------ read
    def exists(self, cid: str) -> bool:
        return self.valid_id(cid) and (self.root / cid / "events.jsonl").is_file()

    def load(self, cid: str) -> tuple[list[dict], dict | None]:
        """(events, session). A last line cut short by a crash is dropped; a missing or unreadable session.json is None (the chat still replays)."""
        d = self.folder(cid)
        events: list[dict] = []
        try:
            with open(d / "events.jsonl", encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        break
                    if isinstance(ev, dict) and "seq" in ev and "type" in ev:
                        events.append(ev)
        except OSError:
            raise KeyError(f"no chat {cid!r}")
        try:
            session = json.loads((d / "session.json").read_text(encoding="utf-8"))
            session = session if isinstance(session, dict) else None
        except (OSError, ValueError):
            session = None
        return events, session

    def list(self) -> list[dict]:
        """[{id, title, updated, seq}] newest first."""
        out = []
        if self.root.is_dir():
            for d in self.root.iterdir():
                if not (d.is_dir() and self.valid_id(d.name) and (d / "events.jsonl").is_file()):
                    continue
                try:
                    s = json.loads((d / "session.json").read_text(encoding="utf-8"))
                    out.append({"id": d.name, "title": s.get("title", "New chat"), "updated": s.get("updated", 0), "seq": s.get("seq", 0)})
                except (OSError, ValueError):
                    out.append({"id": d.name, "title": "New chat", "updated": (d / "events.jsonl").stat().st_mtime, "seq": 0})
        return sorted(out, key=lambda c: c["updated"], reverse=True)

    def latest(self) -> str | None:
        items = self.list()
        return items[0]["id"] if items else None

    def delete(self, cid: str) -> None:
        import shutil
        shutil.rmtree(self.folder(cid), ignore_errors=True)
