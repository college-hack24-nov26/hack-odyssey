"""Plug-in registries: vehicle kinds, tasks, backends and policies are added by registering an object under a name.

User plug-ins are ordinary modules listed in the SIMLAB_PLUGINS environment variable (comma separated); they are
imported by load_plugins() and register themselves at import time.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, what: str):
        self.what = what
        self._items: dict[str, T] = {}
        self._lazy: dict[str, str] = {}

    def add(self, obj: T, name: str | None = None) -> T:
        key = name or obj.name  # type: ignore[attr-defined]
        if key in self._items and self._items[key] is not obj:
            raise ValueError(f"{self.what} {key!r} is already registered")
        self._items[key] = obj
        return obj

    def add_lazy(self, name: str, target: str) -> None:
        """Register 'package.module:Attr' without importing it until first use (heavy optional dependencies)."""
        self._lazy[name] = target

    def get(self, name: str) -> T:
        if name not in self._items and name not in self._lazy:
            load_plugins()                           # the name may come from a plug-in that nothing has needed yet
        if name not in self._items and name in self._lazy:
            module, _, attr = self._lazy[name].partition(":")
            self._items[name] = getattr(importlib.import_module(module), attr)
        try:
            return self._items[name]
        except KeyError:
            raise KeyError(f"unknown {self.what} {name!r}; available: {self.names()}") from None

    def names(self) -> list[str]:
        return sorted(set(self._items) | set(self._lazy))

    def __contains__(self, name: object) -> bool:
        if name not in self._items and name not in self._lazy:
            load_plugins()
        return name in self._items or name in self._lazy


KINDS: Registry = Registry("vehicle kind")
TASKS: Registry = Registry("task")
BACKENDS: Registry = Registry("backend")
POLICIES: Registry = Registry("policy")


PLUGIN_ERRORS: dict[str, str] = {}
_loaded: set[str] = set()


def plugin_names() -> list[str]:
    """Modules to load: SIMLAB_PLUGINS (comma separated) plus the lines of config/plugins.txt ('#' starts a comment)."""
    names = [m.strip() for m in os.environ.get("SIMLAB_PLUGINS", "").split(",")]
    cfg = Path(__file__).resolve().parents[1] / "config" / "plugins.txt"
    if cfg.is_file():
        names += [ln.split("#")[0].strip() for ln in cfg.read_text(encoding="utf-8-sig").splitlines()]
    return [n for n in dict.fromkeys(names) if n]


def load_plugins() -> list[str]:
    """Import every plug-in module once. A plug-in that fails to import is recorded in PLUGIN_ERRORS and skipped:
    one broken extension must never take the simulator down with it."""
    for mod in plugin_names():
        if mod in _loaded:
            continue
        _loaded.add(mod)
        try:
            importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            PLUGIN_ERRORS[mod] = f"{type(e).__name__}: {e}"
    return sorted(_loaded - set(PLUGIN_ERRORS))
