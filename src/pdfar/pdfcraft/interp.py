"""Interpret a page's content stream into positioned text runs.

This mirrors the parts of ``pdfcraft-edit/src/text/interp.rs`` that decide what
text is drawn, in what font and state, and where: the graphics/text state
machine, glyph decoding, advances including ``TJ`` adjustments, and the text
rendering matrix.  It deliberately does not try to be a renderer: clips,
blend modes and soft masks are tracked only far enough to keep the state
correct for the runs this engine edits.

Adapted concepts (not code) from PdfCraft, MIT OR Apache-2.0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from ._compat import fitz

from .fonts import FontMetrics, get_key, object_source
from .matrix import IDENTITY, Matrix, apply, direction, scale_of, then, to_frame, translation
from .model import Shown, TextState
from .stream import Op, PdfString, Ref, parse_ops


MAX_FORM_DEPTH = 10
MAX_FORM_VISITS = 2000


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def _resolve_resources(doc: fitz.Document, value) -> Tuple[dict, Optional[int]]:
    """Return ``(resources_dict, xref)`` for a Resources value."""
    num = value.num if isinstance(value, Ref) else None
    if num is not None:
        source = object_source(doc, value)
        if isinstance(source, dict):
            return source, num
        return {}, num
    if isinstance(value, dict):
        return value, None
    return {}, None


def page_resources(doc: fitz.Document, page: fitz.Page) -> Tuple[dict, Optional[int]]:
    """The page's resources, following /Parent when the page inherits them."""
    node = page.xref
    for _ in range(32):
        value = get_key(doc, node, "Resources")
        if value is not None:
            return _resolve_resources(doc, value)
        parent = get_key(doc, node, "Parent")
        if not isinstance(parent, Ref):
            break
        node = parent.num
    return {}, None


def _font_dict(doc: fitz.Document, resources: dict, resources_xref: Optional[int]):
    value = resources.get("Font") if isinstance(resources, dict) else None
    if value is None and resources_xref is not None:
        value = get_key(doc, resources_xref, "Font")
    if isinstance(value, Ref):
        resolved = object_source(doc, value)
        return resolved if isinstance(resolved, dict) else {}
    return value if isinstance(value, dict) else {}


def _xobject_dict(doc: fitz.Document, resources: dict, resources_xref: Optional[int]):
    value = resources.get("XObject") if isinstance(resources, dict) else None
    if value is None and resources_xref is not None:
        value = get_key(doc, resources_xref, "XObject")
    if isinstance(value, Ref):
        resolved = object_source(doc, value)
        return resolved if isinstance(resolved, dict) else {}
    return value if isinstance(value, dict) else {}


class FontCache:
    """Resource name -> metrics, per (resources object, name)."""

    def __init__(self, doc: fitz.Document):
        self.doc = doc
        self._by_xref: Dict[int, FontMetrics] = {}

    def metrics(self, xref: int) -> FontMetrics:
        cached = self._by_xref.get(xref)
        if cached is None:
            cached = FontMetrics.from_doc(self.doc, xref)
            self._by_xref[xref] = cached
        return cached

    def for_resources(self, resources: dict, resources_xref: Optional[int]) -> Dict[str, FontMetrics]:
        out: Dict[str, FontMetrics] = {}
        for name, value in _font_dict(self.doc, resources, resources_xref).items():
            if isinstance(value, Ref):
                out[str(name)] = self.metrics(value.num)
            elif isinstance(value, dict):
                # An inline font dictionary: rare, but legal.
                out[str(name)] = _metrics_from_dict(self.doc, value)
        return out


def _metrics_from_dict(doc: fitz.Document, value: dict) -> FontMetrics:
    # Inline fonts have no xref; wrap the dictionary in a temporary object so
    # the same reader can be used.  This is only a fallback for unusual files.
    num = doc.get_new_xref()
    from .stream import serialize_operand

    doc.update_object(num, serialize_operand(value).decode("latin-1"))
    return FontMetrics.from_doc(doc, num)


