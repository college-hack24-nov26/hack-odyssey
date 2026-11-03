"""Score an inspect-everything mission in the MuJoCo pipe environment the way the Webots regression does.

    python -m simlab.tasks.pipe_eval --task pipe_star --detector oracle
    python -m simlab.tasks.pipe_eval --task pipe_star --detector rfdetr:models/rfdetr_nano_v1

The vehicle drives the route that visits every goal post (sewerbot.brain.navigation.plan_inspect_all, followed by the same
PathFollower the Sewer tab uses). Each camera frame goes to the detector, detections into the defect tracker, and the confirmed
defects are matched with the planted ones (evaluate_against_truth): recall, precision and false alarms, the same numbers as
docs/RESULTS.md. Detectors:
    oracle      exact boxes from the simulator's segmentation render (tests the environment and the tracker, not a model)
    classical   the training-free OpenCV fallback
    rfdetr:DIR  an RF-DETR model directory (for example one trained on Webots frames: that measures the gap between the two worlds)
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from ..envs.core import Sim
from ..envs.gym_env import as_task, as_vehicle
from ..perception.capture import boxes_from_segmentation
from ..perception.detection import Detection


class OracleDetector:
    """Perfect boxes, read from the segmentation render of the current frame."""
    name = "oracle"

    def __init__(self, sim: Sim, camera: str):
        self.be, self.cam = sim.backend, camera
        self.label_of = sim.backend.label_map()
        self.classes = list(sim.task.spec.labels)

    def detect(self, bgr) -> list[Detection]:
        seg = self.be.render(self.cam, segmentation=True)
        return [Detection(b["cls"], 0.99, tuple(b["box"])) for b in boxes_from_segmentation(seg, self.label_of, self.classes)]


def make_eval_detector(spec: str, sim: Sim, camera: str):
    if spec == "oracle":
        return OracleDetector(sim, camera)
    from sewerbot.brain.detectors import make_detector
    if spec.startswith("rfdetr:"):
        return make_detector("rfdetr", weights=spec.split(":", 1)[1], size="nano")
    return make_detector(spec)


def _pipe_for_pose(m):
    from sewerbot.brain.tracker import PipeFrame

    def f(pose: dict):
        loc = m.locate(pose["x"], pose["y"])
        if "edge" not in loc:
            return None
        e = m.edges[loc["edge"]]
        o, fwd, _ = m.edge_frame(e.id)
        return PipeFrame(e.id, o, fwd, e.section, e.s_min, e.s_max)
    return f


def inspect_all(vehicle, task, detector: str = "oracle", frame_every: int = 1, max_sim_seconds: float | None = None, seed: int = 0) -> dict:
    """Run the mission headless and score it. `frame_every`: run the detector on every n-th policy step."""
    from sewerbot.brain.navigation import PathFollower, plan_inspect_all
    from sewerbot.brain.tracker import CameraModel, DefectTracker, evaluate_against_truth
    from sewerbot.core.profiles import profile_from_spec
    v, t = as_vehicle(vehicle), as_task(task)
    sim = Sim(v, t, seed=seed)
    try:
        m, profile = sim.task.map, profile_from_spec(v)
        cam = next(iter(sim.backend.cameras))
        det = make_eval_detector(detector, sim, cam)
        route, visited, skipped = plan_inspect_all(m, profile, m.start_node)
        follower = PathFollower(m, profile, route)
        tracker = DefectTracker(CameraModel.from_profile(profile))
        pipe_of = _pipe_for_pose(m)
        sim.reset(seed=seed)
        limit = max_sim_seconds or 4.0 * sum(float(np.hypot(*(np.array(m.nodes[b].pos) - np.array(m.nodes[a].pos)))) for a, b in zip(route, route[1:])) / profile.max_speed
        wall0, steps = time.time(), 0
        while sim.obs.state.t < limit:
            st = sim.obs.state
            d = sim.obs.sensors["range"]
            pose = {"x": float(st.pos[0]), "y": float(st.pos[1]), "yaw": float(st.yaw)}
            vel, w, status = follower.step(pose, {"right": float(d[0]), "front": float(d[len(d) // 2]), "left": float(d[-1])})
            if status.state in ("arrived", "done", "blocked"):
                break
            sim.step_physical([vel, w])
            steps += 1
            if steps % frame_every == 0:
                st = sim.obs.state
                pose = {"x": float(st.pos[0]), "y": float(st.pos[1]), "yaw": float(st.yaw)}
                rgb = sim.backend.render(cam)
                dets = det.detect(np.ascontiguousarray(rgb[:, :, ::-1]))
                tracker.update(float(st.t), pipe_of(pose), (pose["x"], pose["y"], pose["yaw"]), dets)
        route_edges = {m.edge_between(route[i], route[i + 1]).id for i in range(len(route) - 1)} if len(route) > 1 else set(m.edges)
        truth = [d for d in sim.task.defects if d["edge"] in route_edges]
        found = [d for d in tracker.defects if d.confirmed]
        return {"vehicle": v.name, "task": t.name, "map": m.name, "detector": getattr(det, "name", detector), "finished": status.state, "route": route,
                "skipped_goals": skipped, "steps": steps, "sim_seconds": round(float(sim.obs.state.t), 1), "wall_seconds": round(time.time() - wall0, 1),
                "planted": len(truth), "confirmed": len(found), "evaluation": evaluate_against_truth(found, truth)}
    finally:
        sim.close()


def main():
    ap = argparse.ArgumentParser(prog="python -m simlab.tasks.pipe_eval", description=__doc__.split("\n\n")[0])
    ap.add_argument("--vehicle", default="mid_4wd")
    ap.add_argument("--task", default="pipe_star")
    ap.add_argument("--detector", default="oracle", help="oracle | classical | rfdetr:<model dir>")
    ap.add_argument("--frame-every", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    print(json.dumps(inspect_all(a.vehicle, a.task, a.detector, a.frame_every, seed=a.seed), indent=1))


if __name__ == "__main__":
    main()
