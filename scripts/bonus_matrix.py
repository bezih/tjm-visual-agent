"""Bonus challenges, live: multiple icons, icon sizes, light/dark themes.

For each condition the script changes the real desktop, takes a fresh screenshot, runs the
default ScreenSeekeR search (no click), and scores the hit against the TextEdit alias's true
bounds as reported by Finder via Accessibility. Those bounds are used ONLY to score the
result, never to find the icon.

Everything it changes is restored in `finally`: the Finder icon size, the wallpaper, light or
dark mode, and the decoy aliases it creates.

    uv run scripts/bonus_matrix.py            # needs a stashed desktop and hands off (~8 min)
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from AppKit import NSScreen, NSWorkspace
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXValueGetValue,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)
from Foundation import NSURL
from PIL import Image

from tjm_agent import config, desktop, trace
from tjm_agent.grounding.backends import ClaudePlanner, VenusGrounder
from tjm_agent.grounding.screenseeker import ScreenSeekeR

DESKTOP = Path.home() / "Desktop"
OUT = Path("artifacts") / time.strftime("bonus-%Y%m%d-%H%M%S")
DECOYS = {  # similar "document-y" apps: white pages, notes, lists
    "Notes": "/System/Applications/Notes.app",
    "Preview": "/System/Applications/Preview.app",
    "Script Editor": "/System/Applications/Utilities/Script Editor.app",
    "Stickies": "/System/Applications/Stickies.app",
}
ALLOWED_ON_DESKTOP = {"TextEdit", "tjm-project", "checkin", "brewd-coffee"}


# ------------------------------------------------------------------ environment helpers


def sh(*cmd, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def finder_icon_size() -> int:
    out = sh("defaults", "read", "com.apple.finder", "DesktopViewSettings").stdout
    return int(
        next(line.split("=")[1].strip(" ;") for line in out.splitlines() if "iconSize" in line)
    )


def set_finder_icon_size(size: int) -> None:
    if finder_icon_size() == size:
        return  # no Finder restart needed (a restart can swallow a wallpaper change)
    plist = Path.home() / "Library/Preferences/com.apple.finder.plist"
    sh(
        "/usr/libexec/PlistBuddy",
        "-c",
        f"Set :DesktopViewSettings:IconViewSettings:iconSize {size}",
        str(plist),
    )
    sh("killall", "cfprefsd", check=False)
    sh("killall", "Finder", check=False)
    time.sleep(4)


def dark_mode() -> bool:
    out = sh(
        "osascript",
        "-e",
        'tell application "System Events" to tell appearance preferences to get dark mode',
    )
    return out.stdout.strip() == "true"


def set_dark_mode(on: bool) -> None:
    sh(
        "osascript",
        "-e",
        'tell application "System Events" to tell appearance preferences '
        f"to set dark mode to {'true' if on else 'false'}",
    )
    time.sleep(2)


def wallpaper() -> tuple:
    ws, screen = NSWorkspace.sharedWorkspace(), NSScreen.mainScreen()
    return ws.desktopImageURLForScreen_(screen), ws.desktopImageOptionsForScreen_(screen)


def set_wallpaper(url, options=None, expect=None) -> None:
    """Set the wallpaper on every desktop via System Events, retrying until a screenshot
    confirms it (`expect` is a brightness predicate). NSWorkspace's setter returns success on
    current macOS but is ignored, and a change sent during a Finder restart can be lost."""
    path = url.path()
    for _ in range(4):
        sh(
            "osascript",
            "-e",
            f'tell application "System Events" to tell every desktop to set picture to "{path}"',
        )
        time.sleep(3)
        if expect is None:
            return
        desktop.show_desktop()
        if expect(mean_brightness(desktop.capture().image)):
            return


def mean_brightness(image) -> float:
    """Average brightness of the screen's middle band (no menu bar or Dock), 0-255."""
    w, h = image.size
    return (
        sum(image.crop((0, h // 5, w, h * 4 // 5)).convert("L").resize((64, 32)).getdata()) / 2048
    )


def solid_wallpaper(name: str, rgb) -> NSURL:
    path = (OUT / f"{name}.png").resolve()
    Image.new("RGB", (3420, 2224), rgb).save(path)
    return NSURL.fileURLWithPath_(str(path))


def make_alias(target: str, name: str) -> Path:
    alias = DESKTOP / name
    url = NSURL.fileURLWithPath_(target)
    data, err = url.bookmarkDataWithOptions_includingResourceValuesForKeys_relativeToURL_error_(
        1 << 10, None, None, None
    )  # NSURLBookmarkCreationSuitableForBookmarkFile
    ok, err = NSURL.writeBookmarkData_toURL_options_error_(
        data, NSURL.fileURLWithPath_(str(alias)), 1 << 10, None
    )
    if not ok:
        raise RuntimeError(f"alias failed: {err}")
    return alias


def icon_bounds(title: str):
    """True bounds (x, y, w, h) in points of a desktop icon, from Finder's Accessibility tree.
    Scoring only."""
    from AppKit import NSRunningApplication

    finder = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.finder")[0]

    def attr(e, a):
        return AXUIElementCopyAttributeValue(e, a, None)[1]

    def walk(e, depth=0):
        if depth > 5:
            return None
        if attr(e, "AXRole") == "AXImage" and str(attr(e, "AXTitle") or "") == title:
            p = AXValueGetValue(attr(e, "AXPosition"), kAXValueCGPointType, None)[1]
            s = AXValueGetValue(attr(e, "AXSize"), kAXValueCGSizeType, None)[1]
            return (p.x, p.y, s.width, s.height)
        for kid in attr(e, "AXChildren") or []:
            hit = walk(kid, depth + 1)
            if hit:
                return hit
        return None

    return walk(AXUIElementCreateApplication(finder.processIdentifier()))


# ------------------------------------------------------------------ run


def main() -> int:
    extra = sorted(
        p.name
        for p in DESKTOP.iterdir()
        if not p.name.startswith(".") and p.name not in ALLOWED_ON_DESKTOP
    )
    if extra:
        sys.exit(f"Stash the desktop first; unexpected items would be sent to the planner: {extra}")
    if desktop.is_running("TextEdit"):
        sys.exit("Quit TextEdit first.")
    OUT.mkdir(parents=True)

    original = {"icon_size": finder_icon_size(), "dark": dark_mode(), "wallpaper": wallpaper()}
    # macOS keeps the real wallpaper setup in the wallpaper service's store; back it up byte for
    # byte so the exact original comes back even if the NSWorkspace URL is only a default.
    store = Path.home() / "Library/Application Support/com.apple.wallpaper/Store/Index.plist"
    store_backup = OUT / "wallpaper-Index.plist.bak"
    store_backup.write_bytes(store.read_bytes())
    print("original:", {k: v for k, v in original.items() if k != "wallpaper"})

    s = config.load()
    engine = ScreenSeekeR(
        ClaudePlanner(model=s.planner_model, effort=s.planner_effort, check_effort=s.check_effort),
        VenusGrounder(repo=s.grounder_repo),
        s.search,
    )
    light_wp = solid_wallpaper("wallpaper_light", (238, 238, 240))
    dark_wp = solid_wallpaper("wallpaper_dark", (24, 24, 28))
    decoys: list[Path] = []

    def setup_decoys():
        decoys.extend(make_alias(t, n) for n, t in DECOYS.items())
        sh("killall", "Finder", check=False)
        time.sleep(4)

    conditions = [
        (
            "multiple_icons",
            "4 lookalike app aliases added (Notes, Preview, Script Editor, Stickies)",
            setup_decoys,
        ),
        (
            "icons_small_32",
            "Finder desktop icon size 32 px (default here: 64)",
            lambda: set_finder_icon_size(32),
        ),
        ("icons_large_128", "Finder desktop icon size 128 px", lambda: set_finder_icon_size(128)),
        (
            "light_theme",
            "Light mode + near-white wallpaper (white icon on white)",
            lambda: (
                set_finder_icon_size(original["icon_size"]),
                set_dark_mode(False),
                set_wallpaper(light_wp, expect=lambda b: b > 200),
            ),
        ),
        (
            "dark_theme",
            "Dark mode + near-black wallpaper",
            lambda: (set_dark_mode(True), set_wallpaper(dark_wp, expect=lambda b: b < 60)),
        ),
    ]
    if "--themes-only" in sys.argv:
        conditions = [c for c in conditions if c[0].endswith("_theme")]
    if "--light-only" in sys.argv:
        conditions = [c for c in conditions if c[0] == "light_theme"]
    rows = []
    try:
        for name, desc, apply in conditions:
            print(f"\n=== {name}: {desc}")
            apply()
            # Finder relaunches after setting changes and restores its windows a few seconds
            # later, so wait for it to be ready, then clear and re-check right before capture.
            desktop.wait_until(lambda: icon_bounds("TextEdit") is not None, timeout=20)
            time.sleep(3)
            clear = False
            for _ in range(4):
                clear = desktop.show_desktop()
                time.sleep(1.5)
                if clear and not desktop.visible_windows():
                    break
            truth = icon_bounds("TextEdit")
            if not (clear and truth):
                row = {
                    "condition": name,
                    "description": desc,
                    "status": "setup_invalid",
                    "correct": None,
                    "windows": desktop.visible_windows(),
                    "truth": truth,
                }
                rows.append(row)
                print(json.dumps(row, default=str))
                continue
            shot = desktop.capture()
            brightness = mean_brightness(shot.image)
            expected = {"light_theme": (lambda b: b > 200), "dark_theme": (lambda b: b < 60)}
            if name in expected and not expected[name](brightness):
                row = {
                    "condition": name,
                    "status": "setup_invalid",
                    "correct": None,
                    "reason": f"wallpaper not applied (mean brightness {brightness:.0f})",
                }
                rows.append(row)
                print(json.dumps(row))
                continue
            o = engine.search(shot.image, s.icon_target)
            click = desktop.point_for(*o.point_px, shot) if o.point_px else None
            hit = bool(
                click
                and truth
                and truth[0] <= click[0] <= truth[0] + truth[2]
                and truth[1] <= click[1] <= truth[1] + truth[3] + 20
            )  # +label row
            row = {
                "condition": name,
                "description": desc,
                "status": o.status,
                "correct": hit,
                "click_pt": click,
                "truth_bounds_pt": truth,
                "screen_brightness": round(brightness),
                "seconds": round(o.seconds, 1),
                "planner_calls": o.planner_calls,
                "grounder_calls": o.grounder_calls,
            }
            rows.append(row)
            trace.save(OUT, name, shot.image, o, click, title=f"bonus:{name}")
            print(json.dumps(row))
    finally:
        print("\nrestoring...")
        for d in decoys:
            d.unlink(missing_ok=True)
        set_wallpaper(*original["wallpaper"])
        store.write_bytes(store_backup.read_bytes())
        sh("killall", "WallpaperAgent", check=False)  # reloads the restored store
        time.sleep(2)
        set_dark_mode(original["dark"])
        set_finder_icon_size(original["icon_size"])
        restored = {"icon_size": finder_icon_size(), "dark": dark_mode()}
        print("restored:", restored, "| decoys removed:", not any(d.exists() for d in decoys))
        (OUT / "summary.json").write_text(
            json.dumps(
                {
                    "rows": rows,
                    "original": {"icon_size": original["icon_size"], "dark": original["dark"]},
                    "restored": restored,
                },
                indent=2,
            )
        )
    valid = [r for r in rows if r["correct"] is not None]
    print(
        f"\n{sum(r['correct'] for r in valid)}/{len(valid)} correct "
        f"({len(rows) - len(valid)} setup-invalid). traces: {OUT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
