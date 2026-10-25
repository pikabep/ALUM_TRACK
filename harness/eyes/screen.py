"""Screenshots and the coordinate space shared by the Eyes and the Hands.

The model sees images no larger than MAX_EDGE on the long side. Every click it
returns is in that image space; Geometry converts it back to real input
coordinates (desktop pixels for PyAutoGUI, CSS pixels for the browser).
"""
from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

MAX_EDGE = 1366  # keeps a 16:9 screen around 1 megapixel, a good size for vision models


def fit(width: int, height: int, max_edge: int = MAX_EDGE) -> tuple[int, int]:
    s = min(1.0, max_edge / max(width, height))
    return max(1, round(width * s)), max(1, round(height * s))


@dataclass(frozen=True)
class Geometry:
    """Maps model-image pixels to input coordinates."""

    left: int
    top: int
    width: int  # input space (PyAutoGUI pixels / browser CSS pixels)
    height: int
    img_w: int  # what the model sees
    img_h: int

    @classmethod
    def for_size(cls, width: int, height: int, left: int = 0, top: int = 0, max_edge: int = MAX_EDGE) -> "Geometry":
        return cls(left, top, width, height, *fit(width, height, max_edge))

    def to_input(self, x: float, y: float) -> tuple[int, int]:
        return (
            self.left + round(x * self.width / self.img_w),
            self.top + round(y * self.height / self.img_h),
        )

    def to_image(self, x: float, y: float) -> tuple[int, int]:
        return (
            round((x - self.left) * self.img_w / self.width),
            round((y - self.top) * self.img_h / self.height),
        )


@dataclass
class Shot:
    png: bytes
    geometry: Geometry
    source: str  # "desktop" | "browser"
    taken_at: float = field(default_factory=time.time)

    @property
    def width(self) -> int:
        return self.geometry.img_w

    @property
    def height(self) -> int:
        return self.geometry.img_h

    def image(self) -> Image.Image:
        return Image.open(io.BytesIO(self.png))

    def b64(self) -> str:
        return base64.b64encode(self.png).decode()

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.png)
        return p


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def from_image(img: Image.Image, geometry: Geometry, source: str) -> Shot:
    img = img.convert("RGB")
    if img.size != (geometry.img_w, geometry.img_h):
        img = img.resize((geometry.img_w, geometry.img_h), Image.LANCZOS)
    return Shot(_png(img), geometry, source)


def _mss():
    import mss

    return getattr(mss, "MSS", None) or mss.mss  # mss >= 10 renamed the class


def desktop_geometry(monitor: int = 1, max_edge: int = MAX_EDGE) -> Geometry:
    # mss reports monitor bounds in the same space PyAutoGUI uses (logical points on
    # macOS, physical pixels on DPI-aware Windows, X11 pixels on Linux).
    with _mss()() as sct:
        m = sct.monitors[monitor]
    return Geometry.for_size(m["width"], m["height"], m["left"], m["top"], max_edge)


def capture_desktop(monitor: int = 1, max_edge: int = MAX_EDGE) -> Shot:
    with _mss()() as sct:
        m = sct.monitors[monitor]
        raw = sct.grab(m)
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")  # raw.size may be 2x on Retina
    return from_image(img, Geometry.for_size(m["width"], m["height"], m["left"], m["top"], max_edge), "desktop")


def diff_ratio(a: Shot, b: Shot, size: tuple[int, int] = (160, 100), threshold: int = 24) -> float:
    """Fraction of the screen that visibly changed between two shots (0.0 to 1.0).
    Cheap 'did my action do anything?' check for the Verify step."""
    ia = a.image().convert("L").resize(size)
    ib = b.image().convert("L").resize(size)
    hist = ImageChops.difference(ia, ib).histogram()
    return sum(hist[threshold + 1 :]) / (size[0] * size[1])


_COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#008080", "#f032e6", "#9a6324"]


def annotate(shot: Shot, boxes: list[dict]) -> Shot:
    """Draw numbered boxes (set-of-marks) so the model can name elements by ref.
    Boxes are in input space ({id, x, y, w, h}); they are scaled to image space here."""
    img = shot.image().convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=12)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    g = shot.geometry
    for b in boxes:
        x0, y0 = g.to_image(b["x"] + g.left, b["y"] + g.top)
        x1, y1 = g.to_image(b["x"] + b["w"] + g.left, b["y"] + b["h"] + g.top)
        color = _COLORS[b["id"] % len(_COLORS)]
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
        label = str(b["id"])
        tw, th = draw.textbbox((0, 0), label, font=font)[2:]
        ly = y0 - th - 2 if y0 - th - 2 >= 0 else y0
        draw.rectangle([x0, ly, x0 + tw + 4, ly + th + 2], fill=color)
        draw.text((x0 + 2, ly), label, fill="white", font=font)
    return Shot(_png(img), g, shot.source, shot.taken_at)
