"""A Python port of PdfCraft's existing-PDF text editing engine.

This package is the engine; :mod:`pdfar.pdfcraft_adapter` is the only public
entry point the application uses.  Nothing here imports PDFAR's current
paragraph editor or its UI, so the two engines can be compared side by side.

Adapted concepts (not code) from PdfCraft (MIT OR Apache-2.0):
  crates/edit/src/text/{interp,lines,blocks,rewrite}.rs
  crates/edit/src/text_regions/grouping.rs
  crates/edit/src/text_session.rs
  crates/content/src/lib.rs
  crates/fonts/src/pdf.rs
"""

from .blocks import group_blocks
from .fonts import FontMetrics, parse_cmap, strip_subset_tag, win_ansi_bytes
from .interp import FontCache, interpret
from .lines import join_lines
from .matrix import IDENTITY, Matrix, apply, direction, then, to_frame, translation
from .model import LineEdit, Origin, Shown, TextBlock, TextLine, TextState
from .page import PageModel, read_page
from .preview import clear_block, render_bytes, render_page
from .rewrite import UnsupportedError, replace_line, rewrite_block
from .session import LayoutResult, TextSession
from .stream import Name, Op, PdfString, Pieces, Ref, parse_ops, splice

__all__ = [
    "FontCache",
    "FontMetrics",
    "LayoutResult",
    "LineEdit",
    "Name",
    "Op",
    "Origin",
    "PageModel",
    "PdfString",
    "Pieces",
    "Ref",
    "Shown",
    "TextBlock",
    "TextLine",
    "TextSession",
    "TextState",
    "UnsupportedError",
    "apply",
    "clear_block",
    "direction",
    "group_blocks",
    "interpret",
    "join_lines",
    "parse_cmap",
    "parse_ops",
    "read_page",
    "render_bytes",
    "render_page",
    "replace_line",
    "rewrite_block",
    "splice",
    "strip_subset_tag",
    "then",
    "to_frame",
    "translation",
    "win_ansi_bytes",
    "IDENTITY",
    "Matrix",
]
