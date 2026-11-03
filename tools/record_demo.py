"""Record short demo clips of the running app (for the README): a headless Chrome is driven over the DevTools protocol, frames are
captured, and ffmpeg turns them into an mp4 and a small gif. No extra Python packages beyond `websockets` (already installed).

    python -m tools.record_demo chat  --base http://127.0.0.1:8011 --out docs/media
    python -m tools.record_demo shell --base http://127.0.0.1:8011 --out docs/media
    python -m tools.record_demo terminal --lines runs/media/rover_loop.json --out docs/media

Start the dashboard first. For a quiet recording that does not use the free AI requests:
    set SIMLAB_CHAT_CONFIG=<a file containing {"ai": "never"}>  and  SIMLAB_WEBOTS_PORT=8781, then  python -m sewerbot --no-browser --port 8011
Waiting stretches (a stress test running) are sped up 4x in the clip; the clip shows them as they happened otherwise.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
          "/usr/bin/google-chrome", "/usr/bin/chromium"]
FAST = 4.0                       # speed-up factor of the stretches marked fast


def find_chrome() -> str:
    for c in CHROME:
        if Path(c).exists():
            return c
    found = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")
    if not found:
        raise SystemExit("Chrome was not found")
    return found


class Cdp:
    def __init__(self, url: str):
        self.ws = connect(url, max_size=None, open_timeout=15)
        self.n = 0

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=60))
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, js: str):
        r = self.call("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text", "js error") + " " + str(r["exceptionDetails"].get("exception", {}).get("description", ""))[:200])
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


class Browser:
    def __init__(self, width=1280, height=800, port=9333):
        self.w, self.h, self.port = width, height, port
        self.profile = tempfile.mkdtemp(prefix="rec_chrome_")
        args = [find_chrome(), "--headless=new", f"--remote-debugging-port={port}", f"--window-size={width},{height}", f"--user-data-dir={self.profile}",
                "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--mute-audio", "--force-device-scale-factor=1",
                "--enable-webgl", "--ignore-gpu-blocklist", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "about:blank"]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.page_ws = ""
        for _ in range(60):
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2).read())
                self.page_ws = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.5)
        if not self.page_ws:
            self.close()
            raise SystemExit("Chrome did not start")
        self.cdp = Cdp(self.page_ws)
        self.cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

    def close(self):
        try:
            self.cdp.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


class Recorder:
    """Captures frames from a second connection to the same page while the scene runs on the first."""

    def __init__(self, browser: Browser, fps=8):
        self.cdp, self.fps = Cdp(browser.page_ws), fps
        self.frames: list[tuple[float, bool, bytes]] = []
        self.fast = False
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        """Chrome's screencast pushes a frame whenever the page changes (screenshots on demand stall while the page navigates)."""
        n = 1
        while not self._stop.is_set():
            try:
                m = json.loads(self.cdp.ws.recv(timeout=1))
            except Exception:
                continue
            if m.get("method") == "Page.screencastFrame":
                p = m["params"]
                self.frames.append((time.time(), self.fast, base64.b64decode(p["data"])))
                n += 1
                self.cdp.ws.send(json.dumps({"id": 1000 + n, "method": "Page.screencastFrameAck", "params": {"sessionId": p["sessionId"]}}))

    def start(self):
        self.cdp.call("Page.enable")
        self.cdp.call("Page.startScreencast", format="jpeg", quality=82, everyNthFrame=1)
        self._t.start()

    def stop(self):
        self._stop.set()
        self._t.join(5)
        self.cdp.close()

    def save(self, out_dir: Path, name: str, width_gif=840):
        """mp4 (full size) and gif (smaller) from the frames; fast stretches are shortened by FAST."""
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="rec_frames_"))
        fr = self.frames
        if len(fr) < 2:
            raise SystemExit("no frames were captured")
        # output timeline: each captured frame lasts until the next one; fast frames last FAST times less
        starts, t = [], 0.0
        for i, (ts, fast, _) in enumerate(fr):
            starts.append(t)
            dur = (fr[i + 1][0] - ts) if i + 1 < len(fr) else 0.2
            t += dur / (FAST if fast else 1.0)
        total, n, j = t, 0, 0
        while n / self.fps < total:
            tt = n / self.fps
            while j + 1 < len(fr) and starts[j + 1] <= tt:
                j += 1
            (tmp / f"f{n:05d}.jpg").write_bytes(fr[j][2])
            n += 1
        ff = shutil.which("ffmpeg") or "ffmpeg"
        src = ["-framerate", str(self.fps), "-i", str(tmp / "f%05d.jpg")]
        mp4, gif = out_dir / f"{name}.mp4", out_dir / f"{name}.gif"
        subprocess.run([ff, "-y", "-loglevel", "error", *src, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", str(mp4)], check=True)
        subprocess.run([ff, "-y", "-loglevel", "error", *src, "-vf", f"fps=8,scale={width_gif}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                        "-loop", "0", str(gif)], check=True)
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"{name}: {n} frames, {total:.1f} s -> {mp4.name} {mp4.stat().st_size // 1024} KB, {gif.name} {gif.stat().st_size // 1024} KB")


# ------------------------------------------------------------------ scene helpers (run on the driver connection)
HELP_JS = r"""
window.__find = (sel, text) => [...document.querySelectorAll(sel)].find(e => (!text || e.textContent.trim().includes(text)) && e.getClientRects().length);
window.__ripple = (el) => {
  const r = el.getBoundingClientRect(), d = document.createElement('div');
  d.style.cssText = `position:fixed;left:${r.left + r.width/2 - 18}px;top:${r.top + r.height/2 - 18}px;width:36px;height:36px;border-radius:50%;` +
    'background:rgba(90,160,255,.35);border:2px solid rgba(120,180,255,.9);z-index:2147483647;pointer-events:none;transition:all .5s ease-out';
  document.body.appendChild(d);
  requestAnimationFrame(() => { d.style.transform = 'scale(1.8)'; d.style.opacity = '0'; });
  setTimeout(() => d.remove(), 600);
};
"""


class Scene:
    def __init__(self, browser: Browser, rec: Recorder):
        self.b, self.rec, self.c = browser, rec, browser.cdp

    def go(self, url: str, settle=1.5):
        self.c.call("Page.navigate", url=url)
        for _ in range(60):
            if self.c.eval("document.readyState") == "complete":
                break
            time.sleep(0.25)
        self.c.eval(HELP_JS)
        time.sleep(settle)

    def pause(self, s: float):
        time.sleep(s)

    def click(self, sel: str, text: str = "", settle=0.8) -> bool:
        ok = self.c.eval(f"(() => {{ const e = window.__find({json.dumps(sel)}, {json.dumps(text)}); if (!e) return false; e.scrollIntoView({{block:'nearest'}}); window.__ripple(e); return true; }})()")
        if not ok:
            print(f"  (could not find {sel!r} {text!r})")
            return False
        time.sleep(0.35)
        self.c.eval(f"window.__find({json.dumps(sel)}, {json.dumps(text)}).click()")
        time.sleep(settle)
        return True

    def type(self, sel: str, text: str, per_key=0.045):
        self.c.eval(f"window.__find({json.dumps(sel)}).focus()")
        for ch in text:
            self.c.call("Input.dispatchKeyEvent", type="keyDown", text=ch)
            self.c.call("Input.dispatchKeyEvent", type="keyUp")
            time.sleep(per_key)

    def key(self, name: str):
        code = {"Enter": 13}[name]
        self.c.call("Input.dispatchKeyEvent", type="keyDown", key=name, code=name, windowsVirtualKeyCode=code, text="\r")
        self.c.call("Input.dispatchKeyEvent", type="keyUp", key=name, code=name, windowsVirtualKeyCode=code)

    def wait_for(self, js_cond: str, timeout: float, fast=True, poll=0.5) -> bool:
        """Wait until the JS condition is true; the stretch is sped up in the clip."""
        self.rec.fast = fast
        end = time.time() + timeout
        try:
            while time.time() < end:
                try:
                    if self.c.eval(js_cond):
                        return True
                except RuntimeError:
                    pass
                time.sleep(poll)
            return False
        finally:
            self.rec.fast = False


# ------------------------------------------------------------------ the scenes
def scene_chat(s: Scene, base: str):
    s.go(f"{base}/chat", 1.0)
    s.c.eval("try { localStorage.setItem('simlab.chat.theme', 'light'); } catch (e) {} document.documentElement.setAttribute('data-theme', 'light'); 1")   # the README clips are light themed
    s.pause(1.0)
    s.pause(1.5)
    s.click(".ch-chip", "sewer pipe", settle=1.0)
    s.wait_for("document.querySelectorAll('.ch-card-h').length >= 3", 30, fast=False)
    s.pause(3.5)
    s.click(".ch-chip", "Run all tests", settle=1.0)
    s.wait_for("/ of /.test(document.body.innerText) && !/Stop after this one/i.test(document.body.innerText) && /Fix the weakest|Make it tougher/i.test(document.body.innerText)", 150)
    s.pause(3.0)
    s.c.eval("document.querySelector('article')?.scrollTo?.({top: 0, behavior: 'smooth'}); window.scrollTo({top: 0, behavior: 'smooth'}); 1")
    s.pause(1.5)


def scene_shell(s: Scene, base: str):
    s.go(f"{base}/chat", 1.0)
    s.c.eval("try { localStorage.setItem('simlab.chat.theme', 'light'); } catch (e) {} document.documentElement.setAttribute('data-theme', 'light'); 1")
    s.pause(1.0)
    s.click(".ch-chip", "sewer pipe", settle=1.0)
    s.wait_for("document.querySelectorAll('.ch-card-h').length >= 3", 30, fast=False)
    s.pause(2.0)
    s.click("#ch-overview-btn", "Panes", settle=1.2)
    for tab in ("Plan", "Simulation", "Tests", "Parts", "Rover", "Tasks"):
        s.click(".ch-rp-tab", tab, settle=2.0)
    s.click("#ch-theme-btn", "", settle=1.5)            # light theme
    s.pause(1.5)
    s.click("#ch-theme-btn", "", settle=1.0)
    s.click("#ch-sb-close, .ch-iconbtn[aria-label*='sidebar' i]", "", settle=0.5)


SCENES = {"chat": scene_chat, "shell": scene_shell}


def record(name: str, base: str, out: Path, width=1280, height=800, fps=8, port=9333):
    b = Browser(width, height, port)
    rec = Recorder(b, fps)
    try:
        rec.start()
        SCENES[name](Scene(b, rec), base)
    finally:
        rec.stop()
        b.close()
    rec.save(out, name)


def record_terminal(lines_json: Path, out: Path, title: str, width=1100, height=700, fps=8, port=9334):
    """Replay captured terminal lines (a real run of the tools) as a typed terminal in a page, and record it."""
    data = json.loads(lines_json.read_text(encoding="utf-8"))
    html = (Path(__file__).parent / "terminal_replay.html").read_text(encoding="utf-8").replace("__TITLE__", title).replace("__DATA__", json.dumps(data))
    page = Path(tempfile.mkdtemp(prefix="rec_term_")) / "t.html"
    page.write_text(html, encoding="utf-8")
    b = Browser(width, height, port)
    rec = Recorder(b, fps)
    try:
        rec.start()
        s = Scene(b, rec)
        s.go(page.as_uri(), 0.5)
        s.wait_for("window.__done === true", 120, fast=False)
        s.pause(2.5)
    finally:
        rec.stop()
        b.close()
    rec.save(out, "rover-loop", width_gif=800)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene", choices=[*SCENES, "terminal"])
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default="docs/media")
    ap.add_argument("--lines", help="terminal scene: JSON file of captured lines")
    ap.add_argument("--title", default="RoboForge: the rover loop on the simulated rover")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if a.scene == "terminal":
        record_terminal(Path(a.lines), out, a.title)
    else:
        record(a.scene, a.base.rstrip("/"), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
