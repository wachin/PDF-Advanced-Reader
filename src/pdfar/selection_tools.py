"""Pure geometry helpers for area and table selection.

Words are PyMuPDF ``get_text("words")`` tuples:
``(x0, y0, x1, y1, text)`` in unrotated page space.  Keeping these helpers
free of Qt makes the table grid logic easy to test.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Iterable, List, Sequence, Tuple

Word = Tuple[float, float, float, float, str]
Rect = Tuple[float, float, float, float]


def _intersects(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def words_in_rect(words: Iterable[Word], rect: Rect) -> List[Word]:
    """Words whose boxes overlap *rect*."""
    return [word for word in words if _intersects(word[:4], rect)]


def group_lines(words: Sequence[Word], tolerance: float = 2.0) -> List[str]:
    """Join words into visual lines, top to bottom and left to right."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
    lines: List[List[Word]] = []
    current: List[Word] = []
    line_center = 0.0
    line_height = 0.0
    for word in ordered:
        center = (word[1] + word[3]) / 2.0
        height = word[3] - word[1]
        if not current:
            current = [word]
            line_center = center
            line_height = height
            continue
        same_line = abs(center - line_center) <= max(
            tolerance, 0.6 * max(height, line_height))
        if same_line:
            current.append(word)
            line_height = max(line_height, height)
        else:
            lines.append(current)
            current = [word]
            line_center = center
            line_height = height
    if current:
        lines.append(current)
    return [
        " ".join(word[4] for word in sorted(line, key=lambda w: w[0]))
        for line in lines
    ]


def find_dividers(words: Sequence[Word], axis: str) -> List[float]:
    """Locate table gutters with Okular's tick-sweep algorithm.

    ``axis`` is ``"x"`` for columns or ``"y"`` for rows.  Each word edge adds
    +1/-1 to a running tally; when it returns to zero, the whitespace up to the
    next edge is a gutter, and its midpoint becomes a divider.
    """
    start, end = (0, 2) if axis == "x" else (1, 3)
    ticks: List[Tuple[float, int]] = []
    for word in words:
        ticks.append((word[start], 1))
        ticks.append((word[end], -1))
    ticks.sort()
    dividers: List[float] = []
    tally = 0
    for index, (position, delta) in enumerate(ticks):
        tally = max(0, tally + delta)
        if tally or index + 1 >= len(ticks):
            continue
        next_position = ticks[index + 1][0]
        if next_position > position:
            dividers.append((position + next_position) / 2.0)
    return dividers


def _cell_index(edges: Sequence[float], position: float) -> int:
    if len(edges) < 2:
        return 0
    index = bisect_right(edges, position) - 1
    return max(0, min(index, len(edges) - 2))


def table_text(words: Sequence[Word], rect: Rect) -> str:
    """Format words inside *rect* as a tab-separated table."""
    selected = words_in_rect(words, rect)
    if not selected:
        return ""
    xs = [rect[0], *find_dividers(selected, "x"), rect[2]]
    ys = [rect[1], *find_dividers(selected, "y"), rect[3]]
    cells: List[List[List[Word]]] = [
        [[] for _ in range(len(xs) - 1)] for _ in range(len(ys) - 1)
    ]
    for word in selected:
        column = _cell_index(xs, (word[0] + word[2]) / 2.0)
        row = _cell_index(ys, (word[1] + word[3]) / 2.0)
        cells[row][column].append(word)

    rows: List[List[str]] = []
    for row in cells:
        rows.append([
            " ".join(
                word[4] for word in sorted(
                    cell, key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
            )
            for cell in row
        ])
    while rows and not any(rows[-1]):
        rows.pop()
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    while width and all(
            len(row) < width or not row[width - 1] for row in rows):
        width -= 1
    if not width:
        return ""
    return "\n".join("\t".join(row[:width]) for row in rows)


def table_size(text: str) -> Tuple[int, int]:
    """Return ``(rows, columns)`` for text produced by :func:`table_text`."""
    lines = text.splitlines()
    if not lines:
        return (0, 0)
    return (len(lines), max(len(line.split("\t")) for line in lines))
