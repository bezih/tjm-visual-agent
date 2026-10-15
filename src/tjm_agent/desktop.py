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
    """Double-click with Quartz events carrying an explicit click count (1 then 2).

    pyautogui's doubleClick posts two independent clicks; Finder does not treat those as a
    double-click on a desktop icon.
    """
    import Quartz

    pyautogui.moveTo(x_pt, y_pt, duration=0.15)
    point = Quartz.CGPointMake(x_pt, y_pt)
    for count in (1, 2):
        for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            ev = Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, count)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.02)
        time.sleep(0.06)


def click(x_pt: float, y_pt: float) -> None:
    pyautogui.moveTo(x_pt, y_pt, duration=0.15)
    pyautogui.click(x_pt, y_pt)


def hotkey(*keys: str) -> None:
    pyautogui.hotkey(*keys, interval=0.05)


def press(key: str) -> None:
    pyautogui.press(key)


_RETURN_KEYCODE = 36


def type_text(text: str, interval: float = 0.006) -> None:
    """Type text as real key events. Each character is posted as a Quartz keyboard event
    carrying its Unicode value (layout-independent, correct case); newlines are Return.

    pyautogui.write is not used: on macOS it drops Shift for some capitals ("Title" → "title").
    """
    import Quartz

    if not text.isascii():
        raise ValueError("type_text only supports ASCII")
    for ch in text:
        if ch == "\n":
            down = Quartz.CGEventCreateKeyboardEvent(None, _RETURN_KEYCODE, True)
            up = Quartz.CGEventCreateKeyboardEvent(None, _RETURN_KEYCODE, False)
        else:
            down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
            up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
            Quartz.CGEventKeyboardSetUnicodeString(down, 1, ch)
            Quartz.CGEventKeyboardSetUnicodeString(up, 1, ch)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(interval)


def mouse_position() -> tuple[float, float]:
    p = pyautogui.position()
    return (p.x, p.y)


# --- application process state (verification only; never used to locate or launch) ---


def frontmost_app() -> str | None:
    from AppKit import NSWorkspace
    from Foundation import NSDate, NSRunLoop

    # NSWorkspace only refreshes frontmostApplication when the run loop turns.
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))
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


# Normal windows (0), floating panels (3), modal alerts (8), utility windows (19) and
# notification banners (<24) can cover desktop icons. The Dock (20) and the menu bar /
# status items (24+) are permanent screen furniture, not obstructions.
_MAX_OBSTRUCTING_LAYER = 23
_SYSTEM_CHROME = {"Dock", "Window Server"}


def _visible_window_info() -> list[dict]:
    import Quartz

    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    return [w for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
            if 0 <= w.get("kCGWindowLayer", -1) <= _MAX_OBSTRUCTING_LAYER
            and w.get("kCGWindowOwnerName") not in _SYSTEM_CHROME]


def visible_windows() -> list[tuple[str, tuple[float, float, float, float]]]:
    """Windows that could cover the desktop on the current Space: (owner, (x, y, w, h)) pt."""
    out = []
    for w in _visible_window_info():
        b = w["kCGWindowBounds"]
        out.append((str(w.get("kCGWindowOwnerName")), (b["X"], b["Y"], b["Width"], b["Height"])))
    return out


def _ax_windows(pid: int) -> list:
    from ApplicationServices import AXUIElementCopyAttributeValue, AXUIElementCreateApplication

    _err, windows = AXUIElementCopyAttributeValue(AXUIElementCreateApplication(pid), "AXWindows",
                                                  None)
    return list(windows or [])


def _minimize_windows(pid: int) -> None:
    """Minimise an app's windows through the Accessibility API (no keystrokes)."""
    from ApplicationServices import AXUIElementSetAttributeValue

    for win in _ax_windows(pid):
        AXUIElementSetAttributeValue(win, "AXMinimized", True)


def _close_ax_window(win) -> None:
    from ApplicationServices import AXUIElementCopyAttributeValue, AXUIElementPerformAction

    _err, button = AXUIElementCopyAttributeValue(win, "AXCloseButton", None)
    if button is not None:
        AXUIElementPerformAction(button, "AXPress")


def _finder_pid() -> int | None:
    from AppKit import NSRunningApplication

    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.finder")
    return int(apps[0].processIdentifier()) if apps else None


