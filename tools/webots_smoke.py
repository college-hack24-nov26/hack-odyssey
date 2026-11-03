"""Live check of simlab's Webots backend (opens the Webots window, so it is not part of pytest).

    python -m tools.webots_smoke                 drive the mid_4wd rover through the straight sewer for ~20 simulated seconds

It exercises exactly what `Sim(vehicle, "sewer_straight", backend="webots")` does: launch Webots with a generated world,
connect over protocol v2, send unicycle commands, read pose / range / camera back.
"""
import sys
import time

import numpy as np

from simlab import get_task_spec, get_vehicle
from simlab.envs import Sim


def main(vehicle: str = "mid_4wd", task: str = "sewer_straight", seconds: float = 20.0) -> int:
    t0 = time.time()
    sim = Sim(get_vehicle(vehicle), get_task_spec(task), backend="webots", mode="fast")
    try:
        vec = sim.reset()
        start = np.array(sim.obs.state.pos[:2])
        print(f"connected in {time.time() - t0:.0f} s; start {start.round(2)}, observation {vec.round(2)}", flush=True)
        n = int(seconds * sim.backend.profile.camera.fps)
        for i in range(n):
            front = sim.obs.sensors["range"][1]
            cmd = [0.0, 0.4] if front < 0.6 else [sim.vehicle.params["max_speed"] * 0.6, 0.0]      # crawl on, turn when something is close
            sim.step_physical(cmd)
        end = np.array(sim.obs.state.pos[:2])
        frame = sim.backend.render("front")
        print(f"{n} steps, sim time {sim.obs.state.t:.1f} s, moved {np.linalg.norm(end - start):.2f} m, "
              f"speed {sim.obs.state.vel_body[0]:.2f} m/s, camera frame {frame.shape}", flush=True)
        ok = np.linalg.norm(end - start) > 0.5 and frame.ndim == 3
        print("OK" if ok else "FAILED: the rover did not move or no camera frame arrived")
        return 0 if ok else 1
    finally:
        sim.close()


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
