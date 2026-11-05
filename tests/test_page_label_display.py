"""F10-4v: the reader is shown the name the file gives the sheet, not its slot.

``/PageLabels`` is a numbering the producer wrote and the reader reads: a catalog can
call its third sheet ``103``, ``A-5`` or ``ii``, and PDFAR had never read it — no
``get_label()`` call existed anywhere in ``src/pdfar/`` before this file, so the
toolbar's page box printed the *position* and a book numbered from 103 was shown as
"Page 3".  Okular treats the name as part of the reading position: the location bar
substitutes it when it differs from the number (``part/minibar.cpp:90-106``), a typed
name is resolved back to a page (``:333-339``), and where both matter it prints both
(``part/annotationmodel.cpp:321-322``).

Everything here rests on measurements taken first, against MuPDF 1.28.2 and PyQt6, and
each one is pinned below rather than trusted:

* ``label_for`` reads the catalog's ranges once and formats with the library's own
  ``utils.construct_label``, because ``Page.get_label()`` re-reads and re-parses
  ``/PageLabels`` on every call — 110 µs a sheet, 86 ms to walk the 540-sheet book,
  against 3.45 ms for the same walk here.  The two answers are pinned equal on every
  style the catalog can name, so the copied arithmetic (the alphabet enumerating from
  zero, everything else from its own number) is a measured fact and not an assumption.
* A slot **below every range** is answered with its position.  ``get_label()`` raises
  ``IndexError`` there — **F10-4w**'s shape, already pinned as a before picture by
  ``tests/test_page_labels.py`` — and PdfCraft answers it with the position too
  (``crates/organize/src/labels.rs:263-272``).  The reader is never shown nothing, and
  the numbering is never repaired to make the question go away.
* The library's own inverse lookup, ``Document.get_page_numbers``, is *not* used: it
  walks slots from the front through the raising call, so on that same book it raises
  inside ``validate``, and it resolves names against ``get_label`` rather than against
  what the box shows — a sheet displayed as ``Page 1`` would then be unreachable by
  typing ``1``.  One test pins both of those.
* ``validate`` and ``valueFromText`` are handed the **whole line-edit text** (``'Page
  4'``) and not the bare typing — measured on this Qt.  A ``valueFromText`` that
  compared names to its argument as given never matched one and sent the reader to the
  position instead of the sheet they asked for, so both forms are stripped and both are
  tested.
* ``setValue`` with the value it already holds **still re-renders** the text, which is
  the whole refresh an edit needs: a delete above the reader changes what the sheet is
  *called*, not which sheet it is.
* A name that is also a number resolves as the name.  On a book whose body starts at 40
  the ``40`` the reader typed is the sheet labelled 40, and the position it collides
  with is the number the box would have shown had the file said nothing.
* The status line is the same sentence said in a second place, on screen at the same
  moment as the box, so it is pinned to the same word: a window that read ``Page 1`` up
  top and ``Page 3/6`` at the bottom would be arguing with itself about one sheet.  The
  count after the slash stays the book's — a total is nobody's name — and both of the
  line's branches (plain and reflow) are built from one expression, which is why one
  test covers the two of them.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QValidator
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox
from test_page_labels import PAGES, ROMAN_THEN_ARABIC, numbered_pdf, printed

from pdfar.main import MainWindow
from pdfar.page_labels import PageSpinBox, label_for, longest_label, resolve_label
from pdfar.pages import DeletePages

#: The same six sheets, said the way a reader reading them would say them.
CALLED = ["Page i", "Page ii", "Page 1", "Page 2", "Page 3", "Page 4"]

#: ``/Nums`` bodies covering every shape the catalog can be written in, each of which
#: the reading has to agree with the library's own ``get_label()`` about.
SHAPES = [
    ("0 << /S /D /St 1 >>", ["1", "2", "3", "4", "5", "6"]),
    ("0 << /S /D /St 103 >>", ["103", "104", "105", "106", "107", "108"]),
    ("0 << /S /r /St 1 >> 3 << /S /D /St 40 >>",
     ["i", "ii", "iii", "40", "41", "42"]),
    ("0 << /S /a /St 1 >>", ["a", "b", "c", "d", "e", "f"]),
    ("0 << /S /A /St 1 >>", ["A", "B", "C", "D", "E", "F"]),
    ("0 << /S /R /St 9 >>", ["IX", "X", "XI", "XII", "XIII", "XIV"]),
    ("0 << /S /None >>", ["1", "2", "3", "4", "5", "6"]),
    ("0 << /P (sec ) >>", ["sec "] * PAGES),
    ("0 << /P (x) /S /D /St 5 >>", ["x5", "x6", "x7", "x8", "x9", "x10"]),
    ("0 << /S /D /St 0 >>", ["0", "1", "2", "3", "4", "5"]),
    ("0 << /S /D /St -5 >>", ["-5", "-4", "-3", "-2", "-1", "0"]),
    ("0 << /S /D /St 999999999999 >>",
     ["999999999999", "1000000000000", "1000000000001",
      "1000000000002", "1000000000003", "1000000000004"]),
    ("0 << /S /xyz /St 7 >>", ["1", "2", "3", "4", "5", "6"]),
    ("0 << /St 7 >>", ["1", "2", "3", "4", "5", "6"]),
    ("2 << /S /r /St 1 >>", ["1", "2", "i", "ii", "iii", "iv"]),
    ("4 << /S /D /St 1 >> 0 << /S /A /St 3 >>", ["C", "D", "E", "F", "1", "2"]),
]


# ------------------------------------------------------------------------ the fixtures


@pytest.fixture
def book(tmp_path):
    """Two roman sheets of front matter and four arabic sheets of body."""
    return numbered_pdf(str(tmp_path / "numbered.pdf"))


@pytest.fixture
def plain(tmp_path):
    """The same six sheets with nothing written in the catalog about numbering."""
    path = str(tmp_path / "plain.pdf")
    build_sample_pdf(path, pages=PAGES)
    return path


@pytest.fixture
def front(tmp_path):
    """A numbering that starts past the front: the F10-4w shape.

    ``1 << /S /r /St 1 >> 4 << /S /D /St 40 >>`` calls sheets 2-4 roman and 5-6 arabic
    from 40, and says nothing at all about sheet 1.
    """
    return numbered_pdf(str(tmp_path / "front.pdf"),
                        ranges=((1, "r", 1), (4, "D", 40)))


def stamped_pdf(path, nums, pages=PAGES):
    """Write a raw ``/Nums`` array, so a shape the setter would normalise can be read."""
    build_sample_pdf(path, pages=pages)
    doc = fitz.open(path)
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels", f"<< /Nums [{nums}] >>")
    staged = path + ".stamped"
    doc.save(staged, deflate=False)
    doc.close()
    os.replace(staged, path)
    return path


def opened(path):
    return fitz.open(path)


# --------------------------------------------------------------- the reading of a name


def test_a_book_with_no_numbering_is_read_by_its_positions(plain):
    doc = opened(plain)
    try:
        assert doc.get_page_labels() == []
        assert [label_for(doc, slot) for slot in range(PAGES)] == \
            ["1", "2", "3", "4", "5", "6"]
    finally:
        doc.close()


def test_the_file_names_the_sheets_and_the_reader_is_told(book):
    """``i ii 1 2 3 4``, not ``1 2 3 4 5 6``."""
    doc = opened(book)
    try:
        assert printed(doc) == ROMAN_THEN_ARABIC
        assert [label_for(doc, slot) for slot in range(PAGES)] == ROMAN_THEN_ARABIC
    finally:
        doc.close()


def test_a_slot_below_every_range_is_answered_with_its_position(front):
    """**F10-4w**, closed as a reading decision: the shape is left alone, and the
    reader is not left with nothing.

    ``get_label()`` raises ``IndexError`` on the sheet the numbering never mentions —
    the before picture ``tests/test_page_labels.py`` pins stays a before picture,
    because clamping slot 0 would invent a numbering for a sheet the producer left out.
    What the reader is *shown* is the other half, and it is the position.
    """
    doc = opened(front)
    try:
        with pytest.raises(IndexError):
            doc.load_page(0).get_label()
        assert label_for(doc, 0) == "1"
        assert [label_for(doc, slot) for slot in range(PAGES)] == \
            ["1", "i", "ii", "iii", "40", "41"]
    finally:
        doc.close()


def test_an_empty_name_is_answered_with_the_position(tmp_path):
    """``/S /None`` covers the sheets and names none of them: measured ``''`` each.

    A blank in the toolbar reads as a crash, so the numbering is respected exactly as
    far as it says anything.
    """
    doc = opened(stamped_pdf(str(tmp_path / "none.pdf"), "0 << /S /None >>"))
    try:
        assert set(printed(doc)) == {""}
        assert [label_for(doc, slot) for slot in range(PAGES)] == \
            ["1", "2", "3", "4", "5", "6"]
    finally:
        doc.close()


def test_a_prefix_that_carries_no_number_is_shown_as_written(tmp_path):
    """``/P (sec )`` with no style is a name, not a gap in one."""
    doc = opened(stamped_pdf(str(tmp_path / "prefix.pdf"), "0 << /P (sec ) >>"))
    try:
        assert set(printed(doc)) == {"sec "}
        assert label_for(doc, 3) == "sec "
    finally:
        doc.close()


@pytest.mark.parametrize("nums,expected", SHAPES)
def test_the_reading_is_the_librarys_own_answer(tmp_path, nums, expected):
    """``label_for`` == ``Page.get_label()``, sheet for sheet, on every shape.

    The one rule this module writes for itself is the enumeration base — ``/S /a`` and
    ``/S /A`` count from zero, the rest from their number — and this is where it is
    held.  The comparison is against the documented call, whose ``''`` and
    ``IndexError`` are turned into the position by the same rule the reading uses, so
    what is asserted is the *naming*, on roman, alphabetic, upper and lower, a prefix
    with and without a style, ``/St`` absent, zero, negative and past 32 bits, an
    unknown style, and two of the shapes the file lists out of order.
    """
    doc = opened(stamped_pdf(str(tmp_path / "shape.pdf"), nums))
    try:
        for slot in range(doc.page_count):
            try:
                said = doc.load_page(slot).get_label()
            except IndexError:
                said = ""
            assert label_for(doc, slot) == (said or str(slot + 1))
        assert [label_for(doc, slot) for slot in range(doc.page_count)] == expected
    finally:
        doc.close()


def test_the_reading_follows_what_a_delete_does_to_the_numbering(book):
    """The name is read from the live document, so a re-aim is shown, not remembered.

    MuPDF shifts the second range up by the hole (F10-4q) and PDFAR's repair keeps the
    first entry at slot 0 (F10-4u): the sheets come back ``i 1 2 3 4`` and the reader is
    told exactly that.
    """
    doc = opened(book)
    try:
        DeletePages(pages=(0,)).apply(doc)
        assert [label_for(doc, slot) for slot in range(doc.page_count)] == \
            ["i", "1", "2", "3", "4"]
    finally:
        doc.close()


# --------------------------------------------------------------- the name typed back


def test_a_typed_name_resolves_to_the_sheet_that_carries_it(book):
    doc = opened(book)
    try:
        assert resolve_label(doc, "ii") == (1, True)
        assert resolve_label(doc, "4") == (5, True)
        assert resolve_label(doc, "1") == (2, True)
    finally:
        doc.close()


def test_a_name_two_sheets_share_resolves_to_the_first_of_them(tmp_path):
    """A prefix-only range calls every sheet it covers the same thing.

    A numbering that repeats itself cannot be read back more precisely than "the first
    sheet it reaches", which is also what a jump has to mean.
    """
    doc = opened(stamped_pdf(str(tmp_path / "shared.pdf"),
                             "0 << /S /D /St 1 >> 3 << /P (sec ) >>"))
    try:
        assert printed(doc) == ["1", "2", "3", "sec ", "sec ", "sec "]
        assert resolve_label(doc, "sec ")[0] == 3
    finally:
        doc.close()


def test_a_name_the_numbering_does_not_carry_answers_nothing(book):
    doc = opened(book)
    try:
        assert resolve_label(doc, "ix") == (None, False)
        assert resolve_label(doc, "") == (None, False)
        assert resolve_label(doc, "Page 3") == (None, False)
    finally:
        doc.close()


def test_a_name_still_being_typed_is_not_a_mistake(front):
    """The second half of the answer is why ``validate`` can accept a partial word.

    ``4`` is no sheet's name here and ``i`` on the numbered book is a complete one, but
    on this book ``4`` is the first keystroke of ``40`` — and a keystroke Qt refuses is
    a keystroke the reader cannot take back.
    """
    doc = opened(front)
    try:
        assert resolve_label(doc, "4") == (None, True)
        assert resolve_label(doc, "40") == (4, True)
        assert resolve_label(doc, "41") == (5, True)
        assert resolve_label(doc, "42") == (None, False)
        assert resolve_label(doc, "v") == (None, False)
    finally:
        doc.close()


def test_the_name_typed_back_is_the_name_shown(front):
    """The inverse inverts *this* function, which is why the library's is not used.

    ``Document.get_page_numbers`` walks the same slots through the raising call, so on
    a book whose numbering starts past the front it raises ``IndexError`` from inside
    ``validate``; and it resolves against ``get_label`` rather than against what the box
    displays, so the sheet shown as ``Page 1`` would answer to nothing.  The rule here is
    one function in both directions: whatever the box prints, typing it finds.
    """
    doc = opened(front)
    try:
        with pytest.raises(IndexError):
            doc.get_page_numbers("1")
        assert resolve_label(doc, "1") == (0, True)
        assert resolve_label(doc, "i") == (1, True)
        for slot in range(doc.page_count):
            name = label_for(doc, slot)
            assert resolve_label(doc, name)[0] <= slot
    finally:
        doc.close()


# --------------------------------------------------------------------- the box itself


@pytest.fixture(autouse=True)
def _an_application_for_the_widgets(qapp):
    """Every case in this file ends up building a widget, and a widget built before the
    application aborts the process rather than raising — so the application is asked for
    by the module, not by each test that remembers."""


def box_over(path):
    """A toolbar box wired to *path*'s document the way the window wires its own."""
    doc = opened(path)
    spin = PageSpinBox()
    spin.setRange(1, doc.page_count)
    spin.setPrefix("Page ")
    spin.set_source(lambda: doc)
    return doc, spin


