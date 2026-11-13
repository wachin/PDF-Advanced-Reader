"""Join positioned runs into text lines.

A line is a run of compatible text-showing operators on one baseline in one
content-stream piece.  Geometry, not ``BT``/``ET`` boundaries, decides: many
producers put every word in its own text object, and others put several
paragraphs in one text object.  Both must read the same way.

Mirrors ``pdfcraft-edit/src/text/lines.rs`` (``text_lines_in``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .fonts import strip_subset_tag
from .matrix import direction, then, to_frame
from .model import Origin, Shown, TextLine


def same_font(a: Shown, b: Shown) -> bool:
    return (
        strip_subset_tag(a.base_font) == strip_subset_tag(b.base_font)
        and a.font_name == b.font_name
    )


def compatible_word(line: TextLine, s: Shown) -> bool:
    """Whether two runs in different text objects may share a line."""
    origin = line.origin
    if origin.state.char_spacing != s.state.char_spacing:
        if abs(origin.state.char_spacing - s.state.char_spacing) > max(s.state.size, 1.0) / 1000.0:
            return False
    if abs(origin.state.word_spacing - s.state.word_spacing) > max(s.state.size, 1.0) / 1000.0:
        return False
    if abs(origin.state.scale - s.state.scale) > 1e-6:
        return False
    if abs(origin.state.rise - s.state.rise) > 1e-6:
        return False
    for i in range(4):
        if abs(origin.tm[i] - s.tm[i]) > 1e-6:
            return False
    for i in range(6):
        if abs(origin.ctm[i] - s.ctm[i]) > 1e-6:
            return False
    if origin.state.fill != s.state.fill:
        return False
    return True


@dataclass
class Pen:
    bt: int
    dir: Tuple[float, float]
    across: float
    end: float
    size: float

    @classmethod
    def after(cls, s: Shown) -> "Pen":
        d = direction(then(s.tm, s.ctm))
        start = to_frame(d, (s.start_x, s.baseline))
        end = to_frame(d, (s.end_x, s.end_y))[0]
        return cls(s.bt, d, start[1], end, max(s.size, 1.0))

    def continues(self, s: Shown, reach: float) -> Tuple[bool, float]:
        d = direction(then(s.tm, s.ctm))
        start = to_frame(d, (s.start_x, s.baseline))
        gap = start[0] - self.end
        same_dir = abs(d[0] - self.dir[0]) < 1e-3 and abs(d[1] - self.dir[1]) < 1e-3
        near = abs(start[1] - self.across) < self.size * 0.3
        return (same_dir and near and gap > -self.size and gap < self.size * reach, gap)


def _same_style(line: TextLine, s: Shown) -> bool:
    return (
        same_font_metrics(line, s)
        and abs(line.size - s.size) < 0.01
        and line.bold == s.bold
        and line.italic == s.italic
        and line.color == s.color
        and line.render_mode == s.render_mode
    )


def same_font_metrics(line: TextLine, s: Shown) -> bool:
    return strip_subset_tag(line.base_font) == strip_subset_tag(s.base_font) and line.font_name == s.font_name


def join_lines(shown: List[Shown]) -> List[TextLine]:
    lines: List[TextLine] = []
    last: Optional[Tuple[int, Pen]] = None

    for s in shown:
        joins = False
        if last is not None and lines:
            piece, pen = last
            same_bt = pen.bt == s.bt
            reach = 3.0 if same_bt else 1.5
            near, _gap = pen.continues(s, reach)
            previous = lines[-1]
            joins = (
                piece == s.stream
                and (same_bt or compatible_word(previous, s))
                and (not s.text.strip() or same_font_metrics(previous, s))
                and (
                    not s.text.strip()
                    or (
                        abs(s.size - previous.size) < 0.01
                        and s.bold == previous.bold
                        and s.italic == previous.italic
                        and s.color == previous.color
                        and s.render_mode == previous.render_mode
                    )
                )
                and near
            )

        if joins and lines:
            line = lines[-1]
            gap = 0.0 if last is None else last[1].continues(s, 0.0)[1]
            scale = abs(s.state.scale)
            spaced = (
                gap - s.tracking > line.size * scale * 0.2
                and not line.text.endswith(" ")
                and not s.text.startswith(" ")
            )
            if spaced:
                line.text += " "
            line.text += s.text
            line.rect = (
                min(line.rect[0], s.rect[0]),
                min(line.rect[1], s.rect[1]),
                max(line.rect[2], s.rect[2]),
                max(line.rect[3], s.rect[3]),
            )
            line.frame = (
                min(line.frame[0], s.frame[0]),
                min(line.frame[1], s.frame[1]),
                max(line.frame[2], s.frame[2]),
                max(line.frame[3], s.frame[3]),
            )
            line.runs.append(s.text)
            line.ops.append(s.op_index)
            line.invisible = line.invisible and s.render_mode == 3
            line.decodable = line.decodable and s.decodable
            line.vertical = line.vertical or s.vertical
        else:
            state = s.state.copy()
            origin = Origin(
                tm=s.tm,
                tlm=s.tlm,
                ctm=s.ctm,
                k=max(1e-9, math.hypot(then(s.tm, s.ctm)[2], then(s.tm, s.ctm)[3])),
                baseline=s.baseline,
                x=s.start_x,
                state=state,
                bt_op=s.bt_op,
                bt_state=s.bt_state.copy(),
            )
            lines.append(
                TextLine(
                    text=s.text,
                    rect=s.rect,
                    frame=s.frame,
                    font_name=s.font_name,
                    base_font=s.base_font,
                    size=s.size,
                    bold=s.bold,
                    italic=s.italic,
                    color=s.color,
                    stream=s.stream,
                    ops=[s.op_index],
                    runs=[s.text],
                    origin=origin,
                    render_mode=s.render_mode,
                    invisible=s.render_mode == 3,
                    tracking=s.tracking,
                    decodable=s.decodable,
                    vertical=s.vertical,
                )
            )
        last = (s.stream, Pen.after(s))

    return [line for line in lines if line.text.strip()]
