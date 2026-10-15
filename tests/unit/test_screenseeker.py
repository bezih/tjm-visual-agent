"""ScreenSeekeR control flow with scripted (fake) planner/grounder — no models, no desktop."""

import pytest
from PIL import Image

from tjm_agent.grounding.backends import (
    CheckVerdict,
    GroundPoint,
    parse_grounder_output,
    parse_position_inference,
)
from tjm_agent.grounding.screenseeker import ScreenSeekeR, SearchConfig

W, H = 3420, 2224
TARGET_PX = (3290.0, 380.0)  # where the "icon" really is, in screenshot pixels


class FakeGrounder:
    """Points at TARGET_PX for any reference whose text mentions 'icon', refuses otherwise."""

    def __init__(self, present=True):
        self.calls = 0
        self.present = present
        self.seen_sizes = []

    def ground(self, image, instruction):
        self.calls += 1
        self.seen_sizes.append(image.size)
        if not self.present or "icon" not in instruction:
            return GroundPoint("[-1,-1]", None, None)
        # The fake knows which crop it is shown via the image's info dict.
        x1, y1 = image.info.get("origin", (0, 0))
        x = (TARGET_PX[0] - x1) / image.width * 1000
        y = (TARGET_PX[1] - y1) / image.height * 1000
        if not (0 <= x <= 1000 and 0 <= y <= 1000):
            return GroundPoint("[-1,-1]", None, None)
        return GroundPoint(f"[{x:.0f},{y:.0f}]", x, y)


class FakePlanner:
    def __init__(self, raw, verdicts):
        self.calls = 0
        self.raw = raw
        self.verdicts = list(verdicts)
        self.checked = 0
        self.contexts = []

    def position_inference(self, image, instruction):
        self.calls += 1
        return parse_position_inference(self.raw)

    def check(self, marked_crop, instruction, context=None):
        self.contexts.append(context)
        self.calls += 1
        self.checked += 1
        return self.verdicts.pop(0)


def _engine(planner, grounder, **cfg):
    eng = ScreenSeekeR(planner, grounder, SearchConfig(**cfg))
    orig_crop = eng._crop

    def crop(viewport):
        img = orig_crop(viewport)
        img.info["origin"] = (viewport.x1, viewport.y1)
        return img

    eng._crop = crop
    return eng


RAW = ("The <element>TextEdit icon</element> is in the <area>desktop icon column</area>, "
       "next to the <neighbor>Google Ads Editor icon</neighbor>.")
SCREEN = Image.new("RGB", (W, H), "white")


def test_found_after_one_recursion_and_check():
    planner = FakePlanner(RAW, [CheckVerdict(analysis="", result="is_target")])
    grounder = FakeGrounder()
    out = _engine(planner, grounder).search(SCREEN, "TextEdit icon on the desktop")
    assert out.status == "found"
    assert abs(out.point_px[0] - TARGET_PX[0]) < 2 and abs(out.point_px[1] - TARGET_PX[1]) < 2
    # leaf crop is no larger than leaf_size and contains the target
    assert max(out.viewport.width, out.viewport.height) <= 1280
    assert out.viewport.contains(*TARGET_PX)
    assert out.planner_calls == 2  # position inference + one check
    assert out.events[0]["event"] == "position_inference"


def test_check_rejection_prunes_and_reports_not_found():
    verdicts = [CheckVerdict(analysis="", result="target_not_found")] * 5
    out = _engine(FakePlanner(RAW, verdicts), FakeGrounder()).search(SCREEN, "TextEdit icon")
    assert out.status == "not_found"
    assert out.point_px is None


def test_absent_target_grounder_refuses_everything():
    out = _engine(FakePlanner(RAW, []), FakeGrounder(present=False)).search(SCREEN, "x icon")
    assert out.status == "not_found"
    assert out.planner_calls == 1  # no voters → no candidates → no checks


def test_no_target_from_planner_short_circuits():
    out = _engine(FakePlanner("No target", []), FakeGrounder()).search(SCREEN, "x icon")
    assert out.status == "not_found"
    assert out.grounder_calls == 0


def test_target_elsewhere_retries_with_rewritten_instruction_once():
    verdicts = [
        CheckVerdict(analysis="", result="target_elsewhere", new_instruction="TextEdit alias icon"),
        CheckVerdict(analysis="", result="is_target"),
    ]
    out = _engine(FakePlanner(RAW, verdicts), FakeGrounder()).search(SCREEN, "TextEdit icon")
    assert out.status == "found"
    assert out.planner_calls == 3


def test_budget_exhaustion_is_reported_not_guessed():
    verdicts = [CheckVerdict(analysis="", result="target_not_found")] * 5
    out = _engine(FakePlanner(RAW, verdicts), FakeGrounder(), max_planner_calls=1).search(
        SCREEN, "TextEdit icon"
    )
    assert out.status == "budget_exhausted"
    assert out.point_px is None


def test_parse_position_inference_dedupes_and_orders():
    pi = parse_position_inference(RAW + " Also the <element>TextEdit icon</element>.")
    assert [(r.kind, r.text) for r in pi.references] == [
        ("element", "TextEdit icon"),
        ("area", "desktop icon column"),
        ("neighbor", "Google Ads Editor icon"),
    ]


def test_parse_grounder_output_formats():
    assert (parse_grounder_output("[512, 300]").x, parse_grounder_output("[512, 300]").y) == (512, 300)
    assert parse_grounder_output("[100,100,200,300]").x == 150
    assert parse_grounder_output("[-1,-1]").refused
    assert parse_grounder_output("I cannot find it").refused
    assert parse_grounder_output("[1500, 20]").refused  # off-grid → treated as refusal


def test_adaptive_fast_path_accepts_verified_direct_hit():
    planner = FakePlanner(RAW, [CheckVerdict(analysis="", result="is_target")])
    grounder = FakeGrounder()
    out = _engine(planner, grounder, mode="adaptive").search(SCREEN, "TextEdit icon")
    assert out.status == "found"
    assert (out.planner_calls, out.grounder_calls) == (1, 1)  # no position inference needed
    assert out.events[0]["event"] == "fast_path_ground"


def test_adaptive_falls_back_to_full_search_when_check_fails():
    verdicts = [CheckVerdict(analysis="", result="target_not_found"),
                CheckVerdict(analysis="", result="is_target")]
    out = _engine(FakePlanner(RAW, verdicts), FakeGrounder(), mode="adaptive").search(
        SCREEN, "TextEdit icon")
    assert out.status == "found"
    events = [e["event"] for e in out.events]
    assert events[:3] == ["fast_path_ground", "fast_path_check", "position_inference"]


def test_result_check_receives_whole_screen_context():
    planner = FakePlanner(RAW, [CheckVerdict(analysis="", result="is_target")])
    _engine(planner, FakeGrounder()).search(SCREEN, "TextEdit icon on the desktop")
    ctx = planner.contexts[0]
    assert ctx is not None and max(ctx.size) <= 1024  # thumbnail of the full screen
    assert ctx.size[0] / ctx.size[1] == pytest.approx(W / H, rel=0.01)
