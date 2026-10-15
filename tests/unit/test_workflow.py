"""Workflow state transitions against a scripted fake desktop and fake models.

These tests never touch the real screen, keyboard, or TextEdit. They pin down the safety
rules: no click on uncertainty or on a changed screen, only allowlisted dialog buttons,
no overwrite, no Return on an unverified field, exact preference restore.
"""


import pytest
from PIL import Image

from tjm_agent import workflow
from tjm_agent.grounding.backends import DialogTriage
from tjm_agent.grounding.screenseeker import Outcome
from tjm_agent.posts import Post
from tjm_agent.workflow import PostResult, PostWorkflow, WorkflowError, safe_to_click

POST = Post(1, "hello", "world")


class FakeShot:
    image = Image.new("RGB", (40, 20))
    scale = 2.0


class FakeDesktop:
    """Records every action; behaviour is scripted per test."""

    def __init__(self):
        self.actions = []
        self.windows_seq = [[]]  # successive visible_windows() results (last one repeats)
        self.launch_on_double_click = True
        self.running = False
        self.field = (None, None)
        self.typed = []

    # screen state
    def show_desktop(self):
        return True

    def capture(self):
        return FakeShot()

    def visible_windows(self):
        return self.windows_seq.pop(0) if len(self.windows_seq) > 1 else self.windows_seq[0]

    def point_for(self, x, y, shot):
        return (x / 2, y / 2)

    def frontmost_app(self):
        return workflow.APP if self.running else "Finder"

    def is_running(self, name):
        return self.running

    def wait_until(self, predicate, timeout, poll=0.25):
        return predicate()

    def app_windows(self, name):
        return ["Untitled"] if self.running else []

    def focused_field(self, name):
        return self.field

    def desktop_area(self):
        return (0.0, 20.0, 1710.0, 1000.0)  # menu bar above, Dock below y=1020

    # actions
    def double_click(self, x, y):
        self.actions.append(("double_click", x, y))
        self.running = self.launch_on_double_click

    def click(self, x, y):
        self.actions.append(("click", x, y))

    def hotkey(self, *keys):
        self.actions.append(("hotkey", keys))

    def press(self, key):
        self.actions.append(("press", key))

    def type_text(self, text):
        self.typed.append(text)
        self.actions.append(("type", text))


class FakePlanner:
    def __init__(self, triages=()):
        self.triages = list(triages)
        self.calls = 0

    def triage(self, image, step, expected):
        self.calls += 1
        return self.triages.pop(0) if self.triages else DialogTriage(
            description="nothing unusual", buttons=[], risk="none")


class FakeEngine:
    def __init__(self, outcomes, planner=None):
        self.outcomes = list(outcomes)
        self.planner = planner or FakePlanner()
        self.targets = []

    def search(self, image, target):
        self.targets.append(target)
        return self.outcomes.pop(0)


def found(x=200.0, y=100.0):
    return Outcome(status="found", instruction="t", point_px=(x, y))


def not_found():
    return Outcome(status="not_found", instruction="t")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    d = FakeDesktop()
    monkeypatch.setattr(workflow, "desktop", d)
    monkeypatch.setattr(workflow.trace, "save", lambda *a, **k: None)
    monkeypatch.setattr(workflow.time, "sleep", lambda s: None)
    return d


def _flow(engine, tmp_path):
    return PostWorkflow(engine, "TextEdit icon", tmp_path, tmp_path / "run", log=lambda m: None)


def test_safe_to_click_rules():
    win = [("Chrome", (0.0, 0.0, 100.0, 100.0))]
    assert safe_to_click((50, 50), [], [])
    assert not safe_to_click((50, 50), win, win)  # point under a window
    assert safe_to_click((150, 50), win, win)  # window elsewhere, unchanged
    assert not safe_to_click((150, 50), [], win)  # a window appeared after the screenshot


def test_launch_clicks_the_grounded_point(fake, tmp_path):
    engine = FakeEngine([found(200, 100)])
    result = PostResult(1, False, "")
    _flow(engine, tmp_path).ground_and_launch(POST, result)
    assert fake.actions == [("double_click", 100, 50)]  # px / scale → points
    assert result.launch_attempts == 1


def test_never_clicks_when_target_not_found(fake, tmp_path):
    engine = FakeEngine([not_found(), not_found(), not_found()])
    with pytest.raises(WorkflowError, match="could not launch"):
        _flow(engine, tmp_path).ground_and_launch(POST, PostResult(1, False, ""))
    assert not [a for a in fake.actions if a[0] in ("click", "double_click")]
    assert engine.planner.calls == 2  # triage between attempts, not after the last


def test_every_attempt_regrounds_from_a_fresh_search(fake, tmp_path):
    engine = FakeEngine([not_found(), found()])
    _flow(engine, tmp_path).ground_and_launch(POST, PostResult(1, False, ""))
    assert len(engine.targets) == 2


