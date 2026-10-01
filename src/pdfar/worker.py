"""Background worker for async PDF page rendering."""

from __future__ import annotations

import fitz


class RenderWorker:
    """Background worker for rendering a single PDF page."""

    def __init__(self, doc_path: str, page_index: int, zoom: float):
        self.doc_path = doc_path
        self.page_index = page_index
        self.zoom = zoom
        self._finished = False
        self.raw_data: bytes | None = None
        self.width: int = 0
        self.height: int = 0
        self.success: bool = False
        self.error_msg: str | None = None

    def is_finished(self) -> bool:
        return self._finished

    def run(self):
        try:
            doc = fitz.open(self.doc_path)
            page = doc.load_page(self.page_index)
            mat = fitz.Matrix(self.zoom, self.zoom)
            pix = page.get_pixmap(matrix=mat)

            self.width = pix.width
            self.height = pix.height
            self.raw_data = pix.tobytes("png")

            doc.close()
            self.success = True

        except Exception as e:
            self.raw_data = None
            self.width = 0
            self.height = 0
            self.success = False
            self.error_msg = str(e)

        self._finished = True
