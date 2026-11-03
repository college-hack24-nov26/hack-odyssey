"""Missions: what the rover is asked to do with the objects around it (follow a bag, orbit it, go bag to bag, stop at the box).

targets  the objects: photos + real size -> a detector (config/targets/<id>/)
steps    a mission as data: a list of steps from a closed vocabulary, validated
runner   turns camera frames + gyro heading into bounded `D left right ms` drive commands, one tick at a time
sim      drives the runner inside the MuJoCo simulator (the same runner will drive the real rover)

Only targets, steps and runner are needed on the rover side; `sim` pulls in the simulator.
"""
from .runner import CameraModel, Drive, DriveModel, MissionRunner, Obs
from .steps import Mission, MissionError, STEP_OPS, step
from .targets import STANDINS, Target, TargetError, TargetRegistry, hit_rate

__all__ = ["CameraModel", "Drive", "DriveModel", "Mission", "MissionError", "MissionRunner", "Obs", "STANDINS", "STEP_OPS", "Target",
           "TargetError", "TargetRegistry", "hit_rate", "step"]