def test_the_box_shows_the_name_and_holds_the_position(book):
    doc, spin = box_over(book)
    try:
        for value, expected in enumerate(CALLED, start=1):
            spin.setValue(value)
            assert spin.value() == value
            assert spin.text() == expected
    finally:
        doc.close()


def test_a_book_with_no_numbering_shows_exactly_what_it_showed_before(plain):
    """No regression, stated as a fact about the widget: the number is its own name."""
    doc, spin = box_over(plain)
    try:
        spin.setValue(4)
        assert spin.text() == "Page 4"
        assert spin.value() == 4
    finally:
        doc.close()


def test_validate_accepts_both_the_bare_name_and_the_whole_line(book):
    """Measured: this Qt hands both hooks the line-edit text, ``'Page 4'``.

    A hook that recognised only one form refused a valid name — the widget fell back to
    the position without a word — so both are stripped and both are asked here.  On this
    book every name is one or two keystrokes long, so a name is always finished by the
    time it is a word and the partial cases are the ones the numbering does not carry:
    ``9`` is neither a name nor a position of a six-sheet book, and ``v`` begins no name
    in ``i ii 1 2 3 4``.
    """
    doc, spin = box_over(book)
    try:
        spin.setValue(1)
        for text in ("ii", "Page ii", "1", "Page 1", "4", "Page 4", "i", "Page i",
                     "6", "Page 6"):
            state, kept, _pos = spin.validate(text, len(text))
            assert state == QValidator.State.Acceptable, text
            assert kept == text, text
        for text in ("", "Page "):
            state, _kept, _pos = spin.validate(text, len(text))
            assert state == QValidator.State.Intermediate, text
        for text in ("nonsense", "Page nonsense", "v", "9", "Page 9", "40"):
            state, _kept, _pos = spin.validate(text, len(text))
            assert state == QValidator.State.Invalid, text
    finally:
        doc.close()


