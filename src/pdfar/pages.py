"""F10-4: the page tree, edited as intent.

A page manipulation is the kind of edit that can quietly destroy a document, so
every one here is an :class:`~pdfar.edits.Edit`: it says what the user asked for,
runs against a throwaway copy, and is adopted only when it returns without
raising.  Nothing in this module touches the live document.

Two items so far, in the order the roadmap needed them: turning a page, which is
where the page-tree machinery was proved, and taking pages out, which is where it
starts to matter that every number above the cut means something else afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from .edits import Edit, EditError, Scope


def contiguous_runs(pages: Iterable[int]) -> Tuple[Tuple[int, int], ...]:
    """The blocks of neighbouring pages a set is made of, in ascending order.

    A page-tree operation costs a rebuild of the whole tree, not a cost per page:
    measured on the 540-page book, one page out and a hundred out both took 45 ms,
    while removing the same hundred one at a time took 2055 ms.  So a pick of a
    block is asked for as one run, and only a scattered pick pays per page.
    """
    runs: List[List[int]] = []
    for page in sorted(set(pages)):
        if runs and runs[-1][1] == page - 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return tuple((start, end) for start, end in runs)


@dataclass(frozen=True)
class RotatePages(Edit):
    """Turn the named pages by *delta* degrees, into the file.

    The delta is a quarter turn or several, because that is all PDF has: an
    angle that is not a multiple of 90 is refused rather than handed to MuPDF,
    which folds it silently to *no turn at all* — a document that looks
    unchanged after a command that promised to change it.

    A page's turn is an **inheritable** page-tree attribute: a ``/Rotate`` on any
    ancestor ``/Pages`` node applies to every page below it unless the page says
    otherwise.  That is why the edit writes the page's own node and why the angle
    it writes is measured from ``page.rotation`` — which is the turn the page is
    *displayed* at, inheritance included — rather than from whatever the page node
    happens to hold.  Writing the sum on the page leaves the ancestors alone, so
    the pages beside the one asked for keep the turn they had.
    """

    pages: Tuple[int, ...]
    delta: int = 90

    scope = Scope.STRUCTURE

    @property
    def label(self) -> str:
        if len(self.pages) == 1:
            return "Rotate Page"
        return f"Rotate {len(self.pages)} Pages"

    def apply(self, doc: fitz.Document) -> None:
        if not self.pages:
            raise EditError("No page was chosen to rotate")
        if self.delta % 90:
            raise EditError(
                f"A page can only be turned by a multiple of 90°, not {self.delta}°")
        count = doc.page_count
        for index in self.pages:
            # Every index is checked before a single one is written: a list that
            # names a page this document does not have is refused whole, so the
            # pages that were valid are not turned on the way out.
            if not 0 <= index < count:
                raise EditError(
                    f"Page {index + 1} is not in this document, which has "
                    f"{count} pages")
        for index in dict.fromkeys(self.pages):
            # A page named twice is one page turned once; the intent is which
            # pages, not how many times the list said so.
            page = doc.load_page(index)
            page.set_rotation((page.rotation + self.delta) % 360)


@dataclass(frozen=True)
class DeletePages(Edit):
    """Take the named pages out of the document, and their marks with them.

    MuPDF removes a page's own annotations with it and leaves the survivors on the
    pages they were on, so the delete is contained — but every page *above* the cut
    takes the number of the one below it, and that is why :attr:`changed` says
    nothing while :attr:`pages` keeps the list the user named.

    Two refusals that are not MuPDF's:

    * a **negative** index.  ``delete_page(-1)`` is accepted and removes the *last*
      page, because MuPDF counts a negative index from the end; the range form does
      refuse one, so what checking it here buys is a sentence that names the page
      and a page tree that was never cut.
    * a request that would leave **no pages**.  MuPDF takes the last one out without
      comment and raises ``cannot save with zero pages`` at the save, well after the
      edit was accepted, undoable and shown to the user as done.
    """

    pages: Tuple[int, ...]

    scope = Scope.STRUCTURE

    @property
    def changed(self) -> Optional[Tuple[int, ...]]:
        """Every page from the cut onwards, which is not worth pretending to know.

        The pages that go are not the pages that move: after page 2 of five goes,
        the marks, thumbnails and outline rows numbered 3 and 4 name different
        sheets than they did.  Naming the deleted indices would let a per-page
        cache keep those entries under numbers that no longer reach them.
        """
        return None

    @property
    def label(self) -> str:
        if len(self.pages) == 1:
            return "Delete Page"
        return f"Delete {len(self.pages)} Pages"

    def apply(self, doc: fitz.Document) -> None:
        if not self.pages:
            raise EditError("No page was chosen to delete")
        count = doc.page_count
        # Checked whole before anything is removed, so a list that names a page this
        # document does not have takes nothing out on the way out.
        for index in self.pages:
            if not 0 <= index < count:
                raise EditError(
                    f"Page {index + 1} is not in this document, which has "
                    f"{count} pages")
        named = set(self.pages)
        if len(named) >= count:
            raise EditError(
                f"This document has {count} pages and all of them are being "
                f"deleted; a page is needed to save it")
        # Highest run first: a delete shifts the numbers above it, so working up
        # from the front would spend the list on the wrong pages.
        for start, end in sorted(contiguous_runs(named), reverse=True):
            doc.delete_pages(start, end)
        self._drop_dead_outline_entries(doc)

    @staticmethod
    def _drop_dead_outline_entries(doc: fitz.Document) -> None:
        """Prune the outline rows MuPDF left pointing at nothing.

        An entry whose page went reads ``page == -1`` with its destination demoted
        to kind 0, which the sidebar can draw but no click can follow; the entries
        that landed on survivors are already shifted, so they need nothing.  The
        tree is rewritten only when something actually died, because ``set_toc``
        writes every entry again — new objects for rows the user did not touch.

        Removing a row can also leave the *shape* invalid, and MuPDF refuses to
        write such a tree rather than repairing it: a first entry deeper than level 1
        (``hierarchy level of item 0 must be 1``) once the row above it is gone, and a
        step of two levels (``bad hierarchy level in row N``) once the row in between
        is.  Both are answered by one clamp: no entry may sit more than one level
        below the entry before it, which lifts an orphan without flattening anything
        that was already sound.
        """
        entries = doc.get_toc(simple=False)
        live = [entry for entry in entries if entry[2] >= 1]
        if len(live) == len(entries):
            return
        previous = 0
        for entry in live:
            entry[0] = previous = max(1, min(entry[0], previous + 1))
        doc.set_toc(live)
