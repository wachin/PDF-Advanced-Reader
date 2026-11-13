"""Read one page into runs, lines and editable blocks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ._compat import fitz

from .blocks import group_blocks
from .fonts import FontMetrics
from .interp import FontCache, interpret, page_resources
from .lines import join_lines
from .model import Shown, TextBlock, TextLine
from .stream import Op, PdfString, Pieces, parse_ops


@dataclass
class PageModel:
    doc: fitz.Document
    page_no: int
    page_xref: int
    stream_xrefs: List[int]
    streams: List[bytes]
    pieces: Pieces
    ops: List[Op]
    resources: dict
    resources_xref: Optional[int]
    fonts: FontCache
    font_map: Dict[str, FontMetrics]
    shown: List[Shown] = field(default_factory=list)
    lines: List[TextLine] = field(default_factory=list)
    blocks: List[TextBlock] = field(default_factory=list)
    # Characters each font resource actually showed: a subset embeds only these.
    font_chars: Dict[str, set] = field(default_factory=dict)
    #: Text-showing operators that carried text but produced no run (their font
    #: or encoding could not be read).  Their text is absent from ``lines``.
    unread_shows: int = 0

    @property
    def editable_lines(self) -> List[Tuple[int, TextLine]]:
        return [(i, line) for i, line in enumerate(self.lines) if line.stream == 0]

    def piece_of(self, op_index: int) -> int:
        op = self.ops[op_index]
        return self.pieces.piece_of(op.start)


def _shows_text(op: Op) -> bool:
    """Whether a text-showing operator carries any non-empty string."""
    if op.op == "TJ":
        array = op.array(-1) or []
        return any(isinstance(piece, PdfString) and piece.data for piece in array)
    if op.op in ("Tj", "'", '"'):
        string = op.string(-1)
        return string is not None and bool(string.data)
    return False


def read_page(
    doc: fitz.Document,
    page_no: int,
    forms: bool = True,
    fonts: Optional[FontCache] = None,
) -> PageModel:
    """Read a page; pass a shared ``FontCache`` to reuse metrics across pages."""
    page = doc[page_no]
    stream_xrefs = list(page.get_contents())
    streams = [doc.xref_stream(xref) or b"" for xref in stream_xrefs]
    pieces = Pieces.join(streams)
    ops = parse_ops(pieces.data)
    resources, resources_xref = page_resources(doc, page)
    fonts = fonts or FontCache(doc)
    font_map = fonts.for_resources(resources, resources_xref)
    shown = interpret(doc, ops, resources, resources_xref, fonts, forms=forms)
    lines = join_lines(shown)
    blocks = group_blocks(lines)
    font_chars: Dict[str, set] = {}
    for run in shown:
        font_chars.setdefault(run.font_name, set()).update(run.text)
    page_shows = sum(1 for op in ops if _shows_text(op))
    page_runs = sum(1 for run in shown if run.stream == 0)
    return PageModel(
        doc=doc,
        page_no=page_no,
        page_xref=page.xref,
        stream_xrefs=stream_xrefs,
        streams=streams,
        pieces=pieces,
        ops=ops,
        resources=resources,
        resources_xref=resources_xref,
        fonts=fonts,
        font_map=font_map,
        shown=shown,
        lines=lines,
        blocks=blocks,
        font_chars=font_chars,
        unread_shows=max(0, page_shows - page_runs),
    )
