"""Compile / flash the rover firmware (arduino-cli). Never uploads without --confirm FLASH.

    python -m tools.flash --check                       compile the v2 firmware only (touches no board)
    python -m tools.flash --check --wifi                the same with Wi-Fi (needs config/wifi.json: {"ssid": "...", "password": "..."})
    python -m tools.flash --port COM6 --confirm FLASH   compile and upload v2 (wheels off the ground; close the serial monitor)
    python -m tools.flash --port COM6 --sketch recovery --confirm FLASH     put the older known-good firmware back
    python -m tools.flash --steps                       the manual Arduino IDE steps

The pin map is read from config/hardware.json ("pins"); defaults: left motor 26/27, right motor 32/33, MPU SDA 21 SCL 22, HC-SR04 TRIG 18 ECHO 35.
"""
from __future__ import annotations

import argparse
import sys

from sewerbot.hardware.flash import FlashError, compile_sketch, flash, manual_steps
from sewerbot.hardware.pins import load_pins


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sketch", choices=("v2", "recovery"), default="v2")
    ap.add_argument("--port")
    ap.add_argument("--wifi", action="store_true", help="include the Wi-Fi link (reads config/wifi.json; the password is never printed)")
    ap.add_argument("--check", action="store_true", help="compile only")
    ap.add_argument("--confirm", help="the word FLASH, typed only after you decided to flash")
    ap.add_argument("--steps", action="store_true", help="print the manual flashing steps")
    a = ap.parse_args(argv)
    if a.steps:
        print(manual_steps(a.sketch))
        return 0
    try:
        if a.check or not a.port:
            print(f"pins: {load_pins()}")
            r = compile_sketch(a.sketch, use_wifi=a.wifi)
            print(r["output"][-600:])
            print("compile OK" if r["ok"] else "compile FAILED", f"(flags: {' '.join(r['flags']) or 'none'})")
            if not a.port:
                print("nothing was flashed. To flash: --port COMx --confirm FLASH")
            return 0 if r["ok"] else 1
        r = flash(a.port, a.confirm or "", a.sketch, use_wifi=a.wifi)
        print(f"flashed {r['sketch']} to {r['port']}. Now run:  python -m tools.rover_bringup --port {a.port}")
        return 0
    except FlashError as e:
        print(f"STOPPED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
