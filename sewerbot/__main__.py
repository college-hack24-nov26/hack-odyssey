"""`python -m sewerbot` starts the dashboard at http://127.0.0.1:8000 (Simulation + Hardware tabs)."""
import argparse
import webbrowser

import uvicorn


def main():
    ap = argparse.ArgumentParser(prog="sewerbot")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if not a.no_browser:
        import threading
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{a.host}:{a.port}/")).start()
    uvicorn.run("sewerbot.dashboard.app:app", host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
