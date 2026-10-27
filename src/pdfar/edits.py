"""F10-1: edits as intent, applied to a copy and adopted only on success.

The model is taken from PdfCraft (see
``docs/pdfcraft-editing-analysis.md`` §1 and §3): an edit describes *what the
user asked for* (``RotatePages { pages, delta }``), never a before/after pair of
values.  That is what makes it replayable, labelable and undoable.

Two rules shape the implementation:

* **Copy and adopt.**  An edit runs against a throwaway clone of the document.
  The live document is replaced only when the edit returns without raising, so a
  failed edit cannot leave the user's document half-changed — the bytes on the
  undo stack are taken from the document *before* the attempt, and the attempt
  never touches it.
* **Undo is a byte snapshot, not an inverse operation.**  Writing the inverse of
  an arbitrary PDF edit correctly is the hard part of a PDF editor; keeping the
  previous state is not.  The cost is memory, so the stack is bounded by both a
  step count and a byte budget.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntFlag
from typing import List, Optional, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz


class Scope(IntFlag):
    """What an edit is allowed to have changed.

    Graded invalidation is what keeps an edit cheap: a metadata change must not
    re-render 540 pages.  ``STRUCTURE`` covers the page tree (count, order,
    size, rotation), which every layout table in the UI is derived from.
    """

    NONE = 0
    METADATA = 1
    STRUCTURE = 2
    CONTENT = 4
    COMMENTS = 8
    LINKS = 16
    FORM = 32
    FULL = METADATA | STRUCTURE | CONTENT | COMMENTS | LINKS | FORM


class EditError(Exception):
    """An edit that could not be carried out.  The document is left untouched."""


class Edit(ABC):
    """One user intent, applied to a document clone."""

    #: What this edit can change.  Subclasses override it; ``FULL`` is the safe
    #: default for anything that rewrites page content.
    scope: Scope = Scope.FULL

    @property
    @abstractmethod
    def label(self) -> str:
        """Human-readable name for the undo/redo menu entry."""

    @abstractmethod
    def apply(self, doc: fitz.Document) -> None:
        """Mutate *doc*.  Raise :class:`EditError` to abort the edit."""


@dataclass(frozen=True)
class SetMetadata(Edit):
    """Set document information entries (title, author, subject, …)."""

    entries: Tuple[Tuple[str, str], ...]

    scope = Scope.METADATA

    @property
    def label(self) -> str:
        return "Edit document properties"

    def apply(self, doc: fitz.Document) -> None:
        meta = dict(doc.metadata or {})
        for key, value in self.entries:
            meta[key] = value
        try:
            doc.set_metadata(meta)
        except Exception as exc:            # noqa: BLE001 - reported to the user
            raise EditError(f"Could not set metadata: {exc}") from exc


@dataclass
class _Step:
    """One accepted edit: what it was called, what it can have changed, and
    the document bytes as they were *before* it ran."""

    label: str
    scope: Scope
    before: bytes


class EditHistory:
    """Undo/redo as whole-document byte snapshots, bounded two ways.

    A step count alone is not a bound: 50 snapshots of a 500 MB scanned manual
    is 25 GB.  So the oldest steps are dropped until the stack fits both
    ``max_steps`` and ``max_bytes``.  Dropping the *oldest* keeps the most
    recent work undoable, which is what a user reaching for Ctrl+Z expects.
    """

    def __init__(self, max_steps: int = 50, max_bytes: int = 256 * 1024 * 1024):
        self.max_steps = max(1, int(max_steps))
        self.max_bytes = max(1, int(max_bytes))
        self._undo: List[_Step] = []
        self._redo: List[_Step] = []
        self._bytes = 0

    # ------------------------------------------------------------- recording
    def record(self, edit: Edit, before: bytes) -> None:
        """Store the pre-edit state for ``edit`` and clear the redo branch."""
        self._undo.append(_Step(edit.label, edit.scope, bytes(before)))
        self._bytes += len(before)
        self._bytes -= sum(len(step.before) for step in self._redo)
        self._redo.clear()
        self._trim()

    def _trim(self) -> None:
        while self._undo and (len(self._undo) > self.max_steps
                              or self._bytes > self.max_bytes):
            dropped = self._undo.pop(0)
            self._bytes -= len(dropped.before)

    # ------------------------------------------------------------------- state
    def __len__(self) -> int:
        return len(self._undo)

    @property
    def bytes_used(self) -> int:
        return self._bytes

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> Optional[str]:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> Optional[str]:
        return self._redo[-1].label if self._redo else None

    # --------------------------------------------------------------- stepping
    @property
    def undo_target(self) -> Optional[_Step]:
        """The step that :meth:`take_undo` would pop, without popping it.

        Callers restore the document first and only then pop, so a failure while
        reopening the snapshot leaves both stacks describing the document the
        user is actually looking at.
        """
        return self._undo[-1] if self._undo else None

    @property
    def redo_target(self) -> Optional[_Step]:
        return self._redo[-1] if self._redo else None

    def take_undo(self, current: bytes) -> _Step:
        """Move to the previous state, parking *current* for redo."""
        if not self._undo:
            raise EditError("Nothing to undo")
        step = self._undo.pop()
        self._bytes -= len(step.before)
        self._redo.append(_Step(step.label, step.scope, current))
        self._bytes += len(current)
        self._trim_redo()
        return step

    def take_redo(self, current: bytes) -> _Step:
        """Move forward again, parking *current* for undo."""
        if not self._redo:
            raise EditError("Nothing to redo")
        step = self._redo.pop()
        self._bytes -= len(step.before)
        self._undo.append(_Step(step.label, step.scope, current))
        self._bytes += len(current)
        self._trim()
        return step

    def _trim_redo(self) -> None:
        # The same budget covers both stacks; overflow drops the oldest redo,
        # then the oldest undo, which is the least useful end of each.
        while self._bytes > self.max_bytes and (self._redo or self._undo):
            if self._redo:
                self._bytes -= len(self._redo.pop(0).before)
            else:
                self._bytes -= len(self._undo.pop(0).before)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._bytes = 0
