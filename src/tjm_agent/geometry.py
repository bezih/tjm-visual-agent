"""Coordinate geometry: boxes, crops, dilation, scoring, NMS, and display mapping.

Conventions
-----------
- All boxes are (x1, y1, x2, y2) floats in *full-screenshot pixel* space unless a function
  says otherwise.
- Grounder outputs on a 0–1000 grid are relative to the image the grounder was shown
  (a viewport crop); `norm1000_to_screenshot` maps them back.
- Click coordinates are macOS *points*: screenshot pixels divided by the display scale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if self.x2 < self.x1 or self.y2 < self.y1:
            raise ValueError(f"degenerate box {self}")

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    def contains(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    def as_int(self) -> tuple[int, int, int, int]:
        return (round(self.x1), round(self.y1), round(self.x2), round(self.y2))

    @classmethod
    def around(cls, x: float, y: float, w: float, h: float) -> Box:
        return cls(x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def clamp(box: Box, bounds: Box) -> Box:
    """Shift (not shrink) a box to lie inside `bounds`; shrink only if it is larger."""
    w = min(box.width, bounds.width)
    h = min(box.height, bounds.height)
    x1 = min(max(box.x1, bounds.x1), bounds.x2 - w)
    y1 = min(max(box.y1, bounds.y1), bounds.y2 - h)
    return Box(x1, y1, x1 + w, y1 + h)


def dilate(box: Box, min_w: float, bounds: Box, min_h: float | None = None) -> Box:
    """Expand a box about its centre to at least `min_w` × `min_h`, kept inside `bounds`.

    Shifting rather than cropping at the border preserves the requested context size.
    """
    cx, cy = box.center
    w = max(box.width, min_w)
    h = max(box.height, min_w if min_h is None else min_h)
    return clamp(Box.around(cx, cy, w, h), bounds)


def patch_size(viewport: Box, leaf_size: float, cap: float) -> tuple[float, float]:
    """Candidate patch size for the next level: at most `leaf_size`, at most cap × viewport."""
    return (min(leaf_size, cap * viewport.width), min(leaf_size, cap * viewport.height))


def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    iy = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    inter = ix * iy
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def center_score(candidate: Box, voter: Box, sigma: float = 0.3) -> float:
    """ScreenSpot-Pro Eq. (1)–(2): Gaussian score of a voting box centre within a candidate."""
    x, y = voter.center
    if not candidate.contains(x, y) or candidate.width == 0 or candidate.height == 0:
        return 0.0
    xn = (x - candidate.x1) / candidate.width
    yn = (y - candidate.y1) / candidate.height
    return math.exp(-((xn - 0.5) ** 2 + (yn - 0.5) ** 2) / (2 * sigma**2))


def score_candidates(candidates: list[Box], voters: list[Box], sigma: float = 0.3) -> list[float]:
    """Sum of center scores over all grounded boxes, per candidate."""
    return [sum(center_score(c, v, sigma) for v in voters) for c in candidates]


def nms(boxes: list[Box], scores: list[float], iou_threshold: float = 0.5) -> list[int]:
    """Greedy non-maximum suppression. Returns kept indices, highest score first."""
    order = sorted(range(len(boxes)), key=lambda i: scores[i], reverse=True)
    kept: list[int] = []
    for i in order:
        if all(iou(boxes[i], boxes[k]) <= iou_threshold for k in kept):
            kept.append(i)
    return kept


def norm1000_to_screenshot(x: float, y: float, viewport: Box) -> tuple[float, float]:
    """Map a 0–1000 point on a viewport image back to full-screenshot pixels."""
    return (viewport.x1 + x / 1000 * viewport.width, viewport.y1 + y / 1000 * viewport.height)


def screenshot_to_points(x: float, y: float, scale: float) -> tuple[float, float]:
    """Full-screenshot pixel → macOS click point (pixels / backing scale)."""
    return (x / scale, y / scale)