def test_a_name_still_being_typed_is_not_refused(front):
    """The ``growing`` half of the answer, in the widget that has to act on it.

    ``4`` names nothing on this book and is not a position either (it has five sheets
    left of that), and the base widget would refuse the keystroke — but it is the first
    digit of sheet 40, and a reader who cannot type the second one cannot go there.
    """
    doc, spin = box_over(front)
    try:
        spin.setValue(1)
        for text in ("4", "Page 4"):
            state, _kept, _pos = spin.validate(text, len(text))
            assert state == QValidator.State.Intermediate, text
        for text in ("40", "Page 40", "41", "i", "Page i"):
            assert spin.validate(text, len(text))[0] == QValidator.State.Acceptable, text
        assert spin.validate("42", len("42"))[0] == QValidator.State.Invalid
    finally:
        doc.close()


def test_a_name_is_read_as_a_name_and_a_number_as_a_number(book):
    """Both forms, bare and prefixed, and the collision where one is also the other.

    ``4`` is the sixth sheet's name and a position in range; the name wins.  ``6`` is no
    sheet's name on this book, so it is the position, which is what the box showed before
    it had a numbering to read.
    """
    doc, spin = box_over(book)
    try:
        for text in ("4", "Page 4"):
            assert spin.valueFromText(text) == 6, text
        for text in ("ii", "Page ii"):
            assert spin.valueFromText(text) == 2, text
        for text in ("6", "Page 6"):
            assert spin.valueFromText(text) == 6, text
    finally:
        doc.close()


