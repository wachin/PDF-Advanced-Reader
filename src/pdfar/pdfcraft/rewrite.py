"""Rewrite existing text without rebuilding untouched operators.

Two operations:

* :func:`replace_line` replaces the glyphs of one line and drops that line's
  other show operators, leaving every other byte of the content stream alone.
  This is the operation that must preserve sibling paragraphs byte-for-byte.
* :func:`rewrite_block` reflows a paragraph.  When the draft has the same rows
  as the source, only the changed rows are rewritten; otherwise the whole
  paragraph is laid out again, keeping its first row's position, font, size,
  colour and leading.

Both write new content-stream objects and repoint ``/Contents``, because a
content stream may be shared with another page (PdfCraft's
``rewritten_contents`` does the same).

Mirrors ``pdfcraft-edit/src/text/rewrite.rs`` and
``text_regions/styled.rs``.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from ._compat import fitz

from .blocks import _frame_origin
from .fonts import FontMetrics, get_key, win_ansi_bytes
from .model import LineEdit, TextLine
from .page import PageModel, read_page
from .stream import (
    Name,
    Op,
    PdfString,
    Ref,
    serialize_ops,
    splice,
)


class UnsupportedError(Exception):
    """The edit cannot be made safely; nothing has been changed."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _state_ops(state) -> List[Op]:
    """The operators that set a text state inside a text object."""
    ops: List[Op] = []
    if state.font_name is not None:
        ops.append(Op("Tf", [Name(state.font_name), state.size]))
    ops.append(Op("Tc", [state.char_spacing]))
    ops.append(Op("Tw", [state.word_spacing]))
    ops.append(Op("Tz", [state.scale * 100.0]))
    ops.append(Op("TL", [state.leading]))
    ops.append(Op("Ts", [state.rise]))
    ops.append(_fill_op(state.fill))
    return ops


def _fill_op(color: Tuple[float, float, float]) -> Op:
    r, g, b = color
    if r == g == b:
        return Op("g", [r])
    return Op("rg", [r, g, b])


def _show_side_effects(op: Op) -> List[Op]:
    """What a dropped ``'`` or ``"`` leaves behind (ISO 32000-2 Table 107)."""
    out: List[Op] = []
    if op.op == '"':
        nums = op.nums()
        if len(nums) >= 2:
            out.append(Op("Tw", [nums[0]]))
            out.append(Op("Tc", [nums[1]]))
    if op.op in ("'", '"'):
        out.append(Op("T*", []))
    return out


def _kept_run_follows(ops: Sequence[Op], drop: set[int]) -> List[bool]:
    """For each operator, whether a kept show follows before any line move."""
    out = [False] * len(ops)
    follows = False
    for i in range(len(ops) - 1, -1, -1):
        out[i] = follows
        name = ops[i].op
        if name in ("ET", "BT", "Td", "TD", "Tm", "T*", "'", '"'):
            follows = False
        elif name in ("Tj", "TJ") and i not in drop:
            follows = True
    return out


def _dropped_advances(pm: PageModel, drop: set[int]) -> Dict[int, Op]:
    """Numeric ``TJ`` advances for dropped runs that following text relies on."""
    follows = _kept_run_follows(pm.ops, drop)
    out: Dict[int, Op] = {}
    for shown in pm.shown:
        if shown.op_index not in drop or not follows[shown.op_index]:
            continue
        denominator = shown.state.size * shown.state.scale
        if not denominator or not shown.advance:
            continue
        adjustment = -1000.0 * shown.advance / denominator
        if adjustment == 0 or adjustment != adjustment:  # zero or NaN
            continue
        out.setdefault(shown.op_index, Op("TJ", [[adjustment]]))
    return out


def _local_index_map(pm: PageModel, piece: int) -> Tuple[List[Op], Dict[int, int]]:
    """The piece's operators with offsets relative to that piece."""
    from dataclasses import replace

    base = pm.pieces.bounds[piece][0]
    local_ops: List[Op] = []
    mapping: Dict[int, int] = {}
    for global_index, op in enumerate(pm.ops):
        if pm.pieces.piece_of(op.start) == piece:
            mapping[global_index] = len(local_ops)
            local_ops.append(
                replace(op, start=op.start - base, kw_start=op.kw_start - base, end=op.end - base)
            )
    return local_ops, mapping


