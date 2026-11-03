"""First contact with the real rover: finds the board, checks what is connected, writes runs/rover/<time>/report.json and log.txt.

    python -m tools.rover_bringup                       find the USB port, run the checks you can do hands-off, ask the rest
    python -m tools.rover_bringup --port COM6           choose the port yourself
    python -m tools.rover_bringup --drive               also the guided wheel check (wheels OFF the ground; it asks you to confirm)
    python -m tools.rover_bringup --camera http://192.168.29.218:81/stream
    python -m tools.rover_bringup --port serial:mock-v2 a simulated rover, to see what the output looks like
    python -m tools.rover_bringup --yes                 never ask questions (the guided checks are skipped)

Nothing moves unless you pass --drive AND type YES when asked. Close the Arduino serial monitor first: only one program can hold the port.
"""
from __future__ import annotations

import argparse
import sys

from sewerbot.hardware.bringup import find_port, run_bringup
from sewerbot.hardware.serial_rover import RoverLinkError, SerialRover

MARK = {"pass": "[ OK ]", "warn": "[LOOK]", "fail": "[FAIL]", "skip": "[ -- ]"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="COM6, /dev/ttyUSB0 or serial:mock-v2 (default: find it)")
    ap.add_argument("--camera", help="the ESP32-CAM stream address (optional)")
    ap.add_argument("--drive", action="store_true", help="include the guided wheel check (wheels off the ground)")
    ap.add_argument("--yes", action="store_true", help="unattended: do not ask anything")
    a = ap.parse_args(argv)

    port = a.port
    if not port:
        port, why = find_port()
        print(f"port: {why}")
        if not port:
            return 2
    try:
        rover = SerialRover(port, us_guard=False)
    except RoverLinkError as e:
        print(f"FAILED to open {port}: {e}")
        return 2

    def ask(prompt: str) -> str:
        try:
            return input(f"\n? {prompt}\n> ")
        except EOFError:
            return ""

    print(f"connected to {port}; checking (this takes about 15 s)...")
    try:
        rep = run_bringup(rover, None if a.yes else ask, camera_url=a.camera, drive=a.drive and not a.yes)
    finally:
        rover.close()
    print()
    for c in rep.checks:
        print(f"{MARK[c.status]} {c.title}: {c.detail}")
    print(f"\n{rep.summary()}. Saved to {rep.folder}")
    print("Send me report.json / log.txt from that folder if anything says LOOK or FAIL.")
    return 0 if rep.ok_to_test else 1


if __name__ == "__main__":
    sys.exit(main())
