"""Targets: the things a mission is about (a bag, a box ...), each learned from a few photos plus its real size.

A target is data: an id, a role (follow | waypoint | goal), its real size in cm, and a folder of 3-10 photos at
`config/targets/<id>/` next to a `target.json`. Adding another object is dropping photos in a folder and saying how big
it is: no code changes. The detector is a colour + shape model learned from those photos:

  * colour: which quantised hue/saturation bins stand out in the *middle* of the photos compared with their edges;
  * shape: the aspect ratio and fill of the blob those bins form (so a lookalike colour of the wrong shape scores low).

It works on the 96x96 frames the ESP32-CAM sends, is a few milliseconds per frame on the laptop, and is tested on the
photos it was learned from (`hit_rate`). Until real photos arrive, `STANDINS` generates coloured stand-in objects, both as
photos here and as objects in the simulator (`simlab/tasks/bag_mission.py`).
"""
from __future__ import annotations

import functools
import json
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TARGETS_DIR = ROOT / "config" / "targets"
ROLES = ("follow", "waypoint", "goal")
SHAPES = ("cylinder", "box")
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
FRAME = 96                      # the rover camera's frame size; photos are shrunk to this before anything is learned or detected
MIN_PHOTOS = 3


class TargetError(ValueError):
    """A target that cannot be built (too few photos, no colour that stands out, a bad id ...), in plain words."""


# ------------------------------------------------------------------------------------------------ camera
@dataclass(frozen=True)
class CameraModel:
    """Pinhole model of the rover camera: turns blob positions and sizes into bearings and distances."""
    width: int = FRAME
    height: int = FRAME
    fov_deg: float = 62.0               # horizontal

    @property
    def focal_px(self) -> float:
        return (self.width / 2) / math.tan(math.radians(self.fov_deg) / 2)

    def bearing(self, cx: float) -> float:
        """Direction of image column `cx` from the optical axis, radians, positive = to the left (counter-clockwise, like yaw)."""
        return -math.atan2(cx - self.width / 2, self.focal_px)

    def distance(self, size_m: float, size_px: float) -> float:
        return self.focal_px * size_m / max(size_px, 1e-6)

    def max_range(self, size_m: float, min_px: float) -> float:
        """Farthest distance at which an object of `size_m` is still `min_px` pixels across."""
        return self.focal_px * size_m / max(min_px, 1e-6)


# ------------------------------------------------------------------------------------------------ images
def read_image(src) -> np.ndarray:
    """RGB uint8 from a path or an array (arrays are taken as RGB already)."""
    if isinstance(src, (str, Path)):
        img = cv2.imread(str(src), cv2.IMREAD_COLOR)
        if img is None:
            raise TargetError(f"cannot read the image {src}")
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    a = np.asarray(src)
    if a.ndim != 3 or a.shape[2] != 3:
        raise TargetError("an image must be height x width x 3 (RGB)")
    return a.astype(np.uint8)


def to_frame(img: np.ndarray, size: int = FRAME) -> np.ndarray:
    """Shrink so the longest side is `size` (keeps the aspect ratio; never enlarges): what the rover camera would deliver."""
    h, w = img.shape[:2]
    k = size / max(h, w)
    if k >= 1.0:
        return img
    return cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)


# ------------------------------------------------------------------------------------------------ colour bins
H_BINS = 24                         # hue bins of 7.5 OpenCV units (15 degrees)
N_ACHRO = 4                         # grey bins by brightness; below SAT_MIN a pixel has no reliable hue
N_BINS = N_ACHRO + H_BINS * 2       # chromatic bins: hue x (moderate | vivid saturation)
SAT_MIN, SAT_VIVID, VAL_MIN = 50, 150, 40


