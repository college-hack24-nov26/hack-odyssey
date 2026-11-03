"""A local stand-in for OpenRouter (models, key info and streamed chat completions) so the planner can be tested without the internet or a key."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FREE = {"pricing": {"prompt": "0", "completion": "0"}}
MODELS = [{"id": "thinkingmachines/inkling:free", "supported_parameters": ["response_format", "reasoning"], **FREE},
          {"id": "thinkingmachines/inkling-small:free", "supported_parameters": ["response_format", "structured_outputs"], **FREE},
          {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "supported_parameters": ["reasoning"], **FREE},
          {"id": "nvidia/nemotron-3.5-super-vl:free", "supported_parameters": ["structured_outputs", "response_format", "reasoning"], **FREE},
          {"id": "google/gemma-4-31b-it:free", "supported_parameters": ["response_format"], **FREE},
          {"id": "some/paid-model", "supported_parameters": [], "pricing": {"prompt": "0.001", "completion": "0.002"}}]


class FakeOpenRouter:
    """`script`: one entry per chat request, consumed in order; an entry is a dict with
    chunks (text pieces), reasoning (pieces), status, error (message), headers, stream_error, delay (seconds between pieces), model."""

    def __init__(self, models=None, key_info=None):
        self.script: list[dict] = []
        self.requests: list[dict] = []
        self.models = MODELS if models is None else models
        self.key_info = key_info or {"free_model_daily_requests": {"used": 3, "limit": 50, "remaining": 47}, "is_free_tier": True}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, status, obj, headers=None):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
                if self.path.startswith("/models"):
                    return self._json(200, {"data": outer.models})
                if self.path.startswith("/key"):
                    return self._json(200, {"data": outer.key_info})
                self._json(404, {"error": {"message": "not found"}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                step = outer.script.pop(0) if outer.script else {"chunks": ['{"ok": true}']}
                status = step.get("status", 200)
                if status != 200:
                    return self._json(status, {"error": {"message": step.get("error", "error")}}, step.get("headers"))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def send(obj):
                    self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
                    self.wfile.flush()
                try:
                    self.wfile.write(b": OPENROUTER PROCESSING\n\n")
                    for piece in step.get("reasoning", []):
                        send({"model": step.get("model", body["model"]), "choices": [{"delta": {"reasoning": piece}}]})
                    for piece in step.get("chunks", []):
                        if step.get("delay"):
                            time.sleep(step["delay"])
                        send({"model": step.get("model", body["model"]), "choices": [{"delta": {"content": piece}}]})
                    if step.get("stream_error"):
                        send({"error": {"message": step["stream_error"]}})
                    send({"model": step.get("model", body["model"]), "choices": [{"delta": {}, "finish_reason": step.get("finish", "stop")}],
                          "usage": {"prompt_tokens": 100, "completion_tokens": 50}})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="fake-openrouter").start()

    def chat_requests(self) -> list[dict]:
        return [r for r in self.requests if r["path"].startswith("/chat")]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