def _apply_edits(pm: PageModel, edits: Dict[int, Tuple[bytes, bool]]) -> List[bytes]:
    """Apply global-index edits to their streams, returning new stream bytes."""
    by_piece: Dict[int, List[Tuple[int, bytes, bool]]] = {}
    for global_index, (insert, keep) in edits.items():
        piece = pm.pieces.piece_of(pm.ops[global_index].start)
        by_piece.setdefault(piece, []).append((global_index, insert, keep))
    new_streams = list(pm.streams)
    for piece, changes in by_piece.items():
        local_ops, mapping = _local_index_map(pm, piece)
        local_edits = {
            mapping[global_index]: (insert, keep) for global_index, insert, keep in changes
        }
        new_streams[piece] = splice(pm.streams[piece], local_ops, local_edits)
    return new_streams


def write_contents(doc: fitz.Document, pm: PageModel, new_streams: Sequence[bytes]) -> None:
    """Point the page at new stream objects for the pieces that changed.

    Unchanged pieces keep their original object; changed pieces become new
    objects, so a stream shared with another page is not modified in place.
    """
    if list(new_streams) == list(pm.streams):
        return
    entries: List[str] = []
    for xref, old, new in zip(pm.stream_xrefs, pm.streams, new_streams):
        if new == old:
            entries.append(f"{xref} 0 R")
            continue
        number = doc.get_new_xref()
        doc.update_object(number, "<<>>")
        try:
            doc.update_stream(number, new, compress=True)
        except TypeError:
            doc.update_stream(number, new)
        entries.append(f"{number} 0 R")
    page = doc[pm.page_no]
    value = entries[0] if len(entries) == 1 else "[" + " ".join(entries) + "]"
    doc.xref_set_key(page.xref, "Contents", value)


# ---------------------------------------------------------------------------
# Font substitution
# ---------------------------------------------------------------------------


def _family_for(base_font: str, bold: bool, italic: bool) -> str:
    plain = base_font.split("+")[-1]
    if "Courier" in plain or "Mono" in plain:
        family = "Courier"
    elif "Times" in plain or "Serif" in plain and "Sans" not in plain:
        family = "Times"
    else:
        family = "Helvetica"
    if bold and italic:
        return family + "-BoldOblique"
    if bold:
        return family + "-Bold"
    if italic:
        return family + "-Oblique"
    return family


def ensure_substitute_font(
    doc: fitz.Document, pm: PageModel, base_font: str, bold: bool = False, italic: bool = False
) -> Tuple[str, str]:
    """Add (once) a standard-14 fallback font to the page's resources."""
    family = _family_for(base_font, bold, italic)
    resource_name = "PCEd" + family.replace("-", "")
    # Already added by an earlier edit in this document?
    fonts = _font_resources(doc, pm)
    if resource_name in fonts:
        return resource_name, family
    number = doc.get_new_xref()
    doc.update_object(
        number,
        f"<< /Type /Font /Subtype /Type1 /BaseFont /{family} /Encoding /WinAnsiEncoding >>",
    )
    _add_font_resource(doc, pm, resource_name, number)
    return resource_name, family


def _font_resources(doc: fitz.Document, pm: PageModel) -> dict:
    from .fonts import object_source

    value = pm.resources.get("Font")
    if isinstance(value, Ref):
        source = object_source(doc, value)
        return source if isinstance(source, dict) else {}
    return value if isinstance(value, dict) else {}


