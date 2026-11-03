"""A small OpenRouter client: model list, free-usage counter, and streamed chat completions with retries, a fallback chain and cancellation.

Built for the free tier: 20 requests a minute and 50 a day (shared by every free model, failed attempts count), a slow model (about 18 tokens a
second), and free endpoints that come and go. So: every call can stream partial text to the caller, can be cancelled between chunks, never retries
a spent daily quota, and every error message is scrubbed of the key.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

import requests

from . import settings

CACHE_S = 600.0
GATED_S = 6 * 3600.0
GATED: dict[str, float] = {}             # model -> when OpenRouter last refused it as "agentic harnesses only"; shared by every client in the process
ATTEMPT_HOOKS: list[Callable[[str], None]] = []      # called with the model name before every chat request is sent: a failed attempt spends quota too, so counters hook in here


class LLMError(RuntimeError):
    """kind: no_key | auth | credits | daily_limit | rate_limit | unavailable | model_unavailable | network | bad_response | cancelled
    `gated` is set when the model exists but OpenRouter only serves it to approved apps (so it is skipped, not retried)."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None, gated: bool = False):
        super().__init__(settings.scrub(message))
        self.kind, self.retry_after, self.gated = kind, retry_after, gated


def is_gated(model: str) -> bool:
    return time.time() - GATED.get(model, -GATED_S) < GATED_S


def gated_models() -> list[str]:
    return sorted(m for m in GATED if is_gated(m))


