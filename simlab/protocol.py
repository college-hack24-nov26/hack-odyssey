"""Wire protocol between a vehicle body (simulator adapter or real hardware) and the brain.

Frame = 4-byte big-endian header length | JSON header | binary payload (header["n"] bytes, e.g. a JPEG).

    body -> brain : {"t": "hello", "proto": 2, "caps": {...}, ...}   once, on connect
                    {"t": "obs", ...} (+ JPEG payload)                 every camera frame
    brain -> body : {"t": "cmd", "space": "unicycle", "u": {"v": m/s, "w": rad/s}, "v": m/s, "w": rad/s}
                    {"t": "quit"}

Protocol 1 sent only {"t": "cmd", "v", "w"}. Version 2 names a command space and carries its channels in "u"; unicycle
commands still repeat v and w at the top level, so an older peer keeps working in both directions.
"""
from __future__ import annotations

import json
import math
import struct
from typing import Iterable, Mapping

PROTOCOL_VERSION = 2
HOST = "127.0.0.1"
PORT = 8770
MAX_HEADER = 1 << 20
MAX_PAYLOAD = 64 << 20


def pack(header: dict, payload: bytes = b"") -> bytes:
    h = dict(header)
    h["n"] = len(payload)
    hb = json.dumps(h, separators=(",", ":")).encode()
    return struct.pack(">I", len(hb)) + hb + payload


class Reader:
    """Incremental decoder: feed() raw bytes, get complete (header, payload) messages back."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[dict, bytes]]:
        self._buf += data
        out = []
        while True:
            if len(self._buf) < 4:
                break
            (hl,) = struct.unpack(">I", self._buf[:4])
            if hl > MAX_HEADER:
                raise ValueError("protocol error: oversized header")
            if len(self._buf) < 4 + hl:
                break
            header = json.loads(bytes(self._buf[4:4 + hl]))
            n = int(header.get("n", 0))
            if n < 0 or n > MAX_PAYLOAD:
                raise ValueError("protocol error: bad payload length")
            if len(self._buf) < 4 + hl + n:
                break
            payload = bytes(self._buf[4 + hl:4 + hl + n])
            del self._buf[:4 + hl + n]
            out.append((header, payload))
        return out


def _finite(x) -> float:
    x = float(x)
    return x if math.isfinite(x) else 0.0


def cmd_header(space: str, values: Mapping[str, float]) -> dict:
    """A command in a named space; unicycle commands also carry the protocol-1 v / w fields."""
    u = {str(k): _finite(v) for k, v in values.items()}
    h = {"t": "cmd", "space": space, "u": u}
    if space == "unicycle":
        h["v"], h["w"] = u.get("v", 0.0), u.get("w", 0.0)
    return h


def parse_cmd(header: Mapping) -> tuple[str, dict[str, float]]:
    """(space, channel values) from a protocol 1 or 2 command."""
    if isinstance(header.get("u"), Mapping):
        return str(header.get("space", "unicycle")), {str(k): _finite(v) for k, v in header["u"].items()}
    return "unicycle", {"v": _finite(header.get("v", 0.0)), "w": _finite(header.get("w", 0.0))}


def hello_caps(spaces: Iterable[str], sensors: Iterable[str], ground_truth: Iterable[str] = (), **extra) -> dict:
    """The 'caps' block of a hello: what this body accepts and reports."""
    return {"spaces": list(spaces), "sensors": list(sensors), "gt": list(ground_truth), **extra}
