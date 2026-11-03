"""OpenRouter settings. The API key lives only in a private, git-ignored file (config/llm.json) or in the OPENROUTER_API_KEY environment
variable. It is read inside the server and used for requests; no function here returns it to a caller that could show it: `public()` says only
whether one is configured. (Never print, log or echo the value of `get_key()`.)"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Checked against the live API on 2026-11-01: thinkingmachines/inkling(-small):free answer a plain API client with 403 "only available on agentic
# harnesses" (OpenRouter gates them to approved coding-agent apps), so the default is the largest free model that does serve a plain client.
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
# Tried in order when the chosen model is busy, rate-limited, gated or gone. All are free variants; the live /models list decides what exists.
FALLBACKS = ("nvidia/nemotron-3.5-super-vl:free", "google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free")


def path() -> Path:
    return Path(os.environ.get("SIMLAB_LLM", Path(__file__).resolve().parents[2] / "config" / "llm.json"))


def _read() -> dict:
    try:
        d = json.loads(path().read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def get_key() -> str | None:
    """The key to use: the file wins over the environment, so a key pasted in the dashboard is the one in effect."""
    k = (_read().get("api_key") or os.environ.get("OPENROUTER_API_KEY") or "").strip()
    return k or None


def get_model() -> str:
    return str(_read().get("model") or DEFAULT_MODEL)


def base_url() -> str:
    return str(os.environ.get("OPENROUTER_BASE_URL") or _read().get("base_url") or "https://openrouter.ai/api/v1").rstrip("/")


def check_key(key: str) -> str:
    """Normalise a pasted key or raise ValueError with a message for the user."""
    k = (key or "").strip().strip("\"'")
    if not k or any(c.isspace() for c in k):
        raise ValueError("the key is empty or contains spaces: paste only the key")
    if len(k) < 20:
        raise ValueError("that is too short to be an OpenRouter key (they look like sk-or-v1-...)")
    return k


def save(api_key: str | None = None, model: str | None = None) -> None:
    """Store the key and/or the model choice. An empty `api_key` ('') removes the saved key; None leaves it alone."""
    d = _read()
    if api_key is not None:
        if api_key == "":
            d.pop("api_key", None)
        else:
            d["api_key"] = check_key(api_key)
    if model is not None:
        if model == "":
            d.pop("model", None)
        else:
            m = model.strip()
            if not m or len(m) > 120 or any(c.isspace() for c in m):
                raise ValueError("the model name looks wrong (for example nvidia/nemotron-3-ultra-550b-a55b:free)")
            d["model"] = m
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1), encoding="utf-8")
    try:
        p.chmod(0o600)                                  # owner only where the platform supports it
    except OSError:
        pass


def public() -> dict:
    """What the browser may know: whether a key exists, where it came from, which model. Never the key."""
    f = _read().get("api_key")
    src = "file" if f else ("environment" if os.environ.get("OPENROUTER_API_KEY") else None)
    return {"configured": src is not None, "source": src, "model": get_model(), "default_model": DEFAULT_MODEL,
            "note": "Free models can keep what you send. Only your job text, your answers and the public parts list are sent."}


def scrub(text: str) -> str:
    """Remove the key from any text before it is shown or logged."""
    k = get_key()
    return text.replace(k, "***") if k and text else text
