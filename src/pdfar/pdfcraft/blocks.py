"""Group text lines into visual rows and editable paragraphs.

Mirrors ``pdfcraft-edit/src/text_regions/grouping.rs`` (``group_layout``):
rows are runs on one baseline; rows join into a paragraph when their edges
align and the gap matches the paragraph's own leading.  A blank line is about
twice the leading, so it starts a new paragraph rather than joining.
"""

from __future__ import annotations

from typing import List, Tuple

from .matrix import direction, then, to_frame
from .model import TextBlock, TextLine


def _plane(line: TextLine) -> Tuple[float, float]:
    return direction(then(line.origin.tm, line.origin.ctm))


def _frame_origin(line: TextLine) -> Tuple[float, float]:
    return to_frame(_plane(line), (line.origin.x, line.origin.baseline))


def _frame_start(line: TextLine) -> float:
    return line.frame[0]


def _frame_end(line: TextLine) -> float:
    return line.frame[2]


def _row_rect(row: List[int], lines: List[TextLine]) -> Tuple[float, float, float, float]:
    rects = [lines[i].rect for i in row]
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def _frame_row_rect(row: List[int], lines: List[TextLine]) -> Tuple[float, float, float, float]:
    """The row's box along and across its baseline (turned text included)."""
    frames = [lines[i].frame for i in row]
    return (
        min(f[0] for f in frames),
        min(f[1] for f in frames),
        max(f[2] for f in frames),
        max(f[3] for f in frames),
    )


def _same_style(a: TextLine, b: TextLine) -> bool:
    return (
        a.base_font == b.base_font
        and a.font_name == b.font_name
        and a.bold == b.bold
        and a.italic == b.italic
        and a.color == b.color
        and abs(a.rotation - b.rotation) < 0.01
        and abs(a.size - b.size) < 0.01
    )


def _same_plane(a: TextLine, b: TextLine) -> bool:
    da = _plane(a)
    db = _plane(b)
    return abs(da[0] - db[0]) < 1e-3 and abs(da[1] - db[1]) < 1e-3


_BULLETS = "•◦▪‣⁃*-\u2013\u2014"


def _opens_with_marker(text: str) -> bool:
    """A bullet, dash, or ``1.``/``a)`` marker followed by space or end.

    Mirrors ``opens_with_marker`` in ``text_regions/grouping.rs``: prose such as
    ``1.5`` or ``Dr. Smith`` is not a marker.
    """
    text = text.lstrip()
    if not text:
        return False
    first = text[0]
    if first in _BULLETS:
        return True
    if not (first.isascii() and (first.isdigit() or first.isalpha())):
        return False
    digits = 0
    while digits < len(text) and text[digits].isascii() and text[digits].isdigit():
        digits += 1
    letters = 0
    while letters < len(text) and text[letters].isascii() and text[letters].isalpha():
        letters += 1
    if digits:
        rest = text[digits:]
    elif letters == 1:
        rest = text[1:]
    else:
        return False
    return bool(rest) and rest[0] in ".)" and (len(rest) == 1 or rest[1].isspace())


def _starts_list_item(row: List[int], lines: List[TextLine]) -> bool:
    """A row that opens with a marker starts its own list item."""
    for index in row:
        text = lines[index].text.lstrip()
        if text:
            return _opens_with_marker(text)
    return False


def _joins_row(prev: TextLine, line: TextLine) -> bool:
    if not _same_plane(prev, line):
        return False
    prev_frame = _frame_origin(prev)
    frame = _frame_origin(line)
    gap = frame[0] - _frame_end(prev)
    small = min(prev.size, line.size)
    large = max(prev.size, line.size)
    baseline_gap = abs(prev_frame[1] - frame[1])
    if baseline_gap >= small * 0.2:
        # A superscript/subscript run may sit higher or lower when it is clearly
        # smaller and does not overlap horizontally.
        if not (gap >= 0.0 and small < large * 0.9 and baseline_gap < large * 0.5):
            return False
    if gap < -small * 0.2 or gap >= large * 1.5:
        return False
    if (
        prev.origin.bt_op != line.origin.bt_op
        and gap <= small * 0.01
        and _same_style(prev, line)
        and not prev.text.endswith((" ", "\t"))
        and not line.text.startswith((" ", "\t"))
    ):
        # Separate labels that merely touch are not one row.
        return False
    return True