def quantise(rgb: np.ndarray) -> np.ndarray:
    """Bin index per pixel: a brightness bin for greys, a hue x saturation bin for colours."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.int32)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    chroma = (s >= SAT_MIN) & (v >= VAL_MIN)
    colour = N_ACHRO + (h * H_BINS // 180) * 2 + (s >= SAT_VIVID)
    return np.where(chroma, colour, v * N_ACHRO // 256)


def _neighbours(b: int) -> list[int]:
    """The hue bins next to chromatic bin `b` (same saturation class); a grey bin has none."""
    if b < N_ACHRO:
        return []
    hb, sb = divmod(b - N_ACHRO, 2)
    return [N_ACHRO + ((hb + d) % H_BINS) * 2 + sb for d in (-1, 1)]


# ------------------------------------------------------------------------------------------------ detection
@dataclass
class Blob:
    """One candidate region in a frame, in pixels."""
    x: int
    y: int
    w: int
    h: int
    area: int
    cx: float
    cy: float
    fill: float                  # area / bounding-box area
    score: float                 # 0..1: how well colour and shape match what was learned
    clipped: bool                # touches the frame edge, so its size is only a lower bound


def _soft_range(x: float, lo: float, hi: float) -> float:
    """1 inside [lo, hi], falling to 0 as x leaves it (in log steps for ratios)."""
    if lo <= x <= hi:
        return 1.0
    miss = math.log(lo / max(x, 1e-6)) if x < lo else math.log(max(x, 1e-6) / hi)
    return max(0.0, 1.0 - miss / math.log(2.0))           # a factor of 2 outside the range scores 0


class ColourShapeDetector:
    """Learned from photos (`fit`), serialisable as plain JSON (`to_dict` / `from_dict`)."""

    MIN_AREA = 5                 # pixels: a speck smaller than this is noise

    def __init__(self, bins: Iterable[int], aspect: tuple[float, float], fill: tuple[float, float],
                 mean_rgb: tuple[int, int, int], n_photos: int = 0, min_score: float = 0.4):
        self.bins = sorted({int(b) for b in bins})
        self.aspect, self.fill = (float(aspect[0]), float(aspect[1])), (float(fill[0]), float(fill[1]))
        self.mean_rgb = tuple(int(c) for c in mean_rgb)
        self.n_photos, self.min_score = int(n_photos), float(min_score)
        self._lut = np.zeros(N_BINS, bool)
        self._lut[self.bins] = True

    # ---- learning
    @classmethod
    def fit(cls, photos: Sequence) -> "ColourShapeDetector":
        """Learn from 3 or more photos in which the object sits in the middle of the picture and fills a good part of it."""
        if len(photos) < MIN_PHOTOS:
            raise TargetError(f"needs at least {MIN_PHOTOS} photos to learn an object, got {len(photos)} (3-10 from different sides work best)")
        frames = [to_frame(read_image(p)) for p in photos]
        fg, bg = np.zeros((len(frames), N_BINS)), np.zeros((len(frames), N_BINS))
        for i, f in enumerate(frames):
            q = quantise(cv2.GaussianBlur(f, (3, 3), 0))
            h, w = q.shape
            centre = np.zeros(q.shape, bool)
            centre[h // 4:3 * h // 4, w // 4:3 * w // 4] = True
            inner = np.zeros(q.shape, bool)
            inner[round(0.12 * h):round(0.88 * h), round(0.12 * w):round(0.88 * w)] = True
            fg[i] = np.bincount(q[centre], minlength=N_BINS) / centre.sum()
            bg[i] = np.bincount(q[~inner], minlength=N_BINS) / max((~inner).sum(), 1)
        mean_fg, mean_bg = fg.mean(0), bg.mean(0)
        ratio = (mean_fg + 0.004) / (mean_bg + 0.004)
        support = (fg >= 0.02).mean(0)                    # share of photos in which this colour is clearly present in the middle
        chosen = {b for b in range(N_BINS) if mean_fg[b] >= 0.04 and ratio[b] >= 2.5 and support[b] >= 0.5}
        if not chosen:
            raise TargetError("no colour stands out in the middle of these photos: centre the object, fill about half the picture, "
                              "and use a plain background")
        for b in list(chosen):                            # lighting shifts a colour into the next hue bin
            chosen.update(n for n in _neighbours(b) if mean_fg[n] >= 0.01 and ratio[n] >= 1.5)
        lut = np.zeros(N_BINS, bool)
        lut[sorted(chosen)] = True
        aspects, fills, colours = [], [], []
        for f in frames:
            blob, mask = _best_blob(lut[quantise(f)], prefer_centre=True)
            if blob is None:
                continue
            aspects.append(blob.w / blob.h)
            fills.append(blob.fill)
            colours.append(f[mask].mean(0))
        if len(aspects) < max(2, len(frames) // 2):
            raise TargetError("the learned colour does not form one clear object in most photos: try plainer backgrounds")
        return cls(chosen, (0.6 * min(aspects), 1.6 * max(aspects)), (0.6 * min(fills), 1.0),
                   tuple(np.mean(colours, 0).round()), len(frames))

    # ---- detecting
    def mask(self, frame: np.ndarray) -> np.ndarray:
        return self._lut[quantise(frame)]

    def detect(self, frame: np.ndarray) -> Blob | None:
        """The best-matching region of `frame` (RGB), or None."""
        blob, _ = _best_blob(self.mask(frame), detector=self)
        return blob if blob is not None and blob.score >= self.min_score else None

    def shape_score(self, w: int, h: int, fill: float, cut_h: bool = False, cut_v: bool = False) -> float:
        """0..1: how much a region looks like the learned object. A side cut off by the frame edge is only a lower bound for that
        dimension, so it can rule a region out (a thin strip across the whole frame) but never count against a big close-up."""
        ratio = w / max(h, 1)
        if cut_h and cut_v:
            asp = 1.0                                      # cut on both axes: nothing to judge
        elif cut_h:
            asp = _soft_range(ratio, 1e-6, self.aspect[1])     # true width >= seen width: the ratio can only be larger than seen
        elif cut_v:
            asp = _soft_range(ratio, self.aspect[0], 1e6)      # true height >= seen height: the ratio can only be smaller than seen
        else:
            asp = _soft_range(ratio, *self.aspect)
        fil = 0.7 if (cut_h or cut_v) else _soft_range(fill, *self.fill)
        return math.sqrt(asp * fil)

    # ---- serialisation
    def to_dict(self) -> dict:
        return {"type": "colour_shape", "bins": self.bins, "aspect": list(self.aspect), "fill": list(self.fill),
                "mean_rgb": list(self.mean_rgb), "n_photos": self.n_photos, "min_score": self.min_score}

    @classmethod
    def from_dict(cls, d: dict) -> "ColourShapeDetector":
        if d.get("type") != "colour_shape":
            raise TargetError(f"unknown detector type {d.get('type')!r}")
        return cls(d["bins"], d["aspect"], d["fill"], d["mean_rgb"], d.get("n_photos", 0), d.get("min_score", 0.4))


_KERNEL = np.ones((5, 3), np.uint8)             # 5 rows x 3 columns: glues gaps of up to 4 pixels top to bottom (a tape line, a cable, the rover's own edge)


def _best_blob(mask: np.ndarray, *, detector: ColourShapeDetector | None = None, prefer_centre: bool = False):
    """(Blob, its pixel mask) for the best connected region of a boolean mask, or (None, None)."""
    m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, _KERNEL)       # shading and thin occluders split an object into pieces: glue them
    n, labels, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
    H, W = m.shape
    best, best_key = None, -1.0
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < ColourShapeDetector.MIN_AREA:
            continue
        cut_h, cut_v = x == 0 or x + w >= W, y == 0 or y + h >= H
        clipped = cut_h or cut_v
        fill = area / (w * h)
        if detector is None:                                  # learning: the region around the middle of the photo wins
            cx, cy = cents[i]
            score = 1.0 - math.hypot(cx / W - 0.5, cy / H - 0.5)
        else:
            score = detector.shape_score(w, h, fill, cut_h, cut_v)
        key = score * math.sqrt(area)
        if key > best_key:
            best_key, best = key, Blob(x, y, w, h, area, float(cents[i][0]), float(cents[i][1]), fill, min(max(score, 0.0), 1.0), clipped)
            best_label = i
    if best is None:
        return None, None
    return best, labels == best_label


# ------------------------------------------------------------------------------------------------ the target
@dataclass
class Sighting:
    """What the rover knows about a target from one frame."""
    target: str
    bearing: float               # rad, positive = target is to the left
    distance: float              # m from the camera to the near surface of the target
    score: float
    blob: Blob
    bound: bool = False          # True: the target is cut off by the frame edge, so `distance` is an upper bound


@dataclass
class Target:
    id: str
    name: str
    role: str                    # follow | waypoint | goal
    width_cm: float              # real horizontal size
    height_cm: float             # real vertical size
    shape: str = "cylinder"      # how the simulator draws it: cylinder | box
    detector: ColourShapeDetector | None = None
    folder: Path | None = None
    source: str = "user"         # user | standin
    hits: dict = field(default_factory=dict)     # hit-rate on its own photos, written when it was learned

    # ---- checks
    def problems(self) -> list[str]:
        out = []
        if not ID_RE.fullmatch(self.id):
            out.append("id: use letters, digits, _ or - (up to 32)")
        if self.role not in ROLES:
            out.append(f"role: must be one of {list(ROLES)}")
        if self.shape not in SHAPES:
            out.append(f"shape: must be one of {list(SHAPES)}")
        if not (2.0 <= self.width_cm <= 200.0 and 2.0 <= self.height_cm <= 200.0):
            out.append("size_cm: width and height must each be 2 to 200 cm (measure the real object)")
        return out

    # ---- geometry
    @property
    def footprint_m(self) -> float:
        """Radius of the ground circle the object occupies: the camera distance to its centre is the surface distance plus this."""
        return self.width_cm / 200.0

    def look(self) -> tuple[str, tuple, tuple]:
        """(shape, rgba, MuJoCo half-sizes) of the simulator's version of this object."""
        w, h = self.width_cm / 100.0, self.height_cm / 100.0
        rgb = (200, 200, 200)
        if self.detector:                       # photos are shaded and darker than the object's true colour: keep hue and saturation, lift brightness
            hsv = cv2.cvtColor(np.array([[self.detector.mean_rgb]], np.uint8), cv2.COLOR_RGB2HSV)[0, 0]
            hsv[2] = max(hsv[2], 200) if hsv[1] >= SAT_MIN else hsv[2]
            rgb = tuple(int(c) for c in cv2.cvtColor(hsv[None, None], cv2.COLOR_HSV2RGB)[0, 0])
        rgb = tuple(c / 255.0 for c in rgb)
        if self.shape == "box":
            return "box", (*rgb, 1.0), (w / 2, w / 2, h / 2)
        return "cylinder", (*rgb, 1.0), (w / 2, h / 2)

    # ---- seeing
    def ensure_detector(self) -> ColourShapeDetector:
        if self.detector is None:
            if self.folder is None:
                raise TargetError(f"target {self.id!r} has no photos to learn from")
            self.detector = ColourShapeDetector.fit(photo_files(self.folder))
        return self.detector

    def locate(self, frame: np.ndarray, camera: CameraModel | None = None) -> Sighting | None:
        """Find this target in an RGB frame: bearing and distance, or None when it is not there."""
        camera = camera or CameraModel(frame.shape[1], frame.shape[0])
        b = self.ensure_detector().detect(frame)
        if b is None:
            return None
        h_m, w_m = self.height_cm / 100.0, self.width_cm / 100.0
        vertical_cut = b.y == 0 or b.y + b.h >= frame.shape[0]
        horizontal_cut = b.x == 0 or b.x + b.w >= frame.shape[1]
        if not vertical_cut:
            dist = camera.distance(h_m, b.h)
        elif not horizontal_cut:
            dist = camera.distance(w_m, b.w)
        else:
            dist = camera.distance(h_m, b.h)
        bearing = camera.bearing(b.cx)
        return Sighting(self.id, bearing, dist / math.cos(bearing), b.score, b, bound=b.clipped)     # size gives the depth along the axis; the range is longer off-axis

    def detection_range_m(self, camera: CameraModel | None = None, min_px: float = 6.0) -> float:
        """How far away the object is still `min_px` tall in the frame: a calculation, to be checked on the real rover."""
        return (camera or CameraModel()).max_range(self.height_cm / 100.0, min_px)

    # ---- storage
    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "role": self.role, "size_cm": {"width": self.width_cm, "height": self.height_cm},
                "shape": self.shape, "source": self.source, "photos": [p.name for p in photo_files(self.folder)] if self.folder else [],
                "detector": self.detector.to_dict() if self.detector else None, "hit_rate": self.hits}

    @classmethod
    def from_dict(cls, d: dict, folder: Path | None = None) -> "Target":
        size = d.get("size_cm") or {}
        det = d.get("detector")
        t = cls(str(d.get("id", "")), str(d.get("name") or d.get("id", "")), str(d.get("role", "waypoint")),
                float(size.get("width", 0.0)), float(size.get("height", 0.0)), str(d.get("shape", "cylinder")),
                ColourShapeDetector.from_dict(det) if det else None, folder, str(d.get("source", "user")), dict(d.get("hit_rate") or {}))
        bad = t.problems()
        if bad:
            raise TargetError(f"target {t.id!r}: " + "; ".join(bad))
        return t