class OpenRouter:
    def __init__(self, key: str | None = None, base_url: str | None = None, sleep: Callable[[float], None] = time.sleep, session=None):
        self._key = key
        self.base = (base_url or settings.base_url()).rstrip("/")
        self._sleep = sleep
        self._http = session or requests
        self._models: tuple[float, list[dict]] | None = None

    # ------------------------------------------------------------------ plumbing
    @property
    def key(self) -> str:
        k = self._key or settings.get_key()
        if not k:
            raise LLMError("no_key", "no OpenRouter key is set: paste one in the settings box on the Goal tab")
        return k

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:8000", "X-Title": "Sewerbot simlab"}

    def _fail(self, r) -> LLMError:
        """Turn an HTTP error response into an LLMError of the right kind."""
        try:
            e = r.json().get("error", {})
        except ValueError:
            e = {}
        msg = str(e.get("message") or r.text[:200] or f"HTTP {r.status_code}")
        retry = None
        try:
            retry = float(r.headers.get("Retry-After")) if r.headers.get("Retry-After") else None
        except ValueError:
            pass
        low = msg.lower()
        if r.status_code == 403 and "harness" in low:
            return LLMError("model_unavailable", "OpenRouter only serves this model to approved apps (agentic harnesses), not to this tool: it is skipped", gated=True)
        if r.status_code == 401 or (r.status_code == 403 and any(w in low for w in ("key", "credential", "authenticat"))):
            return LLMError("auth", "OpenRouter rejected the key: check it in the settings box")
        if r.status_code == 403:
            return LLMError("model_unavailable", msg)                   # moderation, region ...: another model may still answer
        if r.status_code == 402:
            return LLMError("credits", "this model needs credits you do not have; use a :free model")
        if r.status_code == 429:
            if "per-day" in low or "per day" in low or "daily" in low:
                return LLMError("daily_limit", "the free requests for today are used up (they reset at midnight UTC)")
            return LLMError("rate_limit", msg, retry)
        if r.status_code == 404 or "no endpoints" in low or "not a valid model" in low:
            return LLMError("model_unavailable", msg)
        if r.status_code >= 500 or r.status_code in (408, 409):
            return LLMError("unavailable", msg, retry)
        return LLMError("bad_response", msg)

    # ------------------------------------------------------------------ information
    def models(self, force: bool = False) -> list[dict]:
        if not force and self._models and time.time() - self._models[0] < CACHE_S:
            return self._models[1]
        try:
            r = self._http.get(f"{self.base}/models", headers=self._headers(), timeout=(6, 20))
        except requests.RequestException as e:
            raise LLMError("network", f"could not reach OpenRouter ({type(e).__name__})") from e
        if r.status_code != 200:
            raise self._fail(r)
        data = r.json().get("data", [])
        self._models = (time.time(), data)
        return data

    def free_models(self) -> list[dict]:
        def free(m):
            p = m.get("pricing") or {}
            return str(m.get("id", "")).endswith(":free") and float(p.get("prompt") or 0) == 0 and float(p.get("completion") or 0) == 0
        return [m for m in self.models() if free(m)]

    def usage(self) -> dict:
        """{'free_requests_left': n|None, 'free_requests_limit': n|None, 'is_free_tier': bool|None} from GET /key."""
        try:
            r = self._http.get(f"{self.base}/key", headers=self._headers(), timeout=(6, 15))
        except requests.RequestException as e:
            raise LLMError("network", f"could not reach OpenRouter ({type(e).__name__})") from e
        if r.status_code != 200:
            raise self._fail(r)
        d = r.json().get("data", {})
        fm = d.get("free_model_daily_requests") or {}
        return {"free_requests_left": fm.get("remaining"), "free_requests_limit": fm.get("limit"), "is_free_tier": d.get("is_free_tier")}

    def choose_models(self, preferred: str) -> list[str]:
        """`preferred` first, then the fallbacks that are really available. If the live list cannot be read, the static chain."""
        chain = [preferred] + [m for m in settings.FALLBACKS if m != preferred]
        chain = [m for m in chain if not is_gated(m)] or chain         # a refused model costs one of the 50 daily requests each time it is tried
        try:
            live = {m["id"]: m for m in self.free_models()}
        except LLMError:
            return chain
        return [m for m in chain if m in live] or chain

    def supports(self, model: str, param: str) -> bool:
        try:
            for m in self.models():
                if m.get("id") == model:
                    return param in (m.get("supported_parameters") or [])
        except LLMError:
            pass
        return False

    # ------------------------------------------------------------------ chat
    def chat(self, messages: list[dict], model: str, *, json_schema: dict | None = None, max_tokens: int = 3000, temperature: float = 0.3,
             reasoning_effort: str | None = "medium", json_mode: bool = False, on_delta: Callable[[str, str], None] | None = None, cancel: threading.Event | None = None,
             timeout: tuple[float, float] = (8, 120), retries: int = 2) -> dict:
        """One streamed completion. Returns {'text', 'reasoning', 'model', 'usage', 'finish'}. `on_delta(kind, piece)` gets 'text' or 'reasoning' pieces."""
        body: dict = {"model": model, "messages": messages, "stream": True, "max_tokens": max_tokens, "temperature": temperature}
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        if json_schema is not None:
            if self.supports(model, "structured_outputs"):
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "plan", "strict": True, "schema": json_schema}}
                body["provider"] = {"require_parameters": True}
            elif self.supports(model, "response_format"):
                body["response_format"] = {"type": "json_object"}
        if json_mode and "response_format" not in body and self.supports(model, "response_format"):
            body["response_format"] = {"type": "json_object"}
        last: LLMError | None = None
        for attempt in range(retries + 1):
            if cancel is not None and cancel.is_set():
                raise LLMError("cancelled", "stopped")
            try:
                return self._stream(body, on_delta, cancel, timeout)
            except LLMError as e:
                last = e
                if e.gated:
                    GATED[model] = time.time()
                if e.kind not in ("rate_limit", "unavailable", "network") or attempt == retries:
                    raise
                wait = e.retry_after if e.retry_after is not None else min(2.0 ** (attempt + 1), 20.0)
                self._sleep(min(wait, 30.0))
        raise last or LLMError("bad_response", "no answer")

    def chat_with_fallback(self, messages: list[dict], models: list[str], **kw) -> dict:
        """Try each model in turn; move on only for errors that another model can fix. A spent daily quota or a bad key stops at once."""
        err: LLMError | None = None
        for m in models:
            try:
                return self.chat(messages, m, **kw)
            except LLMError as e:
                err = e
                if e.kind not in ("model_unavailable", "unavailable", "rate_limit", "bad_response"):
                    raise
        raise err or LLMError("model_unavailable", "no model is available")

    def _stream(self, body: dict, on_delta, cancel, timeout) -> dict:
        for hook in list(ATTEMPT_HOOKS):
            try:
                hook(body["model"])
            except Exception:                                  # a counter must never be able to stop a request
                pass
        try:
            r = self._http.post(f"{self.base}/chat/completions", headers=self._headers(), json=body, stream=True, timeout=timeout)
        except requests.RequestException as e:
            raise LLMError("network", f"could not reach OpenRouter ({type(e).__name__})") from e
        if r.status_code != 200:
            raise self._fail(r)
        text, reasoning, usage, finish, model = [], [], {}, None, body["model"]
        try:
            for raw in r.iter_lines(decode_unicode=True):
                if cancel is not None and cancel.is_set():
                    raise LLMError("cancelled", "stopped")
                if not raw or raw.startswith(":") or not raw.startswith("data:"):
                    continue                                   # blank lines and OpenRouter's ': PROCESSING' keep-alives
                payload = raw[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ch = json.loads(payload)
                except ValueError:
                    continue
                if ch.get("error"):                            # an error in the middle of a stream
                    e = ch["error"]
                    raise LLMError("unavailable", str(e.get("message") or "the model stopped with an error"))
                model = ch.get("model", model)
                usage = ch.get("usage") or usage
                for choice in ch.get("choices") or []:
                    d = choice.get("delta") or {}
                    if d.get("content"):
                        text.append(d["content"])
                        on_delta and on_delta("text", d["content"])
                    if d.get("reasoning"):
                        reasoning.append(d["reasoning"])
                        on_delta and on_delta("reasoning", d["reasoning"])
                    finish = choice.get("finish_reason") or finish
        except requests.RequestException as e:
            raise LLMError("network", f"the connection broke while streaming ({type(e).__name__})") from e
        finally:
            r.close()
        out = "".join(text)
        if not out.strip():
            raise LLMError("bad_response", "the model returned no text (it may have spent its whole budget thinking)")
        return {"text": out, "reasoning": "".join(reasoning), "model": model, "usage": usage, "finish": finish}
