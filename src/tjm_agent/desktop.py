"""macOS desktop I/O: screenshots, mouse, keyboard, permissions, and app process state.

This module executes actions; it never decides *where* to click.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
import tempfile
import time
from dataclasses import dataclass

import pyautogui
from PIL import Image

from .geometry import screenshot_to_points

pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort
pyautogui.PAUSE = 0.05


@dataclass(frozen=True)
class Screenshot:
    image: Image.Image  # full-resolution pixels
    scale: float  # pixels per point (2.0 on Retina)
    captured_at: float

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


def capture() -> Screenshot:
    """Full-resolution capture of the main display (Retina pixels, not points)."""
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        subprocess.run(["screencapture", "-x", "-m", "-t", "png", f.name], check=True)
        image = Image.open(f.name).convert("RGB")
    scale = image.width / pyautogui.size().width
    return Screenshot(image=image, scale=scale, captured_at=time.time())


def permissions() -> dict[str, bool]:
    app_services = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
    app_services.AXIsProcessTrusted.restype = ctypes.c_bool
    core_graphics = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
    core_graphics.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
    return {
        "accessibility (mouse/keyboard)": bool(app_services.AXIsProcessTrusted()),
        "screen recording": bool(core_graphics.CGPreflightScreenCaptureAccess()),
    }


def point_for(x_px: float, y_px: float, shot: Screenshot) -> tuple[float, float]:
    return screenshot_to_points(x_px, y_px, shot.scale)


def move(x_pt: float, y_pt: float) -> None:
    pyautogui.moveTo(x_pt, y_pt, duration=0.15)


def double_click(x_pt: float, y_pt: float) -> None:
    pyautogui.moveTo(x_pt, y_pt, duration=0.15)
    pyautogui.doubleClick(x_pt, y_pt, interval=0.08)


def click(x_pt: float, y_pt: float) -> None:
    pyautogui.moveTo(x_pt, y_pt, duration=0.15)
    pyautogui.click(x_pt, y_pt)


def hotkey(*keys: str) -> None:
    pyautogui.hotkey(*keys, interval=0.05)


def press(key: str) -> None:
    pyautogui.press(key)


def type_text(text: str, interval: float = 0.004) -> None:
    """Type text as real keystrokes. Newlines are sent as Return."""
    if not text.isascii():
        raise ValueError("type_text only supports ASCII; non-ASCII would be dropped silently")
    pyautogui.write(text, interval=interval)


def mouse_position() -> tuple[float, float]:
    p = pyautogui.position()
    return (p.x, p.y)


# --- application process state (verification only; never used to locate or launch) ---


def frontmost_app() -> str | None:
    from AppKit import NSWorkspace

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(app.localizedName()) if app else None


def is_running(app_name: str) -> bool:
    return subprocess.run(["pgrep", "-x", app_name], check=False, capture_output=True).returncode == 0


def wait_until(predicate, timeout: float, poll: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


def visible_windows() -> list[tuple[str, tuple[float, float, float, float]]]:
    """Normal (layer-0) windows on the current Space: (owner, (x, y, w, h)) in points."""
    import Quartz

    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    out = []
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID):
        if w.get("kCGWindowLayer") == 0:
            b = w["kCGWindowBounds"]
            out.append((str(w.get("kCGWindowOwnerName")), (b["X"], b["Y"], b["Width"], b["Height"])))
    return out


def _finder_window_ids() -> set[int]:
    import Quartz

    info = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
    return {
        int(w["kCGWindowNumber"])
        for w in info
        if w.get("kCGWindowOwnerName") == "Finder" and w.get("kCGWindowLayer") == 0
    }


def show_desktop() -> bool:
    """Bring the desktop to the front: activate Finder, close any window that activation
    itself opened, and hide other apps. Returns True if no normal windows remain visible.

    Finder is activated through Launch Services (`open -b`), because macOS ignores
    programmatic focus-stealing from a background process. This touches Finder only —
    the target app is never launched this way.
    """
    before = _finder_window_ids()
    subprocess.run(["open", "-b", "com.apple.finder"], check=True)
    wait_until(lambda: frontmost_app() == "Finder", timeout=5)
    time.sleep(0.4)
    if _finder_window_ids() - before:  # the reopen event created a window; close only that one
        hotkey("command", "w")
        time.sleep(0.3)
    hotkey("option", "command", "h")  # Finder ▸ Hide Others
    if any(owner == "Finder" for owner, _ in visible_windows()):
        hotkey("option", "command", "m")  # minimise (never close) pre-existing Finder windows
    return wait_until(lambda: not visible_windows(), timeout=3)
