"""Offline comparison: full ScreenSeekeR vs. adaptive fast path on the SAME saved screenshots.

Real models, no desktop interaction. Ground-truth boxes were labelled by hand on the raw
screenshots (px). A 'found' counts as correct only if the point lies inside the truth box;
for absent targets only 'not_found' is correct.

    uv run scripts/compare_modes.py artifacts/compare.json
"""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from PIL import Image

from tjm_agent import config, trace
from tjm_agent.geometry import Box
from tjm_agent.grounding.backends import ClaudePlanner, VenusGrounder
from tjm_agent.grounding.screenseeker import ScreenSeekeR

A = Path("artifacts")
ICON = "TextEdit application icon on the desktop"
CASES = [
    ("top_left", A / "run-20261014-005544/icon_top_left_raw.png", ICON, Box(140, 150, 300, 330)),
    ("bottom_right", A / "run-20261014-010135/icon_bottom_right_raw.png", ICON,
     Box(3230, 1900, 3390, 2075)),
    ("center", A / "run-20261014-010417/icon_center_raw.png", ICON, Box(1640, 1020, 1800, 1200)),
    ("absent_blender", A / "run-20261014-005249/absent_blender_raw.png",
     "Blender application icon on the desktop", None),
    ("decoy_dock_terminal", A / "run-20261014-010417/icon_center_raw.png",
     "Terminal application icon on the desktop", None),
    ("menubar_wifi", A / "run-20261014-010417/icon_center_raw.png",
     "Wi-Fi status icon in the menu bar", Box(2820, 0, 2910, 75)),
]

settings = config.load()
planner = ClaudePlanner(model=settings.planner_model, effort=settings.planner_effort)
grounder = VenusGrounder(repo=settings.grounder_repo)
out_dir = A / time.strftime("compare-%Y%m%d-%H%M%S")
rows = []
for mode in ("screenseeker", "adaptive"):
    engine = ScreenSeekeR(planner, grounder, replace(settings.search, mode=mode))
    for name, path, target, truth in CASES:
        img = Image.open(path).convert("RGB")
        o = engine.search(img, target)
        if truth is None:
            correct = o.status != "found"
        else:
            correct = o.status == "found" and truth.contains(*o.point_px)
        wrong_click = o.status == "found" and not correct
        rows.append({"mode": mode, "case": name, "target": target, "status": o.status,
                     "correct": correct, "wrong_click": wrong_click,
                     "point_px": o.point_px, "seconds": round(o.seconds, 1),
                     "planner_calls": o.planner_calls, "grounder_calls": o.grounder_calls})
        trace.save(out_dir, f"{mode}_{name}", img, o, title=f"{mode}:{name}")
        print(json.dumps(rows[-1]), flush=True)

Path(sys.argv[1] if len(sys.argv) > 1 else out_dir / "compare.json").write_text(
    json.dumps({"traces": str(out_dir), "rows": rows}, indent=2))
