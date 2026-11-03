"""How many free AI requests are left today, honestly approximate.

OpenRouter's free models allow about 50 requests a day, shared by every app using the key, and a failed attempt counts too. So two numbers are kept: a local count of
attempts this UTC day (a hook in the OpenRouter client adds one before every request is sent, file runs/chats/_budget.json), and the key's own `remaining` figure
(GET /key, read at most once a minute and then adjusted by the attempts made since). Zero left switches the AI off until 00:00 UTC. Nothing here ever returns or
logs the key; every error string goes through `settings.scrub`.

The AI setting (config/chat.json, {"ai": "auto" | "never"}) is read here too: "auto" is what the person chose, "never" switches the planner off.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from ..llm import client as llm_client
from ..llm import settings as llm_settings
from . import store, words

DAILY_LIMIT = 50
LOW = 10
TTL = 60.0


def setting_path() -> Path:
    return Path(os.environ.get("SIMLAB_CHAT_CONFIG", store.ROOT / "config" / "chat.json"))


def ai_setting() -> str:
    try:
        v = json.loads(setting_path().read_text(encoding="utf-8-sig")).get("ai", "auto")
    except (OSError, ValueError, AttributeError):
        return "auto"
    return v if v in ("auto", "never") else "auto"


def utc_day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


class Budget:
    def __init__(self, root: Path | None = None, client_factory: Callable | None = None, clock: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic):
        self.root = Path(root) if root is not None else store.default_root()
        self.client_factory = client_factory
        self.clock, self.mono = clock, mono
        self._lock = threading.Lock()
        self._cache: tuple[float, int | None, int | None, int, str] | None = None     # (fetched at, key's remaining, key's limit, local attempts then, error)
        self._hook = self.record_attempt

    # ------------------------------------------------------------ the local count
    @property
    def path(self) -> Path:
        return self.root / "_budget.json"

    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def used_today(self) -> int:
        d = self._read()
        return int(d.get("attempts", 0)) if d.get("day") == utc_day(self.clock()) else 0

    def record_attempt(self, model: str = "") -> None:
        """Called before every chat request goes out (see llm.client.ATTEMPT_HOOKS): counts the attempt for today."""
        with self._lock:
            day = utc_day(self.clock())
            d = self._read()
            n = int(d.get("attempts", 0)) + 1 if d.get("day") == day else 1
            store.atomic_write(self.path, json.dumps({"day": day, "attempts": n}))

    def install(self) -> None:
        if self._hook not in llm_client.ATTEMPT_HOOKS:
            llm_client.ATTEMPT_HOOKS.append(self._hook)

    def uninstall(self) -> None:
        if self._hook in llm_client.ATTEMPT_HOOKS:
            llm_client.ATTEMPT_HOOKS.remove(self._hook)

    # ------------------------------------------------------------ the key's own figure
    def _fetch(self, force: bool) -> None:
        now = self.mono()
        if not force and self._cache and now - self._cache[0] < TTL:
            return
        if self.client_factory is None:
            return
        used = self.used_today()
        try:
            u = self.client_factory().usage()
            self._cache = (now, u.get("free_requests_left"), u.get("free_requests_limit"), used, "")
        except Exception as e:                                          # a network or key problem must not break the chat: fall back to the local count
            self._cache = (now, None, None, used, llm_settings.scrub(f"{type(e).__name__}: {e}")[:160])

    def mark_exhausted(self) -> None:
        """The API said today's requests are spent: treat zero as certain until the day changes."""
        self._cache = (self.mono(), 0, None, self.used_today(), "")

    def snapshot(self, force: bool = False) -> dict:
        configured = bool(llm_settings.get_key())
        setting = ai_setting()
        used = self.used_today()
        left, limit, err = None, DAILY_LIMIT, ""
        if configured:
            self._fetch(force)
            if self._cache:
                _, key_left, key_limit, used_then, err = self._cache
                if key_limit:
                    limit = int(key_limit)
                if key_left is not None:
                    left = max(0, int(key_left) - max(0, used - used_then))
            if left is None:
                left = max(0, limit - used)
        enabled = configured and setting != "never" and (left is None or left > 0)
        reason = None
        if not configured:
            reason = "no key saved"
        elif setting == "never":
            reason = "switched off in the chat settings"
        elif left is not None and left <= 0:
            reason = words.RESETS
        return {"configured": configured, "enabled": enabled, "setting": setting, "model": llm_settings.get_model() if configured else None, "used_today": used,
                "left": left, "limit": limit, "approx": True, "low": left is not None and 0 < left < LOW, "exhausted": left is not None and left <= 0,
                "reset": words.RESETS, "reason": reason, "error": err or None}
