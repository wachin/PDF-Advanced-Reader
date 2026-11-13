"""Editing session for one paragraph, with a document snapshot.

Opening reads the page once and records the document's bytes.  ``layout`` runs
the rewrite on a copy of that snapshot, so a draft never sees another draft's
output.  ``commit`` refuses when the document changed since the session opened,
then writes through the same rewrite the layout measured.

Mirrors ``pdfcraft-edit/src/text_session.rs``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from ._compat import fitz

from .model import LineEdit, TextBlock, TextLine
from .page import read_page
from .rewrite import UnsupportedError, rewrite_block


@dataclass
class LayoutResult:
    rows: List[str]
    lines: List[TextLine]
    blocks: List[TextBlock]
    substituted: Optional[str]
    document: bytes


class TextSession:
    def __init__(self, doc: fitz.Document, page_no: int, block_index: int):
        self.page_no = page_no
        self.block_index = block_index
        self.snapshot = doc.tobytes(no_new_id=True)
        model = read_page(doc, page_no)
        if not 0 <= block_index < len(model.blocks):
            raise UnsupportedError(f"page {page_no + 1} has no paragraph {block_index + 1}")
        self.source = model.blocks[block_index]
        self.source_lines = model.lines

    def is_current(self, doc: fitz.Document) -> bool:
        return doc.tobytes(no_new_id=True) == self.snapshot

    def layout(self, text: str) -> LayoutResult:
        clone = fitz.open("pdf", self.snapshot)
        try:
            model = read_page(clone, self.page_no)
            edit = rewrite_block(clone, self.page_no, self.block_index, text, model=model)
            after = read_page(clone, self.page_no)
            block = after.blocks[self.block_index] if self.block_index < len(after.blocks) else self.source
            rows = ["".join(after.lines[i].text for i in row) for row in block.rows]
            return LayoutResult(
                rows=rows,
                lines=after.lines,
                blocks=after.blocks,
                substituted=edit.substituted,
                document=clone.tobytes(),
            )
        finally:
            clone.close()

    def commit(self, doc: fitz.Document, text: str) -> LineEdit:
        if not self.is_current(doc):
            raise UnsupportedError("the paragraph changed while it was being edited")
        model = read_page(doc, self.page_no)
        return rewrite_block(doc, self.page_no, self.block_index, text, model=model)
