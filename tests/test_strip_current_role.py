"""F10-4k: the strip says "you are here" and "you picked these" through two channels.

``QListWidget.setCurrentItem(item)`` selects as it makes the row current, and the
delegate paints only ``State_Selected``, so for four steps the strip had one way to
show anything: the row the view is on was *also* a picked page.  F10-4h stopped the
worst of it by marking with ``Current | NoUpdate`` while a pick existed -- which is a
conditional, not a fix.  Two measured shapes survive it:

* with nothing picked, the reader's own row is a pick, so a right-click on blank
  space below the last thumbnail -- a request that names no row at all -- answers
  with the page the view happens to be on, and **Delete Page** is offered for it;
* a pick whose sheets have all gone out reads as a one-row pick, which is the lie
  F10-4h's test had to write down instead of asserting
  (``test_a_pick_whose_pages_all_went_is_not_carried_onto_their_neighbours``,
  ``tests/test_reading_position.py:369``).

The split is a paint role: the mark lives on the row as item data, the delegate draws
it as a frame rather than a fill, and the selection model is never touched.  A marking
then cannot edit a pick because it has no channel into one, and ``selected_pages()``
answers what the reader gathered -- which may be nothing.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from conftest import build_sample_pdf, make_session
from PyQt6.QtCore import QModelIndex, QPoint, QRect, Qt
from PyQt6.QtGui import QFontMetrics, QImage, QPainter, QPalette
from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem

from pdfar.edits import Scope
from pdfar.pages import DeletePages
from pdfar.render import RenderPool
from pdfar.sidebar import CURRENT_ROLE, Sidebar

#: Five self-naming sheets, so a row can be asked which page it is on.
PAGES = 5


@pytest.fixture
def strip(qapp, tmp_path):
    """A sidebar over a five-page book, shown so the strip has real geometry."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=PAGES)
    session = make_session(path)
    pool = RenderPool(session, workers=1)
    bar = Sidebar(session, pool)
    bar.show()
    qapp.processEvents()
    try:
        yield bar
    finally:
        pool.shutdown()
        bar.close()
        bar.deleteLater()
        session.close()
        qapp.processEvents()


def marked(bar) -> tuple:
    """Every row the strip paints as "you are here", read off the rows themselves."""
    return tuple(index for index in range(bar.thumbs.count())
                 if bar.thumbs.item(index).data(CURRENT_ROLE) is not None)


def picked(bar) -> tuple:
    """What the reader gathered -- the only thing ``selected_pages`` may answer."""
    return bar.selected_pages()


def center_of(bar, row: int) -> QPoint:
    """A point inside *row*'s thumbnail, in the strip's own coordinates."""
    rect = bar.thumbs.visualItemRect(bar.thumbs.item(row))
    assert rect.isValid(), "the strip was never laid out"
    return rect.center()


def below_the_last_row(bar) -> QPoint:
    """Blank space past the end of the strip: a request that names no row."""
    last = bar.thumbs.count() - 1
    rect = bar.thumbs.visualItemRect(bar.thumbs.item(last))
    return QPoint(rect.center().x(), rect.bottom() + 60)


