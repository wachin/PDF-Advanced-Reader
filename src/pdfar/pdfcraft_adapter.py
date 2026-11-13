"""Read-only paragraph analysis backed by the ported PdfCraft engine.

Phase 6B exposes :mod:`pdfar.pdfcraft` to the application through this module
only.  It **never writes to a document**: it reads a page, returns immutable
lines and paragraph candidates, and reports why a paragraph cannot be rebuilt.
The existing editor (``pdfar.paragraph`` / ``pdfar.paragraph_edit``) is
untouched and remains the implementation the UI uses.

Why an adapter
--------------
The engine works on ``PageModel`` objects that carry byte spans into content
streams.  Those are implementation details: a caller that held them would break
whenever the engine changes.  :class:`PageAnalysis` is a frozen snapshot of
what the engine understood, with provenance reduced to the parts a writer will
need (stream index, operator index, byte range).

DocumentSession and Page invalidation
-------------------------------------
Analysis is read-only, but PyMuPDF is not: ``Page`` objects and cached text
become stale when the document they came from is mutated.  Two rules follow:

* :func:`analyze_session_page` reads a *private copy* from
  :meth:`DocumentSession.open_copy` and closes it before returning.  It never
  touches ``session.doc``, so it cannot invalidate the UI's page handles.
* A result is stamped with the session ``version`` it was read from.
  :meth:`PageAnalysis.is_current` compares that stamp; any accepted edit bumps
  the version, so a result from before an edit is refused rather than used
  against different content.

``DocumentSession.open_copy`` may publish a snapshot, which must happen on the
thread that owns the document.  Call :func:`analyze_session_page` on that
thread, or have the owner call ``session.read_version()`` first and run
:func:`analyze_page` on an ``open_copy`` in the worker.

What the analysis does not claim
--------------------------------
A paragraph is :attr:`AnalyzedParagraph.editable` only when every row is drawn
by page-level content with one font resource, one size, one colour and one
style.  Form XObjects, vertical writing (``/WMode 1``), character codes with no
Unicode mapping and styled runs are reported in ``problems`` rather than
approximated, because the port cannot rebuild them faithfully yet.  Text drawn
with a font the engine cannot read at all produces no line; the count of those
runs is reported in ``PageAnalysis.diagnostics`` so the omission is visible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

try:  # PyMuPDF >= 1.24
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from .pdfcraft import FontCache, PageModel, read_page, strip_subset_tag

Rect = Tuple[float, float, float, float]
Point = Tuple[float, float]


class AnalysisError(Exception):
    """The page could not be read; no partial result is returned."""


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OperatorSpan:
    """Where one text-showing operator lives in the page's content.

    ``start`` and ``end`` are byte offsets **within** ``stream``, which is the
    index of that stream in ``page.get_contents()``.  ``op_index`` is the
    operator's position in the page-wide list, for diagnostics and stable
    ordering.
    """

    stream: int
    op_index: int
    start: int
    end: int
    keyword: str


@dataclass(frozen=True)
class AnalyzedLine:
    """One visual row: the unit a paragraph edit replaces.

    ``rect`` is the glyph box in page space.  ``frame`` is the same row measured
    in its own writing frame — x along the baseline, y across it — which is what
    a rotated row must be laid out in.  ``baseline`` is the row's pen position in
    that frame; for upright text the frame *is* page space.
    """

    index: int
    text: str
    rect: Rect
    baseline: Point
    frame: Rect
    font_resource: str
    base_font: str
    size: float
    rotation: float
    operators: Tuple[OperatorSpan, ...]
    from_form: bool = False
    decodable: bool = True
    vertical: bool = False


@dataclass(frozen=True)
class ParagraphKey:
    """A paragraph's text and box, the identity PdfCraft's ``BlockKey`` uses."""

    text: str
    rect: Rect

    def matches(self, paragraph: "AnalyzedParagraph", tolerance: float = 0.01) -> bool:
        return self.text == paragraph.text and all(
            abs(a - b) <= tolerance for a, b in zip(self.rect, paragraph.rect)
        )


@dataclass(frozen=True)
class AnalyzedParagraph:
    """A paragraph candidate, with provenance and refusal reasons."""

    index: int
    key: ParagraphKey
    text: str
    rect: Rect
    rotation: float
    font_resource: str
    base_font: str
    size: float
    lines: Tuple[AnalyzedLine, ...]
    operators: Tuple[OperatorSpan, ...]
    problems: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()

    @property
    def editable(self) -> bool:
        """Whether the engine can rebuild this paragraph without losing content."""
        return not self.problems

    @property
    def row_count(self) -> int:
        return len(self.lines)

    @property
    def streams(self) -> Tuple[int, ...]:
        return tuple(sorted({span.stream for span in self.operators}))


@dataclass(frozen=True)
class PageAnalysis:
    """Everything the engine understood about one page, at one revision."""

    page_no: int
    version: int
    page_rect: Rect
    paragraphs: Tuple[AnalyzedParagraph, ...]
    lines: Tuple[AnalyzedLine, ...]
    diagnostics: Tuple[str, ...] = ()
    fingerprint: str = ""

    def is_current(self, session) -> bool:
        """Whether *session* still holds the content this analysis read."""
        return getattr(session, "version", None) == self.version

    def find(self, key: ParagraphKey, hint: int = 0) -> Optional[AnalyzedParagraph]:
        """The paragraph with *key*, trying *hint* first (PdfCraft's ``BlockKey.find``)."""
        if 0 <= hint < len(self.paragraphs) and key.matches(self.paragraphs[hint]):
            return self.paragraphs[hint]
        for paragraph in self.paragraphs:
            if key.matches(paragraph):
                return paragraph
        return None

    def paragraph_at(self, x: float, y: float) -> Optional[AnalyzedParagraph]:
        """The smallest paragraph whose box contains the point."""
        hits = [
            paragraph
            for paragraph in self.paragraphs
            if paragraph.rect[0] <= x <= paragraph.rect[2]
            and paragraph.rect[1] <= y <= paragraph.rect[3]
        ]
        if not hits:
            return None
        return min(hits, key=lambda paragraph: _area(paragraph.rect))

    def paragraph_overlapping(self, rect: Rect) -> Optional[AnalyzedParagraph]:
        """The paragraph overlapping *rect* the most (PdfCraft's ``block_overlapping``)."""
        best = None
        best_area = 0.0
        for paragraph in self.paragraphs:
            overlap = _intersection_area(rect, paragraph.rect)
            if overlap > best_area:
                best = paragraph
                best_area = overlap
        return best


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _area(rect: Rect) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersection_area(a: Rect, b: Rect) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return width * height if width > 0 and height > 0 else 0.0


def _union(rects: Iterable[Rect]) -> Rect:
    rects = list(rects)
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def _spans(pm: PageModel, op_indices: Sequence[int]) -> Tuple[OperatorSpan, ...]:
    out: List[OperatorSpan] = []
    for index in op_indices:
        op = pm.ops[index]
        stream = pm.pieces.piece_of(op.start)
        base = pm.pieces.bounds[stream][0]
        out.append(
            OperatorSpan(
                stream=stream,
                op_index=index,
                start=op.start - base,
                end=op.end - base,
                keyword=op.op,
            )
        )
    return tuple(out)


def _unique_spans(spans: Iterable[OperatorSpan]) -> Tuple[OperatorSpan, ...]:
    seen = set()
    out: List[OperatorSpan] = []
    for span in spans:
        key = (span.stream, span.op_index)
        if key in seen:
            continue
        seen.add(key)
        out.append(span)
    return tuple(out)


def _row_text(pm: PageModel, block, row_index: int) -> str:
    rows = block.editor_text.split("\n")
    if row_index < len(rows):
        return rows[row_index]
    row = block.rows[row_index]
    return " ".join(pm.lines[index].text for index in row)


def _analyzed_line(pm: PageModel, block, row_index: int, page_index: int) -> AnalyzedLine:
    row = block.rows[row_index]
    first = pm.lines[row[0]]
    # Form text is drawn from the form's own stream, so its operator offsets are
    # not offsets into the page's content.  It is reported (``from_form``) but
    # carries no provenance a writer could use.
    op_indices: List[int] = []
    for index in row:
        line = pm.lines[index]
        if line.stream == 0:
            op_indices.extend(line.ops)
    return AnalyzedLine(
        index=page_index,
        text=_row_text(pm, block, row_index),
        rect=_union(pm.lines[index].rect for index in row),
        baseline=(first.origin.x, first.origin.baseline),
        frame=_union(pm.lines[index].frame for index in row),
        font_resource=first.font_name,
        base_font=first.base_font,
        size=first.size,
        rotation=first.rotation,
        operators=_spans(pm, op_indices),
        from_form=any(pm.lines[index].stream != 0 for index in row),
        decodable=all(pm.lines[index].decodable for index in row),
        vertical=any(pm.lines[index].vertical for index in row),
    )


def _paragraph_issues(pm: PageModel, block) -> Tuple[List[str], List[str]]:
    """``(problems, notes)`` for one paragraph, in the engine's terms."""
    problems: List[str] = []
    notes: List[str] = []
    lines = [pm.lines[index] for index in block.lines]
    page_lines = [line for line in lines if line.stream == 0]

    if len(page_lines) < len(lines):
        problems.append(
            "This paragraph is drawn by a Form XObject. The engine reads it "
            "but cannot rewrite it yet.")
    if any(not line.decodable for line in lines):
        problems.append(
            "Some of this paragraph's character codes have no Unicode mapping, "
            "so its text is incomplete; a rewrite could lose those glyphs.")
    if any(line.vertical for line in lines):
        problems.append(
            "This paragraph uses vertical writing (a WMode 1 font), which the "
            "engine does not lay out yet.")

    font_names = {line.font_name for line in lines}
    base_fonts = {strip_subset_tag(line.base_font) for line in lines}
    sizes = sorted({round(line.size, 2) for line in lines})
    colours = {tuple(round(channel, 4) for channel in line.color) for line in lines}
    styles = {(line.bold, line.italic) for line in lines}
    modes = {line.render_mode for line in lines}

    if len(font_names) > 1:
        problems.append(
            f"This paragraph mixes {len(font_names)} font resources "
            f"({', '.join(sorted(font_names))}); rebuilding it with one font "
            "would lose the others.")
    elif len(base_fonts) > 1:
        problems.append(
            "This paragraph mixes fonts (" + ", ".join(sorted(base_fonts))
            + "); rebuilding it with one font would lose the others.")
    if len(sizes) > 1:
        problems.append(
            "This paragraph mixes font sizes ("
            + ", ".join(f"{size:g} pt" for size in sizes)
            + "); rebuilding it would change their appearance.")
    if len(colours) > 1:
        problems.append(
            "This paragraph uses more than one text colour, so it cannot be "
            "rebuilt as one block.")
    if len(styles) > 1:
        problems.append(
            "This paragraph mixes bold/italic runs, so it cannot be rebuilt "
            "with one font style.")
    if any(mode != 0 for mode in modes):
        problems.append(
            "This paragraph uses a non-filling text render mode (invisible or "
            "stroked text), so it cannot be reflowed safely.")

    multi_run_rows = 0
    styled_rows = 0
    for row in block.rows:
        if len(row) < 2:
            continue
        multi_run_rows += 1
        row_styles = {
            (pm.lines[index].font_name, pm.lines[index].base_font,
             round(pm.lines[index].size, 2), pm.lines[index].bold,
             pm.lines[index].italic, pm.lines[index].color)
            for index in row
        }
        if len(row_styles) > 1:
            styled_rows += 1
    if styled_rows:
        problems.append(
            f"{styled_rows} row(s) of this paragraph are drawn by several "
            "style runs. Styled-run rewriting is not ported yet.")
    elif multi_run_rows:
        notes.append(
            f"{multi_run_rows} row(s) are drawn by several runs of the same "
            "style; a reflow may normalize their intra-row spacing.")

    streams = {
        pm.pieces.piece_of(pm.ops[line.ops[0]].start)
        for line in page_lines
        if line.ops
    }
    if len(streams) > 1:
        notes.append(
            f"This paragraph spans {len(streams)} content streams; each is "
            "rewritten independently.")
    return problems, notes


def _fingerprint(pm: PageModel, page_xref: int) -> str:
    digest = hashlib.sha1(pm.pieces.data).hexdigest()[:16]
    return f"{page_xref}:{digest}"


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def analyze_page(
    doc: fitz.Document,
    page_no: int,
    *,
    version: int = 0,
    fonts: Optional[FontCache] = None,
) -> PageAnalysis:
    """Read one page with the ported engine.  *doc* is not modified.

    *version* is stamped on the result; pass the ``DocumentSession`` version the
    handle came from.  *fonts* lets a caller share metrics across pages.
    """
    if not 0 <= page_no < doc.page_count:
        raise AnalysisError(
            f"Page {page_no + 1} is outside the document ({doc.page_count} pages).")
    try:
        model = read_page(doc, page_no, fonts=fonts)
    except Exception as exc:  # noqa: BLE001 - one clear failure for the caller
        raise AnalysisError(
            f"Page {page_no + 1} could not be read: {exc}") from exc

    page = doc[page_no]
    lines: List[AnalyzedLine] = []
    paragraphs: List[AnalyzedParagraph] = []
    for block_index, block in enumerate(model.blocks):
        row_lines: List[AnalyzedLine] = []
        for row_index in range(len(block.rows)):
            line = _analyzed_line(model, block, row_index, len(lines))
            lines.append(line)
            row_lines.append(line)
        problems, notes = _paragraph_issues(model, block)
        first = row_lines[0]
        paragraphs.append(
            AnalyzedParagraph(
                index=block_index,
                key=ParagraphKey(block.text, block.rect),
                text=block.text,
                rect=block.rect,
                rotation=block.rotation,
                font_resource=first.font_resource,
                base_font=first.base_font,
                size=first.size,
                lines=tuple(row_lines),
                operators=_unique_spans(
                    span for line in row_lines for span in line.operators
                ),
                problems=tuple(problems),
                notes=tuple(notes),
            )
        )

    diagnostics: List[str] = []
    if not paragraphs:
        diagnostics.append("No text was found on this page.")
    else:
        unsupported = sum(1 for paragraph in paragraphs if not paragraph.editable)
        diagnostics.append(
            f"{len(paragraphs)} paragraph(s), {len(paragraphs) - unsupported} "
            f"editable, {unsupported} unsupported.")
    forms = sum(1 for line in lines if line.from_form)
    if forms:
        diagnostics.append(
            f"{forms} line(s) are drawn by Form XObjects and are read-only.")
    if model.unread_shows:
        diagnostics.append(
            f"{model.unread_shows} text run(s) on this page could not be read "
            "(their font or encoding is unavailable), so their text is missing "
            "from this analysis.")

    return PageAnalysis(
        page_no=page_no,
        version=version,
        page_rect=tuple(page.rect),
        paragraphs=tuple(paragraphs),
        lines=tuple(lines),
        diagnostics=tuple(diagnostics),
        fingerprint=_fingerprint(model, page.xref),
    )


def analyze_session_page(session, page_no: int) -> PageAnalysis:
    """Analyse *page_no* from the session's current revision.

    The session's own document is never touched: a private copy is opened and
    closed.  Call on the thread that owns the session (opening the copy may
    publish a snapshot); for a worker, call ``session.read_version()`` on the
    owner thread and use :func:`analyze_page` on an ``open_copy`` instead.
    """
    version = session.read_version()
    doc = session.open_copy(version)
    try:
        return analyze_page(doc, page_no, version=version)
    finally:
        doc.close()
