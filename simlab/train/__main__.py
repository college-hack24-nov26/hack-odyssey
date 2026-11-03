"""python -m simlab.train --vehicle mid_4wd --task goto_open --steps 300000     train a policy
   python -m simlab.train --config runs/train/<run>/config.json                  re-run with a saved config
   python -m simlab.train eval --vehicle mid_4wd --task goto_open --model runs/train/<run>/model_best.zip
"""
import argparse
import json
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="simlab.train")
    sub = ap.add_subparsers(dest="cmd")
    ev = sub.add_parser("eval", help="compare a trained model with the task's baseline on identical scenarios")
    ev.add_argument("--vehicle", required=True)
    ev.add_argument("--task", required=True)
    ev.add_argument("--model", required=True)
    ev.add_argument("--episodes", type=int, default=50)
    ap.add_argument("--config", help="JSON file with the training settings")
    ap.add_argument("--vehicle")
    ap.add_argument("--task")
    ap.add_argument("--algo", choices=["ppo", "sac"])
    ap.add_argument("--steps", type=int)
    ap.add_argument("--envs", type=int)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--curriculum", type=float, help="fraction of the run over which difficulty ramps up (0 = off)")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    if a.cmd == "eval":
        from .evaluate import compare
        res = compare(a.vehicle, a.task, {"trained": f"model:{a.model}", "baseline": "baseline"}, a.episodes)
        for label, r in res.items():
            lo, hi = r["success_ci95"]
            print(f"{label:9s} success {100 * r['success_rate']:5.1f} % (95% CI {100 * lo:.0f}-{100 * hi:.0f})  "
                  f"time {r['mean_time_to_success']}  contact steps/ep {r['contact_steps_per_episode']:.2f}  crashes {r['crashes']}")
        return 0

    from .rl import train
    cfg = json.load(open(a.config, encoding="utf-8-sig")) if a.config else {}      # -sig: tolerate a Windows BOM
    for key, val in (("vehicle", a.vehicle), ("task", a.task), ("algo", a.algo), ("total_steps", a.steps), ("n_envs", a.envs),
                     ("seed", a.seed), ("curriculum_fraction", a.curriculum)):
        if val is not None:
            cfg[key] = val
    if "vehicle" not in cfg or "task" not in cfg:
        ap.error("--vehicle and --task (or a --config that has them) are required")
    out = train(cfg, a.out)
    print("run saved in", out)
    return 0


if __name__ == "__main__":          # required on Windows: worker processes re-import this module
    sys.exit(main())
