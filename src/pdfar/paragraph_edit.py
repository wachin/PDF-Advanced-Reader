"""Phase 5 — the paragraph reflow edit.

:class:`ReflowParagraph` is a normal :class:`pdfar.edits.Edit`: it runs on the
session's throwaway clone, so undo, redo, dirty-state, save and revert work
exactly as they do for word edits.  One paragraph reflow is one transaction.

The edit does not store a rendered layout.  It stores the paragraph's position
in the page's reading order, the original text as an anchor, and the user's new
text.  On ``apply`` it re-extracts the paragraph from the clone and re-runs the
same planner the preview used, so preview and commit cannot disagree.

Content-stream strategy
-----------------------
The paragraph's text-showing operators must live in one stream, inside one
``BT … ET`` block, and every operator in that block must be either part of the
paragraph or a text-state/positioning operator.  The block is then replaced as
a unit with a freshly written ``BT … ET`` block.  Nothing outside the block is
touched: colours, clipping, graphics state, images, annotations and every other
operator keep their bytes.  If the paragraph spans streams, is drawn twice
through a shared Form XObject, or sits in a block with unrelated content, the
edit refuses with an explanation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from .content_stream import ContentStreamParser
from .edits import Edit, EditError, Scope
from .paragraph import (
    LayoutOptions,
    Paragraph,
    ParagraphPlan,
    ParagraphUnsupported,
    ParagraphOverflow,
    extract_range,
    plan_reflow,
)
from .text_edit_existing import (
    FontIssue,
    FontSubstitution,
    FontSubstitutionRequired,
    OperatorLoc,
    copy_on_write_xobject,
    font_info_for,
    iter_page_runs,
    page_font_codecs,
)

_SUBSET_TAG_RE = re.compile(r"^[A-Z]{6}\+")
_NORM_RE = re.compile(r"\s+")

#: Operators that may share a ``BT … ET`` block with the paragraph.  Anything
#: else (``q``/``Q``, ``cm``, colour, ``Do``, inline images, …) is refused,
#: because deleting it would change the rest of the page.
_BLOCK_OPS = frozenset({
    "BT", "ET", "Tf", "Tm", "Td", "TD", "T*", "TL",
    "Tc", "Tw", "Tz", "Ts", "Tr",
})


def _norm(text: str) -> str:
    return _NORM_RE.sub("", text or "")


def _num(value: float) -> bytes:
    """Format a PDF number without exponent notation."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    if text in ("", "-0", "-"):
        text = "0"
    return text.encode("ascii")


def _font_issue(page: fitz.Page, para: Paragraph,
                plan: ParagraphPlan) -> FontIssue:
    info = font_info_for(page, para.res_owner, para.font_resource)
    return FontIssue(
        reason=plan.substitution_reason,
        text=para.text[:240],
        font_name=para.font,
        font_type=info.type if info is not None else "",
        embedded=bool(info.embedded) if info is not None else False,
        subsetted=bool(_SUBSET_TAG_RE.match(para.font or "")),
        missing=plan.missing,
    )


def _loc_for(piece) -> OperatorLoc:
    """Minimal location object for the copy-on-write helper."""
    return OperatorLoc(
        source=piece.source,
        op_index=piece.op_index,
        operator="",
        text="",
        origin=piece.origin,
        font_resource=piece.font_resource,
        font_size=piece.font_size,
    )