def test_screen_change_between_capture_and_click_prevents_click(fake, tmp_path):
    fake.windows_seq = [[], [("Messages", (0.0, 0.0, 500.0, 500.0))], [], []]
    engine = FakeEngine([found(), found()])
    _flow(engine, tmp_path).ground_and_launch(POST, PostResult(1, False, ""))
    clicks = [a for a in fake.actions if a[0] == "double_click"]
    assert len(clicks) == 1 and len(engine.targets) == 2  # first skipped, second clicked


def test_dialog_covering_icon_dismissed_by_safe_button_then_relaunch(fake, tmp_path):
    triage = DialogTriage(description="Software Update alert", buttons=["Later", "Install"],
                          risk="safe", button_to_click="Later")
    engine = FakeEngine([not_found(), found(30, 30), found(200, 100)], FakePlanner([triage]))
    _flow(engine, tmp_path).ground_and_launch(POST, PostResult(1, False, ""))
    assert engine.targets[1].startswith('"Later" button')  # button grounded by description
    assert fake.actions[0] == ("click", 15, 15)
    assert fake.actions[1] == ("double_click", 100, 50)


def test_non_allowlisted_button_is_never_pressed(fake, tmp_path):
    triage = DialogTriage(description="update", buttons=["Install"], risk="safe",
                          button_to_click="Install")
    engine = FakeEngine([], FakePlanner([triage]))
    result = PostResult(1, False, "")
    assert _flow(engine, tmp_path).intervene(result, "step", "expected") is False
    assert fake.actions == [] and result.interventions[0]["action"] == "stopped"


@pytest.mark.parametrize("risk", ["destructive", "permission", "unknown"])
def test_risky_dialogs_stop_the_run(fake, tmp_path, risk):
    triage = DialogTriage(description="d", buttons=["OK"], risk=risk, button_to_click="OK")
    engine = FakeEngine([], FakePlanner([triage]))
    with pytest.raises(WorkflowError, match=risk):
        _flow(engine, tmp_path).intervene(PostResult(1, False, ""), "step", "expected")
    assert fake.actions == []


def test_existing_output_is_never_overwritten(fake, tmp_path):
    (tmp_path / POST.filename).write_text("user data")
    result = _flow(FakeEngine([]), tmp_path).run_post(POST)
    assert not result.ok and "refusing to overwrite" in result.reason
    assert (tmp_path / POST.filename).read_text() == "user data"
    assert fake.actions == []


def test_save_never_presses_return_on_unverified_field(fake, tmp_path):
    fake.field = ("AXTextField", "post_") # field never shows the full name
    with pytest.raises(WorkflowError, match="save name"):
        _flow(FakeEngine([]), tmp_path).save_as(POST, PostResult(1, False, ""))
    assert ("press", "return") not in fake.actions
    assert fake.typed == [POST.filename, POST.filename]  # one retry, then stop


def test_textedit_prefs_are_restored_exactly(monkeypatch, tmp_path):
    store = {"RichText": "1", "SmartQuotes": "0"}  # user's prior values; others unset
    calls = []

    class R:
        def __init__(self, rc, out=""):
            self.returncode, self.stdout = rc, out

    def fake_run(cmd, **kw):
        calls.append(cmd)
        op, key = cmd[1], cmd[3] if len(cmd) > 3 else None
        if op == "read":
            return R(0, store[key]) if key in store else R(1)
        if op == "write":
            store[key] = cmd[5]
        if op == "delete":
            store.pop(key, None)
        return R(0)

    monkeypatch.setattr(workflow.subprocess, "run", fake_run)
    monkeypatch.setattr(workflow.desktop, "is_running", lambda name: False)
    with workflow.TextEditPrefs(tmp_path / "backup.json"):
        assert store["RichText"] == "0" and store["SmartQuotes"] == "false"
        assert store["NSAutomaticCapitalizationEnabled"] == "false"
    assert store == {"RichText": "1", "SmartQuotes": "false"}
    # SmartQuotes "0" is restored in its boolean spelling; unset keys are deleted again.


def test_prefs_refuse_to_start_while_textedit_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow.desktop, "is_running", lambda name: True)
    with pytest.raises(WorkflowError, match="already running"):
        workflow.TextEditPrefs(tmp_path / "b.json").__enter__()


def test_hit_in_dock_is_never_double_clicked(fake, tmp_path):
    # e.g. the Dock's TextEdit icon accepted for "TextEdit icon on the desktop"
    engine = FakeEngine([found(2630, 2142), found(200, 100)])  # px → (1315, 1071) pt = Dock
    _flow(engine, tmp_path).ground_and_launch(POST, PostResult(1, False, ""))
    assert [a for a in fake.actions if a[0] == "double_click"] == [("double_click", 100, 50)]
