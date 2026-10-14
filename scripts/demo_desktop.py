"""Temporarily clear the Desktop for a clean grounding demo, and restore it exactly.

    uv run scripts/demo_desktop.py stash  --keep TextEdit --keep checkin
    uv run scripts/demo_desktop.py restore

Items are moved (same volume, atomic rename) into ~/DesktopStash with a JSON manifest.
Restore never overwrites: if a name already exists on the Desktop it is reported and skipped.
Hidden files (.DS_Store, .localized, ...) are never touched.
"""

import argparse
import json
import sys
from pathlib import Path

DESKTOP = Path.home() / "Desktop"
STASH = Path.home() / "DesktopStash"
MANIFEST = STASH / "manifest.json"


def stash(keep: set[str]) -> None:
    if MANIFEST.exists():
        sys.exit(f"A stash already exists ({MANIFEST}); restore it first.")
    STASH.mkdir(exist_ok=True)
    moved = []
    for item in sorted(DESKTOP.iterdir()):
        if item.name.startswith(".") or item.name in keep:
            continue
        item.rename(STASH / item.name)
        moved.append(item.name)
    MANIFEST.write_text(json.dumps({"moved": moved, "kept": sorted(keep)}, indent=2))
    print(f"stashed {len(moved)} items to {STASH}; kept: {sorted(keep)}")


def restore() -> None:
    if not MANIFEST.exists():
        sys.exit("No stash manifest found; nothing to restore.")
    names = json.loads(MANIFEST.read_text())["moved"]
    conflicts = []
    for name in names:
        src, dst = STASH / name, DESKTOP / name
        if not src.exists():
            conflicts.append(f"missing in stash: {name}")
        elif dst.exists():
            conflicts.append(f"already on Desktop, left in stash: {name}")
        else:
            src.rename(dst)
    if conflicts:
        print("\n".join(conflicts))
        sys.exit("Restore incomplete; manifest kept.")
    MANIFEST.unlink()
    STASH.rmdir()
    print(f"restored {len(names)} items to {DESKTOP}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["stash", "restore"])
    p.add_argument("--keep", action="append", default=[])
    a = p.parse_args()
    stash(set(a.keep)) if a.action == "stash" else restore()
