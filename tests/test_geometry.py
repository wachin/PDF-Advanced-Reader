"""Unit tests for rotation / coordinate mapping helpers."""

import math

from pdfar.geometry import (
    display_size, map_point, map_rect, normalize_rotation, unmap_point, unmap_rect,
)


def test_normalize_rotation():
    assert normalize_rotation(0) == 0
    assert normalize_rotation(90) == 90
    assert normalize_rotation(450) == 90
    assert normalize_rotation(-90) == 270
    assert normalize_rotation(12) == 0
    assert normalize_rotation(3599) == 0


def test_display_size_swaps_on_quarter_turns():
    assert display_size(200, 100, 0) == (200, 100)
    assert display_size(200, 100, 90) == (100, 200)
    assert display_size(200, 100, 180) == (200, 100)
    assert display_size(200, 100, 270) == (100, 200)


def test_map_point_quarter_turns():
    w, h = 200, 100
    assert map_point(10, 20, w, h, 0) == (10, 20)
    assert map_point(10, 20, w, h, 90) == (80, 10)     # clockwise
    assert map_point(10, 20, w, h, 180) == (190, 80)
    assert map_point(10, 20, w, h, 270) == (20, 190)


def test_map_unmap_roundtrip():
    w, h = 200, 100
    for rot in (0, 90, 180, 270):
        for (x, y) in [(0, 0), (10, 20), (200, 100), (77.5, 33.25)]:
            u, v = map_point(x, y, w, h, rot)
            rx, ry = unmap_point(u, v, w, h, rot)
            assert math.isclose(rx, x, abs_tol=1e-6)
            assert math.isclose(ry, y, abs_tol=1e-6)


def test_map_rect_stays_inside_rotated_page():
    w, h = 200, 100
    rect = (10, 10, 40, 40)
    for rot in (0, 90, 180, 270):
        x0, y0, x1, y1 = map_rect(rect, w, h, rot)
        dw, dh = display_size(w, h, rot)
        assert 0 <= x0 <= x1 <= dw
        assert 0 <= y0 <= y1 <= dh
        assert math.isclose((x1 - x0) * (y1 - y0), 30 * 30, abs_tol=1e-6)


def test_map_rect_known_values():
    # 200x100 page, square at top-left (10,10)-(40,40)
    assert map_rect((10, 10, 40, 40), 200, 100, 0) == (10, 10, 40, 40)
    assert map_rect((10, 10, 40, 40), 200, 100, 90) == (60, 10, 90, 40)    # top-right
    assert map_rect((10, 10, 40, 40), 200, 100, 180) == (160, 60, 190, 90)  # bottom-right
    assert map_rect((10, 10, 40, 40), 200, 100, 270) == (10, 160, 40, 190)  # bottom-left


def test_unmap_rect_matches_map_inverse():
    w, h = 200, 100
    rect = (5, 6, 50, 60)
    for rot in (0, 90, 180, 270):
        back = unmap_rect(map_rect(rect, w, h, rot), w, h, rot)
        for a, b in zip(back, rect):
            assert math.isclose(a, b, abs_tol=1e-6)
