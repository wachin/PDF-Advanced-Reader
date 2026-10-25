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


def test_missing_asset_gives_empty_icon(qapp):
    assert tinted_icon("does-not-exist.svg", "#3c3c3c").isNull()
