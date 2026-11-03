"""The reality-gap engine: measure how a real rover differs from its simulation, say why, and work out the correction.

    protocol.py   short bounded motions on the rover (firmware-timed) and what to record
    diagnose.py   findings with evidence + confidence, the trim that cancels a weak motor (gyro only, no speed or distance needed)
    model.py      the twin's motion model (same law as the simulator's ground plant)
    calibrate.py  fit the twin to the measurements; predicted-vs-measured table
    engine.py     measure_gap(): all of it in one call, evidence saved under runs/rover/
"""
from .calibrate import apply_to_hardware, fit_twin
from .diagnose import Diagnosis, Finding, diagnose, explain, ratio_from_ruler
from .engine import GapResult, measure_gap
from .model import TwinModel
from .protocol import Dataset, ProtocolAborted, Runner, run_protocol

__all__ = ["Dataset", "Diagnosis", "Finding", "GapResult", "ProtocolAborted", "Runner", "TwinModel", "apply_to_hardware", "diagnose", "explain",
           "fit_twin", "measure_gap", "ratio_from_ruler", "run_protocol"]