def test_typing_a_name_jumps_to_the_sheet_that_carries_it(book):
    """The reader types what the page says on it, and the box goes there."""
    doc, spin = box_over(book)
    try:
        spin.setValue(1)
        type_into(spin, "4")
        assert spin.value() == 6, "'4' is the sixth sheet's name, not its position"
        assert spin.text() == "Page 4"
    finally:
        doc.close()


def test_typing_a_position_still_works_on_a_numbered_book(book):
    doc, spin = box_over(book)
    try:
        spin.setValue(1)
        type_into(spin, "6")
        assert spin.value() == 6
    finally:
        doc.close()


def test_typing_something_the_book_does_not_call_anything_leaves_the_page_alone(book):
    """``z`` names no sheet and begins no name, so the keystroke never lands."""
    doc, spin = box_over(book)
    try:
        spin.setValue(3)
        type_into(spin, "zzz")
        assert spin.value() == 3
        assert spin.text() == "Page 1"
    finally:
        doc.close()


def test_the_box_refreshes_when_the_numbering_moves_under_it(book):
    """A delete above the reader changes the sheet's *name*, not its number.

    ``setValue`` with the value it already holds re-renders the text — measured — so the
    refresh is one line and the position never moves.
    """
    doc, spin = box_over(book)
    try:
        spin.setValue(3)
        assert spin.text() == "Page 1"
        DeletePages(pages=(0,)).apply(doc)
        spin.setMaximum(doc.page_count)
        spin.refresh()
        assert spin.value() == 3
        assert spin.text() == "Page 2", "the sheet slid one name to the left"
    finally:
        doc.close()