def photo_files(folder: Path | None) -> list[Path]:
    if folder is None or not Path(folder).is_dir():
        return []
    return sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in IMAGE_EXT)


# ------------------------------------------------------------------------------------------------ hit rate
@dataclass
class HitReport:
    n: int
    hits: int
    details: list[dict]

    @property
    def rate(self) -> float:
        return self.hits / self.n if self.n else 0.0

    def to_dict(self) -> dict:
        return {"n": self.n, "hits": self.hits, "rate": round(self.rate, 3), "details": self.details}


def _far_view(frame: np.ndarray, scale: float) -> np.ndarray:
    """The same picture with the object `scale` times as big as it was (what it looks like from farther away)."""
    if scale >= 1.0:
        return frame
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    pad_y, pad_x = (h - small.shape[0]) // 2, (w - small.shape[1]) // 2
    return cv2.copyMakeBorder(small, pad_y, h - small.shape[0] - pad_y, pad_x, w - small.shape[1] - pad_x, cv2.BORDER_REPLICATE)


def hit_rate(detector: ColourShapeDetector, photos: Sequence, *, scales: Sequence[float] = (1.0,), centre_tol: float = 0.35) -> HitReport:
    """How often the detector finds the object, near the middle of the picture, in each photo (at each `scale` of apparent size)."""
    details, hits = [], 0
    for i, p in enumerate(photos):
        base = to_frame(read_image(p))
        for s in scales:
            frame = _far_view(base, s)
            b = detector.detect(frame)
            ok = bool(b and abs(b.cx / frame.shape[1] - 0.5) <= centre_tol and abs(b.cy / frame.shape[0] - 0.5) <= centre_tol)
            hits += ok
            details.append({"photo": i, "scale": s, "hit": ok, "score": round(b.score, 2) if b else None})
    return HitReport(len(details), hits, details)