def _joins_block(
    block: TextBlock,
    row: List[int],
    rows: List[List[int]],
    row_at: int,
    lines: List[TextLine],
) -> bool:
    if not block.rows:
        return False
    prev_row = block.rows[-1]
    prev = lines[prev_row[0]]
    first = lines[row[0]]
    if not _same_plane(prev, first):
        return False
    if abs(prev.origin.state.scale - first.origin.state.scale) >= 1e-6:
        return False
    if not _same_style(prev, first):
        return False
    prev_frame = _frame_origin(prev)
    frame = _frame_origin(first)
    prev_rect = _frame_row_rect(prev_row, lines)
    rect = _frame_row_rect(row, lines)
    tol = max(prev.size, first.size, 1.0)
    gap = prev_frame[1] - frame[1]
    if gap <= min(prev.size, first.size) * 0.8:
        return False
    if min(prev.size, first.size) < max(prev.size, first.size) * 0.4:
        return False
    aligned = (
        abs(prev_rect[0] - rect[0]) < tol
        or abs((prev_rect[0] + prev_rect[2] - rect[0] - rect[2]) / 2.0) < tol
        or abs(prev_rect[2] - rect[2]) < tol
        # A list item's text sits at a hanging indent under its marker: the row
        # it wraps onto starts further right than the marker row.
        or (
            rect[0] > prev_rect[0]
            and rect[0] - prev_rect[0] <= tol * 3.0
            and _opens_with_marker(lines[block.lines[0]].text.lstrip())
        )
    )
    if not aligned:
        return False

    # The paragraph's own leading, measured from its last two rows.
    own_leading = None
    if len(block.rows) >= 2:
        above = lines[block.rows[-2][0]]
        below = lines[block.rows[-1][0]]
        own_leading = abs(_frame_origin(above)[1] - _frame_origin(below)[1])
    stated = prev.origin.state.leading if prev.origin.state.leading > tol * 0.4 else None
    if stated is not None:
        stated *= prev.origin.k
    # A step that repeats on the following row is this paragraph's leading; a
    # single wider gap is a blank line and stays a break.
    repeated = None
    if row_at + 1 < len(rows):
        next_row = rows[row_at + 1]
        nfirst = lines[next_row[0]]
        ngap = frame[1] - _frame_origin(nfirst)[1]
        nframe = _frame_row_rect(next_row, lines)
        scale = max(abs(gap), abs(ngap))
        if (
            _same_plane(first, nfirst)
            and _same_style(first, nfirst)
            and min(first.size, nfirst.size) >= max(first.size, nfirst.size) * 0.4
            and abs(nframe[0] - frame[0]) < tol
            and ngap > tol * 0.8
            and scale < tol * 4.0
            and abs(ngap - gap) <= scale * 0.08 + 0.5
        ):
            repeated = gap
    lead = own_leading if own_leading and own_leading > tol * 0.4 else stated
    if lead is None:
        lead = repeated
    if lead is not None:
        max_gap = lead * 1.5
    else:
        following = None
        if row_at + 1 < len(rows):
            nfirst = lines[rows[row_at + 1][0]]
            if _same_plane(first, nfirst):
                ngap = frame[1] - _frame_origin(nfirst)[1]
                if tol * 0.8 < ngap < tol * 2.05:
                    following = ngap
        max_gap = min(following * 1.35, tol * 2.05) if following else tol * 2.05
    return gap < max_gap


def group_blocks(lines: List[TextLine]) -> List[TextBlock]:
    rows: List[List[int]] = []
    for index, line in enumerate(lines):
        if rows and _joins_row(lines[rows[-1][-1]], line):
            rows[-1].append(index)
        else:
            rows.append([index])

    blocks: List[TextBlock] = []
    row_texts: List[List[str]] = []
    for row_at, row in enumerate(rows):
        first = lines[row[0]]
        rect = _row_rect(row, lines)
        if (
            not blocks
            or _starts_list_item(row, lines)
            or not _joins_block(blocks[-1], row, rows, row_at, lines)
        ):
            blocks.append(
                TextBlock(
                    text="",
                    rect=rect,
                    base_font=first.base_font,
                    size=first.size,
                    bold=first.bold,
                    italic=first.italic,
                    color=first.color,
                    rotation=first.rotation,
                    lines=[],
                    rows=[],
                    editor_text="",
                    invisible=True,
                )
            )
            row_texts.append([])
        block = blocks[-1]

        parts: List[str] = []
        prev: TextLine | None = None
        for index in row:
            line = lines[index]
            if prev is not None:
                gap = _frame_start(line) - _frame_end(prev)
                if (
                    gap > line.size * 0.12
                    and not (parts and parts[-1].endswith((" ", "\t")))
                    and not line.text.startswith((" ", "\t"))
                ):
                    parts.append(" ")
            parts.append(line.text)
            block.lines.append(index)
            block.invisible = block.invisible and line.invisible
            prev = line
        row_text = "".join(parts)
        row_texts[-1].append(row_text)

        if block.text:
            if block.text.endswith("-"):
                block.text = block.text[:-1]
            else:
                block.text += " "
        block.text += row_text.strip()
        block.rect = (
            min(block.rect[0], rect[0]),
            min(block.rect[1], rect[1]),
            max(block.rect[2], rect[2]),
            max(block.rect[3], rect[3]),
        )
        block.rows.append(list(row))

    for block, texts in zip(blocks, row_texts):
        block.editor_text = "\n".join(texts)
    return blocks