def test_pointing_the_box_at_another_book_renames_the_sheet_it_holds(book, plain):
    """``set_source`` renders, because the value did not move and the name did.

    Two books open on the same slot number is the case: nothing calls ``setValue``
    again afterwards, so a box that only re-rendered on a moving value would go on
    showing the book it last saw.
    """
    first, second = opened(book), opened(plain)
    spin = PageSpinBox()
    spin.setRange(1, PAGES)
    spin.setPrefix("Page ")
    try:
        spin.set_source(lambda: first)
        spin.setValue(3)
        assert spin.text() == "Page 1"
        assert spin.toolTip() == "Sheet 3 of 6, called 1"
        spin.set_source(lambda: second)
        assert spin.value() == 3
        assert spin.text() == "Page 3"
        assert spin.toolTip() == ""
    finally:
        first.close()
        second.close()


def test_the_box_without_a_document_is_a_plain_page_box():
    spin = PageSpinBox()
    spin.setRange(1, 12)
    spin.setPrefix("Page ")
    spin.setValue(7)
    assert spin.text() == "Page 7"
    assert spin.toolTip() == ""
    spin.refresh()
    assert spin.text() == "Page 7"
    assert spin.valueFromText("Page 9") == 9


def test_the_box_names_both_numbers_when_they_differ(book):
    """The tooltip carries the position the name is hiding.

    ``Page 1`` in the toolbar is honest about what the file says and useless about where
    the reader is, so the sentence beside it names the slot and the count — the pair
    Okular prints in its annotation model (``part/annotationmodel.cpp:321-322``).
    """
    doc, spin = box_over(book)
    try:
        spin.setValue(3)
        assert spin.toolTip() == "Sheet 3 of 6, called 1"
        spin.setValue(1)
        assert spin.toolTip() == "Sheet 1 of 6, called i"
    finally:
        doc.close()


def test_a_layout_probe_cannot_steal_the_sentence(book):
    """Qt reads the render hook for its own reasons, and the tooltip is not one of them.

    ``minimumSizeHint`` walks a sample of the box's values through ``textFromValue`` to
    find out how wide the widget has to be — measured: a layout pass left the tooltip
    naming a sheet the reader was not on, because the last value Qt sampled was not the
    one the box held.  So the sentence is written for the held value and only for it, and
    the render still answers every sampled value with its own name.
    """
    doc, spin = box_over(book)
    try:
        spin.setValue(3)
        assert spin.toolTip() == "Sheet 3 of 6, called 1"
        widths = [spin.minimumSizeHint().width() for _ in range(3)]
        spin.sizeHint()
        QApplication.processEvents()
        assert spin.toolTip() == "Sheet 3 of 6, called 1"
        assert spin.text() == "Page 1"
        assert widths and all(w == widths[0] for w in widths)
    finally:
        doc.close()


