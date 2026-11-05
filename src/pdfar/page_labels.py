"""F10-4v: reading the numbering the file wrote.

A PDF can say what its own sheets are called.  ``/PageLabels`` is a list of ranges
over *positions* — "from sheet 0, roman, starting at 1; from sheet 2, arabic,
starting at 1" — and a book whose front matter is ``i, ii`` and whose body starts at
``1`` is not a curiosity but the way a manual, a thesis or a scanned report arrives.
Until this module PDFAR never read it: no ``get_label()`` call existed anywhere in
``src/pdfar/``, so a document that numbers its sheets from 103 was shown to the reader
as "Page 3".

Okular treats the name as part of the reading position rather than as a decoration:
the location bar substitutes it when it differs from the number
(``part/minibar.cpp:90-106``), a name typed there is resolved back to a page
(``:333-339``), and where both matter it prints both — ``part/annotationmodel.cpp:321-322``
renders "Page 4 (ii)".  This module does the same three things to the toolbar's one
page box, and nothing else: the count beside the box stays a count, the sidebar stays
positional, and no file is rewritten.  The numbering is read, never repaired — that is
**F10-4w**'s half of this task, decided here as a reading rather than as an edit: where
the catalog does not name a sheet, the reader is shown its position, which is what
PdfCraft answers too (``crates/organize/src/labels.rs:263-272``).

Everything below rests on measurements taken first, against MuPDF 1.28.2 and PyQt6:

* ``Page.get_label()`` answers a **string**, not the ``(number, text)`` pair its
  docstring describes, and costs 110 µs a call because it re-reads and re-parses the
  catalog every time — fine for the one sheet on screen, 86 ms for a walk of a
  540-sheet book, which is what a typed name needs.
* The catalog read once is the same answer 40× cheaper: 3.45 ms for the whole walk of
  that book, measured identical to ``get_label()`` sheet by sheet on every numbering
  shape in the suite, ``/S /None``, prefix-only, ``/St 0``, negative and 40-billion
  starts included.  ``utils.construct_label`` is the library's own formatter, so roman
  is not re-written here, and the one rule copied from underneath ``get_label_pno`` —
  that ``/S /a`` and ``/S /A`` enumerate from zero, everything else from its number —
  is pinned by the comparison test rather than trusted.
* For a slot **below every range** the library raises ``IndexError: list index out of
  range``, despite both docstrings promising an empty string; ``Document.get_page_numbers``,
  the library's own "which sheet is called X", raises it too, from inside ``validate``,
  on the first sheet of such a book.  Neither route is taken: a sheet with no name is
  read by its position, and the inverse is resolved against the same function the
  display uses — otherwise the box would say ``Page 1`` over a sheet that typing ``1``
  refuses to find.
* An empty answer is not a name either.  A book with no ``/PageLabels`` and a range
  written ``/S /None`` with no prefix both answer ``''`` (measured on each), and a blank
  in the toolbar reads as a crash, so ``''`` falls back on the position as well.
  A prefix that carries no number — ``/P (sec )`` with no ``/S`` — answers ``'sec '`` and
  is shown exactly that, because it is not empty and it is what the producer wrote.
* PyQt6 6.8 hands ``validate`` and ``valueFromText`` the **whole line-edit text**
  (``'Page 4'``), not the bare typing: measured, a ``valueFromText`` that compared the
  names to its argument as given never matched one, and the box quietly went to the
  position instead of the sheet the reader asked for.  Every entry point here strips the
  boilerplate first.
* Qt calls the subclass's ``textFromValue`` **from the base constructor**, before
  ``__init__`` has assigned anything — the first draft died on ``AttributeError`` — which
  is why the document arrives through a class attribute the instance later overrides.
* ``setValue`` with the value it already holds **still re-renders** the text.  That is
  the whole refresh an edit needs, because a delete above the reader changes what the
  sheet is *called* and not which sheet it is, and every update site already calls
  ``setMaximum`` and ``setValue`` — some of them with the signals blocked, which is why
  the tooltip is set from the render hook and not from ``valueChanged``.
* At the toolbar's 96 px the line edit has 76 px, and the widest name a 540-sheet book
  numbers can produce there — ``Page ccxxxviii``, measured over all five hundred and forty
  sheets in the box's own 8 pt font — is 71 px, so no width is touched.  Five pixels of
  spare, which is why the number is written down: a numbering with a long prefix is the
  shape that would clip, and nothing in the suite holds the pixel width.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from pymupdf import utils

from PyQt6.QtGui import QValidator
from PyQt6.QtWidgets import QSpinBox


def _rules(doc: fitz.Document) -> List[dict]:
    """The catalog's ranges, in the order a reader has to walk them.

    Sorted, because a producer may list them out of sequence — the shape
    ``DeletePages`` re-sorts before it deletes (F10-4u) — and the range that reaches a
    sheet is the last one *sorted by start*, not the last one written.  ``Page.get_label``
    sorts the same list, which is what the equality test below holds.
    """
    return sorted(doc.get_page_labels(), key=lambda rule: rule["startpage"])


def _name(rules: List[dict], slot: int) -> str:
    """What the ranges call sheet *slot*, or its position where they say nothing.

    One walk of the ranges, no page object: the ranges are a dozen entries at most and
    the whole book's worth of names costs milliseconds, while ``Page.get_label()``
    re-reads the catalog for every sheet it is asked about.
    """
    reached = [rule for rule in rules if rule["startpage"] <= slot]
    if not reached:
        return str(slot + 1)
    rule = reached[-1]
    style = rule.get("style") or ""
    # The alphabet runs from zero — ``/S /A`` over one sheet is ``A``, not ``B`` — and
    # every other style counts from the number the range starts at.  This is the whole
    # of ``get_label_pno``'s arithmetic, and the equality test below is what holds it.
    delta = -1 if style in ("a", "A") else 0
    number = slot - rule["startpage"] + rule.get("firstpagenum", 1) + delta
    return utils.construct_label(style, rule.get("prefix", ""), number) or str(slot + 1)


def label_for(doc: fitz.Document, slot: int) -> str:
    """What *doc* calls sheet *slot*.

    Never empty and never nothing: a book with no numbering, a sheet below every range
    and a ``/S /None`` range all answer with the position, because the reader has to be
    shown something and the position is the one name that is always true.
    """
    return _name(_rules(doc), slot)


def resolve_label(doc: fitz.Document, text: str) -> Tuple[Optional[int], bool]:
    """Where *text* names a sheet: the first slot that answers to it, and whether any
    name merely begins with it.

    Both come out of one walk because both are asked while the reader is still typing —
    ``i`` is on its way to ``iii``, and refusing it mid-word would refuse the name.  The
    first slot wins a shared name (a prefix-only range calls every sheet it covers the
    same thing) because a numbering that repeats itself cannot be read back more
    precisely than that.
    """
    if not text:
        return None, False
    rules = _rules(doc)
    growing = False
    for slot in range(doc.page_count):
        name = _name(rules, slot)
        if name == text:
            return slot, True
        if not growing and name.startswith(text):
            growing = True
    return None, growing


class PageSpinBox(QSpinBox):
    """The toolbar's page box: it says the sheet's name and holds its position.

    The value is still the slot, one-based, exactly as it was — every route that moves
    the reader, saves the position or greys an action reads ``value()`` and none of them
    is asked to care what the file calls its sheets.  Only the *text* changes, which is
    what makes this a widget problem rather than a document problem, and the widget
    answer is the three Qt hooks that convert between the two.

    A name that is also a number is the one genuine ambiguity: on a book whose body
    starts at 40, ``40`` is both a name and a position.  The name wins, because it is
    what the reader sees in the box and what they typed, and the position it collides
    with is the number the box would have shown had the file said nothing.
    """

    #: The live document, asked for rather than held: a session swaps its ``Document``
    #: object when an edit is undone (``DocumentSession`` clones and assigns), so a
    #: pointer cached here would be a closed file.  It is a *class* attribute because
    #: Qt calls ``textFromValue`` from the base constructor, before any instance exists.
    source: Optional[Callable[[], Optional[fitz.Document]]] = None

    def set_source(self, source: Callable[[], Optional[fitz.Document]]) -> None:
        """Ask *source* which document to read the numbering from, and render again."""
        self.source = source
        self.refresh()

    def document(self) -> Optional[fitz.Document]:
        """The document to read, or ``None`` when there is nothing to read.

        Every call, never once: the box outlives the books it names, and a tab switch
        is not an event it is told about.
        """
        return self.source() if self.source is not None else None

    def refresh(self) -> None:
        """Render the held value again, which is what a changed numbering needs.

        ``setValue`` with the value it already holds re-renders the text — measured — so
        the name follows an edit without the position moving and without a signal
        anybody has to be protected from.
        """
        self.setValue(self.value())

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt's name
        """What the box shows for sheet *value*: its name, with its position in the tooltip.

        The tooltip is written from here because this is the one place every route to a
        new page passes through — the page-change site sets the value with its signals
        blocked, so ``valueChanged`` would leave the sentence a page behind.  And it is
        written *only* for the value the box holds, because Qt calls the same hook to
        sample how wide the widget has to be: measured, a layout pass walked a dozen other
        sheets' names through here and left the tooltip describing a page nobody is on.

        Saying both is Okular's answer, not a hedge (``part/annotationmodel.cpp:321-322``
        renders "Page 4 (ii)"): ``Page 1`` is honest about what the file says and useless
        about where the reader is.
        """
        doc = self.document()
        name = str(value) if doc is None else label_for(doc, value - 1)
        if value == self.value():
            if doc is None or name == str(value):
                self.setToolTip("")
            else:
                self.setToolTip(f"Sheet {value} of {doc.page_count}, called {name}")
        return name

    def valueFromText(self, text: Optional[str]) -> int:  # noqa: N802 - Qt's name
        wanted = self._wanted(text)
        doc = self.document()
        if doc is not None:
            slot, _growing = resolve_label(doc, wanted)
            if slot is not None:
                return slot + 1
        return super().valueFromText(wanted)

    def validate(self, text: Optional[str],
                 pos: int) -> Tuple[QValidator.State, str, int]:  # noqa: N802
        shown = "" if text is None else text
        wanted = self._wanted(text)
        if not wanted:
            # The reader has selected the text and not yet replaced it.
            return QValidator.State.Intermediate, shown, pos
        doc = self.document()
        if doc is not None:
            slot, growing = resolve_label(doc, wanted)
            if slot is not None:
                return QValidator.State.Acceptable, shown, pos
            if growing:
                return QValidator.State.Intermediate, shown, pos
        # Nothing in the book is called that and the typing is not the start of a name,
        # so the number rules are the base widget's own — which is what a document with
        # no numbering has always done, sheet by sheet.
        state, _kept, _where = super().validate(wanted, len(wanted))
        return state, shown, pos

    def _wanted(self, text: Optional[str]) -> str:
        """The typing, without the word the box puts in front of it.

        Measured: this Qt hands both hooks ``'Page 4'`` and not ``'4'``, and a sibling
        version hands the bare form to ``valueFromText`` — so both are stripped here and
        neither form can be the one that silently stops matching.  ``None`` is what Qt's
        own stubs say these hooks may be handed; it has never arrived in a measurement,
        and an empty name is the same question as none.
        """
        wanted = text or ""
        prefix = self.prefix()
        if prefix and wanted.startswith(prefix):
            wanted = wanted[len(prefix):]
        return wanted
