"""ScreenSeekeR (ScreenSpot-Pro, Li et al. 2025, §4.2 / Algorithm 1) — planner-guided visual search.

    screenshot + natural-language target  →  found(point) | ambiguous | not_found | budget_exhausted

Interpretation notes (see docs/design.md §5):
- Algorithm 1 scores `nms_patches` before NMS defines them; we run dilate → score → NMS → sort.
- Algorithm 1 resets `d ← 0` inside the recursive function; we treat it as the initial call.
- Our grounder returns points, not boxes; a voter box of fixed size is formed around each point.
- At a leaf, the grounder predicts the target and the planner verifies it with the paper's
  result-check prompt; only `is_target` terminates the search.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Protocol

from PIL import Image, ImageDraw

from ..geometry import (
    Box,
    dilate,
    nms,
    norm1000_to_screenshot,
    patch_size,
    score_candidates,
)
from .backends import CheckVerdict, GroundPoint, PositionInference


class Planner(Protocol):
    calls: int

    def position_inference(self, image: Image.Image, instruction: str) -> PositionInference: ...
    def check(self, marked_crop: Image.Image, instruction: str) -> CheckVerdict: ...


class Grounder(Protocol):
    calls: int

    def ground(self, image: Image.Image, instruction: str) -> GroundPoint: ...


@dataclass(frozen=True)
class SearchConfig:
    max_depth: int = 3  # Dmax
    leaf_size: int = 1280  # viewport side (px) at which the grounder is invoked directly
    patch_cap: float = 0.5  # candidate side ≤ patch_cap × viewport side (guarantees narrowing)
    voter_size: int = 96  # px box formed around each grounded point
    mark_size: int = 120  # px red box drawn for the result check
    sigma: float = 0.3  # Eq. (1)
    nms_iou: float = 0.5
    max_refs: int = 6  # references grounded per planner call
    max_candidates: int = 3  # patches explored per level
    max_planner_calls: int = 10
    max_grounder_calls: int = 30
    time_limit_s: float = 150.0


Status = Literal["found", "not_found", "budget_exhausted"]


@dataclass
class Outcome:
    status: Status
    instruction: str
    point_px: tuple[float, float] | None = None  # full-screenshot pixels
    viewport: Box | None = None  # crop in which the hit was verified
    events: list[dict] = field(default_factory=list)
    planner_calls: int = 0
    grounder_calls: int = 0
    seconds: float = 0.0


class _Budget(Exception):
    pass


class ScreenSeekeR:
    def __init__(self, planner: Planner, grounder: Grounder, config: SearchConfig | None = None):
        self.planner = planner
        self.grounder = grounder
        self.cfg = config or SearchConfig()

    # ------------------------------------------------------------------ public

    def search(self, screenshot: Image.Image, instruction: str) -> Outcome:
        self._image = screenshot
        self._events: list[dict] = []
        self._p0, self._g0 = self.planner.calls, self.grounder.calls
        self._t0 = time.monotonic()
        root = Box(0, 0, screenshot.width, screenshot.height)
        try:
            hit = self._visual_search(instruction, root, depth=0)
            status: Status = "found" if hit else "not_found"
        except _Budget as e:
            self._log("budget_exhausted", reason=str(e))
            hit, status = None, "budget_exhausted"
        return Outcome(
            status=status,
            instruction=instruction,
            point_px=hit[0] if hit else None,
            viewport=hit[1] if hit else None,
            events=self._events,
            planner_calls=self.planner.calls - self._p0,
            grounder_calls=self.grounder.calls - self._g0,
            seconds=time.monotonic() - self._t0,
        )

    # ------------------------------------------------------------------ algorithm 1

    def _visual_search(self, instruction: str, viewport: Box, depth: int):
        if depth >= self.cfg.max_depth or max(viewport.width, viewport.height) <= self.cfg.leaf_size:
            return self._leaf(instruction, viewport, depth)

        crop = self._crop(viewport)
        self._spend("planner")
        inference = self.planner.position_inference(crop, instruction)
        self._log("position_inference", depth=depth, viewport=viewport.as_int(), raw=inference.raw,
                  references=[(r.kind, r.text) for r in inference.references])
        if inference.no_target:
            return None

        voters: list[Box] = []
        # Planner order is "descending probability"; within the reference cap we ground the
        # target mentions (<element>) first so a second proposed region is not cut off.
        refs = sorted(inference.references, key=lambda r: r.kind != "element")
        for ref in refs[: self.cfg.max_refs]:
            self._spend("grounder")
            gp = self.grounder.ground(crop, ref.text)
            pt = self._to_screenshot(gp, viewport)
            self._log("ground_reference", depth=depth, kind=ref.kind, text=ref.text,
                      raw=gp.raw, point_px=pt)
            if pt:
                voters.append(Box.around(*pt, self.cfg.voter_size, self.cfg.voter_size))
        if not voters:
            return None

        w, h = patch_size(viewport, self.cfg.leaf_size, self.cfg.patch_cap)
        candidates = [dilate(v, w, viewport, h) for v in voters]
        scores = score_candidates(candidates, voters, self.cfg.sigma)
        order = nms(candidates, scores, self.cfg.nms_iou)[: self.cfg.max_candidates]
        self._log("candidates", depth=depth,
                  patches=[(candidates[i].as_int(), round(scores[i], 3)) for i in order])

        for i in order:
            hit = self._visual_search(instruction, candidates[i], depth + 1)
            if hit:
                return hit
        return None

    def _leaf(self, instruction: str, viewport: Box, depth: int):
        crop = self._crop(viewport)
        tried: set[str] = set()
        while instruction not in tried:
            tried.add(instruction)
            self._spend("grounder")
            gp = self.grounder.ground(crop, instruction)
            pt = self._to_screenshot(gp, viewport)
            self._log("ground_target", depth=depth, viewport=viewport.as_int(),
                      instruction=instruction, raw=gp.raw, point_px=pt)
            if not pt:
                return None
            self._spend("planner")
            verdict = self.planner.check(self._mark(crop, viewport, pt), instruction)
            self._log("check", depth=depth, point_px=pt, result=verdict.result,
                      new_instruction=verdict.new_instruction, analysis=verdict.analysis)
            if verdict.result == "is_target":
                return pt, viewport
            if verdict.result == "target_elsewhere" and verdict.new_instruction:
                instruction = verdict.new_instruction  # paper: rewrite and retry
                continue
            return None
        return None

    # ------------------------------------------------------------------ helpers

    def _crop(self, viewport: Box) -> Image.Image:
        return self._image.crop(viewport.as_int())

    def _to_screenshot(self, gp: GroundPoint, viewport: Box):
        if gp.refused:
            return None
        return norm1000_to_screenshot(gp.x, gp.y, viewport)

    def _mark(self, crop: Image.Image, viewport: Box, pt: tuple[float, float]) -> Image.Image:
        img = crop.copy()
        d = ImageDraw.Draw(img)
        x, y = pt[0] - viewport.x1, pt[1] - viewport.y1
        h = self.cfg.mark_size / 2
        d.rectangle([x - h, y - h, x + h, y + h], outline=(255, 0, 0), width=4)
        return img

    def _spend(self, kind: str) -> None:
        if time.monotonic() - self._t0 > self.cfg.time_limit_s:
            raise _Budget(f"time limit {self.cfg.time_limit_s}s")
        if kind == "planner" and self.planner.calls - self._p0 >= self.cfg.max_planner_calls:
            raise _Budget(f"planner call limit {self.cfg.max_planner_calls}")
        if kind == "grounder" and self.grounder.calls - self._g0 >= self.cfg.max_grounder_calls:
            raise _Budget(f"grounder call limit {self.cfg.max_grounder_calls}")

    def _log(self, event: str, **data) -> None:
        self._events.append({"event": event, "t": round(time.monotonic() - self._t0, 2), **data})

