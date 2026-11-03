"""A tiny MJCF writer: nested elements with numbers/vectors formatted the way MuJoCo parses them."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np


def fmt(v) -> str:
    if isinstance(v, (bool, np.bool_)):
        return "true" if v else "false"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return f"{float(v):.9g}"
    if isinstance(v, (tuple, list, np.ndarray)):
        return " ".join(fmt(x) for x in v)
    return str(v)


class X:
    """One XML element; add() returns the child so trees read top-down."""

    def __init__(self, el: ET.Element):
        self.el = el

    @classmethod
    def root(cls, tag: str, **attrs) -> "X":
        return cls(ET.Element(tag, {k: fmt(v) for k, v in attrs.items() if v is not None}))

    def add(self, tag: str, **attrs) -> "X":
        return X(ET.SubElement(self.el, tag, {k: fmt(v) for k, v in attrs.items() if v is not None}))

    def tostring(self) -> str:
        ET.indent(self.el)
        return ET.tostring(self.el, encoding="unicode")
