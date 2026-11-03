"""Procedural, tileable wall/floor textures for the sewer (no external assets, no licences)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

TILE_M = 1.0          # one texture tile covers 1 m x 1 m of wall
TEX_PX = 512          # => 512 px/m texel density; defect patches are baked at the same density

# name -> (roughness, description)
MATERIALS = {
    "concrete": 0.90,
    "brick": 0.85,
    "pvc": 0.35,
    "floor": 0.55,
}


def _periodic_noise(size: int, seed: int, beta: float = 2.0) -> np.ndarray:
    """1/f^beta noise, periodic by construction (FFT), normalised to 0..1."""
    rng = np.random.default_rng(seed)
    fx = np.fft.fftfreq(size)[:, None]
    fy = np.fft.fftfreq(size)[None, :]
    f = np.sqrt(fx ** 2 + fy ** 2)
    f[0, 0] = 1.0
    spec = (rng.normal(size=(size, size)) + 1j * rng.normal(size=(size, size))) / f ** (beta / 2)
    spec[0, 0] = 0
    n = np.real(np.fft.ifft2(spec))
    n -= n.min()
    return n / max(n.max(), 1e-9)


def _tint(base: tuple[int, int, int], n: np.ndarray, amp: float) -> np.ndarray:
    img = np.empty(n.shape + (3,), np.float32)
    for c in range(3):
        img[..., c] = base[c] * (1 - amp + 2 * amp * n)
    return img


def make_concrete(size: int = TEX_PX, seed: int = 1) -> np.ndarray:
    coarse, fine, pits = _periodic_noise(size, seed, 2.6), _periodic_noise(size, seed + 1, 1.2), _periodic_noise(size, seed + 2, 0.6)
    img = _tint((112, 110, 104), 0.6 * coarse + 0.4 * fine, 0.22)
    img *= (1 - 0.35 * (pits > 0.93))[..., None]                        # small pits
    stain = _periodic_noise(size, seed + 3, 3.2)
    img *= (1 - 0.30 * np.clip((stain - 0.55) * 3, 0, 1))[..., None]     # damp stains
    return np.clip(img, 0, 255).astype(np.uint8)


def make_brick(size: int = TEX_PX, seed: int = 4) -> np.ndarray:
    rows, cols = 8, 4
    h, w = size // rows, size // cols
    img = np.zeros((size, size, 3), np.float32)
    rng = np.random.default_rng(seed)
    noise = _periodic_noise(size, seed, 1.6)
    for r in range(rows):
        off = (w // 2) if r % 2 else 0
        for c in range(cols + 1):
            x0 = (c * w + off) % size
            tone = rng.uniform(0.75, 1.1)
            col = np.array([138, 78, 62], np.float32) * tone
            ys = slice(r * h, (r + 1) * h)
            xs = [(x0 + i) % size for i in range(w)]
            img[ys, xs] = col
    mortar = np.zeros((size, size), bool)
    for r in range(rows):
        mortar[r * h:r * h + 4, :] = True
    for r in range(rows):
        off = (w // 2) if r % 2 else 0
        for c in range(cols):
            x = (c * w + off) % size
            mortar[r * h:(r + 1) * h, x:x + 4] = True if x + 4 <= size else mortar[r * h:(r + 1) * h, x:x + 4]
    img[mortar] = (150, 146, 138)
    img *= (0.8 + 0.4 * noise)[..., None]
    return np.clip(img, 0, 255).astype(np.uint8)


def make_pvc(size: int = TEX_PX, seed: int = 7) -> np.ndarray:
    return np.clip(_tint((150, 146, 136), _periodic_noise(size, seed, 2.8), 0.07), 0, 255).astype(np.uint8)


def make_floor(size: int = TEX_PX, seed: int = 9) -> np.ndarray:
    n = 0.5 * _periodic_noise(size, seed, 2.2) + 0.5 * _periodic_noise(size, seed + 1, 1.0)
    img = _tint((72, 66, 56), n, 0.25)
    wet = _periodic_noise(size, seed + 2, 3.0) > 0.6
    img[wet] *= 0.7
    return np.clip(img, 0, 255).astype(np.uint8)


_MAKERS = {"concrete": make_concrete, "brick": make_brick, "pvc": make_pvc, "floor": make_floor}


def ensure_textures(tex_dir: Path) -> dict[str, np.ndarray]:
    """Write the base textures if missing; return them as RGB uint8 arrays."""
    tex_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, fn in _MAKERS.items():
        p = tex_dir / f"{name}.png"
        if not p.exists():
            cv2.imwrite(str(p), cv2.cvtColor(fn(), cv2.COLOR_RGB2BGR))
        out[name] = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
    return out
