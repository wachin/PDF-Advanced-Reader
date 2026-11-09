"""Inline editor popover for editing an existing PDF word.

A small, self-contained Qt widget shown over the clicked word.  It owns the text
field, Apply/Cancel buttons, and reports the result through Qt signals so the
viewer can keep the preview and the edit bookkeeping separate.
"""

from __future__ import annotations

from typing import Optional
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLineEdit, QPushButton, QWidget,
)
from PyQt6.QtGui import QKeyEvent


class ExistingTextEditor(QFrame):
    """Floating editor for one existing word.

    Signals
    -------
    applied(str): the user accepted the new text.
    cancelled(): the user dismissed the editor.
    """

    applied = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, parent: Optional[QWidget], initial: str,
                 font_size: float = 12.0) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setWindowFlags(Qt.WindowType.SubWindow)
        self.setAutoFillBackground(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.line = QLineEdit(initial)
        self.line.setMinimumWidth(max(120, int(font_size * len(initial) * 0.8) + 40))
        self.line.selectAll()
        layout.addWidget(self.line)

        self.apply_btn = QPushButton("Apply")
        self.cancel_btn = QPushButton("Cancel")
        layout.addWidget(self.apply_btn)
        layout.addWidget(self.cancel_btn)

        self.apply_btn.clicked.connect(self._on_apply)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.line.returnPressed.connect(self._on_apply)
        self.line.textChanged.connect(lambda _t: self._emit_preview())
        self._preview_cb = None

    def set_preview_callback(self, cb) -> None:
        self._preview_cb = cb

    def _emit_preview(self) -> None:
        if self._preview_cb is not None:
            self._preview_cb(self.line.text())

    def _on_apply(self) -> None:
        self.applied.emit(self.line.text())

    def _on_cancel(self) -> None:
        self.cancelled.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            self._on_apply()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        # Keep focus inside the editor so a click elsewhere does not strand it;
        # the Cancel button or Escape still closes it.
        if self.line is not None and not self.isActiveWindow():
            self.line.setFocus()
        super().focusOutEvent(event)
