"""One call for the whole gap test: run the protocol, diagnose, fit the twin, and keep the evidence (runs/rover/<time>/gap.json)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .calibrate import fit_twin
from .diagnose import Diagnosis, diagnose, explain
from .model import TwinModel
from .protocol import Dataset, run_protocol

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class GapResult:
    dataset: Dataset
    diagnosis: Diagnosis
    twin: TwinModel
    table: list[dict]
    folder: str = ""
    environment: str = ""

    def lines(self) -> list[str]:
        return explain(self.diagnosis)

    def to_dict(self) -> dict:
        return {"environment": self.environment, "diagnosis": self.diagnosis.to_dict(), "table": self.table,
                "twin": {"side_gain": list(self.twin.side_gain), "yaw_gain": self.twin.yaw_gain, "start_duty": list(self.twin.start_duty), "trim": list(self.twin.trim)},
                "dataset": self.dataset.to_dict()}


def save(result: GapResult, out_dir: Path | None = None, label: str = "gap") -> str:
    folder = (out_dir or ROOT / "runs" / "rover") / time.strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{label}.json").write_text(json.dumps(result.to_dict(), indent=1, default=str), encoding="utf-8")
    (folder / f"{label}.txt").write_text("\n".join(result.lines()) + "\n", encoding="utf-8")
    return str(folder)


def measure_gap(rover, environment: str = "", out_dir: Path | None = None, ruler: dict | None = None, label: str = "gap", **protocol_kw) -> GapResult:
    """Run the tests on `rover` (a SerialRover on a real or emulated board), diagnose, fit. The caller supplies `guard`/`abort`/`progress` in protocol_kw."""
    ds = run_protocol(rover, **protocol_kw)
    diag = diagnose(ds, ruler)
    twin, table = fit_twin(ds, diag)
    res = GapResult(ds, diag, twin, table, environment=environment)
    res.folder = save(res, out_dir, label)
    return res
