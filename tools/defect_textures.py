"""Procedural crack / leak generation, cropped tight and baked onto the wall texture beneath.

Baking (instead of alpha-blending a decal) keeps the patch seamless and makes the patch quad
equal to the defect's tight bounding box, which doubles as perfect ground truth for training.

Patch image axes: x <-> perimeter coordinate t, y <-> pipe axis coordinate s.
Mesh UV convention (identical for wall and patch): U = t / TILE_M, V = s / TILE_M, V pointing UP the image.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from sewerbot.core.defects import DefectSpec
from .materials import MATERIALS, TEX_PX, TILE_M

MAX_PX = 1024
V_UP = True   # image row 0 is the TOP (highest V). Verified against Webots in the render check.


@dataclass
class DefectAsset:
    albedo: np.ndarray            # HxWx3 uint8 (RGB), wall texture with the defect baked in
    rough: np.ndarray             # HxW uint8 roughness map
    size: tuple[float, float]     # tight (t extent, s extent) in metres
    center_off: tuple[float, float]  # shift of the tight crop centre vs the requested centre (dt, ds) metres
    coverage: float               # fraction of the patch that is actual defect


def _smooth(a: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(a, (0, 0), sigma)


def _lowfreq(shape, rng, scale):
    h, w = shape
    small = rng.random((max(2, int(h / scale)), max(2, int(w / scale)))).astype(np.float32)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)


# ------------------------------------------------------------------ cracks
def _walk(mask, p0, ang, length, w0, rng, p_branch, depth=0):
    x, y = p0
    w, done = w0, 0.0
    while done < length:
        step = float(rng.uniform(3, 7))
        ang += float(rng.normal(0, 0.10 + 0.05 * depth))
        nx, ny = x + step * math.cos(ang), y + step * math.sin(ang)
        cv2.line(mask, (int(x), int(y)), (int(nx), int(ny)), 1.0, thickness=max(1, int(round(w))), lineType=cv2.LINE_AA)
        if depth < 2 and rng.random() < p_branch:
            side = 1 if rng.random() < 0.5 else -1
            _walk(mask, (nx, ny), ang + side * float(rng.uniform(0.4, 0.9)), length * float(rng.uniform(0.2, 0.45)),
                  w * 0.65, rng, p_branch * 0.5, depth + 1)
        x, y, done, w = nx, ny, done + step, max(1.0, w * 0.994)


def _crack_field(spec: DefectSpec, W: int, H: int, rng) -> tuple[np.ndarray, np.ndarray]:
    mask = np.zeros((H, W), np.float32)
    w0 = 1.4 + 4.6 * spec.severity
    sub = spec.subtype
    if sub == "longitudinal":
        _walk(mask, (W * float(rng.uniform(0.35, 0.65)), 0), math.pi / 2, H * 1.1, w0, rng, 0.006)
    elif sub == "circumferential":
        _walk(mask, (0, H * float(rng.uniform(0.35, 0.65))), 0.0, W * 1.1, w0, rng, 0.006)
    elif sub == "hairline":
        a = float(rng.uniform(0, math.pi))
        _walk(mask, (W * 0.2, H * 0.2), a, 0.8 * min(W, H), max(1.0, w0 * 0.4), rng, 0.01)
    else:  # branching
        a = float(rng.uniform(0.3, 1.2))
        _walk(mask, (W * 0.1, H * 0.1), a, 0.9 * math.hypot(W, H) * 0.6, w0, rng, 0.05)
    core = _smooth(mask, 0.7)
    halo = _smooth(mask, 2.0 + w0)
    return np.clip(core, 0, 1), np.clip(halo * 0.9, 0, 1)


# ------------------------------------------------------------------ leaks
def _leak_field(spec: DefectSpec, W: int, H: int, rng, down: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (wet, ring, drop) in 0..1 with gravity pointing to +x (flipped afterwards if down<0)."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    nx, ny = xx / W, yy / H
    sev = spec.severity
    noise = _lowfreq((H, W), rng, max(8, min(W, H) / 6))
    drop = np.zeros((H, W), np.float32)
    if spec.subtype == "seep":
        r = np.sqrt(((nx - 0.5) / 0.46) ** 2 + ((ny - 0.5) / 0.46) ** 2)
        field = np.clip(1.15 - r + 0.55 * (noise - 0.5), 0, 1)
        wet = np.clip(field * 3, 0, 1)
    elif spec.subtype == "streak":
        yc = 0.5 + 0.12 * np.sin(nx * 5 + rng.uniform(0, 6)) + 0.15 * (noise - 0.5)
        sig = (0.07 + 0.07 * nx) * (0.7 + 0.8 * sev)
        wet = np.exp(-((ny - yc) ** 2) / (2 * sig ** 2)) * np.clip(nx * 6, 0, 1) * (1 - 0.25 * nx)
    elif spec.subtype == "drip":
        yc = 0.5 + 0.05 * np.sin(nx * 9 + rng.uniform(0, 6))
        wet = 1.0 * np.exp(-((ny - yc) ** 2) / (2 * 0.05 ** 2)) * np.clip(nx * 8, 0, 1) * (nx < 0.84)
        cx, cy = 0.88, 0.5
        drop = np.exp(-(((nx - cx) / 0.04) ** 2 + ((ny - cy) / 0.06) ** 2))
        wet = np.maximum(wet, drop * 0.9)
    else:  # flow
        edge = 0.2 + 0.15 * (noise - 0.5)
        band = np.clip((ny - edge) * 14, 0, 1) * np.clip(((1 - edge) - ny) * 14, 0, 1)
        wet = band * np.clip(nx * 5, 0, 1)
        wet *= 0.8 + 0.2 * np.sin(nx * 30 + noise * 6)
    wet = np.clip(_smooth(wet.astype(np.float32), 1.5), 0, 1)
    ring = np.clip(1 - np.abs(wet - 0.18) / 0.12, 0, 1) * (spec.subtype in ("seep", "streak"))
    if down < 0:
        wet, ring, drop = wet[:, ::-1], ring[:, ::-1], drop[:, ::-1]
    return np.ascontiguousarray(wet), np.ascontiguousarray(ring.astype(np.float32)), np.ascontiguousarray(drop)


def _sample_base(tex: np.ndarray, t_lo: float, s_hi: float, W: int, H: int, ppm: int) -> np.ndarray:
    """Sample the wall texture exactly as the mesh UVs do (nearest texel, wrapping)."""
    th, tw = tex.shape[:2]
    cols = (np.floor(((t_lo + (np.arange(W) + 0.5) / ppm) / TILE_M % 1.0) * tw)).astype(int) % tw
    ss = s_hi - (np.arange(H) + 0.5) / ppm
    v = (ss / TILE_M) % 1.0
    rows = (np.floor((1.0 - v if V_UP else v) * th)).astype(int) % th
    return tex[np.ix_(rows, cols)].astype(np.float32)


def make_defect_asset(spec: DefectSpec, base_tex: np.ndarray, material: str, t_center: float, s_center: float,
                      down: int = 1, ppm: int = TEX_PX) -> DefectAsset:
    rng = np.random.default_rng(spec.seed + 1000)
    W = int(np.clip(round(spec.size[0] * ppm), 32, MAX_PX))
    H = int(np.clip(round(spec.size[1] * ppm), 32, MAX_PX))
    if spec.type == "crack":
        core, halo = _crack_field(spec, W, H, rng)
        content = core
        ring = np.zeros_like(core)
        drop = np.zeros_like(core)
        wet = np.zeros_like(core)
    else:
        wet, ring, drop = _leak_field(spec, W, H, rng, down)
        core = np.zeros_like(wet)
        halo = np.zeros_like(wet)
        content = wet

    ys, xs = np.where(content > (0.12 if spec.type == "leak" else 0.10))
    if len(xs) == 0:      # degenerate draw: keep the whole canvas
        x0, x1, y0, y1 = 0, W, 0, H
    else:
        pad = 6
        x0, x1 = max(0, xs.min() - pad), min(W, xs.max() + 1 + pad)
        y0, y1 = max(0, ys.min() - pad), min(H, ys.max() + 1 + pad)
    sl = (slice(y0, y1), slice(x0, x1))
    core, halo, wet, ring, drop = core[sl], halo[sl], wet[sl], ring[sl], drop[sl]
    h, w = core.shape

    # shift of crop centre relative to the requested patch centre; canvas row 0 is the TOP (high s)
    dx_m = ((x0 + x1) / 2 - W / 2) / ppm
    dy_m = -(((y0 + y1) / 2) - H / 2) / ppm
    t_c, s_c = t_center + dx_m, s_center + dy_m
    base = _sample_base(base_tex, t_c - w / ppm / 2, s_c + h / ppm / 2, w, h, ppm)
    out = base.copy()
    rough = np.full((h, w), MATERIALS[material] * 255, np.float32)

    if spec.type == "crack":
        dark = np.array([20, 18, 16], np.float32)
        out = out * (1 - 0.30 * halo[..., None])                   # shadow / damp edge
        out = out * (1 - core[..., None]) + dark * core[..., None]
        rough = rough * (1 - 0.15 * core)
    else:
        wetk = (wet * (0.60 + 0.35 * spec.severity))[..., None]
        tint = np.array([0.62, 0.70, 0.74], np.float32)
        algae = np.array([34, 48, 28], np.float32)
        out = out * (1 - wetk) + (out * tint) * wetk * 0.9 + algae * wetk * 0.15
        out = out * (1 - 0.2 * ring[..., None]) + np.array([200, 196, 186], np.float32) * 0.2 * ring[..., None]  # faint efflorescence
        out = out * (1 - 0.45 * drop[..., None]) + np.array([185, 200, 210], np.float32) * 0.45 * drop[..., None]
        rough = rough * (1 - 0.8 * wet)
        rough = np.where(drop > 0.5, 12.0, rough)
    albedo = np.clip(out, 0, 255).astype(np.uint8)
    cover = float((content[sl] > 0.1).mean())
    return DefectAsset(albedo, np.clip(rough, 0, 255).astype(np.uint8), (w / ppm, h / ppm), (dx_m, dy_m), cover)
