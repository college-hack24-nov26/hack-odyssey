"""The build sheet: what to buy, how to wire it and in what order, for a proposed machine.

Honest scope: the parts list (with where each number came from and how sure we are) is made for every proposal. Pin-level wiring
and flashing steps are made only for the ESP32 + DRV8833 rover, because that is the one machine whose firmware and wiring were
built and checked on the bench. Drones and boats get the parts list, the power chain and safety rules, and are told to wire
from the parts' own datasheets.
"""
from __future__ import annotations

import html
import time

from ..parts import get_part
from ..parts.build import Build
from .design import part_names

# firmware/esp32_rover_serial/esp32_rover_serial.ino (ESP32 DevKit)
ROVER_PINS = (("GPIO26", "IN1", "left motor, forward"), ("GPIO27", "IN2", "left motor, reverse"),
              ("GPIO32", "IN3", "right motor, forward"), ("GPIO33", "IN4", "right motor, reverse"))
SENSOR_PINS = {"hc_sr04": (("GPIO18", "TRIG", "direct"), ("GPIO19", "ECHO", "THROUGH A 10k + 20k DIVIDER: the echo is 5 V and the ESP32 is 3.3 V only"),
                           ("5V", "VCC", "from the 5 V rail"), ("GND", "GND", "")),
               "ir_obstacle_module": (("GPIO21", "OUT", "3.3 V module, or through a divider if it runs on 5 V"), ("3V3 or 5V", "VCC", "as the module requires"),
                                      ("GND", "GND", ""))}

SAFETY = {
    "ground": ["Check the polarity of the battery pack before connecting it: reversed polarity destroys the boards.",
               "Set the 5 V boost converter's output with a multimeter BEFORE connecting the ESP32 boards (about 5.1 V).",
               "First power-up with the wheels off the ground (the rover on a block) and the Hardware tab's checklist ticked.",
               "Keep a hand on the power switch for the first runs."],
    "air": ["Take the propellers OFF for every bench test. A LiPo battery can start a fire: charge it in a LiPo bag, never unattended.",
            "Check every motor's direction with the propellers off, then fit them and test over open ground away from people.",
            "Use a flight controller with its own failsafes (return home, low battery, link loss). This tool does not provide one.",
            "Check your local drone rules before flying."],
    "water": ["Test in shallow, calm water first, with a safety line attached.",
              "Waterproof every electrical connection and seal the battery box: a lead-acid or LiPo battery in water is dangerous.",
              "Fit a physical kill switch, and a way for the boat to stop on its own if the radio link is lost.",
              "Never test near people in the water."]}


