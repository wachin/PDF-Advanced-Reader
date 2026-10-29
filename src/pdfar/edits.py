"""F10-1: edits as intent, applied to a copy and adopted only on success.

The model is taken from PdfCraft (see
``docs/pdfcraft-editing-analysis.md`` §1 and §3): an edit describes *what the
user asked for* (``RotatePages { pages, delta }``), never a before/after pair of
values.  That is what makes it replayable, labelable and undoable.

Two rules shape the implementation:

* **Copy and adopt.**  An edit runs against a throwaway clone of the document.
  The live document is replaced only when the edit returns without raising, so a
  failed edit cannot leave the user's document half-changed — the state on the
  undo stack is taken from the document *before* the attempt, and the attempt
  never touches it.
* **Undo is a snapshot, not an inverse operation.**  Writing the inverse of an
  arbitrary PDF edit correctly is the hard part of a PDF editor; keeping the
  previous state is not.  The cost is a whole document per step, so the stack is
  bounded by both a step count and a byte budget, and since F10-3c the whole
  document is a *file* in :mod:`pdfar.revisions` rather than a buffer in RAM.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntFlag
from typing import List, Optional, Tuple, Union

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


@dataclass(frozen=True)
class Renumbering:
    """What one step did to the page *numbers*, in the direction it went.

    ``order[i]`` is the page number the sheet now at position ``i`` held before
    the step, so ``len(order)`` is the page count after it and ``-1`` marks a sheet
    with no past — a page an edit invented rather than moved.

    :meth:`where` answers the question a bookmark, a saved reading position or a
    numbering range has to ask when the page tree moves: the number is held outside
    the document, so after the step it names another sheet.  None of it can be
    restored from a snapshot, because the reader may have changed it since the step
    went in — so the step carries a map instead, and both directions of the map are
    exact for anything that points at a sheet which exists.
    """

    #: Where each page came from; see above.
    order: Tuple[int, ...]
    #: True when this is the *undoing* of the step, so numbers run back.
    backward: bool = False

    def where(self, page: int) -> int:
        """The number the sheet on *page* wears once the step is taken.

        With ``backward`` the step is being undone, so the question runs the other
        way: the sheet now at *page* goes home to the number it came from.

        A number whose own sheet went in the step does not point at nothing: it
        lands on the first sheet to arrive at it, which is where the reader's page
        carried on when a delete took the one they were on (F10-4f).
        """
        if self.backward:
            if not 0 <= page < len(self.order):
                return page
            home = self.order[page]
            return page if home < 0 else home
        try:
            return self.order.index(page)
        except ValueError:
            # The sheet went.  Carried on where the reading is: the number the
            # survivors under it count up to, clamped to the last page when none
            # of them came from below -- which is what ``shifted_index`` answers
            # for the same delete, page minus the pages taken out below it.
            arrived = sum(1 for old in self.order if old < page)
            return max(0, min(arrived, len(self.order) - 1))


class Edit(ABC):
    """One user intent, applied to a document clone."""

    #: What this edit can change.  Subclasses override it; ``FULL`` is the safe
    #: default for anything that rewrites page content.
    scope: Scope = Scope.FULL

    #: Which pages this edit writes to.  ``()`` says none of them, ``None`` says
    #: the edit cannot say — which is the default, because being wrong in that
    #: direction is the safe one: a reader that caches per page re-reads the
    #: whole document when it sees ``None``, and only wastes time when the edit
    #: named pages that turned out not to change.
    pages: Optional[Tuple[int, ...]] = None

    @property
    def changed(self) -> Optional[Tuple[int, ...]]:
        """Which pages a cache keyed by page number has to look at again.

        Not the same question as :attr:`pages`, which names the pages the *user*
        asked about.  An edit that works on the page tree moves the numbers of the
        pages that stay: delete page 2 of five and pages 3 and 4 are now 2 and 3,
        so their marks, their thumbnails and their place in the outline all sit
        under a number that no longer means what it said.  Such an edit overrides
        this to ``None`` — the honest answer when the set is "everything from the
        cut onwards" — and keeps :attr:`pages` for the label and the caller.
        """
        return self.pages

    def page_order(self, before_count: int) -> Optional[Tuple[int, ...]]:
        """Where each page of the document that comes out came from, or ``None``.

        ``None`` says the edit leaves the numbering alone — the answer for a turn,
        which rewrites a page's ``/Rotate`` and moves no sheet, and for every edit
        that is not about the page tree at all.  It is not an optimisation: a step
        that says nothing is what tells the marks and the reading position held
        outside the document to stay where they are.  An edit that renumbers
        overrides it with the :class:`Renumbering` order, whose length is the page
        count *after*.
        """
        return None

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
    pages = ()

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


@dataclass(frozen=True)
class Snapshot:
    """The document as it was before one step: a byte buffer, or a revision file.

    Two shapes because two mechanisms reach them.  A session with a file to seed
    from keeps whole revisions on disk (:mod:`pdfar.revisions`) and hands them
    over as paths, which costs a copy of the file and no RAM at all; a session
    without one keeps MuPDF's ``tobytes()`` buffer in memory, which is what F10-1
    did.  ``size`` is what the byte budget counts — the buffer's length or the
    file's size, so one number still means "what this step is holding".
    """

    data: Optional[bytes] = None
    path: Optional[str] = None
    size: int = 0

    @property
    def is_revision(self) -> bool:
        return self.path is not None

    def content(self) -> bytes:
        """The snapshot as PDF bytes, reading the revision file if that is what it is."""
        if self.data is not None:
            return self.data
        if self.path is None:
            raise ValueError("an empty snapshot has no content")
        with open(self.path, "rb") as handle:
            return handle.read()


def _as_snapshot(value: Union["Snapshot", bytes, str]) -> "Snapshot":
    """Accept the shapes callers reach for: a snapshot, a buffer, or a path."""
    if isinstance(value, Snapshot):
        return value
    if isinstance(value, (bytes, bytearray)):
        return Snapshot(data=bytes(value), size=len(value))
    if isinstance(value, str):
        try:
            size = os.path.getsize(value)
        except OSError:
            size = 0
        return Snapshot(path=value, size=size)
    raise TypeError(f"not a document snapshot: {type(value).__name__}")


@dataclass
class _Step:
    """One accepted edit: what it was called, what it can have changed, and
    the document as it was *before* it ran."""
    label: str
    scope: Scope
    before: Snapshot
    changed: Optional[Tuple[int, ...]] = None
    #: What the edit did to the page numbers, or ``None`` when it moved none.
    #: It belongs to the step rather than to the session, so it travels with the
    #: step between the undo and the redo stacks.
    order: Optional[Tuple[int, ...]] = None

    @property
    def size(self) -> int:
        return self.before.size


class EditHistory:
    """Undo/redo as whole-document snapshots, bounded two ways.

    A step count alone is not a bound: 50 snapshots of a 500 MB scanned manual
    is 25 GB.  So the oldest steps are dropped until the stack fits both
    ``max_steps`` and ``max_bytes``.  Dropping the *oldest* keeps the most
    recent work undoable, which is what a user reaching for Ctrl+Z expects.

    ``on_release`` is called with the path of a revision file that no step refers
    to any more; a buffer needs nothing.
    """

    def __init__(self, max_steps: int = 50, max_bytes: int = 256 * 1024 * 1024,
                 on_release=None):
        self.max_steps = max(1, int(max_steps))
        self.max_bytes = max(1, int(max_bytes))
        self._on_release = on_release
        self._undo: List[_Step] = []
        self._redo: List[_Step] = []
        self._bytes = 0

    # ------------------------------------------------------------- recording
    def record(self, edit: Edit, before: Union[Snapshot, bytes, str],
               order: Optional[Tuple[int, ...]] = None) -> None:
        """Store the pre-edit state for ``edit`` and clear the redo branch.

        ``order`` is what the edit did to the page numbers — the session reads it
        off the edit while it still holds the count from *before*, because after
        the adoption the document cannot be asked what it used to be.
        """
        step = _Step(edit.label, edit.scope, _as_snapshot(before), edit.changed,
                     order)
        self._undo.append(step)
        self._bytes += step.size
        self._bytes -= sum(s.size for s in self._redo)
        for dropped in self._redo:
            self._release(dropped.before)
        self._redo.clear()
        self._trim()

    def _release(self, snapshot: Snapshot) -> None:
        if snapshot.path and self._on_release is not None:
            self._on_release(snapshot.path)

    def _trim(self) -> None:
        while self._undo and (len(self._undo) > self.max_steps
                              or self._bytes > self.max_bytes):
            dropped = self._undo.pop(0)
            self._bytes -= dropped.size
            self._release(dropped.before)

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

    def take_undo(self, current: Union[Snapshot, bytes, str]) -> _Step:
        """Move to the previous state, parking *current* for redo."""
        if not self._undo:
            raise EditError("Nothing to undo")
        step = self._undo.pop()
        self._bytes -= step.size
        parked = _Step(step.label, step.scope, _as_snapshot(current), step.changed,
                       step.order)
        self._redo.append(parked)
        self._bytes += parked.size
        self._trim_redo()
        return step

    def take_redo(self, current: Union[Snapshot, bytes, str]) -> _Step:
        """Move forward again, parking *current* for undo."""
        if not self._redo:
            raise EditError("Nothing to redo")
        step = self._redo.pop()
        self._bytes -= step.size
        parked = _Step(step.label, step.scope, _as_snapshot(current), step.changed,
                       step.order)
        self._undo.append(parked)
        self._bytes += parked.size
        self._trim()
        return step

    def _trim_redo(self) -> None:
        # The same budget covers both stacks; overflow drops the oldest redo,
        # then the oldest undo, which is the least useful end of each.
        while self._bytes > self.max_bytes and (self._redo or self._undo):
            if self._redo:
                dropped = self._redo.pop(0)
            else:
                dropped = self._undo.pop(0)
            self._bytes -= dropped.size
            self._release(dropped.before)

    def clear(self) -> None:
        for step in self._undo + self._redo:
            self._release(step.before)
        self._undo.clear()
        self._redo.clear()
        self._bytes = 0
