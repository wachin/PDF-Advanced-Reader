"""Annotation system for PDFAR.

The document holds the annotations (F10-3): a highlight is a PDF ``/Highlight``
annotation, and every change to it is a :class:`pdfar.edits.Edit`.  That is what
makes a highlight undoable, what makes it mark the document dirty, and what makes
a background reader see it without a save in between.  ``AnnotationManager`` is a
*view* of the document: it re-reads the list after a change rather than keeping a
pending one that Save would have to push in.

An annotation's id is its ``/NM`` name, so a sidebar row, a model key and a PDF
object all mean the same thing across an undo, a save and a reopen.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QObject, pyqtSignal

from .edits import Edit, EditError, Scope
from .save import SaveError, save_document
from .session import DocumentSession

#: How solid a highlight paints.  Stored as the annotation's ``/CA``, so a model
#: read back out of a file reports what was written into it.
HIGHLIGHT_OPACITY = 0.4

#: What a damaged annotation can raise while the list is being read.  MuPDF
#: signals its failures through these; asking for less than ``Exception`` means a
#: mistake in :func:`_highlight_model` still surfaces instead of quietly
#: emptying the sidebar.
ANNOT_READ_ERRORS = (RuntimeError, fitz.mupdf.FzErrorBase)


class AnnotationType(Enum):
    HIGHLIGHT = "highlight"
    NOTE = "note"
    FREEHAND = "freehand"
    SHAPE = "shape"
    STAMP = "stamp"


@dataclass
class Annotation:
    """Base annotation class."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: AnnotationType = AnnotationType.HIGHLIGHT
    page: int = 0                          # 0-based page index
    rects: List[tuple] = field(default_factory=list)  # List of (x0, y0, x1, y1) in PDF points
    color: str = "#FFFF00"                 # Hex color string
    opacity: float = HIGHLIGHT_OPACITY     # 0.0 - 1.0
    author: str = ""
    created: str = field(default_factory=lambda: datetime.now().isoformat())
    modified: str = field(default_factory=lambda: datetime.now().isoformat())
    content: str = ""                      # For notes: the text content
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "page": self.page,
            "rects": self.rects,
            "color": self.color,
            "opacity": self.opacity,
            "author": self.author,
            "created": self.created,
            "modified": self.modified,
            "content": self.content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Annotation":
        ann = cls(
            id=data.get("id", str(uuid.uuid4())),
            type=AnnotationType(data.get("type", "highlight")),
            page=data.get("page", 0),
            rects=data.get("rects", []),
            color=data.get("color", "#FFFF00"),
            opacity=data.get("opacity", HIGHLIGHT_OPACITY),
            author=data.get("author", ""),
            created=data.get("created", datetime.now().isoformat()),
            modified=data.get("modified", datetime.now().isoformat()),
            content=data.get("content", ""),
            metadata=data.get("metadata", {}),
        )
        return ann


@dataclass
class HighlightAnnotation(Annotation):
    """Text highlight annotation.

    A note on a highlight is the highlight's own ``/Contents``, so it needs no
    second annotation and travels with the highlight it belongs to.
    """
    type: AnnotationType = AnnotationType.HIGHLIGHT

    def has_note(self) -> bool:
        """Check if this highlight has an associated note."""
        return bool(self.content.strip())

    def get_note(self) -> str:
        """Get the note text for this highlight."""
        return self.content


@dataclass
class NoteAnnotation(Annotation):
    """Standalone note annotation (not associated with a highlight).

    Anchored to a specific position on a page.
    """
    type: AnnotationType = AnnotationType.NOTE
    # position: (x, y) in PDF points where the note icon appears
    position: tuple = (0.0, 0.0)