def _qty(b: dict) -> dict[str, int]:
    drive, power, chassis = b.get("drive", {}), b.get("power", {}), b.get("chassis", {})
    q: dict[str, int] = {}
    def add(name, n=1):
        if name:
            q[name] = q.get(name, 0) + int(n)
    add(chassis.get("frame"))
    add(chassis.get("hull"))
    add(drive.get("motor"), drive.get("motor_count", 1))
    layout = {"diff2wd": 2, "skid4wd": 4, "skid6wd": 6, "tracked": 2}.get(chassis.get("drive"), drive.get("motor_count", 2))
    add(drive.get("wheel"), layout)
    add(drive.get("driver"), max(1, -(-int(drive.get("motor_count", 2)) // 2)) if drive.get("driver") else 0)
    add(drive.get("propulsor"), 4 if "propulsor" in drive else 0)
    add(drive.get("esc"), 4 if "esc" in drive else 0)
    add(drive.get("thruster"), drive.get("count", 1))
    add(power.get("battery"), int(power.get("series", 1)) * int(power.get("parallel", 1)))
    add(power.get("regulator"))
    add(b.get("controller"))
    for s in b.get("sensors", []):
        add(s.get("part"))
    if b.get("lamp", {}).get("part"):
        add(b["lamp"]["part"], b["lamp"].get("count", 1))
    return q


def _propulsor_count(b: dict) -> int:
    """Rotors on the airframe: the frame's layout decides (quad 4, hex 6, octo 8)."""
    try:
        return int(get_part(b["chassis"]["frame"]).params.get("rotors", 4))
    except Exception:
        return 4


def bill(build: dict, owned: dict | None = None) -> list[dict]:
    owned = owned or {}
    q = _qty(build)
    if "propulsor" in build.get("drive", {}):
        n = _propulsor_count(build)
        q[build["drive"]["propulsor"]] = n
        q[build["drive"]["esc"]] = n
    rows = []
    for name in part_names(build):
        p = get_part(name)
        if p is None:
            continue
        need = q.get(name, 1)
        rows.append({"name": name, "title": p.title, "kind": p.kind, "qty": need, "have": min(int(owned.get(name, 0)), need),
                     "source": p.source, "confidence": p.confidence, "mass_g": round(1000 * p.mass_kg * need, 1)})
    return rows


def wiring(build: dict) -> dict:
    """Pin tables where the rover firmware defines them, else an explicit 'not generated' with the reason."""
    drv = build.get("drive", {}).get("driver", "")
    if build.get("platform") == "ground" and build.get("controller") == "esp32_devkit" and drv.startswith("drv8833") and len(_qty(build)) and build["drive"].get("motor_count", 2) == 2:
        tables = [{"title": "ESP32 DevKit to the DRV8833 module", "rows": [{"from": a, "to": b, "note": c} for a, b, c in ROVER_PINS]}]
        for s in build.get("sensors", []):
            if s.get("part") in SENSOR_PINS:
                tables.append({"title": f"{get_part(s['part']).title}: not in the default firmware, build it with the sensor enabled",
                               "rows": [{"from": a, "to": b, "note": c} for a, b, c in SENSOR_PINS[s["part"]]]})
        return {"generated": True, "tables": tables,
                "firmware": "firmware/esp32_rover_serial/esp32_rover_serial.ino" + (" (build with -DENABLE_ULTRASONIC=1 for the HC-SR04)" if any(s.get("part") == "hc_sr04" for s in build.get("sensors", [])) else "")}
    return {"generated": False, "tables": [], "firmware": "",
            "reason": "Pin-level wiring is generated only for the ESP32 + DRV8833 two-motor rover, whose firmware and wiring were built and checked. "
                      "For this machine, wire from the datasheets linked in the parts list, and check the Builder's findings about voltages and logic levels first."}


def steps(build: dict, platform: str) -> list[str]:
    if platform == "ground":
        reg = build.get("power", {}).get("regulator")
        out = ["Lay out the chassis, motors and wheels. Mount the camera a few centimetres above the floor, tilted slightly up (not level)."]
        out.append("Wire the power: battery pack to the motor driver's motor supply, and to the boost converter" if reg else "Wire the power: battery pack to the motor driver and the controller (the Builder warns if the controller needs a regulator)")
        if reg:
            out.append("Set the boost converter's output to about 5.1 V with a multimeter before connecting any board to it.")
        out += ["Wire the controller to the motor driver with the pin table. Connect one common ground between every board and the battery.",
                "Flash the rover firmware (Hardware tab > Firmware & wiring > Check USB rover, then upload with arduino-cli).",
                "Power up with the wheels off the ground. Open the dashboard's Hardware tab, connect, tick the checklist and test forward, reverse, spin and Stop.",
                "Calibrate: drive for a fixed time, measure the distance and the turn, and enter them (Hardware tab > Calibration)."]
        return out
    if platform == "air":
        return ["Assemble the frame, motors and ESCs. Do NOT fit the propellers yet.", "Connect the battery through a fuse or a current-limited bench supply for the first power-up.",
                "Wire the ESCs to the flight controller, set the motor directions and calibrate the ESCs per their manuals.",
                "Configure the flight controller's failsafes (low battery, link loss, return home) before the first flight.",
                "Fit the propellers only for a tethered or open-field test, away from people."]
    return ["Fit the thruster, rudder servo and battery box in the hull; keep the heavy battery low and central.",
            "Seal every connection and the electronics box against water.", "Wire the motor controller to the thruster and the rudder servo to the controller.",
            "Bench-test the thruster out of the water for a few seconds only, then float-test in shallow water on a safety line.",
            "Set a failsafe so the boat stops or returns if the radio link is lost."]


def make_sheet(proposal: dict, goal: dict, owned: dict | None = None) -> dict:
    b = dict(proposal["build"])
    b["platform"] = goal["platform"]
    return {"goal": goal["title"], "platform": goal["platform"], "parts": bill(b, owned), "wiring": wiring(b), "steps": steps(b, goal["platform"]),
            "safety": SAFETY[goal["platform"]], "caveat": "Numbers on this sheet are estimates from datasheets, not measurements of your machine. "
                                                           "Verify every connection against your parts' datasheets before connecting power."}


def render(sheet: dict) -> str:
    e = html.escape
    rows = "".join(f'<tr><td>{r["qty"]}x</td><td><b>{e(r["title"])}</b><div class="m">{e(r["name"])}</div></td><td>{"have" if r["have"] >= r["qty"] else "buy"}</td>'
                   f'<td>{e(r["confidence"])}</td><td class="m">{e(r["source"])}</td><td>{r["mass_g"]:g} g</td></tr>' for r in sheet["parts"])
    w = sheet["wiring"]
    wire = "".join(f'<h3>{e(t["title"])}</h3><table><tr><th>From</th><th>To</th><th>Note</th></tr>' + "".join(
        f'<tr><td>{e(r["from"])}</td><td>{e(r["to"])}</td><td>{e(r["note"])}</td></tr>' for r in t["rows"]) + "</table>" for t in w["tables"])
    wire = wire or f'<p>{e(w.get("reason", ""))}</p>'
    fw = f'<p>Firmware: <code>{e(w["firmware"])}</code></p>' if w["firmware"] else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Build sheet: {e(sheet["goal"])}</title><style>
body{{font:15px/1.5 system-ui,sans-serif;max-width:920px;margin:0 auto;padding:24px 16px;color:#111;background:#fff}}h1{{font-size:22px}}h2{{font-size:17px;margin-top:26px}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:5px 8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}.m{{color:#666;font-size:12px}}
.box{{border:2px solid #c33;border-radius:8px;padding:10px 14px;margin:12px 0}}code{{background:#eee;padding:1px 4px}}@media print{{.box{{break-inside:avoid}}}}</style></head><body>
<h1>Build sheet: {e(sheet["goal"])}</h1>
<div class="box"><b>Safety</b><ul>{"".join(f"<li>{e(s)}</li>" for s in sheet["safety"])}</ul></div>
<h2>Parts to buy</h2><table><tr><th></th><th>Part</th><th></th><th>How sure</th><th>Where the numbers came from</th><th>Weight</th></tr>{rows}</table>
<h2>Wiring</h2>{wire}{fw}<h2>Steps</h2><ol>{"".join(f"<li>{e(s)}</li>" for s in sheet["steps"])}</ol>
<p class="m">{e(sheet["caveat"])} Made {time.strftime("%Y-%m-%d %H:%M")}.</p></body></html>"""