def _validate_block(ops, lo: int, hi: int, keys: Sequence[int]) -> None:
    """Refuse a range that contains anything but the paragraph and its state.

    Balanced ``q``/``Q`` wrappers and identical ``cm`` matrices are allowed:
    producers routinely wrap every text block in ``q cm … Q`` (rotated text is
    always drawn this way), and a balanced wrapper cannot change the state
    seen by the rest of the page.  A ``cm`` is only accepted inside a ``q``,
    and every ``cm`` in the range must be identical, because the block is
    rebuilt under one transform.  Anything else that changes state (colour,
    ``gs``, ``Do``, inline images, …) is refused.
    """
    key_set = set(keys)
    depth = 0
    for index in range(lo):
        if ops[index].operator == "q":
            depth += 1
        elif ops[index].operator == "Q":
            depth -= 1
    start_depth = depth
    matrices: List[Tuple] = []
    bt_depth = 0
    for index in range(lo, hi + 1):
        operator = ops[index].operator
        if operator == "q":
            depth += 1
        elif operator == "Q":
            depth -= 1
            if depth < 0:
                raise ParagraphUnsupported(
                    "The paragraph's text block has an unbalanced 'Q' "
                    "operator, so it cannot be rebuilt safely.")
        elif operator == "BT":
            bt_depth += 1
        elif operator == "ET":
            bt_depth -= 1
            if bt_depth < 0:
                raise ParagraphUnsupported(
                    "The paragraph's text block has an unbalanced 'ET' "
                    "operator, so it cannot be rebuilt safely.")
        elif operator == "cm":
            if depth <= 0:
                raise ParagraphUnsupported(
                    "The paragraph's text block changes the transform outside "
                    "a saved graphics state, so removing it would move other "
                    "content. The edit was refused.")
            matrices.append(tuple(ops[index].operands))
        elif operator in ContentStreamParser.TEXT_SHOW_OPS:
            if index not in key_set:
                raise ParagraphUnsupported(
                    "The paragraph's text block also draws text that is not "
                    "part of the selected paragraph. Rebuilding the block "
                    "would duplicate or remove that text, so the edit was "
                    "refused. Select the paragraph's lines manually or edit "
                    "the individual lines instead.")
        elif operator not in _BLOCK_OPS:
            raise ParagraphUnsupported(
                f"The paragraph's text block contains a '{operator}' operator "
                f"between its text. Removing the block would change graphics, "
                f"colour or clipping, so the edit was refused.")
    if depth != start_depth:
        raise ParagraphUnsupported(
            "The paragraph's text block leaves a 'q' unbalanced, so it cannot "
            "be rebuilt safely.")
    if bt_depth != 0:
        raise ParagraphUnsupported(
            "The paragraph's text block leaves a 'BT' unbalanced, so it "
            "cannot be rebuilt safely.")
    if len(set(matrices)) > 1:
        raise ParagraphUnsupported(
            "The paragraph's lines are drawn under different transforms. "
            "Rebuilding them as one block would move some of the lines, so "
            "the edit was refused.")
    if any(index < lo or index > hi for index in keys):
        raise ParagraphUnsupported(
            "The paragraph's text is not contained in one rebuildable block.")


def _block_bounds(ops, keys: Sequence[int]) -> Tuple[int, int, int, int]:
    """Find the rebuildable range around the paragraph.

    Returns ``(lo, hi, first_bt, q_count)``.  ``lo`` is grown over the
    ``q``/``cm`` prefix that establishes the paragraph's transform and ``hi``
    over the matching ``Q`` closers.  The prefix is re-emitted verbatim, so a
    per-block ``q cm`` wrapper (the normal way to draw rotated text) survives.
    """
    first, last = keys[0], keys[-1]
    bt = first
    while bt >= 0 and ops[bt].operator != "BT":
        if ops[bt].operator == "ET":
            raise ParagraphUnsupported(
                "The paragraph's text is not inside a BT/ET block.")
        bt -= 1
    if bt < 0:
        raise ParagraphUnsupported(
            "The paragraph's text is not inside a BT/ET block.")
    et = last
    while et < len(ops) and ops[et].operator != "ET":
        if ops[et].operator == "BT":
            raise ParagraphUnsupported(
                "The paragraph's text spans more than one BT/ET block.")
        et += 1
    if et >= len(ops):
        raise ParagraphUnsupported(
            "The paragraph's text has no closing ET operator.")
    lo, hi = bt, et
    while lo > 0 and ops[lo - 1].operator in ("q", "cm"):
        lo -= 1
    while hi + 1 < len(ops) and ops[hi + 1].operator == "Q":
        hi += 1
    q_count = sum(1 for index in range(lo, bt) if ops[index].operator == "q")
    return lo, hi, bt, q_count