# -------------------------------------------------------------------------- color
def _hex_to_rgb(text: str) -> Tuple[float, float, float]:
    """``#RRGGBB`` as the three floats a PDF color is."""
    digits = (text or "").lstrip("#")
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    if len(digits) != 6:
        raise EditError(f"“{text}” is not a color")
    try:
        red, green, blue = (int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        raise EditError(f"“{text}” is not a color") from None
    return (red, green, blue)


def _rgb_to_hex(color: Sequence[float]) -> str:
    """A PDF color as upper-case hex, which is how :data:`HIGHLIGHT_COLORS` writes
    them — so a highlight read back out of a saved file still matches its color
    name in the sidebar instead of being listed as "Custom"."""
    channels = [round(min(1.0, max(0.0, c)) * 255) for c in tuple(color)[:3]]
    return "#{:02X}{:02X}{:02X}".format(*channels)


# ----------------------------------------------------------------------- identity
def annot_key(annot: fitz.Annot) -> str:
    """The model id of a document annotation: its ``/NM`` name.

    PyMuPDF reports ``/NM`` as ``info["id"]`` and gives every annotation it makes
    a unique one, which is what lets an id survive a save and a clone.  An
    annotation from another program may have none, and naming it on open would
    turn reading the file into changing it, so those are keyed by object number.
    """
    return annot.info.get("id") or f"anon:{annot.xref}"


def _name(doc: fitz.Document, annot: fitz.Annot, ann_id: str) -> None:
    """Stamp an annotation with the id the interface already holds.

    PDFAR's ids are UUIDs, whose characters are exactly the ones a PDF literal
    string needs, so nothing in them has to be escaped.
    """
    doc.xref_set_key(annot.xref, "NM", f"({ann_id})")


def _find_highlight(page: fitz.Page, ann_id: str) -> fitz.Annot:
    """The annotation *ann_id* names on *page*, or an edit that refuses.

    Matching by name rather than by position matters: an undo can leave the page
    with no such annotation, and changing whatever annotation happened to sit at
    that object number instead would damage the document.
    """
    for annot in page.annots():
        if annot_key(annot) == ann_id:
            return annot
    raise EditError("That annotation is not in this document any more.")


def _quad_rects(annot: fitz.Annot) -> List[tuple]:
    """One rectangle per highlight quad — the shape the model stores.

    The quads are the only faithful record of what was selected, and writing them
    back as one multi-quad annotation reproduces the annotation unchanged, so a
    highlight survives a reload without drifting.
    """
    vertices = annot.vertices or []
    rects = []
    for i in range(0, len(vertices) - 3, 4):
        quad = vertices[i:i + 4]
        # Indexed, not `.x`: PyMuPDF hands these back as plain (x, y) tuples on
        # some releases and as Point objects on others.
        xs = [corner[0] for corner in quad]
        ys = [corner[1] for corner in quad]
        rects.append((min(xs), min(ys), max(xs), max(ys)))
    if rects:
        return rects
    rect = annot.rect        # a foreign highlight drawn without quad points
    return [(rect.x0, rect.y0, rect.x1, rect.y1)]


def _highlight_model(annot: fitz.Annot, page_idx: int) -> HighlightAnnotation:
    """Read one document annotation into the model."""
    info = annot.info
    opacity = annot.opacity
    return HighlightAnnotation(
        id=annot_key(annot),
        page=page_idx,
        rects=_quad_rects(annot),
        color=_rgb_to_hex(annot.colors.get("stroke") or (1.0, 1.0, 0.0)),
        opacity=HIGHLIGHT_OPACITY if opacity is None else opacity,
        author=info.get("title") or "",
        created=info.get("creationDate") or "",
        modified=info.get("modDate") or "",
        content=info.get("content") or "",
    )


# -------------------------------------------------------------------------- edits
@dataclass(frozen=True)
class HighlightSpec:
    """One page's share of a highlight: the id it will have, and its rectangles."""

    ann_id: str
    page: int
    rects: Tuple[tuple, ...]


@dataclass(frozen=True)
class AddHighlight(Edit):
    """Highlight the selected rectangles.

    One edit carries every page the selection touched, because the user selected
    once — an edit per page would make Ctrl+Z undo the answer a page at a time.
    """

    specs: Tuple[HighlightSpec, ...]
    color: str = "#FFFF00"
    opacity: float = HIGHLIGHT_OPACITY
    author: str = ""
    note: str = ""

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Highlight"

    def apply(self, doc: fitz.Document) -> None:
        if not self.specs:
            raise EditError("There is nothing to highlight")
        stroke = _hex_to_rgb(self.color)
        for spec in self.specs:
            page = doc.load_page(spec.page)
            # One annotation holding every line of the selection: quads are how
            # a PDF records a selection that broke across lines.
            annot = page.add_highlight_annot([fitz.Rect(*rect) for rect in spec.rects])
            annot.set_colors(stroke=stroke)
            annot.set_opacity(self.opacity)
            if self.author:
                annot.set_info(title=self.author)
            if self.note:
                annot.set_info(content=self.note)
            annot.update()
            _name(doc, annot, spec.ann_id)


@dataclass(frozen=True)
class RemoveHighlight(Edit):
    """Delete the annotation *ann_id* names."""

    ann_id: str
    page: int

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Delete annotation"

    def apply(self, doc: fitz.Document) -> None:
        page = doc.load_page(self.page)
        page.delete_annot(_find_highlight(page, self.ann_id))


@dataclass(frozen=True)
class SetHighlightColor(Edit):
    """Recolor an existing highlight."""

    ann_id: str
    page: int
    color: str
    opacity: float = HIGHLIGHT_OPACITY

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Change highlight color"

    def apply(self, doc: fitz.Document) -> None:
        page = doc.load_page(self.page)
        annot = _find_highlight(page, self.ann_id)
        annot.set_colors(stroke=_hex_to_rgb(self.color))
        annot.set_opacity(self.opacity)
        # The color is baked into the appearance stream, so without a new
        # appearance the page keeps painting the color it was drawn with.
        annot.update()


@dataclass(frozen=True)
class SetHighlightNote(Edit):
    """Set or clear the note on a highlight."""

    ann_id: str
    page: int
    note: str
    #: Whether the highlight already had a note when the user asked.  Only the
    #: manager knows that, and it decides which of these three steps is called.
    had_note: bool = False

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        if not self.note.strip():
            return "Remove note"
        return "Edit note" if self.had_note else "Add note"

    def apply(self, doc: fitz.Document) -> None:
        # ``page`` stays named and in scope: PyMuPDF unbinds an ``Annot`` the
        # moment its ``Page`` is garbage, and writing to a handle in that state
        # raises "annotation not bound to any page".
        page = doc.load_page(self.page)
        annot = _find_highlight(page, self.ann_id)
        if self.note:
            annot.set_info(content=self.note)
            return
        # ``set_info`` reads an empty string as "nothing to change", so clearing a
        # note means removing the key from the annotation object itself.
        doc.xref_set_key(annot.xref, "Contents", "null")


class AnnotationManager(QObject):
    """The annotations the document holds, as a list the interface can show.

    A change is an :class:`pdfar.edits.Edit` applied through the session, so it
    undoes, marks the document dirty and reaches the render workers as a new
    content version.  The list is re-read from the document afterwards: it is a
    cache of what the file would say, never a change waiting for Save.
    """

    annotations_changed = pyqtSignal()
    #: An edit was accepted; carries the ``Scope`` the view has to invalidate.
    annotations_edited = pyqtSignal(int)
    #: An edit the user asked for did not happen, and why.
    annotation_edit_failed = pyqtSignal(str)

    def __init__(self, session: DocumentSession, parent=None):
        super().__init__(parent)
        self.session = session
        self._annotations: Dict[str, Annotation] = {}
        # -1 so that the first read happens even for a session at generation 0.
        self._generation = -1
        self.reload_from_document()

    @property
    def doc(self) -> fitz.Document:
        """The live document; an accepted edit replaces the object."""
        return self.session.doc

    # ---------------------------------------------------------------- the model
    def reload_from_document(self) -> None:
        """Re-read the list when it no longer describes the document we hold.

        An adopted edit, an undo or a revert hands back a different
        ``fitz.Document``, which is what ``session.generation`` counts.  Re-read
        then, so the page and the id a row carries come from the document the user
        is looking at rather than from the one that was edited.
        """
        if self._generation == self.session.generation:
            return
        found: Dict[str, Annotation] = {}
        doc = self.doc
        for page_idx in range(doc.page_count):
            page = doc.load_page(page_idx)
            try:
                for annot in page.annots():
                    if annot.type[0] != fitz.PDF_ANNOT_HIGHLIGHT:
                        continue
                    model = _highlight_model(annot, page_idx)
                    found[model.id] = model
            except ANNOT_READ_ERRORS:
                # Annotations are a linked chain, so one that the file cannot
                # read ends this page's list.  What was read before it stays, and
                # the rest of the document is unaffected.  The guard covers the
                # data errors only, so a mistake in here still surfaces instead
                # of quietly emptying the sidebar.
                continue
        self._annotations = found
        self._generation = self.session.generation
        self.annotations_changed.emit()

    def save_to_pdf(self) -> bool:
        """Write the document, which already holds every annotation.

        The write itself is F10-2's: staged, verified, incremental while the
        document is still the file handle it came from.  A refusal is reported
        as ``False`` here; the main window's Save command surfaces the reason.
        """
        try:
            return save_document(self.session) is not None
        except SaveError:
            return False

    # --------------------------------------------------------------- mutators
    def _run(self, edit: Edit) -> bool:
        """Carry out one annotation edit: document first, then list, then view.

        The session applies to a copy and adopts it only on success, so a refused
        edit leaves the page, the version and the undo stack exactly as they were.
        """
        try:
            _version, scope = self.session.apply(edit)
        except EditError as exc:
            self.annotation_edit_failed.emit(str(exc))
            return False
        self.reload_from_document()
        self.annotations_edited.emit(int(scope))
        return True

    def add_highlights(self, by_page: Dict[int, Sequence[tuple]],
                       color: str = "#FFFF00", author: str = "",
                       note: str = "") -> List[HighlightAnnotation]:
        """Highlight a selection that may cover several pages, as one edit.

        Pages with nothing on them are dropped, so an empty selection cannot
        become an undo step that appears to do nothing.
        """
        specs = tuple(
            HighlightSpec(ann_id=str(uuid.uuid4()), page=int(page),
                          rects=tuple(rects))
            for page, rects in by_page.items() if rects)
        if not specs:
            return []
        if not self._run(AddHighlight(specs=specs, color=color, author=author,
                                      note=note)):
            return []
        return [h for h in (self.get_highlight(spec.ann_id) for spec in specs)
                if h is not None]

    def add_highlight(self, page: int, rects: Sequence[tuple],
                      color: str = "#FFFF00", author: str = "",
                      note: str = "") -> Optional[HighlightAnnotation]:
        """Highlight one page's rectangles.  ``None`` if the edit was refused."""
        made = self.add_highlights({page: rects}, color, author, note)
        return made[0] if made else None

    def remove_annotation(self, annotation_id: str) -> bool:
        """Remove an annotation by ID."""
        ann = self._annotations.get(annotation_id)
        if ann is None:
            return False
        return self._run(RemoveHighlight(ann_id=annotation_id, page=ann.page))

    def set_highlight_color(self, annotation_id: str, color: str) -> bool:
        """Recolor a highlight, keeping how solid it paints."""
        highlight = self.get_highlight(annotation_id)
        if highlight is None:
            return False
        return self._run(SetHighlightColor(ann_id=annotation_id,
                                           page=highlight.page, color=color,
                                           opacity=highlight.opacity))

    def add_note_to_highlight(self, highlight_id: str,
                              note_text: str) -> Optional[HighlightAnnotation]:
        """Add or update a note on an existing highlight."""
        if not self._set_note(highlight_id, note_text):
            return None
        return self.get_highlight(highlight_id)

    def remove_note_from_highlight(self, highlight_id: str) -> bool:
        """Remove the note from a highlight (keeps the highlight)."""
        return self._set_note(highlight_id, "")

    def _set_note(self, highlight_id: str, note: str) -> bool:
        highlight = self.get_highlight(highlight_id)
        if highlight is None:
            return False
        return self._run(SetHighlightNote(ann_id=highlight_id, page=highlight.page,
                                          note=note, had_note=highlight.has_note()))

    # ------------------------------------------------------------------- reads
    def get_annotation(self, annotation_id: str) -> Optional[Annotation]:
        """One annotation by id, or ``None`` if there is no such annotation."""
        return self._annotations.get(annotation_id)

    def get_highlight(self, annotation_id: str) -> Optional[HighlightAnnotation]:
        """One highlight by id; ``None`` for any other kind of annotation."""
        ann = self._annotations.get(annotation_id)
        return ann if isinstance(ann, HighlightAnnotation) else None

    def get_annotations(self, page: Optional[int] = None,
                        ann_type: Optional[AnnotationType] = None) -> List[Annotation]:
        """Get all annotations, optionally filtered by page and type."""
        result = []
        for ann in self._annotations.values():
            if page is not None and ann.page != page:
                continue
            if ann_type is not None and ann.type != ann_type:
                continue
            result.append(ann)
        return result

    def get_annotations_for_page(self, page: int) -> List[Annotation]:
        """Get all annotations for a specific page."""
        return self.get_annotations(page=page)

    def get_highlights_for_page(self, page: int) -> List[HighlightAnnotation]:
        """Get all highlights for a specific page."""
        return [a for a in self.get_annotations(page=page, ann_type=AnnotationType.HIGHLIGHT)
                if isinstance(a, HighlightAnnotation)]

    def get_notes_for_page(self, page: int) -> List[NoteAnnotation]:
        """Get all standalone notes for a specific page."""
        return [a for a in self.get_annotations(page=page, ann_type=AnnotationType.NOTE)
                if isinstance(a, NoteAnnotation)]

    def get_note_for_highlight(self, highlight_id: str) -> Optional[str]:
        """Get the note text for a highlight."""
        highlight = self.get_highlight(highlight_id)
        return highlight.get_note() if highlight is not None else None


# Standard highlight colors (Okular-style)
HIGHLIGHT_COLORS = [
    ("Yellow", "#FFFF00"),
    ("Green", "#90EE90"),
    ("Blue", "#87CEEB"),
    ("Pink", "#FFB6C1"),
    ("Orange", "#FFD580"),
    ("Purple", "#DDA0DD"),
    ("Red", "#FF6B6B"),
    ("Cyan", "#80FFFF"),
]
