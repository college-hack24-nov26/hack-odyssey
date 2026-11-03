"""Training-free baseline: ridge filter for cracks, darker-and-cooler regions for leaks.

Works on day one (no GPU, no data), is the fallback when no trained model is present and the sanity baseline the
learned detectors must beat. Thresholds are tuned on simulator frames; real footage needs re-tuning (see docs).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .base import Detection, nms


@dataclass
class ClassicalParams:
    work_width: int = 480
    crack_scales: tuple = (1.8, 2.6, 3.6)
    crack_ridge_thr: float = 0.060        # relative Hessian response (dark line); strong seeds need 3x this
    crack_dark_ratio: float = 0.90        # pixel must be darker than its neighbourhood
    crack_min_len: float = 0.06           # skeleton length, fraction of image width
    crack_max_width: float = 0.020        # thicker dark bands (pipe joints) are not cracks
    joint_span: float = 0.55              # components spanning this fraction of the frame are joints/shadows
    leak_dark_ratio: float = 0.84
    leak_cool_shift: float = 1.5          # Lab b* must drop this far below the neighbourhood
    leak_min_area: float = 0.0025         # fraction of the frame
    leak_max_area: float = 0.35


class ClassicalDetector:
    name = "classical"

    def __init__(self, params: ClassicalParams | None = None):
        self.p = params or ClassicalParams()

    # ---------------------------------------------------------------- helpers
    def _ridge(self, gray: np.ndarray) -> np.ndarray:
        """Dark-ridge strength: largest positive Hessian eigenvalue, scale-normalised, divided by local brightness."""
        g = cv2.fastNlMeansDenoising(gray, None, 9, 5, 15).astype(np.float32) / 255.0
        local = cv2.GaussianBlur(g, (0, 0), 10) + 0.02
        n = g / local
        best = np.zeros_like(n)
        for s in self.p.crack_scales:
            b = cv2.GaussianBlur(n, (0, 0), s)
            hxx = cv2.Sobel(b, cv2.CV_32F, 2, 0, ksize=3)
            hyy = cv2.Sobel(b, cv2.CV_32F, 0, 2, ksize=3)
            hxy = cv2.Sobel(b, cv2.CV_32F, 1, 1, ksize=3)
            tmp = np.sqrt(((hxx - hyy) * 0.5) ** 2 + hxy ** 2)
            lam_max = (hxx + hyy) * 0.5 + tmp          # strongly positive across a dark line
            lam_min = (hxx + hyy) * 0.5 - tmp
            resp = np.clip(lam_max, 0, None) * s * s
            resp *= (np.abs(lam_min) < 0.5 * lam_max)   # line-like, not blob-like
            best = np.maximum(best, resp)
        return best

    # ---------------------------------------------------------------- cracks
    def _cracks(self, bgr: np.ndarray, gray: np.ndarray) -> list[Detection]:
        p = self.p
        H, W = gray.shape
        ridge = self._ridge(gray)
        g = gray.astype(np.float32)
        dark = g / (cv2.GaussianBlur(g, (0, 0), 8) + 1.0) < p.crack_dark_ratio
        weak = ((ridge > p.crack_ridge_thr) & dark & (g > 14)).astype(np.uint8)
        weak = cv2.morphologyEx(weak, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        strong = ((ridge > 3 * p.crack_ridge_thr) & (weak > 0))
        n, lab, stats, _ = cv2.connectedComponentsWithStats(weak, connectivity=8)
        seeded = set(np.unique(lab[strong]).tolist())
        out = []
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < 20 or i not in seeded:
                continue
            comp = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
            length = float(area) / max(1.0, np.mean(cv2.distanceTransform(comp, cv2.DIST_L2, 3)[comp > 0]) * 2)
            thickness = area / max(length, 1.0)
            if length < p.crack_min_len * W or thickness > p.crack_max_width * W:
                continue
            if max(w / W, h / H) > p.joint_span:          # long arcs spanning the frame = pipe joints / edges
                continue
            fill = area / float(w * h)
            if fill > 0.55 and min(w, h) > 0.05 * W:      # a filled blob is not a crack
                continue
            conf = float(np.clip(ridge[y:y + h, x:x + w][comp > 0].mean() / (3 * p.crack_ridge_thr), 0.05, 0.99))
            out.append(Detection("crack", conf, (int(x), int(y), int(w), int(h))))
        return out

    # ---------------------------------------------------------------- leaks
    def _leaks(self, bgr: np.ndarray, gray: np.ndarray) -> list[Detection]:
        p = self.p
        H, W = gray.shape
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
        L, b = lab[..., 0], lab[..., 2]
        Lbg = cv2.GaussianBlur(L, (0, 0), H / 6.0) + 1.0
        bbg = cv2.GaussianBlur(b, (0, 0), H / 6.0)
        texture = cv2.GaussianBlur(np.abs(L - cv2.GaussianBlur(L, (0, 0), 2.0)), (0, 0), 3.0)
        mask = ((L / Lbg < p.leak_dark_ratio) & (bbg - b > p.leak_cool_shift) & (texture < 4.0) & (L > 22)).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        n, labm, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        out = []
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            frac = area / float(W * H)
            if frac < p.leak_min_area or frac > p.leak_max_area:
                continue
            if area / float(w * h) < 0.30:
                continue
            reg = labm[y:y + h, x:x + w] == i
            darkness = 1.0 - float((L[y:y + h, x:x + w][reg] / Lbg[y:y + h, x:x + w][reg]).mean())
            out.append(Detection("leak", float(np.clip(darkness * 2.2, 0.05, 0.95)), (int(x), int(y), int(w), int(h))))
        return out

    # ---------------------------------------------------------------- API
    def detect(self, bgr: np.ndarray) -> list[Detection]:
        H0, W0 = bgr.shape[:2]
        scale = self.p.work_width / float(W0) if W0 > self.p.work_width else 1.0
        img = cv2.resize(bgr, (int(W0 * scale), int(H0 * scale)), interpolation=cv2.INTER_AREA) if scale != 1.0 else bgr
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        dets = self._cracks(img, gray) + self._leaks(img, gray)
        if scale != 1.0:
            for d in dets:
                x, y, w, h = d.box
                d.box = (int(x / scale), int(y / scale), int(w / scale), int(h / scale))
        return nms(dets, 0.4)
