"""Background worker for async PDF page rendering."""

from __future__ import annotations

import fitz
from PyQt6.QtCore import QObject, pyqtSignal


class RenderWorker(QObject):
    """Background worker for rendering a single PDF page.
    
    Uses moveToThread() pattern for async rendering instead of QRunnable.
    """

    # Signal emitted when rendering is complete
    finished = pyqtSignal(int, bytes, int, int, bool, str)
    # (page_index, raw_data, width, height, success, error_msg)

    def __init__(self, doc_path: str, page_index: int, zoom: float):
        super().__init__()
        self.doc_path = doc_path
        self.page_index = page_index
        self.zoom = zoom
        self._cancelled = False

    def run(self):
        """Run rendering in background thread."""
        try:
            doc = fitz.open(self.doc_path)
            page = doc.load_page(self.page_index)
            mat = fitz.Matrix(self.zoom, self.zoom)
            pix = page.get_pixmap(matrix=mat)

            self.raw_data = pix.tobytes("png")
            self.width = pix.width
            self.height = pix.height
            self.success = True
            self.error_msg = ""

            doc.close()

        except Exception as e:
            self.raw_data = None
            self.width = 0
            self.height = 0
            self.success = False
            self.error_msg = str(e)

        # Emit signal to notify completion (connects to GUI thread)
        self.finished.emit(
            self.page_index,
            self.raw_data,
            self.width,
            self.height,
            self.success,
            self.error_msg,
        )