def _add_font_resource(doc: fitz.Document, pm: PageModel, name: str, number: int) -> None:
    """Add ``/name number 0 R`` to the page's /Font dictionary."""
    if pm.resources_xref is None:
        # Resources are inline: promote them to an object so they can be edited.
        resources = dict(pm.resources)
        fonts = dict(resources.get("Font") or {})
        fonts[name] = Ref(number)
        resources["Font"] = fonts
        new_xref = doc.get_new_xref()
        doc.update_object(new_xref, serialize_operand_dict(resources))
        page = doc[pm.page_no]
        doc.xref_set_key(page.xref, "Resources", f"{new_xref} 0 R")
        pm.resources_xref = new_xref
        pm.resources = resources
        return

    value = get_key(doc, pm.resources_xref, "Font")
    if isinstance(value, Ref):
        doc.xref_set_key(value.num, name, f"{number} 0 R")
        return
    fonts = dict(value) if isinstance(value, dict) else {}
    fonts[name] = Ref(number)
    new_xref = doc.get_new_xref()
    doc.update_object(new_xref, serialize_operand_dict(fonts))
    doc.xref_set_key(pm.resources_xref, "Font", f"{new_xref} 0 R")


def serialize_operand_dict(value: dict) -> str:
    from .stream import serialize_operand

    return serialize_operand(value).decode("latin-1")


# ---------------------------------------------------------------------------
# Line replacement
# ---------------------------------------------------------------------------


def _can_encode(pm: PageModel, font_name: str, metrics: Optional[FontMetrics], text: str) -> bool:
    """Whether the source font can show ``text``, glyphs included.

    A subset font's ``/Widths`` and encoding cover codes the program may not
    embed, so a character the document never showed is treated as missing.
    """
    if metrics is None or metrics.encode(text) is None:
        return False
    used = pm.font_chars.get(font_name)
    if used is not None and metrics.subset:
        return all(char in used or char.isspace() for char in text)
    return True


def _replacement_ops(
    doc: fitz.Document,
    pm: PageModel,
    line: TextLine,
    new_text: str,
) -> Tuple[List[Op], Optional[str]]:
    """The operators that show ``new_text`` in the line's own place."""
    metrics = pm.font_map.get(line.font_name)
    size = line.origin.state.size
    ops: List[Op] = []

    if _can_encode(pm, line.font_name, metrics, new_text):
        encoded = metrics.encode(new_text)
        ops.append(Op("Tj", [PdfString(encoded, encoded)]))
        return ops, None

    substitute, family = ensure_substitute_font(doc, pm, line.base_font, line.bold, line.italic)
    payload = win_ansi_bytes(new_text)
    if payload is None:
        raise UnsupportedError(
            f'"{new_text}" has characters neither {line.base_font} nor {family} can show'
        )
    ops.append(Op("Tf", [Name(substitute), size]))
    ops.append(Op("Tj", [PdfString(payload, payload)]))
    ops.append(Op("Tf", [Name(line.font_name), size]))
    return ops, family


def _splice_replacements(
    doc: fitz.Document,
    pm: PageModel,
    first_ops: Dict[int, bytes],
    drop: set[int],
) -> None:
    """Replace the show operators in ``first_ops`` and remove ``drop``.

    A dropped run whose advance later text still relies on leaves a numeric
    ``TJ`` adjustment, so relative positioning keeps working (PdfCraft's
    ``dropped_advances``).
    """
    edits: Dict[int, Tuple[bytes, bool]] = {}
    for op_index, payload in first_ops.items():
        side = serialize_ops(_show_side_effects(pm.ops[op_index]))
        edits[op_index] = (side + payload, False)

    follows = _kept_run_follows(pm.ops, drop)
    for shown in pm.shown:
        index = shown.op_index
        if index not in drop or index in first_ops or not follows[index]:
            continue
        denominator = shown.state.size * shown.state.scale
        if not denominator or not shown.advance:
            continue
        adjustment = -1000.0 * shown.advance / denominator
        if adjustment and adjustment == adjustment:
            edits[index] = (serialize_ops([Op("TJ", [[adjustment]])]), False)

    for index in drop:
        if index in first_ops:
            continue
        side = serialize_ops(_show_side_effects(pm.ops[index]))
        advance = edits.get(index, (b"", False))[0]
        edits[index] = (side + advance, False)

    new_streams = _apply_edits(pm, edits)
    write_contents(doc, pm, new_streams)