def _encode_tj(line, encode, font_size: float, h_scale: float,
               space_advance: float) -> bytes:
    """Build a ``TJ`` array with word gaps and optional justification.

    Spaces are written as gaps rather than space glyphs: many PDFs draw them
    that way, and some subsetted fonts no longer contain a space glyph at all.
    A gap of ``space_advance`` em is exactly what a natural space would
    advance, so extraction still sees a word break.
    """
    words = line.text.split(" ")
    base = -space_advance * 1000.0
    extra = 0.0
    if line.gap_extra:
        extra = line.gap_extra * 1000.0 / (font_size * h_scale)
    step = base - extra
    items: List[bytes] = []
    for index, word in enumerate(words):
        items.append(b"<" + encode(word).hex().encode("ascii") + b">")
        if index < len(words) - 1 and abs(step) > 0.01:
            items.append(_num(step))
    return b"[" + b" ".join(items) + b"] TJ"


def build_block(para: Paragraph, plan: ParagraphPlan, encode,
                resource: str) -> bytes:
    """Serialize the planned lines as one ``BT … ET`` block.

    Each line gets an explicit ``Tm`` so rotation, scaling and leading are
    reproduced without relying on ``Td`` state left by the original content.
    """
    tm = para.tm
    if tm is None:
        raise ParagraphUnsupported(
            "The paragraph's text matrix is unknown, so the new layout cannot "
            "be written.")
    a, b, c, d, e0, f0 = (float(v) for v in tm[:6])
    font_size = plan.font_size
    h_scale = para.h_scale or 1.0
    normal = para.normal_scale or 1.0
    leading_text = plan.leading / normal

    parts: List[bytes] = [b"BT"]
    parts.append(b"/" + resource.encode("latin-1") + b" "
                 + _num(font_size) + b" Tf")
    if abs(para.char_spacing) > 1e-9:
        parts.append(_num(para.char_spacing) + b" Tc")
    if abs(para.word_spacing) > 1e-9:
        parts.append(_num(para.word_spacing) + b" Tw")
    if abs(h_scale - 1.0) > 1e-9:
        parts.append(_num(h_scale * 100.0) + b" Tz")
    if abs(para.rise) > 1e-9:
        parts.append(_num(para.rise) + b" Ts")
    if para.render_mode:
        parts.append(str(int(para.render_mode)).encode("ascii") + b" Tr")

    for index, line in enumerate(plan.lines):
        if line.blank:
            continue
        tx = e0 + a * line.x + c * (-leading_text * index)
        ty = f0 + b * line.x + d * (-leading_text * index)
        parts.append(b" ".join((_num(a), _num(b), _num(c), _num(d),
                                _num(tx), _num(ty))) + b" Tm")
        parts.append(_encode_tj(line, encode, font_size, h_scale,
                                plan.space_advance))
    parts.append(b"ET")
    return b"\n".join(parts)


def _verify_written_text(page: fitz.Page, plan: ParagraphPlan) -> None:
    """Fail closed if the written text does not extract back as intended."""
    try:
        extracted = page.get_text()
    except Exception:
        return
    if _norm(plan.text) and _norm(plan.text) not in _norm(extracted):
        raise EditError(
            "The reflowed paragraph could not be verified after writing: the "
            "new text does not extract from the page. The document was left "
            "unchanged.")


