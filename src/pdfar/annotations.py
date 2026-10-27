"""Annotation system for PDFAR.

Handles highlights, notes, and other annotations with PDF persistence.
Designed to work with the existing geometry layer for correct positioning.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QObject, pyqtSignal

from .save import SaveError, save_document
from .session import DocumentSession


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
    opacity: float = 0.4                   # 0.0 - 1.0
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
            opacity=data.get("opacity", 0.4),
            author=data.get("author", ""),
            created=data.get("created", datetime.now().isoformat()),
            modified=data.get("modified", datetime.now().isoformat()),
            content=data.get("content", ""),
            metadata=data.get("metadata", {}),
        )
        return ann


@dataclass
class HighlightAnnotation(Annotation):
    """Text highlight annotation."""
    type: AnnotationType = AnnotationType.HIGHLIGHT
    # Note: notes are stored in the `content` field of the highlight
    # For backward compatibility and simplicity, we use the same object

    @classmethod
    def from_selection(cls, page: int, rects: List[tuple], color: str = "#FFFF00",
                       author: str = "") -> "HighlightAnnotation":
        """Create a highlight from a text selection."""
        return cls(
            type=AnnotationType.HIGHLIGHT,
            page=page,
            rects=rects,
            color=color,
            author=author,
        )

    def has_note(self) -> bool:
        """Check if this highlight has an associated note."""
        return bool(self.content.strip())

    def set_note(self, text: str) -> None:
        """Set the note text for this highlight."""
        self.content = text
        self.modified = datetime.now().isoformat()

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


class AnnotationManager(QObject):
    """Manages annotations for a single document.

    Handles both PDF-embedded annotations and external annotations.
    Emits signals for UI updates.
    """

    annotations_changed = pyqtSignal()
    annotation_added = pyqtSignal(object)    # Annotation
    annotation_removed = pyqtSignal(str)     # annotation id
    annotation_modified = pyqtSignal(object) # Annotation

    def __init__(self, session: DocumentSession, parent=None):
        super().__init__(parent)
        self.session = session
        self._annotations: Dict[str, Annotation] = {}
        # Which document the model was last materialized into, and whether the
        # model has moved since.  Both are needed: without the generation, an
        # undo that hands back a different document would silently save without
        # the highlights; without the flag, a second Ctrl+S would rewrite a
        # document whose highlights are already in it.
        self._materialized = session.generation
        self._pending = False
        self._load_from_pdf()

    def _touch_model(self) -> None:
        """The model no longer matches whatever document it was last written to."""
        self._pending = True
        self.annotations_changed.emit()

    @property
    def doc(self) -> fitz.Document:
        """The live document; an accepted edit replaces the object."""
        return self.session.doc

    @property
    def doc_path(self) -> str:
        return self.session.path

    def _load_from_pdf(self) -> None:
        """Load annotations from the PDF file."""
        try:
            for page_idx in range(self.doc.page_count):
                page = self.doc.load_page(page_idx)
                annots = page.annots() or []
                for annot in annots:
                    if annot.type[0] == fitz.PDF_ANNOT_HIGHLIGHT:
                        # Extract highlight info
                        color = annot.colors.get("stroke", (1, 1, 0))
                        hex_color = "#{:02x}{:02x}{:02x}".format(
                            int(color[0] * 255), int(color[1] * 255), int(color[2] * 255)
                        )
                        rect = annot.rect
                        quad_rects = []
                        vertices = getattr(annot, "vertices", None)
                        if vertices and len(vertices) >= 4 and len(vertices) % 4 == 0:
                            for i in range(0, len(vertices), 4):
                                pts = vertices[i:i+4]
                                # pts can be list of fitz.Point or list of (x, y) tuples
                                def get_x(p):
                                    return p.x if hasattr(p, "x") else p[0]
                                def get_y(p):
                                    return p.y if hasattr(p, "y") else p[1]
                                x0 = min(get_x(p) for p in pts)
                                y0 = min(get_y(p) for p in pts)
                                x1 = max(get_x(p) for p in pts)
                                y1 = max(get_y(p) for p in pts)
                                quad_rects.append((x0, y0, x1, y1))

                        highlight = HighlightAnnotation(
                            id=str(uuid.uuid4()),
                            page=page_idx,
                            rects=quad_rects if quad_rects else [(rect.x0, rect.y0, rect.x1, rect.y1)],
                            color=hex_color,
                            opacity=0.4,
                            author=annot.info.get("title", ""),
                            created=annot.info.get("creationDate", ""),
                            modified=annot.info.get("modDate", ""),
                            content=annot.info.get("content", ""),
                        )
                        self._annotations[highlight.id] = highlight
        except Exception:
            pass  # Silently ignore load errors

    def reload_from_document(self) -> None:
        """Rebuild the model from the live document, dropping what is not in it.

        Revert needs this: once the file is the truth again, a highlight that was
        never saved must not go on living in the sidebar list.
        """
        self._annotations.clear()
        self._load_from_pdf()
        self._pending = False
        self._materialized = self.session.generation
        self.annotations_changed.emit()

    def sync_to_document(self) -> bool:
        """Materialize the highlights into the live document.

        Returns whether the document changed, and bumps the session version when
        it did — the change is made in place, and a reader that is not turned
        away by a new version would paint the old content back over it.  A model
        that has not moved and already describes this document writes nothing, so
        saving twice costs one write.
        """
        if not self._pending and self._materialized == self.session.generation:
            return False
        changed = False
        for page_idx in range(self.doc.page_count):
            page = self.doc.load_page(page_idx)
            for annot in list(page.annots() or []):
                if annot.type[0] == fitz.PDF_ANNOT_HIGHLIGHT:
                    page.delete_annot(annot)
                    changed = True

        for ann in self._annotations.values():
            if ann.type == AnnotationType.HIGHLIGHT and ann.rects:
                page = self.doc.load_page(ann.page)
                for rect in ann.rects:
                    x0, y0, x1, y1 = rect
                    highlight = page.add_highlight_annot(fitz.Rect(x0, y0, x1, y1))
                    if highlight:
                        # Parse color
                        color_hex = ann.color.lstrip("#")
                        r = int(color_hex[0:2], 16) / 255.0
                        g = int(color_hex[2:4], 16) / 255.0
                        b = int(color_hex[4:6], 16) / 255.0
                        highlight.set_colors(stroke=(r, g, b))
                        highlight.set_opacity(ann.opacity)
                        if ann.author:
                            highlight.set_info(title=ann.author)
                        if ann.content:
                            highlight.set_info(content=ann.content)
                        highlight.update()
                        changed = True

        self._pending = False
        self._materialized = self.session.generation
        if changed:
            self.session.content_changed()
        return changed

    def save_to_pdf(self) -> bool:
        """Save all annotations to the PDF file.

        The write itself is F10-2's: staged, verified, incremental while the
        document is still the file handle it came from.  A refusal is reported
        as ``False`` here; the main window's Save command surfaces the reason.
        """
        try:
            self.sync_to_document()
            return save_document(self.session) is not None
        except SaveError:
            return False

    def add_highlight(self, page: int, rects: List[tuple], color: str = "#FFFF00",
                      author: str = "") -> HighlightAnnotation:
        """Add a new highlight annotation."""
        highlight = HighlightAnnotation.from_selection(page, rects, color, author)
        self._annotations[highlight.id] = highlight
        self.annotation_added.emit(highlight)
        self._touch_model()
        return highlight

    def remove_annotation(self, annotation_id: str) -> bool:
        """Remove an annotation by ID."""
        if annotation_id in self._annotations:
            del self._annotations[annotation_id]
            self.annotation_removed.emit(annotation_id)
            self._touch_model()
            return True
        return False

    def update_annotation(self, annotation_id: str, **kwargs) -> Optional[Annotation]:
        """Update an annotation's properties."""
        if annotation_id in self._annotations:
            ann = self._annotations[annotation_id]
            for key, value in kwargs.items():
                if hasattr(ann, key):
                    setattr(ann, key, value)
            ann.modified = datetime.now().isoformat()
            self.annotation_modified.emit(ann)
            self._touch_model()
            return ann
        return None

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

    def add_note_to_highlight(self, highlight_id: str, note_text: str) -> Optional[HighlightAnnotation]:
        """Add or update a note on an existing highlight."""
        if highlight_id in self._annotations:
            ann = self._annotations[highlight_id]
            if ann.type == AnnotationType.HIGHLIGHT:
                ann.set_note(note_text)
                self.annotation_modified.emit(ann)
                self._touch_model()
                return ann
        return None

    def get_note_for_highlight(self, highlight_id: str) -> Optional[str]:
        """Get the note text for a highlight."""
        if highlight_id in self._annotations:
            ann = self._annotations[highlight_id]
            if ann.type == AnnotationType.HIGHLIGHT:
                return ann.get_note()
        return None

    def remove_note_from_highlight(self, highlight_id: str) -> bool:
        """Remove the note from a highlight (keeps the highlight)."""
        if highlight_id in self._annotations:
            ann = self._annotations[highlight_id]
            if ann.type == AnnotationType.HIGHLIGHT:
                ann.set_note("")
                self.annotation_modified.emit(ann)
                self._touch_model()
                return True
        return False

    def clear_page_annotations(self, page: int) -> int:
        """Remove all annotations from a page. Returns count removed."""
        ids_to_remove = [aid for aid, ann in self._annotations.items() if ann.page == page]
        for aid in ids_to_remove:
            del self._annotations[aid]
            self.annotation_removed.emit(aid)
        if ids_to_remove:
            self._touch_model()
        return len(ids_to_remove)


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