def test_a_plain_book_leaves_the_tooltip_saying_nothing_about_labels(plain):
    """The tooltip is for the case where the two answers differ, and only that one."""
    doc, spin = box_over(plain)
    try:
        spin.setValue(4)
        assert spin.toolTip() == ""
        assert spin.text() == "Page 4"
    finally:
        doc.close()


# ------------------------------------------------------------------ in the real window


@pytest.fixture
def window(qapp, monkeypatch):
    """A real window, with every box this feature raises answered for us.

    The delete asks before it cuts, so a test cannot reach the delete without answering
    the question, and anything offering to *save* is answered "don't" — closing the window
    at teardown finds a document the test made dirty, and a Yes there would write a
    fixture to disk and raise a second box on top of the first.
    """

    def answer(_parent, _title, _text, buttons, *rest):
        if buttons & QMessageBox.StandardButton.Save:
            return QMessageBox.StandardButton.Discard
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(answer))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    win = MainWindow()
    win.resize(1100, 760)
    win.show()
    qapp.processEvents()
    try:
        yield win
    finally:
        win._close_all_tabs(confirm=False)
        win.close()
        win.deleteLater()
        qapp.processEvents()


KEYS = {"i": Qt.Key.Key_I, "v": Qt.Key.Key_V, "z": Qt.Key.Key_Z,
        "1": Qt.Key.Key_1, "2": Qt.Key.Key_2, "3": Qt.Key.Key_3,
        "4": Qt.Key.Key_4, "5": Qt.Key.Key_5, "6": Qt.Key.Key_6}


def type_into(spin, keys):
    """What the reader's hands do: replace the text, then press Enter."""
    line = spin.lineEdit()
    line.selectAll()
    for char in keys:
        if char == " ":
            QTest.keyPress(spin, Qt.Key.Key_Space)
            QTest.keyRelease(spin, Qt.Key.Key_Space)
        else:
            QTest.keyClick(spin, KEYS[char])
    QTest.keyClick(spin, Qt.Key.Key_Enter)
    QApplication.processEvents()


def settle(window):
    """Let the window finish what an edit or a page turn set in motion."""
    QApplication.processEvents()


def test_the_toolbar_reads_the_book_the_way_the_book_reads_itself(window, book):
    assert window.open_document(book)
    window.view.goto_page(2)
    settle(window)
    assert window.page_spin.text() == "Page 1", "sheet 3 is the body's first page"
    assert window.page_spin.value() == 3


def test_turning_a_page_moves_the_name_with_the_sheet(window, book):
    assert window.open_document(book)
    shown = []
    for page in range(PAGES):
        window.view.goto_page(page)
        settle(window)
        shown.append(window.page_spin.text())
    assert shown == CALLED


def test_a_delete_above_the_reader_renames_what_the_toolbar_shows(window, book):
    """The reader follows the sheet they were reading, and the sheet keeps its name.

    Slot 2 of ``i ii 1 2 3 4`` is the sheet called ``1``; deleting the front sheet leaves
    ``i 1 2 3 4``, where that same sheet sits at slot 1.  The reading position is re-aimed
    by the edit and the numbering is re-aimed by the catalog, and the box has to say so
    with the reader nowhere near it and no signal of its own to be told by.
    """
    assert window.open_document(book)
    window.view.goto_page(2)
    settle(window)
    assert window.page_spin.text() == "Page 1"
    window._delete_pages((0,))
    settle(window)
    assert window.view.session.doc.page_count == PAGES - 1
    assert window.page_spin.value() == 2
    assert window.page_spin.text() == "Page 1", "the sheet slid, its name did not"


def test_the_undo_of_a_delete_puts_the_names_back(window, book):
    """The session hands back a *cloned* document, so nothing here may hold a pointer.

    ``DocumentSession`` swaps ``self.doc`` on undo, and the box reads through the source
    it was given rather than the document it last saw — which is the difference between
    a name and an exception on a closed file.
    """
    assert window.open_document(book)
    window.view.goto_page(2)
    settle(window)
    live = window.view.session.doc
    assert window.page_spin.text() == "Page 1"
    window._delete_pages((0,))
    settle(window)
    window._undo_edit()
    settle(window)
    assert window.view.session.doc is not live, "the undo really swapped the document"
    assert window.view.session.doc.page_count == PAGES
    assert window.page_spin.text() == "Page 1"