# ---------------------------------------------------------------------------
# Tracking: the part of a TJ gap that is kerning, not a word space
# ---------------------------------------------------------------------------


def typical_tracking(gaps: List[float], limit: float) -> float:
    if len(gaps) < 3:
        return 0.0
    gaps = sorted(gaps)
    value = gaps[len(gaps) // 3]
    if not math.isfinite(value) or value > limit:
        return 0.0
    return max(0.0, value)


def tj_tracking(pieces: Sequence[object], metrics: FontMetrics, size: float, scale: float) -> float:
    gaps: List[float] = []
    previous = False
    gap = 0.0
    for piece in pieces:
        if isinstance(piece, PdfString):
            for code, length in metrics.codes(piece.data):
                text = metrics.text_of(code)
                blank = metrics.is_space(code, length) or (
                    text is not None and text != "" and text.isspace()
                )
                if previous and not blank:
                    gaps.append(gap)
                previous = not blank
                gap = 0.0
        elif isinstance(piece, (int, float)) and not isinstance(piece, bool):
            gap += -float(piece) / 1000.0 * size * scale
    return typical_tracking(gaps, abs(size * scale) * 0.5)


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


@dataclass
class _FormCall:
    data: bytes
    resources: dict
    resources_xref: Optional[int]
    ctm: Matrix
    stream_id: int


class _Interpreter:
    def __init__(self, doc: fitz.Document, fonts: FontCache, forms: bool = True):
        self.doc = doc
        self.fonts = fonts
        self.forms = forms
        self.out: List[Shown] = []
        self.next_stream = 1
        self.visits = 0
        self.depth = 0

    def run(
        self,
        ops: Sequence[Op],
        resources: dict,
        resources_xref: Optional[int],
        ctm: Matrix = IDENTITY,
        stream: int = 0,
    ) -> List[Shown]:
        font_map = self.fonts.for_resources(resources, resources_xref)
        state = TextState()
        tm: Matrix = IDENTITY
        tlm: Matrix = IDENTITY
        stack: List[Tuple[Matrix, TextState, Matrix, Matrix]] = []
        bt = 0
        bt_op = 0
        bt_state = state.copy()

        for index, op in enumerate(ops):
            name = op.op
            if name == "q":
                stack.append((ctm, state.copy(), tm, tlm))
            elif name == "Q":
                if stack:
                    ctm, state, tm, tlm = stack.pop()
            elif name == "cm":
                nums = op.nums()
                if len(nums) == 6:
                    ctm = then((*nums,), ctm)
            elif name == "BT":
                tm = IDENTITY
                tlm = IDENTITY
                bt += 1
                bt_op = index
                bt_state = state.copy()
            elif name == "ET":
                pass
            elif name == "Tf":
                if len(op.operands) >= 2 and isinstance(op.operands[1], (int, float)):
                    state.size = float(op.operands[1])
                font_name = op.name(0)
                if font_name is not None:
                    state.font_name = font_name
            elif name == "Tc":
                nums = op.nums()
                if nums:
                    state.char_spacing = float(nums[0])
            elif name == "Tw":
                nums = op.nums()
                if nums:
                    state.word_spacing = float(nums[0])
            elif name == "Tz":
                nums = op.nums()
                if nums:
                    state.scale = float(nums[0]) / 100.0
            elif name == "TL":
                nums = op.nums()
                if nums:
                    state.leading = float(nums[0])
            elif name == "Ts":
                nums = op.nums()
                if nums:
                    state.rise = float(nums[0])
            elif name == "Tr":
                nums = op.nums()
                if nums:
                    state.render_mode = int(nums[0])
            elif name in ("g", "rg", "k"):
                state.fill = _fill_color(name, op.nums())
            elif name in ("sc", "scn"):
                state.fill = _fill_from_components(op.nums())
            elif name == "Td" or name == "TD":
                nums = op.nums()
                if len(nums) >= 2:
                    if name == "TD":
                        state.leading = -float(nums[1])
                    tlm = then(translation(float(nums[0]), float(nums[1])), tlm)
                    tm = tlm
            elif name == "Tm":
                nums = op.nums()
                if len(nums) == 6:
                    tlm = (*nums,)
                    tm = tlm
            elif name == "T*":
                tlm = then(translation(0.0, -state.leading), tlm)
                tm = tlm
            elif name in ("Tj", "TJ", "'", '"'):
                if name in ("'", '"'):
                    if name == '"':
                        nums = op.nums()
                        if len(nums) >= 2:
                            state.word_spacing = float(nums[0])
                            state.char_spacing = float(nums[1])
                    tlm = then(translation(0.0, -state.leading), tlm)
                    tm = tlm
                tm = self._show(op, index, font_map, state, ctm, tm, tlm, bt, bt_op, bt_state, stream)
            elif name == "Do" and self.forms:
                self._do(op, resources, resources_xref, ctm, stream)
        return self.out

    def _do(self, op: Op, resources: dict, resources_xref: Optional[int], ctm: Matrix, stream: int) -> None:
        xobject_name = op.name(0)
        if xobject_name is None:
            return
        xobjects = _xobject_dict(self.doc, resources, resources_xref)
        value = xobjects.get(xobject_name)
        if not isinstance(value, Ref):
            return
        if get_key(self.doc, value.num, "Subtype") != "Form":
            return
        if self.depth >= MAX_FORM_DEPTH or self.visits >= MAX_FORM_VISITS:
            return
        data = self.doc.xref_stream(value.num) or b""
        matrix_value = get_key(self.doc, value.num, "Matrix")
        form_matrix: Matrix = IDENTITY
        if isinstance(matrix_value, list) and len(matrix_value) == 6:
            try:
                form_matrix = tuple(float(v) for v in matrix_value)  # type: ignore[assignment]
            except (TypeError, ValueError):
                form_matrix = IDENTITY
        form_resources, form_xref = _resolve_resources(
            self.doc, get_key(self.doc, value.num, "Resources") or resources
        )
        form_ctm = then(form_matrix, ctm)
        stream_id = self.next_stream
        self.next_stream += 1
        self.visits += 1
        self.depth += 1
        try:
            self.run(parse_ops(data), form_resources, form_xref, form_ctm, stream_id)
        finally:
            self.depth -= 1

    def _show(
        self,
        op: Op,
        index: int,
        font_map: Dict[str, FontMetrics],
        state: TextState,
        ctm: Matrix,
        tm: Matrix,
        tlm: Matrix,
        bt: int,
        bt_op: int,
        bt_state: TextState,
        stream: int,
    ) -> Matrix:
        if state.font_name is None:
            return tm
        metrics = font_map.get(state.font_name)
        if metrics is None:
            return tm
        pieces: List[object]
        if op.op == "TJ":
            # A surplus array (`[(A)] [(B)] TJ`) is malformed, but the operator
            # takes the one immediately before it (PdfCraft #3569).
            array = op.array(-1)
            pieces = list(array) if isinstance(array, list) else []
        else:
            # `"` carries its string last, after the two spacing operands.
            string = op.string(-1)
            pieces = [string] if string is not None else []

        tracking = tj_tracking(pieces, metrics, state.size, state.scale) if op.op == "TJ" else 0.0
        trm = then(tm, ctm)
        start = apply(trm, 0.0, state.rise)
        text_parts: List[str] = []
        glyph_count = 0
        x_text = 0.0
        decodable = True

        for piece in pieces:
            if isinstance(piece, PdfString):
                for code, length in metrics.codes(piece.data):
                    glyph_count += 1
                    shown = metrics.text_of(code)
                    if shown is not None:
                        text_parts.append(shown)
                    else:
                        decodable = False
                    width = metrics.width(code) * state.size
                    spacing = state.char_spacing + (state.word_spacing if metrics.is_space(code, length) else 0.0)
                    x_text += (width + spacing) * state.scale
            elif isinstance(piece, (int, float)) and not isinstance(piece, bool):
                dx = -float(piece) / 1000.0 * state.size * state.scale
                if dx - tracking > abs(state.size * state.scale) * 0.2 and not (
                    text_parts and text_parts[-1].endswith(" ")
                ):
                    text_parts.append(" ")
                x_text += dx

        text = "".join(text_parts)
        if glyph_count == 0:
            return tm

        tm_after = then(translation(x_text, 0.0), tm)
        end_x, end_y = apply(then(tm_after, ctm), 0.0, state.rise)
        # The box: from descent to ascent across the run.
        corners = [
            apply(trm, 0.0, state.rise + metrics.descent * state.size),
            apply(trm, x_text, state.rise + metrics.descent * state.size),
            apply(trm, 0.0, state.rise + metrics.ascent * state.size),
            apply(trm, x_text, state.rise + metrics.ascent * state.size),
        ]
        rect = (
            min(p[0] for p in corners),
            min(p[1] for p in corners),
            max(p[0] for p in corners),
            max(p[1] for p in corners),
        )
        # The same corners along and across the baseline: the run's box in its own
        # frame, so turned text lines up the way upright text does.
        run_dir = direction(trm)
        framed = [to_frame(run_dir, p) for p in corners]
        frame = (
            min(p[0] for p in framed),
            min(p[1] for p in framed),
            max(p[0] for p in framed),
            max(p[1] for p in framed),
        )
        _h, vertical_scale = scale_of(trm)
        size_user = vertical_scale * state.size
        self.out.append(
            Shown(
                op_index=index,
                text=text,
                glyph_count=glyph_count,
                advance=x_text,
                tracking=tracking,
                tm=tm,
                tlm=tlm,
                ctm=ctm,
                state=state.copy(),
                bt_state=bt_state.copy(),
                bt=bt,
                bt_op=bt_op,
                rect=rect,
                frame=frame,
                baseline=start[1],
                start_x=start[0],
                end_x=end_x,
                end_y=end_y,
                font_name=state.font_name,
                base_font=metrics.base_font,
                bold=metrics.bold,
                italic=metrics.italic,
                size=size_user if size_user > 0 else state.size,
                color=state.fill,
                render_mode=state.render_mode,
                stream=stream,
                decodable=decodable,
                vertical=metrics.vertical,
            )
        )
        return then(translation(x_text, 0.0), tm)


def _fill_color(name: str, nums: Sequence[float]) -> Tuple[float, float, float]:
    if name == "g" and nums:
        v = min(1.0, max(0.0, float(nums[0])))
        return (v, v, v)
    if name == "rg" and len(nums) >= 3:
        return tuple(min(1.0, max(0.0, float(v))) for v in nums[:3])  # type: ignore[return-value]
    if name == "k" and len(nums) >= 4:
        c, m, y, k = (float(v) for v in nums[:4])
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return (0.0, 0.0, 0.0)


def _fill_from_components(nums: Sequence[float]) -> Tuple[float, float, float]:
    if len(nums) == 1:
        v = min(1.0, max(0.0, float(nums[0])))
        return (v, v, v)
    if len(nums) >= 3:
        return tuple(min(1.0, max(0.0, float(v))) for v in nums[:3])  # type: ignore[return-value]
    return (0.0, 0.0, 0.0)


def interpret(
    doc: fitz.Document,
    ops: Sequence[Op],
    resources: dict,
    resources_xref: Optional[int],
    fonts: FontCache,
    ctm: Matrix = IDENTITY,
    forms: bool = True,
) -> List[Shown]:
    return _Interpreter(doc, fonts, forms).run(ops, resources, resources_xref, ctm)
