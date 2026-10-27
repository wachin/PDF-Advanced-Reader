"""P1-1 — multi-document tab support.

A :class:`DocumentTab` owns the per-document objects of one open PDF:
its ``PDFView`` and its ``Sidebar``.  The main window coordinates them;
this class only guarantees deterministic teardown of the RenderPool
(one pool per document, Golden Rule 2.5).
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from .sidebar import Sidebar
from .viewer import PDFView


class DocumentTab(QWidget):
    """Container widget for one open document."""

    document_changed = pyqtSignal(object)     # this tab, after a change (F10-1)

    def __init__(self, doc_path: str, parent: Optional[QWidget] = None,
                 password: str = ""):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = PDFView(doc_path, parent=self, password=password)
        # Sidebar is created without a parent so it can be moved to the dock widget.
        # It is hidden initially and will be shown when placed in the dock widget.
        self.sidebar = Sidebar(self.view.session, self.view.pool, None)
        self.sidebar.hide()
        layout.addWidget(self.view)
        self.view.content_changed.connect(self._on_content_changed)

    @property
    def doc_path(self) -> str:
        return self.view.session.path

    @property
    def title(self) -> str:
        return os.path.basename(self.doc_path)

    @property
    def dirty(self) -> bool:
        """The document holds changes the file on disk does not have."""
        return self.view.session.dirty

    @property
    def display_title(self) -> str:
        """Tab label; the bullet marks edits that are not in the file yet."""
        return f"• {self.title}" if self.dirty else self.title

    def _on_content_changed(self, version: int, scope: int) -> None:
        self.document_changed.emit(self)

    def shutdown(self) -> None:
        """Stop all async work owned by this document.  Safe to call twice."""
        self.sidebar.shutdown()
        self.view.shutdown()