def test_a_plain_book_is_shown_exactly_as_it_was(window, plain):
    assert window.open_document(plain)
    window.view.goto_page(3)
    settle(window)
    assert window.page_spin.text() == "Page 4"
    assert window.page_spin.value() == 4
    assert window.page_label.text() == f"/ {PAGES}"
    assert window.page_spin.toolTip() == ""


def test_the_count_beside_the_box_stays_a_count(window, book):
    """The name belongs to one sheet; the total belongs to the book.

    ``/ 6`` is nobody's name and no numbering changes it, so the one widget that is
    positional on purpose is pinned as positional on purpose.
    """
    assert window.open_document(book)
    window.view.goto_page(0)
    settle(window)
    assert window.page_spin.text() == "Page i"
    assert window.page_label.text() == f"/ {PAGES}"


def test_typing_a_name_in_the_toolbar_moves_the_reader(window, book):
    assert window.open_document(book)
    window.view.goto_page(0)
    settle(window)
    type_into(window.page_spin, "3")
    assert window.page_spin.value() == 5
    assert window.view.current_page == 4, "the body's third sheet, where '3' is written"
    assert window.page_spin.text() == "Page 3"


def test_closing_the_book_leaves_no_name_behind(window, book, plain):
    assert window.open_document(book)
    window.view.goto_page(1)
    settle(window)
    assert window.page_spin.text() == "Page ii"
    window._close_all_tabs(confirm=False)
    settle(window)
    assert window.page_spin.text() == "Page 1"
    assert window.page_spin.toolTip() == ""
    assert window.open_document(plain)
    window.view.goto_page(1)
    settle(window)
    assert window.page_spin.text() == "Page 2"


def test_a_numbering_that_starts_past_the_front_shows_a_position_in_the_toolbar(
        window, front):
    """F10-4w's reader-visible half, in the widget the reader actually looks at."""
    assert window.open_document(front)
    window.view.goto_page(0)
    settle(window)
    assert window.page_spin.text() == "Page 1"
    window.view.goto_page(4)
    settle(window)
    assert window.page_spin.text() == "Page 40"
    assert window.page_spin.toolTip() == "Sheet 5 of 6, called 40"


def test_a_second_book_cannot_inherit_the_first_names(window, book, plain):
    """The box asks which document is open every time, on the tab it is looking at."""
    assert window.open_document(book)
    window.view.goto_page(1)
    settle(window)
    assert window.page_spin.text() == "Page ii"
    assert window.open_document(plain)
    settle(window)
    assert window.page_spin.text() == "Page 1"
    window.view.goto_page(2)
    settle(window)
    assert window.page_spin.text() == "Page 3"
    window.tabs.setCurrentIndex(0)
    settle(window)
    page = window.view.current_page
    assert window.page_spin.value() == page + 1
    assert window.page_spin.text() == f"Page {ROMAN_THEN_ARABIC[page]}", \
        "the first book is the one being read, so it is the first book being named"


# ---------------------------------------------------------------- the status line


def test_the_status_line_names_the_sheet_the_box_names(window, book):
    """Two widgets, one fact: the status line is on screen beside the toolbar.

    It prints the reading position with the zoom, and a window that said ``Page 1`` up
    top and ``Page 3/6`` at the bottom would be arguing with itself about the sheet the
    reader is on.  Okular's minibar does the same substitution
    (``part/minibar.cpp:90-106``).
    """
    assert window.open_document(book)
    window.view.goto_page(2)
    settle(window)
    assert window.page_spin.text() == "Page 1"
    assert window.status_right.text().startswith("Page 1/6")


def test_the_status_line_keeps_the_books_count_after_the_slash(window, front):
    """``Page 40/6``: the name is the sheet's, the six is the book's.

    The numbering says nothing about the front sheet and calls the fifth ``40``, so this
    is both halves of **F10-4w** and the count in one sentence.
    """
    assert window.open_document(front)
    window.view.goto_page(4)
    settle(window)
    assert window.status_right.text().startswith("Page 40/6")


def test_a_plain_books_status_line_says_exactly_what_it_said_before(window, plain):
    assert window.open_document(plain)
    window.view.goto_page(3)
    settle(window)
    assert window.status_right.text().startswith("Page 4/6")


def test_the_reflow_line_names_the_sheet_too(window, book):
    """The branch that prints the text size instead of the zoom says it the same way.

    ``reflow_mode`` is set rather than entered: the reflow *worker* is an async render of
    the text and nothing about this sentence depends on it, while asking for it would
    make the test wait on a thread to check one string.
    """
    assert window.open_document(book)
    window.view.goto_page(2)
    settle(window)
    window.view.reflow_mode = True
    try:
        window._update_status()
        assert window.status_right.text().startswith("Page 1/6")
        assert "Reflow" in window.status_right.text()
    finally:
        window.view.reflow_mode = False


