"""Model clients. The only module that imports model SDKs.

Planner  — Claude (Anthropic API): position inference + result checking (paper: GPT-4o).
Grounder — UI-Venus-1.5-2B via MLX, local: instruction → point (paper: OS-Atlas-7B).
"""

from __future__ import annotations

import base64
import io
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Literal

from PIL import Image
from pydantic import BaseModel

from . import prompts

# ----------------------------------------------------------------------------- planner


@dataclass(frozen=True)
class Reference:
    kind: Literal["element", "area", "neighbor"]
    text: str


@dataclass(frozen=True)
class PositionInference:
    raw: str
    references: list[Reference]
    no_target: bool


class CheckVerdict(BaseModel):
    analysis: str
    result: Literal["is_target", "target_elsewhere", "target_not_found"]
    new_instruction: str | None = None


_TAG = re.compile(r"<(element|area|neighbor)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)


def parse_position_inference(raw: str) -> PositionInference:
    refs: list[Reference] = []
    seen: set[tuple[str, str]] = set()
    for kind, text in _TAG.findall(raw):
        key = (kind.lower(), " ".join(text.split()))
        if key[1] and key not in seen:
            seen.add(key)
            refs.append(Reference(kind=key[0], text=key[1]))  # type: ignore[arg-type]
    no_target = not refs and "no target" in raw.lower()
    return PositionInference(raw=raw, references=refs, no_target=no_target)


def _jpeg_b64(image: Image.Image, max_side: int) -> str:
    img = image
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode()


@dataclass
class ClaudePlanner:
    model: str = "claude-opus-5-5"
    effort: str = "low"  # position inference and triage
    check_effort: str = "medium"  # the result check gates every click; low was inconsistent
    max_side: int = 1568
    calls: int = 0
    seconds: float = 0.0
    _client: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        import anthropic

        self._client = anthropic.Anthropic(max_retries=3, timeout=120)

    def _content(self, image: Image.Image, text: str) -> list[dict]:
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": _jpeg_b64(image, self.max_side),
                },
            },
            {"type": "text", "text": text},
        ]

    def _check_content(self, crop: Image.Image, instruction: str,
                       context: Image.Image | None) -> list[dict]:
        text = prompts.RESULT_CHECK.format(instruction=instruction)
        if context is None:
            return self._content(crop, text)
        content = self._content(context, text + prompts.RESULT_CHECK_CONTEXT)
        return [content[0], *self._content(crop, "")[:1], content[1]]

    def position_inference(self, image: Image.Image, instruction: str) -> PositionInference:
        t0 = time.monotonic()
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4000,
            output_config={"effort": self.effort},
            messages=[
                {
                    "role": "user",
                    "content": self._content(
                        image, prompts.POSITION_INFERENCE.format(instruction=instruction)
                    ),
                }
            ],
        )
        self.calls += 1
        self.seconds += time.monotonic() - t0
        if resp.stop_reason == "refusal":
            raise RuntimeError("planner refused position inference")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return parse_position_inference(text)

    def check(self, marked_crop: Image.Image, instruction: str,
              context: Image.Image | None = None) -> CheckVerdict:
        t0 = time.monotonic()
        resp = self._client.messages.parse(
            model=self.model,
            max_tokens=4000,
            output_config={"effort": self.check_effort},
            output_format=CheckVerdict,
            messages=[{"role": "user", "content": self._check_content(marked_crop, instruction,
                                                                      context)}],
        )
        self.calls += 1
        self.seconds += time.monotonic() - t0
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise RuntimeError(f"planner check failed (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    def triage(self, screenshot: Image.Image, step: str, expected: str) -> DialogTriage:
        t0 = time.monotonic()
        resp = self._client.messages.parse(
            model=self.model,
            max_tokens=4000,
            output_config={"effort": self.effort},
            output_format=DialogTriage,
            messages=[
                {
                    "role": "user",
                    "content": self._content(
                        screenshot, prompts.DIALOG_TRIAGE.format(step=step, expected=expected)
                    ),
                }
            ],
        )
        self.calls += 1
        self.seconds += time.monotonic() - t0
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise RuntimeError(f"planner triage failed (stop_reason={resp.stop_reason})")
        return resp.parsed_output


class DialogTriage(BaseModel):
    description: str
    buttons: list[str]
    risk: Literal["safe", "destructive", "permission", "none", "unknown"]
    button_to_click: str | None = None


# ----------------------------------------------------------------------------- grounder

_POINT = re.compile(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?:,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*)?\]")


@dataclass(frozen=True)
class GroundPoint:
    """A grounder prediction on the 0–1000 grid of the image it was shown, or a refusal."""

    raw: str
    x: float | None
    y: float | None

    @property
    def refused(self) -> bool:
        return self.x is None


def parse_grounder_output(raw: str) -> GroundPoint:
    """Accept [x,y] or [x1,y1,x2,y2] (→ centre). [-1,-1] or no match → refusal."""
    m = _POINT.search(raw)
    if not m:
        return GroundPoint(raw, None, None)
    a, b, c, d = (float(v) if v is not None else None for v in m.groups())
    x, y = (a, b) if c is None else ((a + c) / 2, (b + d) / 2)
    if x < 0 or y < 0 or x > 1000 or y > 1000:
        return GroundPoint(raw, None, None)
    return GroundPoint(raw, x, y)


@dataclass
class VenusGrounder:
    repo: str = "mlx-community/UI-Venus-1.5-2B-6bit"
    max_side: int = 1280  # input long side; larger costs memory/latency on 8 GB
    calls: int = 0
    seconds: float = 0.0
    _model: object = field(default=None, repr=False)
    _processor: object = field(default=None, repr=False)

    def _load(self) -> None:
        if self._model is None:
            from mlx_vlm import load

            self._model, self._processor = load(self.repo)

    def ground(self, image: Image.Image, instruction: str) -> GroundPoint:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        self._load()
        img = image
        if max(img.size) > self.max_side:
            img = img.copy()
            img.thumbnail((self.max_side, self.max_side), Image.LANCZOS)
        instruction = instruction.rstrip(".")
        prompt = apply_chat_template(
            self._processor,
            self._model.config,
            prompts.GROUNDER.format(instruction=instruction),
            num_images=1,
        )
        t0 = time.monotonic()
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            img.save(f.name)
            out = generate(
                self._model,
                self._processor,
                prompt,
                image=[f.name],
                max_tokens=32,
                temperature=0.0,
                verbose=False,
            )
        self.calls += 1
        self.seconds += time.monotonic() - t0
        return parse_grounder_output(out.text)
