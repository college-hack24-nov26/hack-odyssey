# The Goal tab: describe the job, get a machine, stress-test it, build it

Open the dashboard (`startup.bat`): **Goal** is the first tab.

1. **What should it do?** Describe the job in your own words, as much as you like, and press **Plan it with AI**. A language model (OpenRouter free tier, key in *AI settings*) thinks it through: what machine fits, what can go wrong, what the machine needs, which parts, what to stress-test. See [PLANNER.md](PLANNER.md) for what it decides and how every claim is checked. A job no machine here can do (underwater, space) is answered honestly as unsupported.
   *Without an AI key*, or when the AI fails or the free quota is spent, open **Ready-made jobs**: *Inspect pipes*, *Rescue on the water*, *Watch from the air* (observation only), the offline path described below. A sentence that matches nothing is not guessed at.
2. **A few questions**, only the ones that change the design (the AI asks them; the ready-made jobs ask pipe width, distance, how rough the water is, how long it must stay up...). Tick **My ESP32 rover kit** if you want the design to use the parts you already have.
3. **Your machine.** The software checks every candidate (the AI's designs, then a search of the parts library) with the Builder's evaluator and against the requirements, and shows one to three designs with a traffic light, three numbers, what it meets, and which parts you have or must buy. When nothing in the library meets the goal it says so and shows the closest design and what it misses. With an AI plan the page also shows **what the AI understood**, every situation it thought of (marked *can be simulated* or *not testable here*), and what the checker corrected or dropped.
4. **Watch it work:** opens the Lab with the machine doing the job in calm conditions. For an AI plan there is also **Watch in the worst conditions**, which applies the worst waves, wind, current or grip the plan asked for (a rover on near-ice just sits there, which is the honest answer).
5. **How tough is it?** The stress test (below).
6. **Build sheet:** a printable page with the parts to buy (with where each number came from and how sure we are), wiring, steps and safety rules.
7. **Use on my real rover** (pipes only): copies the machine's size and camera mount to the Hardware tab. See [HARDWARE.md](HARDWARE.md) for connecting and driving the rover.

*Advanced: open in the Builder* sends the design to the Builder to change any part.

## How a machine is chosen

`simlab/goals/design.py`. Each goal has a starting build and a search over the library's parts (a few hundred candidates, about 2-4 s). A candidate must pass the Builder's own checks (voltages, currents, logic levels, thrust, fit) and the goal's requirements, which the answers turn into numbers:

| Goal | What the answers require |
|---|---|
| Inspect pipes | fits the pipe width; drives the distance and back with a margin; enough pulling power if the pipe is wet |
| Rescue on the water | stays out the time; round trip within 80 % of the runtime; freeboard (hull above the water) at least 0.7x the wave height; thruster at least 1.3x the wind's push on the hull; a second thruster for rough water |
| Watch from the air | flight time 1.2x what you asked; thrust-to-weight enough to lean into the wind |

Parts you own come first, then fewest warnings, then weight. Extra designs are shown only when they are clearly different (15 % longer time out, 15 % lighter). The wind and wave rules are loose pre-filters; the stress test is the real check, and it has already shown the pre-filter to be wrong once (a tall hull with a small thruster passed the waves rule and was blown away by 8 m/s wind in the test; the wind rule was added).

Goals are YAML files in `simlab/goals/catalogue/` (yours in `config/goals/`). What the answers mean is code, so a goal cannot ask for something the software cannot check.

## The stress test

`simlab/hazards/`. For each hazard in the catalogue (`hazards/catalogue/<platform>.yaml`, each with a sourced range and a plain explanation) the machine is run on the **same scenes at every level** (same seeds), from mild to severe, and the result is the level where it starts to fail (success below 75-80 %). Faults (a motor, thruster or rotor stops; the rudder jams; commands are lost) are single cases. The result is compared with what you asked for ("you asked for 8 m/s wind: covered").

| Platform | Hazards swept | Faults | Task used |
|---|---|---|---|
| Pipes | slippery floor, mud/debris (share of pulling force), slow commands | a drive motor fails, lost commands | `pipe_short` |
| Water | waves, wind, current, fixed obstacles | thruster fails, rudder jams, a storm | `water_rescue` |
| Air | wind | a rotor fails, weak motors | `waypoints_easy` |

A sweep runs in the background (about 16 s for the rover, 2-3 minutes for the boat) and writes `runs/reports/<time>_goal_<name>/report.html` and `.json`.

**What it never says:** that the machine is safe. Every report ends with *what was not tested* and a statement that these are simulator results with a simple built-in controller. A better controller may do better, a worse one worse.

## What each hazard does to the simulation

| Hazard | How it is modelled (`Sim(vehicle, task, hazards={...})`) |
|---|---|
| grip (rover) | the tyre grip coefficient limits acceleration and sideways force on a flat floor |
| mud and debris (rover) | uses up a share of the motors' pulling force; the top speed falls by the same share (linear motor curve) |
| slow or lost commands | commands arrive late, or some never arrive |
| a motor fails | one side's drive is zero |
| waves (boat) | a few linear deep-water waves with a realistic spread; buoyancy is sampled over the hull. **No** breaking waves, slamming, diffraction or horizontal wave force |
| wind (boat, drone) | quadratic drag on the part above the water (boat) or the airframe (drone), with gusts for the drone |
| current | the water moves; the hull is dragged along |
| fixed obstacles | boxes that do not move when hit; the simple controller does not steer around them |
| thruster / rotor / rudder failures | zero or reduced thrust, stuck rudder; the drone's simple autopilot has no fault-tolerant control and flips |

All seeded: the same seed gives the same wave phases, wind and current directions at every severity.

## Build sheet scope

The parts list is made for every proposal. **Pin-level wiring and flashing steps exist only for the ESP32 + DRV8833 two-motor rover**, because that machine's firmware and wiring were built and checked. Drones and boats get the parts list, power notes and safety rules, and are told to wire from the datasheets. No sheet replaces reading your parts' datasheets before connecting power.

## Honest limits

* Estimates from datasheets and a simulator; none of it has been compared with your real machine until you calibrate it (calibration exists for ground vehicles).
* The detector problem is separate: simulator-trained detectors do not read real cracks reliably, and the rover's camera streams only 96x96 pixels.
* Not built: choosing from a free-text sentence with AI (keywords only), prices and availability, a drifting person, visual waves in the 3D viewer, firmware generation, driving the drone or boat hardware (the real-hardware link exists only for the ESP32 rover), and any interception or disabling of other aircraft.