# ------------------------------------------------------------- F10-4bc
# The page box is sized to the longest label the document can produce, rather than
# to a constant.  A fixed width clipped long names — halving the width to 48 px left
# the whole suite green, which is how the constant was discovered — and this step
# sizes it the way Okular sizes its location bar (part/minibar.cpp:99-108).


def test_the_page_box_is_wide_enough_for_the_longest_label(window, book, tmp_path):
    """A long prefix forces the box wider than the 96 px it started at.

    ``/P (Appendix A-1) >>`` names every sheet ``Appendix A-1``, so the box shows
    ``Page Appendix A-1`` and must be wider than the 96 px it was sized to before
    F10-4bc.  The fixture is rewritten in place, so this also exercises the width
    being applied when the document the reader is on changes.
    """
    from PyQt6.QtGui import QFontMetrics
    src = book
    dst = str(tmp_path / "appendix.pdf")
    import shutil
    shutil.copy(src, dst)
    doc = fitz.open(dst)
    doc.set_page_labels([{"startpage": 0, "prefix": "Appendix A-1",
                          "firstpagenum": 0, "count": PAGES}])
    # Save to a different path to avoid "save to original must be incremental"
    out = str(tmp_path / "appendix_out.pdf")
    doc.save(out)
    doc.close()
    assert window.open_document(out)
    settle(window)

    fm = QFontMetrics(window.page_spin.font())
    longest = longest_label(fitz.open(out), font_metrics=fm)
    expected = window._page_box_width_for(longest)
    assert window.page_spin.width() >= expected
    assert window.page_spin.width() > 96
    assert window.page_spin.text() == f"Page {longest}"


def test_a_plain_book_keeps_the_default_width(window, plain):
    """A book that numbers by position leaves the box at its default width."""
    assert window.open_document(plain)
    settle(window)
    assert window.page_spin.width() == 96, "no numbering means no resize"


def test_a_numbered_book_shrinks_back_on_close(window, book, tmp_path):
    """Closing a long-label book returns the box to its default width."""
    from PyQt6.QtGui import QFontMetrics
    src = book
    dst = str(tmp_path / "appendix.pdf")
    import shutil
    shutil.copy(src, dst)
    doc = fitz.open(dst)
    doc.set_page_labels([{"startpage": 0, "prefix": "Appendix A-1",
                          "firstpagenum": 0, "count": PAGES}])
    out = str(tmp_path / "appendix_out.pdf")
    doc.save(out)
    doc.close()
    assert window.open_document(out)
    settle(window)

    fm = QFontMetrics(window.page_spin.font())
    longest = longest_label(fitz.open(out), font_metrics=fm)
    assert window.page_spin.width() > 96
    assert window.page_spin.width() >= window._page_box_width_for(longest)
    window._close_all_tabs(confirm=False)
    settle(window)
    assert window.page_spin.width() == 96


def test_the_page_box_wider_for_appendix_than_for_body(window, book, plain, tmp_path):
    """The width follows the document in front of the reader: two books, two widths.

    Open a long-prefix book, then a plain one, then the long-prefix book again: the
    box must grow for the appendix and shrink back for the plain book, because the
    width is re-derived for whichever document the reader is on.
    """
    from PyQt6.QtGui import QFontMetrics
    src = book
    dst = str(tmp_path / "appendix.pdf")
    import shutil
    shutil.copy(src, dst)
    doc = fitz.open(dst)
    doc.set_page_labels([{"startpage": 0, "prefix": "Appendix A-1",
                          "firstpagenum": 0, "count": PAGES}])
    out = str(tmp_path / "appendix_out.pdf")
    doc.save(out)
    doc.close()
    assert window.open_document(out)
    settle(window)

    fm = QFontMetrics(window.page_spin.font())
    longest = longest_label(fitz.open(out), font_metrics=fm)
    appendix_width = window.page_spin.width()
    assert appendix_width > 96
    assert appendix_width >= window._page_box_width_for(longest)

    assert window.open_document(plain)
    settle(window)
    assert window.page_spin.width() == 96

    window._close_all_tabs(confirm=False)
    assert window.open_document(out)
    settle(window)
    assert window.page_spin.width() == appendix_width