def paint(bar, row: int):
    """One row as the delegate draws it, on an image nobody has to look at.

    The option is built the way the view builds one, from the widget, so the strip
    is never asked to be on screen for its own painting to be judged.
    """
    item = bar.thumbs.item(row)
    option = QStyleOptionViewItem()
    option.initFrom(bar.thumbs)
    option.rect = QRect(QPoint(0, 0), bar.thumbs.visualItemRect(item).size())
    option.fontMetrics = QFontMetrics(bar.thumbs.font())
    if bar.thumbs.selectionModel().isRowSelected(row, QModelIndex()):
        option.state |= QStyle.StateFlag.State_Selected
    image = QImage(option.rect.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    bar.thumbs.delegate.paint(painter, option, bar.thumbs.indexFromItem(item))
    painter.end()
    return image


def highlight(bar):
    return bar.thumbs.palette().color(QPalette.ColorRole.Highlight)


def painted_here(bar, image, where: QPoint) -> bool:
    """Whether the highlight colour is on this pixel of a painted row."""
    return image.pixelColor(where).name() == highlight(bar).name()


def frame_pixel(image):
    """The left edge of the row, mid-height: the frame, and nothing else, is here.

    The thumbnail is centred in a wider row, so the strip's left margin is free of
    it whatever the sheet looks like, and the frame is the only thing drawn there.
    """
    box = image.rect()
    return QPoint(box.left() + 1, box.center().y())


def fill_pixel(image):
    """A band above the thumbnail and inside the row: a fill reaches here.

    The frame is two pixels wide and hugs the row, the thumbnail starts below this
    line and the label ends above it, so this is the fill or nothing.
    """
    box = image.rect()
    return QPoint(box.center().x(), box.top() + 4)


def delete(bar, pages) -> None:
    """Take *pages* out, the way the command does: edit, then rebuild the strip."""
    edit = DeletePages(pages=tuple(pages))
    bar.session.apply(edit)
    bar.invalidate_thumbnails(bar.session.version, int(Scope.STRUCTURE))


# ------------------------------------------------------- a mark is not a pick
def test_marking_a_row_picks_nothing(strip):
    """The whole of the split, in one line."""
    strip.set_current_page(2)
    assert picked(strip) == ()


def test_the_mark_lands_on_the_row_the_view_is_on(strip):
    strip.set_current_page(2)
    assert marked(strip) == (2,)


def test_a_marking_leaves_a_gathered_pick_alone(strip):
    """F10-4h's conditional, without the condition."""
    strip.select_pages((1, 3))
    strip.set_current_page(4)
    assert picked(strip) == (1, 3)
    assert marked(strip) == (4,)


def test_only_one_row_is_ever_here(strip):
    strip.set_current_page(1)
    strip.set_current_page(3)
    assert marked(strip) == (3,)


def test_a_mark_can_share_its_row_with_a_pick(strip):
    """Two channels, two answers, one row: both are true at once."""
    strip.select_pages((2,))
    strip.set_current_page(2)
    assert picked(strip) == (2,)
    assert marked(strip) == (2,)


def test_the_mark_moves_the_current_row_without_selecting_it(strip):
    """Keyboard and scroll still follow the view; only the pick is off limits."""
    strip.set_current_page(3)
    assert strip.thumbs.currentRow() == 3
    assert picked(strip) == ()


def test_a_page_outside_the_book_marks_nothing(strip):
    """A page with no row is not remembered, not even for a later, longer book."""
    strip.set_current_page(-1)
    strip.set_current_page(PAGES)
    assert marked(strip) == ()
    assert picked(strip) == ()
    assert strip._thumb_page is None, "the strip keeps no page it cannot show"


def test_marking_the_row_it_is_already_on_changes_nothing(strip):
    strip.set_current_page(2)
    strip.select_pages((0, 2))
    strip.set_current_page(2)
    assert marked(strip) == (2,)
    assert picked(strip) == (0, 2)


# ------------------------------------------------------ what a request answers
def test_a_request_on_a_row_names_that_row_and_not_the_reading_place(strip):
    strip.set_current_page(4)
    assert strip._picked_pages(center_of(strip, 1)) == (1,)


def test_a_request_on_blank_space_names_nothing_at_all(strip):
    """The harm, exactly: today the reader's own row is the pick it offers to cut.

    Below the last thumbnail there is no row, so there is no answer -- and a menu
    built on the page the view happens to be on is a **Delete Page** for a sheet the
    reader never pointed at.
    """
    strip.set_current_page(4)
    assert strip._picked_pages(below_the_last_row(strip)) == ()


def test_a_request_offers_the_pick_and_not_the_mark(strip):
    strip.select_pages((1, 3))
    strip.set_current_page(0)
    assert strip._picked_pages(center_of(strip, 4)) == (1, 3)


# ------------------------------------------------------------- through an edit
def test_a_pick_that_loses_every_sheet_it_named_is_no_pick(strip):
    """The assertion F10-4h could not make.

    A selection may shrink to nothing; a reader may not be nowhere.  With the two
    channels split, an emptied pick answers as what it is.
    """
    strip.select_pages((1, 2, 3))
    strip.set_current_page(0)
    delete(strip, (1, 2, 3))
    assert picked(strip) == ()
    assert marked(strip) == (0,), "the reader is still on the sheet they were on"


def test_the_mark_survives_a_rebuild_that_moved_no_sheets(strip):
    """A turn rebuilds the strip and renumbers nothing: the row stays the row."""
    strip.set_current_page(3)
    strip.invalidate_thumbnails(2, int(Scope.STRUCTURE))
    assert marked(strip) == (3,)


def test_the_mark_is_put_back_when_the_strip_is_rebuilt(strip):
    """``thumbs.clear()`` destroys the rows, and the mark with them."""
    strip.select_pages((2,))
    strip.set_current_page(1)
    strip.invalidate_thumbnails(3, int(Scope.STRUCTURE))
    assert marked(strip) == (1,)
    assert picked(strip) == (2,)


def test_a_rebuild_to_a_shorter_book_leaves_the_mark_for_the_view_to_place(strip):
    """A remembered page that no longer has a row is not marked on a guess.

    The strip does not know where the reader landed -- the view does, and says so
    through ``set_current_page`` a moment later.  Until then nothing is "here",
    which is a missing mark rather than a wrong one.
    """
    strip.set_current_page(PAGES - 1)
    delete(strip, (PAGES - 2, PAGES - 1))
    assert strip.thumbs.count() == PAGES - 2
    assert marked(strip) == ()


# ------------------------------------------------------------------- the paint
def test_the_mark_is_painted(strip):
    """An invisible mark is the bug this whole step exists to avoid.

    The frame is drawn from the row, not from the thumbnail, so it is there before
    the sheet has rendered -- which is every row of a book that has just opened.
    """
    strip.set_current_page(1)
    image = paint(strip, 1)
    assert painted_here(strip, image, frame_pixel(image)), "the row is here"
    assert not painted_here(strip, image, fill_pixel(image)), \
        "a mark is a frame, not a fill"


def test_an_ordinary_row_is_painted_with_nothing_on_it(strip):
    image = paint(strip, 2)
    assert not painted_here(strip, image, frame_pixel(image))
    assert not painted_here(strip, image, fill_pixel(image))


def test_a_pick_is_still_painted_as_a_fill(strip):
    """A pick is a band of colour; the mark, below, is its edge."""
    strip.select_pages((2,))
    image = paint(strip, 2)
    assert painted_here(strip, image, fill_pixel(image))


def test_a_pick_and_a_mark_do_not_look_alike(strip):
    """The reader can tell which is which from the strip, without counting rows.

    Same colour, different shape: a filled band is a gathered page, an outlined one
    is where they are reading.
    """
    strip.select_pages((2,))
    picked_image = paint(strip, 2)
    strip.thumbs.clearSelection()
    strip.set_current_page(2)
    marked_image = paint(strip, 2)
    assert picked_image != marked_image


def test_a_row_that_is_picked_and_here_shows_both(strip):
    strip.select_pages((2,))
    strip.set_current_page(2)
    image = paint(strip, 2)
    assert painted_here(strip, image, fill_pixel(image)), "the pick"
    assert painted_here(strip, image, frame_pixel(image)), "the mark"
