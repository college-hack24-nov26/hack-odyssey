"""The training runs reported in docs/RESULTS_LAB.md. Each call trains one policy and prints its comparison with the baseline.

    python -m tools.train_experiments rover_safe       rover, stronger collision penalty
    python -m tools.train_experiments quad_waypoints    250-class quadcopter through waypoints
"""
import sys

from simlab import get_task_spec
from simlab.train.rl import train

EXPERIMENTS = {
    "rover_safe": dict(vehicle="mid_4wd", task=lambda: {**get_task_spec("goto_open").to_dict(), "name": "goto_open_safe",
                                                         "reward": {"progress": 1.0, "reached": 10.0, "collision": -3.0, "time": -0.05, "action": -0.01}},
                       total_steps=600_000, curriculum_fraction=0.3),
    "quad_waypoints": dict(vehicle="quad_250", task=lambda: get_task_spec("waypoints_easy").to_dict(), total_steps=600_000,
                           curriculum_fraction=0.3),
}


def main(name: str) -> None:
    e = dict(EXPERIMENTS[name])
    task = e.pop("task")()
    out = train({**e, "task": task, "n_envs": 8, "eval_every": 40_000, "eval_episodes": 20, "final_episodes": 100, "seed": 1})
    print("saved", out)


if __name__ == "__main__":
    main(sys.argv[1])
