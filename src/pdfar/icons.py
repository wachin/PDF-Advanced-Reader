"""Monochrome SVG icons rendered at the exact size Qt asks for.

Rendering the SVG into a fixed set of pixmap sizes makes Qt rescale them
whenever the requested size is absent (toolbar and tab icons are 18 px),
which visibly softens strokes. A QIconEngine renders the vector at the
requested size and device pixel ratio instead, so every icon stays sharp.
"""

from __future__ import annotations

import os
from functools import lru_cache

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

_ASSET_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "icons")


def asset_path(name: str) -> str:
    """Resolve an asset from the source checkout, falling back to the CWD."""
    path = os.path.join(_ASSET_DIR, name)
    return path if os.path.exists(path) else os.path.join("assets", "icons", name)


class _TintedSvgEngine(QIconEngine):
    """Render a monochrome SVG in one colour at whatever size is requested."""

    def __init__(self, path: str, color: str):
        super().__init__()
        self._path = path
        self._color = QColor(color)
        self._renderer = QSvgRenderer(path)

    def is_valid(self) -> bool:
        return self._renderer.isValid()

    def clone(self) -> "_TintedSvgEngine":
        return _TintedSvgEngine(self._path, self._color.name())

    def _render(self, size: QSize, scale: float = 1.0) -> QPixmap:
        width = max(1, round(size.width() * scale))
        height = max(1, round(size.height() * scale))
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._renderer.render(painter, QRectF(0, 0, width, height))
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(QRectF(0, 0, width, height), self._color)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        return pixmap

    def pixmap(self, size, mode, state):  # noqa: N802 - Qt API
        return self._render(size)

    def scaledPixmap(self, size, mode, state, scale):  # noqa: N802 - Qt API
        return self._render(size, scale)

    def paint(self, painter, rect, mode, state):  # noqa: N802 - Qt API
        device = painter.device()
        dpr = device.devicePixelRatioF() if device is not None else 1.0
        pixmap = self._render(rect.size().toSize(), dpr)
        painter.drawPixmap(rect.topLeft(), pixmap)


@lru_cache(maxsize=None)
def tinted_icon(name: str, color: str) -> QIcon:
    """Return a vector icon for *name* tinted in *color*."""
    engine = _TintedSvgEngine(asset_path(name), color)
    if not engine.is_valid():
        return QIcon()
    return QIcon(engine)
