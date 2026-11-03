"""Camera-pipeline helpers shared by the simulator adapter and tests."""
from __future__ import annotations

import numpy as np

_GAMMA = 2.2
_TARGET = 0.46 ** _GAMMA      # linear-light target for the 80th-percentile pixel
_LUT_IN = np.arange(256, dtype=np.float32) / 255.0
_TO_LIN = _LUT_IN ** _GAMMA


def auto_expose(bgr: np.ndarray, percentile: float = 80.0, min_gain: float = 0.6, max_gain: float = 10.0) -> np.ndarray:
    """Emulate a sensor's AEC/AGC: scale linear light so the `percentile`-th pixel reaches a fixed level.

    Real ESP32-CAM/OV2640 modules do this on-chip, so detectors must see exposure-normalised frames.
    Noise is amplified together with the signal, exactly as with analogue gain on a real sensor.
    """
    lin = _TO_LIN[bgr]
    ref = float(np.percentile(lin[::4, ::4], percentile))
    gain = float(np.clip(_TARGET / max(ref, 1e-5), min_gain, max_gain))
    out = np.clip(lin * gain, 0.0, 1.0) ** (1.0 / _GAMMA)
    return (out * 255.0 + 0.5).astype(np.uint8)
