"""Deterministic ten-post workflow. Visual grounding is used wherever a screen location is
needed (the desktop icon, buttons in unexpected dialogs); everything else is keyboard-driven
and verified against process/window state and the saved file.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import desktop, trace
from .grounding.screenseeker import ScreenSeekeR
from .posts import Post
from .verification import verify_saved

APP = "TextEdit"
BUNDLE = "com.apple.TextEdit"

# Applied to TextEdit's own preference domain for the run only, then restored.
# Each one prevents TextEdit from altering or redirecting what we type.
RUN_PREFS: dict[str, tuple[str, str]] = {
    "RichText": ("-int", "0"),  # new documents are plain text
    "SmartQuotes": ("-bool", "false"),
    "SmartDashes": ("-bool", "false"),
    "TextReplacement": ("-bool", "false"),
    "CorrectSpellingAutomatically": ("-bool", "false"),
    "NSAutomaticSpellingCorrectionEnabled": ("-bool", "false"),
    "NSAutomaticCapitalizationEnabled": ("-bool", "false"),
    "NSAutomaticPeriodSubstitutionEnabled": ("-bool", "false"),
    "NSShowAppCentricOpenPanelInsteadOfUntitledFile": ("-bool", "false"),  # open an Untitled doc
    "ApplePersistenceIgnoreState": ("-bool", "true"),  # never restore last session's windows
}

# The only dialog buttons the workflow may press on its own.
SAFE_BUTTONS = {"ok", "cancel", "close", "not now", "later", "continue", "new document",
                "use .txt", "dismiss", "don't send", "done"}


class WorkflowError(RuntimeError):
    pass


@dataclass
class PostResult:
    post_id: int
    ok: bool
    reason: str
    launch_attempts: int = 0
    grounding: list[dict] = field(default_factory=list)
    interventions: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    fatal: bool = False  # the desktop is in a state where continuing would be unsafe


# --------------------------------------------------------------------------- preferences


class TextEditPrefs:
    """Context manager: set RUN_PREFS, restore the user's previous values afterwards."""

    def __init__(self, backup_path: Path):
        self.backup_path = backup_path

    def _read(self, key: str) -> str | None:
        r = subprocess.run(["defaults", "read", BUNDLE, key], capture_output=True, text=True,
                           check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def __enter__(self):
        if desktop.is_running(APP):
            raise WorkflowError(f"{APP} is already running. Save your work and quit it first; "
                                "the workflow never closes documents it did not create.")
        previous = {k: self._read(k) for k in RUN_PREFS}
        self.backup_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_path.write_text(json.dumps(previous, indent=2))
        for key, (kind, value) in RUN_PREFS.items():
            subprocess.run(["defaults", "write", BUNDLE, key, kind, value], check=True)
        self.previous = previous
        return self

    def __exit__(self, *exc):
        for key, value in self.previous.items():
            if value is None:
                subprocess.run(["defaults", "delete", BUNDLE, key], capture_output=True, check=False)
            else:
                kind = RUN_PREFS[key][0]
                if kind == "-bool":
                    value = "true" if value in ("1", "true", "YES") else "false"
                subprocess.run(["defaults", "write", BUNDLE, key, kind, value], check=True)
        return False


# --------------------------------------------------------------------------- workflow


class PostWorkflow:
    def __init__(self, engine: ScreenSeekeR, icon_target: str, output_dir: Path, run_dir: Path,
                 log=print):
        self.engine = engine
        self.icon_target = icon_target
        self.output_dir = output_dir
        self.run_dir = run_dir
        self.log = log

    # ---- launch -----------------------------------------------------------------------

    def ground_and_launch(self, post: Post, result: PostResult, max_attempts: int = 2) -> None:
        for attempt in range(1, max_attempts + 1):
            result.launch_attempts = attempt
            if not desktop.show_desktop():
                self.log("  warning: some windows still visible over the desktop")
            shot = desktop.capture()  # fresh screenshot before every launch
            outcome = self.engine.search(shot.image, self.icon_target)
            click_pt = desktop.point_for(*outcome.point_px, shot) if outcome.point_px else None
            name = f"post{post.id:02d}_launch{attempt}"
            trace.save(self.run_dir, name, shot.image, outcome, click_pt, title=name)
            result.grounding.append({
                "attempt": attempt, "status": outcome.status, "point_px": outcome.point_px,
                "click_pt": click_pt, "seconds": round(outcome.seconds, 1),
                "planner_calls": outcome.planner_calls, "grounder_calls": outcome.grounder_calls,
            })
            self.log(f"  grounding #{attempt}: {outcome.status} {click_pt or ''} "
                     f"({outcome.seconds:.1f}s, planner {outcome.planner_calls}, "
                     f"grounder {outcome.grounder_calls})")
            if outcome.status != "found":
                continue  # never click on uncertainty; re-capture and search again

            # The screen may have changed since the screenshot (another app took focus).
            # Only click if the desktop is still what we grounded on.
            if desktop.visible_windows():
                self.log("  screen changed since screenshot; re-grounding instead of clicking")
                continue
            desktop.double_click(*click_pt)
            launched = desktop.wait_until(
                lambda: desktop.is_running(APP) and desktop.frontmost_app() == APP, timeout=12)
            if launched:
                return
            # Wrong click: something else opened (e.g. a folder). Close only what we opened.
            front = desktop.frontmost_app()
            self.log(f"  launch failed: frontmost is {front!r}; recovering")
            if front == "Finder" and desktop.visible_windows():
                desktop.hotkey("command", "w")
        raise WorkflowError(f"could not launch {APP} from its desktop icon "
                            f"after {max_attempts} attempts")

    def ensure_untitled_document(self, result: PostResult) -> None:
        def untitled() -> bool:
            return any(t.startswith("Untitled") for t in desktop.app_windows(APP))

        if desktop.wait_until(untitled, timeout=6):
            return
        self.intervene(result, step="waiting for TextEdit to show a new empty document",
                       expected="a TextEdit window titled 'Untitled'")
        if not desktop.wait_until(untitled, timeout=4):
            desktop.hotkey("command", "n")  # File ▸ New, then re-check
            if not desktop.wait_until(untitled, timeout=4):
                raise WorkflowError(f"no Untitled document; windows: {desktop.app_windows(APP)}")

    # ---- unexpected UI ----------------------------------------------------------------

    def intervene(self, result: PostResult, step: str, expected: str) -> bool:
        """Triage an unexpected state; press a button only if it is on the safe list."""
        shot = desktop.capture()
        triage = self.engine.planner.triage(shot.image, step, expected)
        record = {"step": step, **triage.model_dump()}
        result.interventions.append(record)
        self.log(f"  unexpected state: {triage.risk} — {triage.description}")
        button = (triage.button_to_click or "").strip()
        if triage.risk != "safe" or button.lower() not in SAFE_BUTTONS:
            record["action"] = "stopped"
            if triage.risk in ("destructive", "permission", "unknown"):
                raise WorkflowError(f"stopped at {triage.risk} dialog: {triage.description}")
            return False
        target = f'"{button}" button in the {triage.description}'
        outcome = self.engine.search(shot.image, target)
        name = f"post{result.post_id:02d}_intervention{len(result.interventions)}"
        click_pt = desktop.point_for(*outcome.point_px, shot) if outcome.point_px else None
        trace.save(self.run_dir, name, shot.image, outcome, click_pt, title=name)
        if outcome.status != "found":
            record["action"] = f"button not grounded ({outcome.status})"
            raise WorkflowError(f"could not locate '{button}' in {triage.description}")
        desktop.click(*click_pt)
        record["action"] = f"clicked {button} at {click_pt}"
        time.sleep(0.8)
        return True

    # ---- one post ---------------------------------------------------------------------

    def run_post(self, post: Post) -> PostResult:
        t0 = time.monotonic()
        result = PostResult(post.id, ok=False, reason="")
        target = self.output_dir / post.filename
        try:
            if target.exists():
                raise WorkflowError(f"{target} already exists; refusing to overwrite")
            self.ground_and_launch(post, result)
            self.ensure_untitled_document(result)

            if desktop.frontmost_app() != APP:
                raise WorkflowError("TextEdit lost focus before typing; not typing blind")
            desktop.type_text(post.text)
            time.sleep(0.3)

            self.save_as(post, result)
            check = verify_saved(target, post.text)
            if not check.ok:
                raise WorkflowError(f"saved file failed verification: {check.reason}")
            result.ok, result.reason = True, check.reason
        except WorkflowError as e:
            result.reason = str(e)
        finally:
            try:
                self.quit_app(result)
            except WorkflowError as e:
                result.ok, result.fatal = False, True
                result.reason = (result.reason + "; " if result.reason else "") + str(e)
            result.seconds = round(time.monotonic() - t0, 1)
        return result

    def _type_into_focused_field(self, text: str, what: str) -> None:
        """Select-all + type into the focused text field, then read it back via
        Accessibility. Never press Return on a field whose content we have not confirmed."""
        for _ in range(2):
            desktop.hotkey("command", "a")
            desktop.type_text(text)
            if desktop.wait_until(lambda: desktop.focused_field(APP)[1] == text, timeout=2):
                return
        raise WorkflowError(f"{what} field shows {desktop.focused_field(APP)[1]!r}, "
                            f"expected {text!r}")

    def _wait_for_text_field(self, what: str, timeout: float = 6) -> None:
        if not desktop.wait_until(lambda: desktop.focused_field(APP)[0] == "AXTextField",
                                  timeout=timeout):
            raise WorkflowError(f"{what} did not appear (focused: {desktop.focused_field(APP)})")

    def save_as(self, post: Post, result: PostResult) -> None:
        target = self.output_dir / post.filename
        desktop.hotkey("command", "s")
        self._wait_for_text_field("save sheet name field")
        time.sleep(0.3)  # let the sheet finish animating
        self._type_into_focused_field(post.filename, "save name")

        desktop.hotkey("command", "shift", "g")  # Go to Folder
        if not desktop.wait_until(
                lambda: desktop.focused_field(APP)[0] == "AXTextField"
                and desktop.focused_field(APP)[1] != post.filename, timeout=5):
            raise WorkflowError("Go to Folder field did not appear")
        self._type_into_focused_field(str(self.output_dir), "Go to Folder")
        time.sleep(0.4)  # path completion list settles
        desktop.press("return")  # confirm folder; focus returns to the name field

        if not desktop.wait_until(lambda: desktop.focused_field(APP)[1] == post.filename,
                                  timeout=4):
            # Some macOS versions reset/blur the name field after navigation; re-enter it.
            self._wait_for_text_field("save sheet name field", timeout=2)
            self._type_into_focused_field(post.filename, "save name")
        desktop.press("return")  # default button: Save
        if desktop.wait_until(target.exists, timeout=6):
            return
        intervened = self.intervene(
            result, step=f"saving the document as {target}",
            expected=f"the file {post.filename} to be saved and the sheet closed")
        if intervened and desktop.wait_until(target.exists, timeout=4):
            return
        raise WorkflowError(f"{target} was not created by the save dialog")

    def quit_app(self, result: PostResult) -> None:
        if not desktop.is_running(APP):
            return
        if desktop.frontmost_app() == APP:
            desktop.hotkey("command", "q")
        if desktop.wait_until(lambda: not desktop.is_running(APP), timeout=6):
            return
        try:
            self.intervene(result, step="quitting TextEdit after saving",
                           expected="TextEdit to quit")
        except WorkflowError as e:
            result.reason = (result.reason + "; " if result.reason else "") + str(e)
        if not desktop.wait_until(lambda: not desktop.is_running(APP), timeout=6):
            raise WorkflowError(f"{APP} did not quit; stopping so the next launch is not ambiguous")


def write_summary(run_dir: Path, results: list[PostResult], extra: dict) -> Path:
    path = run_dir / "summary.json"
    path.write_text(json.dumps({**extra, "posts": [asdict(r) for r in results]}, indent=2,
                               default=str))
    return path
