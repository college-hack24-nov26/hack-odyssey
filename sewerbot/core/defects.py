"""Defect vocabulary shared by the map generator, the dataset builder and the detectors."""
from __future__ import annotations

from dataclasses import dataclass, field

CLASSES = ("crack", "leak")            # detector classes (index = YOLO/COCO category id)
CRACK_SUBTYPES = ("hairline", "longitudinal", "circumferential", "branching")
LEAK_SUBTYPES = ("seep", "streak", "drip", "flow")
SUBTYPES = {"crack": CRACK_SUBTYPES, "leak": LEAK_SUBTYPES}


@dataclass
class DefectSpec:
    id: str
    edge: str
    s: float                      # metres from the `from` node centre along the pipe axis
    angle: float                  # degrees around the section: 0 right, 90 crown, 180 left, 270 floor
    type: str = "crack"           # crack | leak
    subtype: str = "longitudinal"
    size: tuple[float, float] = (0.30, 0.30)   # (extent around the wall, extent along the pipe), metres
    seed: int = 0
    severity: float = 0.5         # 0..1, drives crack width / leak wetness

    def validate(self) -> "DefectSpec":
        if self.type not in CLASSES:
            raise ValueError(f"defect type must be one of {CLASSES}")
        if self.subtype not in SUBTYPES[self.type]:
            raise ValueError(f"{self.type} subtype must be one of {SUBTYPES[self.type]}")
        return self