def _replace_lines(
    doc: fitz.Document, pm: PageModel, replacements: Sequence[Tuple[int, str]]
) -> LineEdit:
    """Replace several lines in one page pass."""
    first_ops: Dict[int, bytes] = {}
    drop: set[int] = set()
    substituted: Optional[str] = None

    for line_index, new_text in replacements:
        line = pm.lines[line_index]
        if line.stream != 0:
            raise UnsupportedError("text drawn by a Form XObject cannot be rewritten yet")
        if not line.ops:
            raise UnsupportedError("the line has no text-showing operators")
        first_op = line.ops[0]
        new_ops, family = _replacement_ops(doc, pm, line, new_text)
        substituted = substituted or family
        first_ops[first_op] = serialize_ops(new_ops)
        for index in line.ops[1:]:
            drop.add(index)
        drop.add(first_op)

    _splice_replacements(doc, pm, first_ops, drop)
    return LineEdit(substituted=substituted)


def replace_line(
    doc: fitz.Document, page_no: int, line_index: int, new_text: str, model: Optional[PageModel] = None
) -> LineEdit:
    """Replace the text of line ``line_index`` with ``new_text``."""
    pm = model or read_page(doc, page_no)
    if not 0 <= line_index < len(pm.lines):
        raise UnsupportedError(f"page {page_no + 1} has no line {line_index + 1}")
    return _replace_lines(doc, pm, [(line_index, new_text)])


# ---------------------------------------------------------------------------
# Paragraph reflow
# ---------------------------------------------------------------------------


def _wrap(text: str, width: float, advance) -> List[str]:
    rows: List[str] = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        words = paragraph.split(" ")
        line = ""
        for word in words:
            candidate = word if not line else line + " " + word
            if line and advance(candidate) > width:
                rows.append(line)
                line = word
            else:
                line = candidate
        rows.append(line)
    return rows


def _leading_user(pm: PageModel, block) -> float:
    if len(block.rows) >= 2:
        above = pm.lines[block.rows[0][0]]
        below = pm.lines[block.rows[1][0]]
        gap = abs(_frame_origin(above)[1] - _frame_origin(below)[1])
        if gap > 0:
            return gap
    first = pm.lines[block.lines[0]]
    if first.origin.state.leading > 0:
        return first.origin.state.leading * first.origin.k
    return first.size * 1.2


def _bt_bounds(pm: PageModel, bt_op: int) -> Tuple[int, int]:
    """The ``BT`` and matching ``ET`` operator indices."""
    et_op = bt_op
    depth = 0
    for index in range(bt_op, len(pm.ops)):
        if pm.ops[index].op == "BT":
            depth += 1
        elif pm.ops[index].op == "ET":
            depth -= 1
            if depth == 0:
                et_op = index
                break
    return bt_op, et_op


