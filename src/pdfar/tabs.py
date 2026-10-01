"""P1-1 — multi-document tab support.

A :class:`DocumentTab` owns the per-document objects of one open PDF:
its ``PDFView`` and its ``Sidebar``.  The main window coordinates them;
this class only guarantees deterministic teardown of the RenderPool
(one pool per document, Golden Rule 2.5).
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from .sidebar import Sidebar
from .viewer import PDFView


class DocumentTab(QWidget):
    """Container widget for one open document."""

    def __init__(self, doc_path: str, parent: Optional[QWidget] = None,
                 password: str = ""):
        super().__init__(parent)
        self.doc_path = doc_path

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = PDFView(doc_path, parent=self, password=password)
        self.sidebar = Sidebar(self.view.doc, self.view.pool, self)
        layout.addWidget(self.view)

    @property
    def title(self) -> str:
        return os.path.basename(self.doc_path)

    def shutdown(self) -> None:
        """Stop all async work owned by this document.  Safe to call twice."""
        self.view.shutdown()
