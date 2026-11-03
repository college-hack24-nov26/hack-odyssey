"""One thread for all OpenGL work.

GLFW windows and OpenGL contexts belong to the thread that created them. Letting whichever thread happens to render
(the dashboard's run thread, a test, a dataset job) create and destroy contexts corrupts GLFW's state after a few
rounds and crashes the process with an access violation on Windows. Every render and every renderer clean-up therefore
runs on this single long-lived thread; callers just block until their call returns.
"""
from __future__ import annotations

import queue
import threading

_lock = threading.Lock()
_gl: "GLThread | None" = None


class _Call:
    __slots__ = ("fn", "args", "kw", "done", "result", "error")

    def __init__(self, fn, args, kw):
        self.fn, self.args, self.kw = fn, args, kw
        self.done, self.result, self.error = threading.Event(), None, None


class GLThread:
    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="mujoco-gl")
        self.thread.start()

    def _loop(self):
        while True:
            call = self._q.get()
            try:
                call.result = call.fn(*call.args, **call.kw)
            except BaseException as e:  # noqa: BLE001 - handed back to the caller, which re-raises it
                call.error = e
            call.done.set()

    def call(self, fn, *args, **kw):
        """Run fn on the GL thread and return its result (or raise its exception)."""
        if threading.current_thread() is self.thread:
            return fn(*args, **kw)
        c = _Call(fn, args, kw)
        self._q.put(c)
        c.done.wait()
        if c.error is not None:
            raise c.error
        return c.result

    def post(self, fn, *args, **kw) -> None:
        """Run fn on the GL thread without waiting (clean-up from a finaliser)."""
        self._q.put(_Call(fn, args, kw))


def gl() -> GLThread:
    global _gl
    with _lock:
        if _gl is None:
            _gl = GLThread()
        return _gl
