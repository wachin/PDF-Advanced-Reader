"""F10-4j: a comments file says which edition of the document it was written for.

An XFDF names every mark's page by *number* (`src/pdfar/xfdf.py:283`), and the
sidecar is named from the PDF's own path (`xfdf.py:1265`), so the file that sits
beside ``report.pdf`` is the one the last export wrote and the one the next import
reads.  Neither half knows how many pages the other was made of.

The harm has a shape: take two pages out of a six-page document, export the
comments again -- which overwrites the file that held the six-page numbering --
and an import of any older copy is a straight lie.  The marks that named pages 5
and 6 are left out with a reason that is true but incomplete
(`xfdf.py:879`, "names a page this document does not have"), and the marks that
named pages 1 to 4 arrive, quietly, on the sheets those numbers reach now.
Nothing in the file says the document it describes was longer, and nothing in the
session can reconstruct it: a delete's renumbering lives on the step, and the
history a reader has undone past is not in the file either.

So the export records the count it was written against, in PDFAR's own namespace
on the root element, where a conforming reader treats it as somebody else's
business and an older PDFAR ignores an attribute it never asked for.  The import
compares it with the document in hand and says what disagrees -- not a refusal,
because a file about a longer book still carries real comments, and not silence,
because a mark that lands on the wrong sheet looks exactly like a mark that was
always meant to be there.

A file that does not say is not held to it: Acrobat, Okular and every PDFAR
export before this step write no count, and an absent number is not evidence of a
disagreement.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import xml.etree.ElementTree as ET

import pytest
from PyQt6.QtWidgets import QMessageBox

from conftest import build_sample_pdf, make_session
from pdfar import xfdf
from pdfar.annotations import AnnotationManager
from pdfar.main import MainWindow
from pdfar.xfdf import export_xfdf, page_anchors, parse_xfdf
from test_xfdf_import import HEIGHT, envelope, read
from test_xfdf_ui import _open, _save_as

#: The attribute the export writes and the import reads, as a parser sees it.
PAGES = f"{{{xfdf.PDFAR_NAMESPACE}}}pages"

#: One note, on the page the file names.
NOTE = ('<text page="{page}" name="{name}" '
        f'rect="10,{HEIGHT - 26},26,{HEIGHT - 10}"/>')


# The window and the dialog recorder are test_xfdf_ui's, but a fixture imported
# from another test module is a name its tests re-declare as parameters, which
# reads as a redefinition; these two are scaffolding, so they are repeated here.
@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    w._close_all_tabs(confirm=False)
    w.close()
    w.deleteLater()
    qapp.processEvents()


@pytest.fixture(autouse=True)
def seen(monkeypatch):
    """Capture the dialogs a modal test run must never actually show."""
    seen = []
    for name in ("critical", "warning", "information", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: seen.append(a[2])))
    return seen


@pytest.fixture
def book(tmp_path):
    """Managers over documents of a chosen page count, all closed afterwards."""
    made = []

    def build(pages: int, name: str = "") -> AnnotationManager:
        path = os.path.join(str(tmp_path), name or f"book{pages}.pdf")
        session = make_session(build_sample_pdf(path, pages=pages))
        manager = AnnotationManager(session)
        made.append(manager)
        return manager

    yield build
    for manager in made:
        manager.session.close()


def sidecar(tmp_path, name: str, *marks: str, pages=None) -> str:
    """A comments file that says it was written against *pages* pages.

    *pages* travels as text, so a file can be written saying ``pages="many"`` as
    easily as ``pages="6"``.
    """
    path = os.path.join(str(tmp_path), name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(envelope(*marks, pages=pages))
    return path


def note(page: int, name: str) -> str:
    return NOTE.format(page=page, name=name)


# ------------------------------------------------------------- what it writes
def test_the_file_says_how_many_pages_it_was_written_against(book):
    made = book(3)
    made.add_note(0, (30.0, 30.0), "one note")
    assert ET.fromstring(export_xfdf(made)).get(PAGES) == "3"


def test_the_count_is_the_document_not_the_marks_it_carries(book):
    """One note on page 1 of an eight-page book still says eight.

    The number is the shape of the document the page numbers in the file count
    against, so it has nothing to do with how many marks were written or where.
    """
    made = book(8)
    made.add_note(0, (30.0, 30.0), "one note")
    assert ET.fromstring(export_xfdf(made)).get(PAGES) == "8"


def test_a_one_page_document_says_one_and_not_nothing(book):
    """``pages="1"`` is a claim; the absence of the attribute is not."""
    made = book(1)
    assert ET.fromstring(export_xfdf(made)).get(PAGES) == "1"


def test_an_empty_document_still_says_what_it_held(book):
    """A file with no marks is a statement about a document, not a shrug."""
    made = book(5)
    text = export_xfdf(made)
    assert text.count("pdfar:pages") == 1
    assert ET.fromstring(text).get(PAGES) == "5"


def test_the_count_is_a_function_of_the_file_like_everything_else(book):
    """The rule the envelope already keeps: no timestamp, so a second export of
    the same document is the same file, and a sidecar stays diffable."""
    made = book(4)
    made.add_note(1, (30.0, 30.0), "one note")
    assert export_xfdf(made) == export_xfdf(made)


def test_a_file_the_export_wrote_imports_without_a_word(book):
    """The round trip is the common case, and it must stay silent.

    A notice that fires on a matching document teaches the reader to dismiss it,
    which is worse than no notice at all.
    """
    made = book(3, "there.pdf")
    made.add_note(2, (30.0, 30.0), "on the last page")
    here = book(3, "here.pdf")
    plan = read(export_xfdf(made), here.doc)
    assert plan.count == 1
    assert plan.written_against == 3
    assert plan.pages_disagree is False
    assert plan.page_notice() == ""


# ------------------------------------------------------------- what it reads
def test_a_file_written_for_a_longer_book_says_so(book):
    """Six pages in the file, three in hand: the two do not agree."""
    here = book(3)
    plan = read(envelope(note(0, "a"), pages="6"), here.doc)
    assert plan.written_against == 6
    assert plan.pages_disagree is True
    notice = plan.page_notice()
    assert "6 pages" in notice and "3 pages" in notice, notice


def test_a_file_written_for_a_shorter_book_says_so_too(book):
    """Pages added since the file was written move nothing, but still disagree.

    The import cannot tell which of the two happened, and a mark on a sheet that
    did move is as wrong in one direction as the other.
    """
    here = book(6)
    plan = read(envelope(note(0, "a"), pages="3"), here.doc)
    assert plan.pages_disagree is True
    assert "3 pages" in plan.page_notice()
    assert "6 pages" in plan.page_notice()


def test_a_file_that_says_the_count_it_matches_says_nothing(book):
    here = book(3)
    plan = read(envelope(note(0, "a"), pages="3"), here.doc)
    assert plan.written_against == 3
    assert plan.pages_disagree is False
    assert plan.page_notice() == ""


def test_a_file_from_another_reader_says_nothing_about_pages(book):
    """No count in the file, so no claim to check and nothing to report.

    Acrobat, Okular and every PDFAR export before this step land here.  The marks
    still come in; only the sentence is missing, because a number nobody wrote is
    not evidence of a disagreement.
    """
    here = book(3)
    plan = read(envelope(note(0, "a")), here.doc)
    assert plan.count == 1
    assert plan.written_against is None
    assert plan.pages_disagree is False
    assert plan.page_notice() == ""


@pytest.mark.parametrize("written", ["many", "-2", "3.5", "", "  ", "0x4", "1e2.5"])
def test_a_count_that_is_not_a_count_is_no_evidence(book, written):
    """A value PDFAR would not have written is treated as one nobody wrote.

    The file is still read: an attribute that cannot be a page count says nothing
    about this document, and it is no business of the parser to refuse a file
    for it.
    """
    here = book(3)
    plan = read(envelope(note(0, "a"), pages=written), here.doc)
    assert plan.count == 1, plan.skipped
    assert plan.written_against is None
    assert plan.pages_disagree is False


def test_the_count_is_read_in_pdfars_namespace_only(book):
    """An unprefixed ``pages`` is some other vendor's attribute.

    The same rule every other PDFAR extra follows (`xfdf.py:624`), and the mirror
    image of the mistake the export guards: trusting a bare ``pages`` would read
    somebody else's number as a claim about this document.
    """
    here = book(3)
    text = envelope(note(0, "a")).replace('<xfdf ', '<xfdf pages="6" ', 1)
    assert ' pages="6"' in text, "the attribute is unprefixed"
    plan = read(text, here.doc)
    assert plan.count == 1
    assert plan.written_against is None
    assert plan.pages_disagree is False


def test_zero_pages_is_a_claim_a_document_can_contradict(book):
    """``pages="0"`` is a number, and a three-page book is not zero pages.

    Not a value the export writes, but the one the guard above answers for: a
    count of zero is not the absence of a count.
    """
    here = book(3)
    plan = read(envelope(note(0, "a"), pages="0"), here.doc)
    assert plan.written_against == 0
    assert plan.pages_disagree is True


def test_the_disagreement_is_said_even_when_every_mark_arrives(book):
    """The silent half of the harm: nothing was left out, and that is the problem.

    A file about a six-page book whose marks all sit on pages 1 to 3 imports
    completely into a three-page document, and every one of them is on the sheet
    its number names now rather than the sheet it was made on.  The skipped list
    is empty, so a notice tied to that list would say nothing at all.
    """
    here = book(3)
    plan = read(envelope(note(0, "a"), note(2, "b"), pages="6"), here.doc)
    assert plan.count == 2, plan.skipped
    assert plan.skipped == ()
    assert plan.pages_disagree is True
    assert "sheet" in plan.page_notice()


def test_the_plan_carries_both_counts_without_a_document(book):
    """The parser stays pure: the count it compares to is the one it was given.

    ``len(anchors)`` is the document's page count by construction
    (`xfdf.py:1213`), and it is the same number the per-mark check at
    `xfdf.py:877` uses, so the notice and the skipped reasons cannot disagree
    about how big this document is.
    """
    here = book(3)
    anchors = page_anchors(here.doc)
    assert len(anchors) == 3
    plan = parse_xfdf(envelope(note(0, "a"), pages="6"), anchors)
    assert (plan.written_against, plan.document_pages) == (6, 3)


# --------------------------------------------------------------- in the window
def test_importing_an_older_sidecar_says_what_disagrees(
        win, monkeypatch, tmp_path, seen):
    """The box appears for the disagreement alone, with no mark left out.

    Before this step the window spoke only when something was skipped, so the
    case above -- every mark arriving on a sheet that moved -- passed in silence.
    """
    path = str(build_sample_pdf(tmp_path / "three.pdf", pages=3))
    assert win.open_document(path)
    tab = win._active_tab()
    _open(monkeypatch, sidecar(tmp_path, "old.xfdf",
                               note(0, "a"), note(2, "b"), pages="6"))

    assert win._import_tab_comments(tab) is True
    assert len(tab.view.annotation_manager.get_annotations()) == 2
    assert seen, "the reader is told"
    assert "6 pages" in seen[0] and "3 pages" in seen[0], seen[0]


def test_a_sidecar_that_matches_this_document_imports_without_a_word(
        win, monkeypatch, tmp_path, seen):
    path = str(build_sample_pdf(tmp_path / "three.pdf", pages=3))
    assert win.open_document(path)
    tab = win._active_tab()
    _open(monkeypatch, sidecar(tmp_path, "now.xfdf", note(0, "a"), pages="3"))

    assert win._import_tab_comments(tab) is True
    assert seen == []
    assert win.status_left.text().startswith("Imported 1 mark")


def test_a_foreign_sidecar_still_imports_without_a_word(
        win, monkeypatch, tmp_path, seen):
    """A file that says nothing about pages is not treated as a warning."""
    path = str(build_sample_pdf(tmp_path / "three.pdf", pages=3))
    assert win.open_document(path)
    tab = win._active_tab()
    _open(monkeypatch, sidecar(tmp_path, "acrobat.xfdf", note(0, "a")))

    assert win._import_tab_comments(tab) is True
    assert seen == []


def test_the_box_that_lists_marks_left_out_says_the_count_as_well(
        win, monkeypatch, tmp_path, seen):
    """One box, both halves: what did not arrive, and what may have moved."""
    path = str(build_sample_pdf(tmp_path / "three.pdf", pages=3))
    assert win.open_document(path)
    tab = win._active_tab()
    _open(monkeypatch, sidecar(tmp_path, "long.xfdf",
                               note(0, "here"), note(99, "there"),
                               pages="120"))

    assert win._import_tab_comments(tab) is True
    assert len(seen) == 1, seen
    text = seen[0]
    assert "120 pages" in text and "3 pages" in text, text
    assert "Left out:" in text, text
    assert "text on page 100" in text, text
    assert "names a page this document does not have" in text, text
    # The notice comes first: it is about every mark in the box, not one of them.
    assert text.index("120 pages") < text.index("Left out:")


def test_the_notice_survives_a_file_that_imported_nothing(
        win, monkeypatch, tmp_path, seen):
    """A file whose every mark is out of reach is still a file about another book.

    The empty plan is not applied, so this is the one case where the counts
    disagree and no mark lands anywhere -- and the reader still has to be told
    why the comments they expected are not on the page.
    """
    path = str(build_sample_pdf(tmp_path / "three.pdf", pages=3))
    assert win.open_document(path)
    tab = win._active_tab()
    _open(monkeypatch, sidecar(tmp_path, "elsewhere.xfdf",
                               note(99, "there"), pages="120"))

    assert win._import_tab_comments(tab) is True
    assert seen and "Nothing was imported" in seen[0], seen
    assert "120 pages" in seen[0]


# ------------------------------------------------------------ the real shape
def test_a_sidecar_written_before_a_delete_says_what_it_cannot_know(
        win, monkeypatch, tmp_path, seen):
    """The roadmap's case, end to end, through the two commands.

    Six notes exported from a six-page report, then the same report with two
    pages taken out and saved -- which is the file the reader opens next -- and
    the older sidecar imported into it.  The marks that named pages 5 and 6
    cannot come, and the four that do are on sheets that moved.  The window says
    both, in one box.
    """
    long_book = str(build_sample_pdf(tmp_path / "report.pdf", pages=6))
    assert win.open_document(long_book)
    tab = win._active_tab()
    made = tab.view.annotation_manager
    for page in range(6):
        made.add_note(page, (30.0, 30.0), f"note {page + 1}")

    out = str(tmp_path / "report.xfdf")
    _save_as(monkeypatch, out)
    assert win._export_tab_comments(tab) is True
    with open(out, encoding="utf-8") as handle:
        written = handle.read()
    assert 'pdfar:pages="6"' in written, written

    # The shortened report, as a document of its own: four pages, no marks yet,
    # so every name in the file is new and only the page numbers are stale.
    win._close_all_tabs(confirm=False)
    short_book = str(build_sample_pdf(tmp_path / "report-4.pdf", pages=4))
    assert win.open_document(short_book)
    tab = win._active_tab()
    assert tab.view.page_count == 4

    _open(monkeypatch, out)
    assert win._import_tab_comments(tab) is True
    arrived = [mark for mark in tab.view.annotation_manager.get_annotations()]
    assert len(arrived) == 4, [mark.page for mark in arrived]

    assert len(seen) == 1, seen
    text = seen[0]
    assert "6 pages" in text and "4 pages" in text, text
    # The two marks that named pages 5 and 6 are named as left out...
    assert "text on page 5" in text, text
    assert "text on page 6" in text, text
    # ...and the four that came in are said to be on sheets that may have moved.
    assert "moved" in text, text


def test_the_export_after_a_delete_is_the_cure_and_the_loss(
        win, monkeypatch, tmp_path, seen):
    """Re-exporting makes the notice go away, and keeps what made it.

    The sidecar keeps the document's name, not its edition, so the second write
    below replaces a six-page numbering with a four-page one and the import that
    follows is silent -- which is the right answer for the file that exists now.
    The older record is no longer lost with it: F10-4m copies a sidecar that does
    not describe this document aside before overwriting it, and the assertions
    under the seam below are what that leaves behind.  What this step settled is
    that the file that survives says how many pages it was written against.
    """
    long_book = str(build_sample_pdf(tmp_path / "report.pdf", pages=6))
    assert win.open_document(long_book)
    tab = win._active_tab()
    tab.view.annotation_manager.add_note(0, (30.0, 30.0), "note 1")
    out = str(tmp_path / "report.xfdf")
    _save_as(monkeypatch, out)
    assert win._export_tab_comments(tab) is True
    with open(out, encoding="utf-8") as handle:
        assert 'pdfar:pages="6"' in handle.read()

    short_book = str(build_sample_pdf(tmp_path / "report-4.pdf", pages=4))
    win._close_all_tabs(confirm=False)
    assert win.open_document(short_book)
    tab = win._active_tab()
    tab.view.annotation_manager.add_note(0, (30.0, 30.0), "the short one")
    _save_as(monkeypatch, out)
    assert win._export_tab_comments(tab) is True
    with open(out, encoding="utf-8") as handle:
        written = handle.read()
    assert 'pdfar:pages="4"' in written, written
    assert 'pdfar:pages="6"' not in written, written

    # ...and the numbering that went is readable beside it, whole.
    with open(str(tmp_path / "report.previous.xfdf"), encoding="utf-8") as handle:
        older = handle.read()
    assert 'pdfar:pages="6"' in older, older
    assert "note 1" in older, older

    # Opened again from disk: the mark the file describes was never saved, so
    # this document is empty and the sidecar that fits it says nothing.
    win._close_all_tabs(confirm=False)
    assert win.open_document(short_book)
    tab = win._active_tab()
    assert tab.view.annotation_manager.get_annotations() == []

    _open(monkeypatch, out)
    assert win._import_tab_comments(tab) is True
    assert len(tab.view.annotation_manager.get_annotations()) == 1
    assert seen == [], "the file describes the document in hand"
