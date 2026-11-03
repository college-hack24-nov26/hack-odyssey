"""Measure how the real rover differs from its simulation, and (only if you say so) apply the correction.

    python -m tools.rover_gap --port COM6                         run the tests (about 1 minute), print the findings, save runs/rover/<time>/gap.json
    python -m tools.rover_gap --port COM6 --apply                 then offer the recommended trim (RAM only), re-measure the pull, and offer to save it in the board
    python -m tools.rover_gap --port serial:mock-v2               try it on the emulator

Needs firmware v2 and the MPU6050 (run tools.rover_bringup first). The rover drives forward and back about 0.3 m, spins on the spot and moves each wheel
alone: it needs 1.5 m of clear, flat floor and you next to the power switch. Nothing happens until you type YES.
"""
from __future__ import annotations

import argparse
import sys

from sewerbot.hardware.serial_rover import RoverLinkError, SerialRover
from simlab.twin import ProtocolAborted, diagnose, explain, measure_gap, run_protocol


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True)
    ap.add_argument("--environment", default="", help="a name for the floor and set-up, saved with the run (e.g. 'tile floor, tape line')")
    ap.add_argument("--apply", action="store_true", help="offer the recommended trim, re-measure, and offer to save it")
    a = ap.parse_args(argv)
    try:
        r = SerialRover(a.port, us_guard=False)
    except RoverLinkError as e:
        print(f"FAILED to open {a.port}: {e}")
        return 2
    try:
        while not (r.connected or r.error):
            pass
        if input("Type YES if the rover is on clear flat floor (1.5 m ahead), its battery is charged, and your hand is by the power switch: ").strip() != "YES":
            print("Nothing was moved.")
            return 1
        print("measuring (about a minute)...")

        def progress(name, i, n):
            print(f"  {name} {i + 1}/{n}", end="\r")
        res = measure_gap(r, environment=a.environment, progress=progress)
        print("\n" + "\n".join(explain(res.diagnosis)))
        for row in res.table:
            print(f"  {row['what']}: ideal simulation {row['ideal_simulation']} | rover {row['measured']} | twin after fit {row['after_fit']} {row['unit']}")
        print(f"saved to {res.folder}")
        imb = res.diagnosis.get("imbalance")
        if a.apply and imb and imb.action:
            t = imb.action["cfg"]
            if input(f"Apply trimL={t['trimL']} trimR={t['trimR']} (temporary until saved) and re-measure? Type YES: ").strip() == "YES":
                r.ask(f"CFG trimL={t['trimL']} trimR={t['trimR']}", "cfg")
                after = diagnose(run_protocol(r, include=("rest", "straight", "spin"), progress=progress))
                print("\nafter the trim: " + " ".join(explain(after)))
                if input("Keep this trim in the board (survives power-off)? Type SAVE: ").strip() == "SAVE":
                    print(r.ask("SAVE", "saved") is not None and "saved." or "the board did not confirm: run again")
        return 0
    except ProtocolAborted as e:
        print(f"\nSTOPPED: {e}")
        return 1
    finally:
        r.close()


if __name__ == "__main__":
    sys.exit(main())