# --------------------------------------------------------------------------- #
# The Edit
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ReflowParagraph(Edit):
    """Replace one paragraph with a re-wrapped version of *new_text*."""

    page_num: int
    first_index: int
    last_index: int
    new_text: str
    #: The paragraph's original text, used to detect a changed page.
    anchor: str = ""
    options: LayoutOptions = field(default_factory=LayoutOptions)
    #: User-approved substitute font (``font_discovery.FontFace``).
    substitute: Optional[FontSubstitution] = None
    #: Page-space line width override chosen in the dialog.
    region_width: Optional[float] = None

    scope = Scope.CONTENT
    pages = None
    requires_assembly = False

    @property
    def label(self) -> str:
        first = (self.anchor or self.new_text).split("\n", 1)[0][:24]
        return f"Reflow paragraph: '{first}'"

    def page_order(self, before_count: int):
        return None

    def apply(self, doc: fitz.Document) -> None:
        page = doc[self.page_num]
        counts: dict = {}
        runs = iter_page_runs(page, counts)
        para = extract_range(page, self.first_index, self.last_index, runs)

        if self.anchor and _norm(para.text) != _norm(self.anchor):
            raise EditError(
                "The page content changed since the paragraph was selected, "
                "so the reflow was not applied. Select the paragraph again.")

        for piece in para.run_pieces:
            if not piece.source.in_xobject:
                continue
            if piece.source.depth > 1:
                raise ParagraphUnsupported(
                    "The paragraph is drawn inside a Form XObject nested in "
                    "another Form XObject. Editing it is not supported "
                    "because the enclosing object cannot be isolated safely.")
            if counts.get(piece.source.xref, 1) > 1:
                raise ParagraphUnsupported(
                    "The paragraph is drawn by a Form XObject that is used "
                    "more than once on this page. Editing it would change "
                    "every copy, so the edit was refused.")

        # Every line must be drawn under the same transform; otherwise one
        # rebuilt block cannot reproduce all of them.
        transforms = {tuple(piece.ctm) for piece in para.run_pieces
                      if piece.ctm is not None}
        if len(transforms) > 1:
            raise ParagraphUnsupported(
                "The paragraph's lines are drawn under different transforms, "
                "so they cannot be rebuilt as one block. The edit was refused.")

        face = self.substitute.face if self.substitute is not None else None
        plan = plan_reflow(
            page, para, self.new_text, self.options, face=face,
            region_width=self.region_width)

        if plan.needs_substitution and face is None:
            raise FontSubstitutionRequired(
                plan.substitution_reason
                or "The original font cannot draw the new paragraph text.",
                _font_issue(page, para, plan))
        if not plan.feasible:
            message = " ".join(plan.problems) or (
                "The reflowed paragraph does not fit.")
            raise ParagraphOverflow(message, plan)

        pieces = para.run_pieces
        if not pieces:
            raise ParagraphUnsupported(
                "The paragraph could not be matched to the content stream.")
        source = pieces[0].source

        # Copy-on-write before anything is written: a shared XObject must
        # never be modified in place.
        if source.in_xobject:
            target = copy_on_write_xobject(page, _loc_for(pieces[0]))
            data = doc.xref_stream(target)
        else:
            target = source.xref
            data = doc.xref_stream(target)
        if not data:
            raise EditError("The paragraph's content stream is empty.")

        ops = ContentStreamParser.parse(data)
        keys = sorted({piece.op_index for piece in pieces})
        if keys[-1] >= len(ops):
            raise EditError(
                "The paragraph's text operators could not be found in the "
                "content stream. The document may have changed.")

        lo, hi, first_bt, q_count = _block_bounds(ops, keys)
        _validate_block(ops, lo, hi, keys)

        if face is not None:
            from .font_embed import FontEmbedError, attach_font, build_font

            try:
                embedded = build_font(doc, face, self.new_text)
            except FontEmbedError as exc:
                raise EditError(str(exc)) from exc
            owner = (target if source.res_owner == source.xref
                     else page.xref)
            # attach_font chooses the resource name, so read it afterwards.
            resource = attach_font(doc, owner, embedded)
            encode = embedded.encode
        else:
            codec = page_font_codecs(page, para.res_owner)(para.font_resource)
            if codec is None:
                raise ParagraphUnsupported(
                    "The paragraph's font encoding cannot be verified, so the "
                    "new text cannot be encoded safely.")
            resource = para.font_resource
            encode = codec.encode

        try:
            core = build_block(para, plan, encode, resource)
        except (ValueError, UnicodeEncodeError) as exc:
            raise EditError(
                f"The new text cannot be encoded with the paragraph's font: "
                f"{exc}.") from exc

        # Re-emit the wrapper that established the paragraph's transform
        # (``q cm`` for rotated text) and close it, plus one extra pair of our
        # own so the text state cannot leak into the rest of the page.
        prefix = data[ops[lo].start_pos:ops[first_bt].start_pos]
        block = (b"q\n" + prefix + core + b"\n"
                 + b"Q\n" * (q_count + 1))
        new_stream = (data[:ops[lo].start_pos] + block
                      + data[ops[hi].end_pos:])
        doc.update_stream(target, new_stream)
        _verify_written_text(page, plan)