def false_alarm_rate(detector: ColourShapeDetector, images: Sequence) -> float:
    """Share of pictures that do NOT contain the object but in which the detector still reports one."""
    if not images:
        return 0.0
    return sum(detector.detect(to_frame(read_image(p))) is not None for p in images) / len(images)


# ------------------------------------------------------------------------------------------------ stand-in objects
@dataclass(frozen=True)
class StandIn:
    name: str
    role: str
    shape: str
    rgb: tuple
    width_cm: float
    height_cm: float


STANDINS: dict[str, StandIn] = {
    "bag_a": StandIn("Bag A (blue stand-in)", "follow", "cylinder", (35, 95, 225), 26.0, 22.0),
    "bag_b": StandIn("Bag B (magenta stand-in)", "waypoint", "cylinder", (200, 40, 170), 26.0, 22.0),
    "box": StandIn("Goal box (green stand-in)", "goal", "box", (35, 175, 70), 28.0, 20.0),
}


def _jitter_colour(rgb: tuple, rng: np.random.Generator) -> tuple:
    hsv = cv2.cvtColor(np.array([[rgb]], np.uint8), cv2.COLOR_RGB2HSV)[0, 0].astype(float)
    hsv[0] = (hsv[0] + rng.uniform(-4, 4)) % 180
    hsv[1] = np.clip(hsv[1] * rng.uniform(0.85, 1.1), 0, 255)
    hsv[2] = np.clip(hsv[2] * rng.uniform(0.8, 1.1), 0, 255)
    return tuple(int(c) for c in cv2.cvtColor(hsv.astype(np.uint8)[None, None], cv2.COLOR_HSV2RGB)[0, 0])