def rewrite_block(
    doc: fitz.Document,
    page_no: int,
    block_index: int,
    new_text: str,
    model: Optional[PageModel] = None,
) -> LineEdit:
    """Rewrite paragraph ``block_index``, rewrapping it to its own width."""
    pm = model or read_page(doc, page_no)
    if not 0 <= block_index < len(pm.blocks):
        raise UnsupportedError(f"page {page_no + 1} has no paragraph {block_index + 1}")
    block = pm.blocks[block_index]
    if any(pm.lines[i].stream != 0 for i in block.lines):
        raise UnsupportedError("text drawn by a Form XObject cannot be rewritten yet")

    old_rows = ["".join(pm.lines[i].text for i in row) for row in block.rows]
    new_rows = new_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # Same number of rows: rewrite only the rows whose text changed.
    if len(new_rows) == len(old_rows):
        changes: List[Tuple[int, str]] = []
        for row, old, new in zip(block.rows, old_rows, new_rows):
            if new != old:
                if len(row) != 1:
                    changes = []
                    break
                changes.append((row[0], new))
        if changes:
            return _replace_lines(doc, pm, changes)
        if all(new == old for new, old in zip(new_rows, old_rows)):
            return LineEdit()

    first = pm.lines[block.lines[0]]
    metrics = pm.font_map.get(first.font_name)
    if metrics is None:
        raise UnsupportedError("the paragraph's font is not readable")
    state = first.origin.state
    size = state.size
    k = first.origin.k
    scale = state.scale
    char_spacing = state.char_spacing
    word_spacing = state.word_spacing

    # Choose one font for the whole block: the source font if it encodes every
    # row, otherwise a standard-14 fallback.
    show_font = first.font_name
    substituted = None
    encode_row = metrics.encode
    if any(not _can_encode(pm, first.font_name, metrics, row) for row in new_rows):
        show_font, substituted = ensure_substitute_font(doc, pm, first.base_font, first.bold, first.italic)
        fallback = fitz.Font(_family_for(first.base_font, first.bold, first.italic))
        encode_row = win_ansi_bytes
        if any(win_ansi_bytes(row) is None for row in new_rows):
            raise UnsupportedError("the new text cannot be shown in the paragraph's font")

        def advance(text: str) -> float:
            return fallback.text_length(text, size) * scale * k

    else:

        def advance(text: str) -> float:
            return metrics.advance(text, size, char_spacing, word_spacing) * scale * k

    # The paragraph's own width, measured along its baseline: ``block.rect`` is
    # the upright bounding box, which is the text's height for turned text.
    frame_left = min(pm.lines[i].frame[0] for i in block.lines)
    frame_right = max(pm.lines[i].frame[2] for i in block.lines)
    width = frame_right - frame_left
    if width <= 0:
        width = max(first.size * 10.0, 72.0)
    rows = _wrap("\n".join(new_rows), width + 0.5, advance)
    encoded_rows = [encode_row(row) for row in rows]
    if any(value is None for value in encoded_rows):
        raise UnsupportedError("a wrapped row cannot be shown in the paragraph's font")

    leading = _leading_user(pm, block)
    lead_text = leading / k if k else leading

    body: List[Op] = [
        Op("Tf", [Name(show_font), size]),
        Op("Tc", [char_spacing]),
        Op("Tw", [word_spacing]),
        Op("Tz", [scale * 100.0]),
        Op("TL", [state.leading]),
        Op("Ts", [state.rise]),
        _fill_op(state.fill),
        Op("Tm", list(first.origin.tm)),
    ]
    for row_index, row in enumerate(rows):
        if row_index:
            body.append(Op("Td", [0.0, -lead_text]))
        payload = encoded_rows[row_index] if row_index < len(encoded_rows) else b""
        if payload is None:
            payload = b""
        if payload:
            body.append(Op("Tj", [PdfString(payload, payload)]))

    bt_op, et_op = _bt_bounds(pm, first.origin.bt_op)
    block_ops = {index for line_index in block.lines for index in pm.lines[line_index].ops}
    # A paragraph may be one text object (common), or one text object per row
    # (LibreOffice, Word): the rows still group into one paragraph, but only the
    # first object can hold the reflowed rows.
    bts = {pm.lines[i].origin.bt_op for i in block.lines}
    owns_bt = len(bts) == 1 and all(
        pm.ops[index].op not in ("Tj", "TJ", "'", '"') or index in block_ops
        for index in range(bt_op + 1, et_op)
    )

    if owns_bt:
        # The paragraph owns its text object: replace BT ... ET whole.
        start = pm.ops[bt_op].start
        end = pm.ops[et_op].end
        piece = pm.pieces.piece_of(start)
        base = pm.pieces.bounds[piece][0]
        block_bytes = serialize_ops([Op("BT", [])] + body + [Op("ET", [])])
        new_data = pm.streams[piece][:start - base] + block_bytes + pm.streams[piece][end - base:]
        new_streams = list(pm.streams)
        new_streams[piece] = new_data
        write_contents(doc, pm, new_streams)
        return LineEdit(substituted=substituted)

    # The paragraph's rows are spread over several text objects: emit the new
    # rows where the first row was, and clear every other show the paragraph
    # used.  Operators between them (Td, Tf, Tm, q/Q, cm) are left alone.
    _splice_replacements(
        doc, pm, {min(block_ops): serialize_ops(body)}, set(block_ops)
    )
    return LineEdit(substituted=substituted)
