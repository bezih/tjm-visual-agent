import math

import pytest

from tjm_agent.geometry import (
    Box,
    center_score,
    dilate,
    iou,
    nms,
    norm1000_to_screenshot,
    score_candidates,
    screenshot_to_points,
)

SCREEN = Box(0, 0, 3420, 2224)


def test_center_score_peaks_at_center_and_matches_eq1():
    cand = Box(0, 0, 100, 100)
    assert center_score(cand, Box.around(50, 50, 4, 4)) == pytest.approx(1.0)
    # voter centre at normalised (0.8, 0.5): exp(-(0.3^2)/(2*0.3^2)) = exp(-0.5)
    assert center_score(cand, Box.around(80, 50, 4, 4)) == pytest.approx(math.exp(-0.5))


def test_center_score_zero_outside():
    assert center_score(Box(0, 0, 100, 100), Box.around(150, 50, 4, 4)) == 0.0


def test_score_candidates_sums_votes():
    cands = [Box(0, 0, 100, 100), Box(200, 200, 300, 300)]
    voters = [Box.around(50, 50, 2, 2), Box.around(50, 50, 2, 2), Box.around(250, 250, 2, 2)]
    s = score_candidates(cands, voters)
    assert s[0] == pytest.approx(2.0)
    assert s[1] == pytest.approx(1.0)


def test_dilate_expands_to_min_size_about_center():
    d = dilate(Box.around(1000, 1000, 40, 40), 1280, SCREEN)
    assert (d.width, d.height) == (1280, 1280)
    assert d.center == (1000, 1000)


def test_dilate_shifts_inside_bounds_at_corner():
    d = dilate(Box.around(10, 10, 40, 40), 1280, SCREEN)
    assert (d.x1, d.y1, d.width, d.height) == (0, 0, 1280, 1280)
    d = dilate(Box.around(3410, 2214, 40, 40), 1280, SCREEN)
    assert (d.x2, d.y2) == (3420, 2224)


def test_dilate_never_exceeds_bounds():
    small = Box(0, 0, 500, 400)
    d = dilate(Box.around(250, 200, 10, 10), 1280, small)
    assert d == small


def test_iou_and_nms():
    a, b, c = Box(0, 0, 10, 10), Box(1, 1, 11, 11), Box(50, 50, 60, 60)
    assert iou(a, a) == 1.0
    assert iou(a, c) == 0.0
    assert nms([a, b, c], [0.9, 0.8, 0.5], 0.5) == [0, 2]


def test_norm1000_maps_through_viewport_crop():
    viewport = Box(1000, 500, 2280, 1780)  # a 1280 px crop
    assert norm1000_to_screenshot(0, 0, viewport) == (1000, 500)
    assert norm1000_to_screenshot(500, 500, viewport) == (1640, 1140)
    assert norm1000_to_screenshot(1000, 1000, viewport) == (2280, 1780)


def test_retina_pixels_to_points():
    assert screenshot_to_points(3420, 2224, 2.0) == (1710, 1112)
    assert screenshot_to_points(1920, 1080, 1.0) == (1920, 1080)


def test_degenerate_box_rejected():
    with pytest.raises(ValueError):
        Box(10, 10, 5, 20)
