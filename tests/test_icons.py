"""Monochrome vector icons render at the exact requested size."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor

from pdfar.icons import tinted_icon


def test_icon_renders_at_requested_size(qapp):
    pixmap = tinted_icon("zoom_in.svg", "#3c3c3c").pixmap(QSize(18, 18))
    assert not pixmap.isNull()
    assert pixmap.size() == QSize(18, 18)
    assert pixmap.devicePixelRatio() == 1.0


def test_icon_renders_at_device_pixel_ratio(qapp):
    pixmap = tinted_icon("zoom_in.svg", "#3c3c3c").pixmap(QSize(18, 18), 2.0)
    assert not pixmap.isNull()
    assert pixmap.size() == QSize(36, 36)
    assert pixmap.devicePixelRatio() == 2.0


def test_icon_is_tinted(qapp):
    pixmap = tinted_icon("zoom_in.svg", "#e6e6e6").pixmap(QSize(24, 24))
    image = pixmap.toImage()
    opaque_colors = set()
    for y in range(image.height()):
        for x in range(image.width()):
            color = QColor.fromRgba(image.pixel(x, y))
            if color.alpha() > 200:
                opaque_colors.add((color.red(), color.green(), color.blue()))
    assert (230, 230, 230) in opaque_colors


def test_tinted_icons_are_cached_per_color(qapp):
    light = tinted_icon("zoom_in.svg", "#3c3c3c")
    light_again = tinted_icon("zoom_in.svg", "#3c3c3c")
    dark = tinted_icon("zoom_in.svg", "#e6e6e6")
    assert light is light_again
    assert light is not dark


def test_right_dock_icons_have_solid_strokes(qapp):
    """The icon-only dock tabs must stay bold enough to read at 18 px."""
    for name, minimum in (("search.svg", 50), ("page_numbers.svg", 90)):
        image = tinted_icon(name, "#3c3c3c").pixmap(QSize(18, 18)).toImage()
        solid = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if QColor.fromRgba(image.pixel(x, y)).alpha() > 200)
        assert solid >= minimum, f"{name} has only {solid} solid pixels"


def test_missing_asset_gives_empty_icon(qapp):
    assert tinted_icon("does-not-exist.svg", "#3c3c3c").isNull()


def test_selection_tool_icons_have_solid_strokes(qapp):
    for name, minimum in (("explore.svg", 55), ("select_text.svg", 35),
                          ("select_area.svg", 35), ("select_table.svg", 60)):
        image = tinted_icon(name, "#e6e6e6").pixmap(QSize(18, 18)).toImage()
        colors = set()
        for y in range(image.height()):
            for x in range(image.width()):
                color = QColor.fromRgba(image.pixel(x, y))
                if color.alpha() > 200:
                    colors.add((color.red(), color.green(), color.blue()))
        assert sum(1 for color in colors) > 0, f"{name} drew nothing"
        assert (230, 230, 230) in colors, f"{name} was not tinted: {colors}"
        solid = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if QColor.fromRgba(image.pixel(x, y)).alpha() > 200)
        assert solid >= minimum, f"{name} has only {solid} solid pixels"
