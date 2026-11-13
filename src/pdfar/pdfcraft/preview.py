"""Textless preview of a paragraph, for an inline editor background.

Removing a paragraph's glyphs while keeping its advance is what PdfCraft's
``clear_block`` does (``crates/edit/src/text/blocks.rs``): each show becomes a
numeric ``TJ`` adjustment, so the text matrix still moves as it did and
unselected text positioned relative to it stays put.  This is a preview
operation: call it on a clone.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from ._compat import fitz

from .page import PageModel, read_page
from .rewrite import _apply_edits, _show_side_effects, write_contents
from .stream import Op, serialize_ops


def clear_block(
    doc: fitz.Document, page_no: int, block_index: int, model: Optional[PageModel] = None
) -> None:
    pm = model or read_page(doc, page_no)
    if not 0 <= block_index < len(pm.blocks):
        raise IndexError(f"page {page_no + 1} has no paragraph {block_index + 1}")
    block = pm.blocks[block_index]
    by_op = {shown.op_index: shown for shown in pm.shown}
    edits: Dict[int, Tuple[bytes, bool]] = {}
    for line_index in block.lines:
        line = pm.lines[line_index]
        if line.stream != 0:
            raise ValueError("text drawn by a Form XObject cannot be previewed")
        for index in line.ops:
            shown = by_op.get(index)
            side = _show_side_effects(pm.ops[index])
            if shown is None:
                edits[index] = (serialize_ops(side), False)
                continue
            denominator = shown.state.size * shown.state.scale
            adjustment = -1000.0 * shown.advance / denominator if denominator else 0.0
            if adjustment != adjustment:
                raise ValueError("the paragraph's advance cannot be previewed")
            edits[index] = (serialize_ops(side + [Op("TJ", [[adjustment]])]), False)
    new_streams = _apply_edits(pm, edits)
    write_contents(doc, pm, new_streams)


def render_page(doc: fitz.Document, page_no: int, dpi: int = 96) -> fitz.Pixmap:
    return doc[page_no].get_pixmap(dpi=dpi)


def render_bytes(doc: fitz.Document, page_no: int, dpi: int = 96) -> bytes:
    return render_page(doc, page_no, dpi).tobytes("png")