def _backdrop(rng: np.random.Generator, size: int) -> np.ndarray:
    """A floor-and-wall style picture: grey/brown/beige gradient with tile lines and a few lookalike-free blobs near the edges."""
    base = np.array(rng.choice([(110, 112, 118), (150, 130, 105), (95, 100, 110), (170, 165, 150), (70, 75, 85)]), float) + rng.normal(0, 8, 3)
    yy = np.linspace(rng.uniform(0.8, 1.2), rng.uniform(0.8, 1.2), size)[:, None, None]
    img = np.clip(base * yy * np.ones((size, size, 1)), 0, 255).astype(np.uint8)
    for k in range(rng.integers(2, 6)):
        c = int(rng.integers(0, size))
        cv2.line(img, (c, 0), (c + int(rng.integers(-20, 20)), size), tuple(int(v) for v in base * 0.75), 1)
        cv2.line(img, (0, c), (size, c + int(rng.integers(-10, 10))), tuple(int(v) for v in base * 0.75), 1)
    for _ in range(rng.integers(0, 3)):                       # clutter along the edges, in colours that are not the object's
        p = (int(rng.choice([rng.integers(0, size // 8), rng.integers(7 * size // 8, size)])), int(rng.integers(0, size)))
        cv2.circle(img, p, int(rng.integers(4, size // 9)), tuple(int(v) for v in rng.integers(40, 220, 3)), -1)
    return img


def _draw_standin(s: StandIn, rng: np.random.Generator, size: int = 128) -> np.ndarray:
    """One synthetic photo of a stand-in object: centred, 45-70 % of the picture tall, shaded, with lighting and noise jitter."""
    img = _backdrop(rng, size)
    col = _jitter_colour(s.rgb, rng)
    h = size * rng.uniform(0.45, 0.70)
    w = h * (s.width_cm / s.height_cm) * rng.uniform(0.85, 1.2)
    cx, cy = size / 2 + rng.uniform(-0.07, 0.07) * size, size / 2 + rng.uniform(-0.07, 0.07) * size
    x0, x1, y0, y1 = int(cx - w / 2), int(cx + w / 2), int(cy - h / 2), int(cy + h / 2)
    body = np.zeros((size, size, 3), np.uint8)
    cv2.rectangle(body, (x0, y0), (x1, y1), col, -1)
    if s.shape == "cylinder":                                 # a rounded sack: soft top and bottom
        cv2.ellipse(body, ((x0 + x1) // 2, y0), ((x1 - x0) // 2, max(2, int(h * 0.1))), 0, 0, 360, tuple(min(255, int(c * 1.15)) for c in col), -1)
        cv2.ellipse(body, ((x0 + x1) // 2, y1), ((x1 - x0) // 2, max(2, int(h * 0.1))), 0, 0, 360, col, -1)
    else:                                                     # a box: a lighter lid seen from a little above
        lid = np.array([[x0, y0], [x1, y0], [x1 - int(w * 0.08), y0 - int(h * 0.1)], [x0 + int(w * 0.08), y0 - int(h * 0.1)]], np.int32)
        cv2.fillPoly(body, [lid], tuple(min(255, int(c * 1.2)) for c in col))
    mask = body.any(2)
    shade = np.linspace(rng.uniform(1.0, 1.1), rng.uniform(0.7, 0.85), size)[:, None, None] * np.linspace(1.0, rng.uniform(0.8, 0.95), size)[None, :, None]
    img[mask] = np.clip(body[mask] * shade[mask], 0, 255).astype(np.uint8)
    gain = rng.uniform(0.75, 1.25) * rng.uniform(0.92, 1.08, 3)                # exposure and white balance
    img = np.clip(img.astype(float) * gain + rng.normal(0, rng.uniform(2, 6), img.shape), 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (3, 3), 0) if rng.random() < 0.4 else img


def standin_photos(target_id: str, n: int = 6, seed: int = 0, size: int = 128) -> list[np.ndarray]:
    """`n` synthetic photos of a stand-in object (RGB arrays), the same ones for the same seed."""
    if target_id not in STANDINS:
        raise TargetError(f"no stand-in called {target_id!r}; stand-ins: {sorted(STANDINS)}")
    rng = np.random.default_rng([seed, sum(map(ord, target_id))])
    return [_draw_standin(STANDINS[target_id], rng, size) for _ in range(n)]


def background_photos(n: int = 8, seed: int = 0, size: int = 128) -> list[np.ndarray]:
    """Pictures with no target in them, for measuring false alarms."""
    rng = np.random.default_rng([seed, 99])
    return [_backdrop(rng, size) for _ in range(n)]


@functools.lru_cache(maxsize=None)
def _builtin(target_id: str) -> Target:
    s = STANDINS[target_id]
    det = ColourShapeDetector.fit(standin_photos(target_id))
    t = Target(target_id, s.name, s.role, s.width_cm, s.height_cm, s.shape, det, None, "standin")
    t.hits = hit_rate(det, standin_photos(target_id)).to_dict() | {"details": []}
    return t


# ------------------------------------------------------------------------------------------------ the registry
class TargetRegistry:
    """The targets a mission may name: every folder under `config/targets/` (or $SIMLAB_TARGETS) plus the built-in stand-ins (a folder of the same id wins)."""

    def __init__(self, root: str | Path | None = None, standins: bool = True):
        self.root = Path(root) if root is not None else Path(os.environ.get("SIMLAB_TARGETS", TARGETS_DIR))
        self.standins = standins

    def ids(self) -> list[str]:
        found = {p.parent.name for p in self.root.glob("*/target.json")} if self.root.is_dir() else set()
        return sorted(found | (set(STANDINS) if self.standins else set()))

    def __contains__(self, target_id: object) -> bool:
        return target_id in self.ids()

    def get(self, target_id: str) -> Target:
        f = self.root / str(target_id) / "target.json"
        if ID_RE.fullmatch(str(target_id)) and f.is_file():
            return Target.from_dict(json.loads(f.read_text(encoding="utf-8-sig")), f.parent)
        if self.standins and target_id in STANDINS:
            return _builtin(target_id)
        raise KeyError(f"unknown target {target_id!r}; known: {self.ids()}")

    def all(self) -> list[Target]:
        return [self.get(i) for i in self.ids()]

    def add(self, target_id: str, name: str, role: str, width_cm: float, height_cm: float, photos: Sequence,
            shape: str | None = None) -> Target:
        """Learn a target from photos (paths or RGB arrays), store them under `<root>/<id>/` and return it. Nothing is written if learning fails."""
        t = Target(target_id, name or target_id, role, float(width_cm), float(height_cm), shape or ("box" if role == "goal" else "cylinder"))
        bad = t.problems()
        if bad:
            raise TargetError("; ".join(bad))
        t.detector = ColourShapeDetector.fit(photos)
        folder = self.root / target_id
        folder.mkdir(parents=True, exist_ok=True)
        for old in photo_files(folder):
            old.unlink()
        for i, p in enumerate(photos):
            cv2.imwrite(str(folder / f"photo{i + 1:02d}.png"), cv2.cvtColor(read_image(p), cv2.COLOR_RGB2BGR))
        t.folder = folder
        t.hits = hit_rate(t.detector, photo_files(folder), scales=(1.0, 0.5)).to_dict()
        (folder / "target.json").write_text(json.dumps(t.to_dict(), indent=1), encoding="utf-8")
        return t

    def remove(self, target_id: str) -> None:
        if ID_RE.fullmatch(str(target_id)) and (self.root / target_id / "target.json").is_file():
            shutil.rmtree(self.root / target_id)


def main(argv: list[str] | None = None) -> int:
    """`python -m simlab.missions standin [id ...]` writes stand-in photos + target.json under config/targets/ so you can look at them;
    `report [id ...]` prints each target's hit-rate on its own photos."""
    import argparse
    ap = argparse.ArgumentParser(prog="simlab.missions")
    ap.add_argument("command", choices=("standin", "report"))
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--root", default=str(TARGETS_DIR))
    a = ap.parse_args(argv)
    reg = TargetRegistry(a.root)
    if a.command == "standin":
        for i in a.ids or sorted(STANDINS):
            s = STANDINS[i]
            t = reg.add(i, s.name, s.role, s.width_cm, s.height_cm, standin_photos(i), s.shape)
            print(f"{i}: wrote {len(photo_files(t.folder))} photos to {t.folder}  (hit-rate {t.hits['rate']:.0%})")
    else:
        for i in a.ids or reg.ids():
            t = reg.get(i)
            r = hit_rate(t.ensure_detector(), photo_files(t.folder) or standin_photos(i), scales=(1.0, 0.5, 0.33)) if (t.folder or i in STANDINS) else None
            print(f"{i}: {r.hits}/{r.n} = {r.rate:.0%}" if r else f"{i}: no photos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