def show_desktop() -> bool:
    """Uncover the desktop without sending any keystrokes. Returns True when Finder is
    frontmost and no normal windows remain visible.

    1. Activate Finder via Launch Services (`open -b`) — the only reliable way to leave a
       full-screen Space. If that reopen event creates a Finder window, close exactly that
       window via its Accessibility close button.
    2. Hide every other app with windows on screen (NSRunningApplication.hide).
    3. Minimise (never close) any pre-existing Finder windows via Accessibility.

    Keystrokes are deliberately avoided: shortcuts go to whichever app is frontmost, and
    modifier taps can trigger user-configured global hotkeys.
    """
    from AppKit import NSRunningApplication

    finder = _finder_pid()
    if finder is None:
        return False
    before = len(_ax_windows(finder))
    subprocess.run(["open", "-b", "com.apple.finder"], check=True)
    wait_until(lambda: frontmost_app() == "Finder", timeout=5)
    time.sleep(0.4)
    after = _ax_windows(finder)
    if len(after) > before:
        _close_ax_window(after[0])  # the window our reopen event created (frontmost)
        time.sleep(0.3)

    for _ in range(2):
        pids = {int(w["kCGWindowOwnerPID"]): str(w.get("kCGWindowOwnerName"))
                for w in _visible_window_info()}
        for pid, owner in pids.items():
            if owner == "Finder":
                _minimize_windows(pid)
            else:
                app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                if app is not None:
                    app.hide()
        if wait_until(lambda: not visible_windows(), timeout=3):
            break
    return frontmost_app() == "Finder" and not visible_windows()


def app_windows(app_name: str) -> list[str]:
    """Titles of the app's on-screen windows (verification only)."""
    import Quartz

    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    return [
        str(w.get("kCGWindowName") or "")
        for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
        if w.get("kCGWindowOwnerName") == app_name and w.get("kCGWindowLayer") == 0
    ]


def focused_field(app_name: str) -> tuple[str | None, str | None]:
    """(AXRole, AXValue) of the app's focused UI element — used to confirm what a text
    field actually contains before pressing Return. Verification only."""
    from AppKit import NSRunningApplication, NSWorkspace
    from ApplicationServices import AXUIElementCopyAttributeValue, AXUIElementCreateApplication

    pid = next((int(a.processIdentifier()) for a in NSWorkspace.sharedWorkspace().runningApplications()
                if a.localizedName() == app_name), None)
    if pid is None or NSRunningApplication.runningApplicationWithProcessIdentifier_(pid) is None:
        return None, None
    _e, el = AXUIElementCopyAttributeValue(AXUIElementCreateApplication(pid), "AXFocusedUIElement",
                                           None)
    if el is None:
        return None, None
    _e, role = AXUIElementCopyAttributeValue(el, "AXRole", None)
    _e, value = AXUIElementCopyAttributeValue(el, "AXValue", None)
    return (str(role) if role else None, str(value) if value is not None else None)


def desktop_area() -> tuple[float, float, float, float]:
    """(x, y, w, h) in top-left points of the screen area that is neither menu bar nor Dock
    (NSScreen.visibleFrame). Desktop icons can only be here."""
    from AppKit import NSScreen

    screen = NSScreen.mainScreen()
    full, vis = screen.frame(), screen.visibleFrame()
    top = full.size.height - (vis.origin.y + vis.size.height)  # Cocoa origin is bottom-left
    return (vis.origin.x, top, vis.size.width, vis.size.height)


def dock_rect() -> tuple[float, float, float, float] | None:
    """(x, y, w, h) in top-left points of the Dock's icon strip, via Accessibility.

    The Dock reports the strip's position with y at its bottom edge (y=1112 on a 1112-pt
    screen), so the rectangle is clamped back inside the screen.
    """
    from AppKit import NSRunningApplication, NSScreen
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        AXValueGetValue,
        kAXValueCGPointType,
        kAXValueCGSizeType,
    )

    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.dock")
    if not apps:
        return None
    _e, kids = AXUIElementCopyAttributeValue(
        AXUIElementCreateApplication(apps[0].processIdentifier()), "AXChildren", None)
    for kid in kids or []:
        _e, role = AXUIElementCopyAttributeValue(kid, "AXRole", None)
        if role != "AXList":
            continue
        _e, pos = AXUIElementCopyAttributeValue(kid, "AXPosition", None)
        _e, size = AXUIElementCopyAttributeValue(kid, "AXSize", None)
        p = AXValueGetValue(pos, kAXValueCGPointType, None)[1]
        s = AXValueGetValue(size, kAXValueCGSizeType, None)[1]
        frame = NSScreen.mainScreen().frame()
        x = min(max(p.x, 0), frame.size.width - s.width)
        y = min(max(p.y, 0), frame.size.height - s.height)
        return (x, y, s.width, s.height)
    return None


def on_desktop_surface(x_pt: float, y_pt: float) -> bool:
    """False if the point is in the menu bar or on the Dock's icon strip (where a desktop icon
    cannot be). Falls back to the conservative visible frame if the Dock cannot be read."""
    vx, vy, vw, vh = desktop_area()
    if y_pt < vy:  # menu bar
        return False
    dock = dock_rect()
    if dock is None:
        return vx <= x_pt <= vx + vw and vy <= y_pt <= vy + vh
    dx, dy, dw, dh = dock
    return not (dx <= x_pt <= dx + dw and dy <= y_pt <= dy + dh)
