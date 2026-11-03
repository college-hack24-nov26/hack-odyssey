"""Draws docs/media/banner.png: the README banner (the simulated path, the real path with its gap, and the corrected path)."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, S = 1600, 440, 2                      # drawn at 2x, saved at 1x
FONTS = Path(r"C:\Windows\Fonts")


def font(name: str, size: int):
    for f in (FONTS / name, Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        if f.exists():
            return ImageFont.truetype(str(f), size * S)
    return ImageFont.load_default()


def main(out: Path = Path("docs/media/banner.png")):
    img = Image.new("RGB", (W * S, H * S), "#f4f8ff")
    px = img.load()
    for y in range(H * S):                  # vertical gradient + a glow on the right
        for x in range(0, W * S, 4):
            g = y / (H * S)
            glow = max(0.0, 1 - math.hypot(x - 1250 * S, y - 200 * S) / (520 * S))
            c = (int(246 - 14 * g - 18 * glow), int(249 - 12 * g - 10 * glow), int(255 - 6 * g))
            for k in range(4):
                if x + k < W * S:
                    px[x + k, y] = c
    d = ImageDraw.Draw(img, "RGBA")
    for x in range(0, W, 40):               # faint grid
        d.line([(x * S, 0), (x * S, H * S)], fill=(60, 100, 180, 22), width=S)
    for y in range(0, H, 40):
        d.line([(0, y * S), (W * S, y * S)], fill=(60, 100, 180, 22), width=S)

    def path(f, x0, x1, n=160):
        return [((x0 + (x1 - x0) * i / n) * S, f(i / n) * S) for i in range(n + 1)]
    base = 300
    sim = path(lambda t: base, 930, 1540)                                         # the simulation: dead straight
    real = path(lambda t: base + 78 * math.sin(t * math.pi * 0.5) ** 1.5, 930, 1540)   # the real rover: pulls off to one side
    fixed = path(lambda t: base - 16 + 6 * math.sin(t * 9), 930, 1540)            # after the correction: back on the line
    for i in range(0, len(sim) - 1, 6):     # dashed ghost line
        d.line([sim[i], sim[i + 1]], fill=(70, 110, 190, 200), width=3 * S)
    d.line(real, fill=(225, 55, 85, 235), width=4 * S, joint="curve")
    d.line(fixed, fill=(22, 160, 80, 255), width=5 * S, joint="curve")
    # the gap marker
    gx, gy0, gy1 = 1440 * S, sim[130][1], real[130][1]
    d.line([(gx, gy0), (gx, gy1)], fill=(200, 140, 0, 240), width=2 * S)
    for yy in (gy0, gy1):
        d.line([(gx - 8 * S, yy), (gx + 8 * S, yy)], fill=(200, 140, 0, 240), width=2 * S)
    d.text((gx + 14 * S, (gy0 + gy1) / 2 - 12 * S), "the gap", font=font("segoeuib.ttf", 20), fill=(176, 120, 0, 255))
    d.text((940 * S, 322 * S), "simulation (ideal)", font=font("segoeui.ttf", 18), fill=(60, 100, 180, 255))
    d.text((1230 * S, 396 * S), "real rover", font=font("segoeui.ttf", 18), fill=(200, 40, 70, 255))
    d.text((1230 * S, 250 * S), "after the fix", font=font("segoeui.ttf", 18), fill=(18, 130, 66, 255))

    # a small rover at the start of the path
    rx, ry = 880 * S, base * S
    d.rounded_rectangle([rx - 46 * S, ry - 24 * S, rx + 46 * S, ry + 20 * S], radius=8 * S, fill=(240, 140, 36, 255))
    d.rounded_rectangle([rx + 14 * S, ry - 16 * S, rx + 40 * S, ry + 2 * S], radius=4 * S, fill=(255, 235, 200, 255))   # camera window
    for wx in (-26, 26):
        d.ellipse([rx + (wx - 15) * S, ry + 8 * S, rx + (wx + 15) * S, ry + 38 * S], fill=(30, 36, 52, 255), outline=(110, 120, 145, 255), width=2 * S)
        d.ellipse([rx + (wx - 5) * S, ry + 18 * S, rx + (wx + 5) * S, ry + 28 * S], fill=(190, 200, 220, 255))

    d.text((70 * S, 92 * S), "RoboForge", font=font("segoeuib.ttf", 96), fill=(16, 30, 62, 255))
    d.text((70 * S, 206 * S), "Describe a machine. Simulate it. Test the real one.", font=font("segoeui.ttf", 30), fill=(50, 70, 110, 255))
    d.text((70 * S, 252 * S), "Measure the gap. Fix it. Flash it.", font=font("segoeui.ttf", 30), fill=(50, 70, 110, 255))
    d.text((70 * S, 340 * S), "ESP32 rover  ·  chat-first planner  ·  MuJoCo + Webots  ·  reality-gap loop", font=font("segoeui.ttf", 22), fill=(90, 115, 160, 255))
    img = img.resize((W, H), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    print(out, out.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
