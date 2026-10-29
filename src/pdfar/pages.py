"""F10-4: the page tree, edited as intent.

A page manipulation is the kind of edit that can quietly destroy a document, so
every one here is an :class:`~pdfar.edits.Edit`: it says what the user asked for,
runs against a throwaway copy, and is adopted only when it returns without
raising.  Nothing in this module touches the live document.

The first item is the smallest and the one the rest depend on: turning a page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from .edits import Edit, EditError, Scope


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
