"""Projects on disk: runs/projects/<id>/project.json (the project, replaced atomically) and runs.jsonl (one JSON run record per line, appended).

Same pattern as simlab/chat/store.py: a 12-hex id (it can never name a path outside the folder), atomic replace with Windows retries, and a last line
cut short by a crash is dropped on load. A damaged project.json is reported as such, never silently replaced by an empty project.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

from ..chat.store import atomic_write

ROOT = Path(__file__).resolve().parents[2]
ID_RE = re.compile(r"^[0-9a-f]{12}$")


def default_root() -> Path:
    return Path(os.environ.get("SIMLAB_PROJECTS", ROOT / "runs" / "projects"))


class ProjectStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else default_root()
        self._lock = threading.RLock()

    # ------------------------------------------------------------ ids and paths
    @staticmethod
    def valid_id(pid) -> bool:
        return isinstance(pid, str) and bool(ID_RE.match(pid))

    def new_id(self) -> str:
        while True:
            pid = uuid.uuid4().hex[:12]
            if not (self.root / pid).exists():
                return pid

    def folder(self, pid: str) -> Path:
        if not self.valid_id(pid):
            raise KeyError(f"no project {pid!r}")
        return self.root / pid

    # ------------------------------------------------------------ project.json
    def exists(self, pid: str) -> bool:
        return self.valid_id(pid) and (self.root / pid / "project.json").is_file()

    def save(self, pid: str, data: dict) -> None:
        with self._lock:
            atomic_write(self.folder(pid) / "project.json", json.dumps(data, separators=(",", ":"), default=str))

    def load(self, pid: str) -> dict:
        """The project's dict. KeyError when there is no such project; ValueError when its file is damaged."""
        path = self.folder(pid) / "project.json"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            raise KeyError(f"no project {pid!r}")
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError(f"project {pid} is damaged (project.json is not valid JSON)")
        if not isinstance(data, dict):
            raise ValueError(f"project {pid} is damaged (project.json is not an object)")
        return data

    # ------------------------------------------------------------ runs.jsonl
    def append_run(self, pid: str, run: dict) -> None:
        d = self.folder(pid)
        line = json.dumps(run, separators=(",", ":"), default=str) + "\n"
        with self._lock:
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "runs.jsonl", "a", encoding="utf-8") as f:
                f.write(line)

    def load_runs(self, pid: str) -> list[dict]:
        out: list[dict] = []
        try:
            with open(self.folder(pid) / "runs.jsonl", encoding="utf-8") as f:
                for line in f:
                    try:
                        run = json.loads(line)
                    except ValueError:
                        break                                   # a line cut short by a crash ends the log
                    if isinstance(run, dict) and "id" in run and "kind" in run:
                        out.append(run)
        except OSError:
            pass
        return out

    # ------------------------------------------------------------ list / delete
    def list(self) -> list[dict]:
        """[{id, name, stage, updated, moving}] newest first; a damaged project is listed with damaged=True so it can be seen and deleted."""
        out = []
        if self.root.is_dir():
            for d in self.root.iterdir():
                if not (d.is_dir() and self.valid_id(d.name) and (d / "project.json").is_file()):
                    continue
                try:
                    p = json.loads((d / "project.json").read_text(encoding="utf-8"))
                    out.append({"id": d.name, "name": p.get("name", "Untitled project"), "stage": p.get("stage", ""), "updated": p.get("updated", 0),
                                "moving": p.get("moving", True), "damaged": False})
                except (OSError, ValueError, AttributeError):
                    out.append({"id": d.name, "name": "Damaged project", "stage": "", "updated": (d / "project.json").stat().st_mtime, "moving": True, "damaged": True})
        return sorted(out, key=lambda c: c["updated"], reverse=True)

    def delete(self, pid: str) -> None:
        import shutil
        shutil.rmtree(self.folder(pid), ignore_errors=True)
