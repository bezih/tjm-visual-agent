"""Search traces: JSON events + an annotated screenshot of what the engine looked at."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .grounding.screenseeker import Outcome

COLORS = {
    "element": (255, 64, 129),
    "area": (255, 193, 7),
    "neighbor": (0, 188, 212),
}


def _font(size: int):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


def annotate(
    screenshot: Image.Image, outcome: Outcome, click_pt=None, title: str = ""
) -> Image.Image:
    img = screenshot.copy().convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    small, big = _font(26), _font(40)

    for ev in outcome.events:
        if ev["event"] == "candidates":
            for rank, (box, score) in enumerate(ev["patches"], 1):
                d.rectangle(box, outline=(255, 235, 59, 255), width=5)
                d.text(
                    (box[0] + 10, max(box[1] + 8, 230)),
                    f"candidate #{rank}  score {score}",
                    fill=(255, 235, 59),
                    font=small,
                    stroke_width=3,
                    stroke_fill="black",
                )
        elif ev["event"] == "ground_reference" and ev.get("point_px"):
            x, y = ev["point_px"]
            c = COLORS.get(ev["kind"], (255, 255, 255))
            d.ellipse([x - 12, y - 12, x + 12, y + 12], fill=c + (230,), outline="black", width=3)
            label = f"{ev['kind']}: {ev['text'][:40]}"
            right_edge = x > img.width - 700
            d.text(
                (x - 18 if right_edge else x + 18, y),
                label,
                fill=c,
                font=small,
                anchor="rm" if right_edge else "lm",
                stroke_width=3,
                stroke_fill="black",
            )
        elif ev["event"] == "ground_target" and ev.get("viewport"):
            box = ev["viewport"]
            d.rectangle(box, outline=(33, 150, 243, 255), width=6)
            d.text(
                (box[0] + 10, box[3] - 12),
                "searched here (leaf crop)",
                anchor="ls",
                fill=(100, 181, 246),
                font=small,
                stroke_width=3,
                stroke_fill="black",
            )
        elif ev["event"] == "check" and ev["result"] != "is_target" and ev.get("point_px"):
            x, y = ev["point_px"]
            r = 45
            d.line([x - r, y - r, x + r, y + r], fill=(244, 67, 54, 255), width=9)
            d.line([x - r, y + r, x + r, y - r], fill=(244, 67, 54, 255), width=9)
            why = ev["result"].replace("_", " ")
            right_edge = x > img.width - 900
            d.text(
                (x - 60 if right_edge else x + 60, y - 70),
                f"grounder guess rejected: {why}",
                anchor="rs" if right_edge else "ls",
                fill=(255, 205, 210),
                font=small,
                stroke_width=3,
                stroke_fill="black",
            )

    if outcome.status == "found":
        x, y = outcome.point_px
        d.rectangle([x - 80, y - 80, x + 80, y + 80], outline=(244, 67, 54, 255), width=8)
        d.line([x - 110, y, x + 110, y], fill=(244, 67, 54, 255), width=4)
        d.line([x, y - 110, x, y + 110], fill=(244, 67, 54, 255), width=4)
        label = f"FOUND + verified  px=({x:.0f},{y:.0f})"
        if click_pt:
            label += f"  click pt=({click_pt[0]:.0f},{click_pt[1]:.0f})"
        tx = min(max(20, x - 300), img.width - 1100)
        ty = y + 100 if y < img.height - 320 else y - 170
        d.text((tx, ty), label, fill=(244, 67, 54), font=big, stroke_width=4, stroke_fill="white")
    else:
        msg = {
            "not_found": "NOT FOUND - no click (agent abstained)",
            "budget_exhausted": "BUDGET EXHAUSTED - no click",
        }[outcome.status]
        d.rectangle([0, 140, img.width, 220], fill=(183, 28, 28, 220))
        d.text((24, 180), msg, anchor="lm", fill="white", font=big)

    header = (
        f'{title}  |  target: "{outcome.instruction}"  |  {outcome.status}  |  '
        f"{outcome.seconds:.1f}s, planner calls {outcome.planner_calls}, "
        f"grounder calls {outcome.grounder_calls}"
    )
    # Info bar at the top (under the menu bar) so the Dock area stays visible.
    d.rectangle([0, 70, img.width, 140], fill=(0, 0, 0, 200))
    d.text((24, 105), header.strip(" |"), anchor="lm", fill="white", font=big)
    return img


def save(
    run_dir: Path,
    name: str,
    screenshot: Image.Image,
    outcome: Outcome,
    click_pt=None,
    title: str = "",
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    screenshot.save(run_dir / f"{name}_raw.png")
    annotate(screenshot, outcome, click_pt, title).save(run_dir / f"{name}_annotated.png")
    data = asdict(outcome)
    data["viewport"] = outcome.viewport.as_int() if outcome.viewport else None
    data["click_pt"] = click_pt
    (run_dir / f"{name}.json").write_text(json.dumps(data, indent=2, default=str))
    return run_dir / f"{name}_annotated.png"


def new_run_dir(root: Path = Path("artifacts")) -> Path:
    return root / time.strftime("run-%Y%m%d-%H%M%S")


def explain(events: list[dict], status: str, instruction: str) -> str:
    """Plain-language story of one search, built from its trace events."""
    lines = [f'Target: "{instruction}"', ""]
    step = 0
    for ev in events:
        kind = ev["event"]
        if kind == "fast_path_ground":
            step += 1
            lines.append(
                f"{step}. FAST PATH: the local model looked at the whole screen and "
                f"pointed at {_pt(ev.get('point_px'))}."
            )
        elif kind == "fast_path_check":
            lines.append(f"   Claude checked that guess -> {ev['result']}")
            lines.append(f"   Claude's reasoning: {ev['analysis']}")
        elif kind == "position_inference":
            step += 1
            lines.append(f"{step}. PLAN (Claude, looking at the screen): where is it likely to be?")
            lines.append("   Claude's answer, word for word:")
            lines += [f"     {t}" for t in ev["raw"].strip().splitlines() if t.strip()]
            refs = ev.get("references") or []
            if not refs:
                lines.append("   (No target: Claude does not see it, so the search stops here.)")
        elif kind == "ground_reference":
            where = _pt(ev.get("point_px")) if ev.get("point_px") else "(not found)"
            lines.append(f"   - local model located {ev['kind']} '{ev['text']}' at {where}")
        elif kind == "candidates":
            step += 1
            lines.append(
                f"{step}. ZOOM: each located spot becomes a zoom area, scored by how "
                "many spots sit near its centre (paper's formula):"
            )
            for rank, (box, score) in enumerate(ev["patches"], 1):
                lines.append(f"   candidate #{rank}: score {score}  (box {box})")
            lines.append("   It searches the highest-scoring area first.")
        elif kind == "ground_target":
            step += 1
            where = _pt(ev.get("point_px")) if ev.get("point_px") else "nothing (it refused)"
            lines.append(
                f"{step}. POINT: inside that zoomed area, the local model pointed at {where}."
            )
        elif kind == "check":
            verdict = {
                "is_target": "YES, this is the target",
                "target_elsewhere": "NO, but the target is elsewhere",
                "target_not_found": "NO, and the target is not here",
            }[ev["result"]]
            lines.append(f"   CHECK (Claude, seeing the red box + the whole screen): {verdict}")
            lines.append(f"   Claude's reasoning: {ev['analysis']}")
            if ev.get("new_instruction") and ev["result"] == "target_elsewhere":
                lines.append(
                    f'   Claude rewrote the target as: "{ev["new_instruction"]}" '
                    "and the search retries with it."
                )
        elif kind == "budget_exhausted":
            lines.append(f"   Stopped: {ev['reason']} (never guesses).")
    lines.append("")
    lines.append(
        {
            "found": "RESULT: found and verified, so it is allowed to click.",
            "not_found": "RESULT: not found, so it does NOT click.",
            "budget_exhausted": "RESULT: ran out of budget, so it does NOT click.",
        }[status]
    )
    return "\n".join(lines)


def _pt(p) -> str:
    return f"({p[0]:.0f}, {p[1]:.0f}) px" if p else "-"
