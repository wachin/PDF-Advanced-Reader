"""F10-4i: the numbering range belongs to the reader, not to the book open now.

Page numbers go into a *copy* of the file, and the range to stamp is a
preference: `pageNumbers/startPage` and `pageNumbers/endPage` (and the
`pageNumbers/erase/` pair) are stored **globally**, with no document in the key,
because a reader sets the range once and numbers several books with it.
`page_range()` clamps that pair to the document in front of them at every use,
which is right.  The panel clamps the same pair silently and permanently, and in
the wrong direction: `set_page_count()` narrows each spin's range to the open
book, Qt folds the value down into it, and `settings()` reads the widget -- so
the widget that answers for "what the reader set" answers for "what the last
book was", and the copy path saves it straight back.

Three measured facts carry this.

* `QSpinBox.setRange(1, 8)` on a spin holding 100 leaves it holding **8**: the
  value is folded, not refused, and nothing says so.
* The fold does not come back.  `set_page_count()` restores the *start* from
  `self._start_page_value`, which only moves when the reader edits, but the
  *end* from `self.last_page_spin.value()` -- the already-folded widget -- so an
  explicit end of page 100 is 8 forever after one look at a short book, and
  `_save_numbered_copy` / `_save_erased_copy` write that 8 to the store.
* A document switch is not an edit the reader made: `_apply_numbering_preview`
  runs `set_page_count()` on every tab change with `_updating` set, so no signal
  marks it as one -- and the answer the reader is shown, the label under the
  spins, already asks `page_range()` and so already tells the clamped truth.

So the panel keeps two answers and gets the second one right: the boxes stay
inside the book in hand, because a spin whose maximum is the page count is what
stops a reader typing page 100 of 8, and the range the reader set is the
panel's own, reported by `settings()` and `erase_settings()` and saved as it
was.  Clamping stays where it already was -- at use.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf
from pdfar import APP_NAME
from pdfar.main import MainWindow
from pdfar.page_numbers import (
    EraseSettings, PageNumbersPanel, PageNumberSettings, page_range,
)
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QFileDialog, QMessageBox

#: A long book and a short one: the pair the range is set in one and folded by.
WIDE = 120
SHORT = 8


def _settings(**overrides) -> PageNumberSettings:
    values = dict(position="bottom-center", format="number/total",
                  font_family="helv", font_size=12, margin_x=20,
                  margin_y=20, start_page=1, end_page=0, start_number=1,
                  mask_left=4, mask_right=4, mask_top=4, mask_bottom=4,
                  mask_color="#ffffff")
    values.update(overrides)
    return PageNumberSettings(**values)


def _panel(start: int = 10, end: int = 100,
           pages: int = WIDE) -> PageNumbersPanel:
    """A panel on a long book with an explicit range the reader typed."""
    return PageNumbersPanel(pages, _settings(start_page=start, end_page=end))


def asked(settings) -> tuple:
    """The pair the panel says the reader set."""
    return (settings.start_page, settings.end_page)


@pytest.fixture
def store(qapp):
    """An empty preference page, so nothing else can be read back as ours."""
    handle = QSettings(APP_NAME, APP_NAME)
    handle.remove("pageNumbers")
    try:
        yield handle
    finally:
        handle.remove("pageNumbers")


# ----------------------------------------------------- the range outlives the book
def test_a_shorter_book_does_not_narrow_the_range_the_reader_set(qapp):
    """Page 10 to page 100 of a 120-page book is the reader's answer, still."""
    panel = _panel()
    assert asked(panel.settings()) == (10, 100)

    panel.set_page_count(SHORT)
    assert asked(panel.settings()) == (10, 100), "the book moved, the reader did not"


def test_the_boxes_still_stop_at_the_book_in_hand(qapp):
    """The display is the document: a reader cannot type page 100 of eight.

    What `set_page_count` is for stays exactly as it was -- only what
    `settings()` reports about it changes.
    """
    panel = _panel()
    panel.set_page_count(SHORT)
    assert panel.first_page_spin.maximum() == SHORT
    assert panel.last_page_spin.maximum() == SHORT
    assert (panel.first_page_spin.value(), panel.last_page_spin.value()) == (8, 8)


def test_the_range_a_copy_is_stamped_with_is_clamped_where_it_is_used(qapp):
    """The clamping rule the panel already follows, kept where it belongs."""
    panel = _panel()
    panel.set_page_count(SHORT)
    assert page_range(panel.settings(), SHORT) == (7, 7)
    assert page_range(panel.settings(), WIDE) == (9, 99)


def test_the_range_comes_back_when_the_book_does(qapp):
    """A fold that cannot be undone is a lost preference, not a clamp."""
    panel = _panel()
    panel.set_page_count(SHORT)
    panel.set_page_count(WIDE)
    assert (panel.first_page_spin.value(), panel.last_page_spin.value()) == (10, 100)
    assert asked(panel.settings()) == (10, 100)


def test_closing_the_book_does_not_cost_the_range(qapp):
    """`set_page_count(0)` is a document gone, not a reader asking for page 1."""
    panel = _panel()
    panel.set_page_count(0)
    assert asked(panel.settings()) == (10, 100)
    panel.set_page_count(WIDE)
    assert (panel.first_page_spin.value(), panel.last_page_spin.value()) == (10, 100)


def test_the_label_promises_what_this_book_will_get(qapp):
    """The one place the clamp is shown, and the reason a hidden fold is wrong."""
    panel = _panel()
    panel.set_page_count(SHORT)
    assert panel.range_label.text().startswith("Page 8 ")


def test_a_document_switch_is_not_an_edit_the_reader_made(qapp):
    """No range changed, so no preview is rebuilt -- the fold emitted one.

    `set_page_count` emits when its own view of the settings moved; a clamp of
    the display moves nothing the reader set, and the window pushes the preview
    on the tab change anyway.
    """
    emitted = []
    panel = _panel()
    panel.settings_changed.connect(emitted.append)
    panel.set_page_count(SHORT)
    assert emitted == []


# ---------------------------------------------------------- the store is the harm
def test_a_short_book_saves_a_short_range_for_every_book(qapp, store):
    """The route the preference is lost on: the copy path writes what the panel said."""
    panel = _panel()
    panel.set_page_count(SHORT)
    panel.settings().save()
    assert asked(PageNumberSettings.load()) == (10, 100)


def test_the_erase_copy_keeps_the_range_too(qapp, store):
    """The erase tab shares the two boxes, so it shares the fold -- and the fix."""
    panel = _panel()
    panel.set_page_count(SHORT)
    panel.erase_settings().save()
    assert asked(EraseSettings.load()) == (10, 100)


def test_changing_anything_else_does_not_adopt_the_fold(qapp):
    """A font size is not a page number, and must not be read as one.

    The boxes still show this book's pages; the answer the panel keeps is only
    the one the reader left in a *page* box.
    """
    panel = _panel()
    panel.set_page_count(SHORT)
    panel.size_spin.setValue(18)
    panel.margin_x_spin.setValue(48)
    panel.unit_combo.setCurrentIndex(panel.unit_combo.findData("mm"))
    assert asked(panel.settings()) == (10, 100)
    assert panel.settings().font_size == 18


def test_a_new_panel_opens_with_the_range_the_store_holds(qapp, store):
    """The other half of the round trip: a preference survives the app restarting.

    Reopen the short book with the wide number still in the store and the panel
    reads (10, 100) again, showing 8.
    """
    panel = _panel()
    panel.set_page_count(SHORT)
    panel.settings().save()

    reopened = PageNumbersPanel(
        SHORT, PageNumberSettings.load(), EraseSettings.load())
    assert asked(reopened.settings()) == (10, 100)
    assert (reopened.first_page_spin.value(),
            reopened.last_page_spin.value()) == (8, 8)
    reopened.set_page_count(WIDE)
    assert (reopened.first_page_spin.value(),
            reopened.last_page_spin.value()) == (10, 100)


def test_the_erase_answer_follows_the_range_it_was_handed(qapp):
    """Both `apply_*` routes keep one rule: what the panel is told is its answer.

    A drag hands back what the panel already said, so this is the case today's
    only caller never produces — it is here because the erase half of the range
    is written the same way the numbering half is, and must mean it.
    """
    panel = _panel()
    panel.apply_erase_settings(EraseSettings(
        position="top-left", width=80, height=20, margin_x=10, margin_y=10,
        start_page=3, end_page=4))
    assert asked(panel.erase_settings()) == (3, 4)
    panel.set_page_count(SHORT)
    assert asked(panel.erase_settings()) == (3, 4)


def test_a_range_set_in_this_book_is_the_range_kept(qapp, store):
    """The reader's own edit wins, in this document and in the next.

    Authority is not stubbornness: page 5 of eight typed by hand is what the
    reader meant, and a wider book must not reach back to the 100 they set in
    the other one.
    """
    panel = _panel(start=1, end=WIDE)
    panel.set_page_count(SHORT)
    panel.last_page_spin.setValue(5)
    assert asked(panel.settings()) == (1, 5)
    panel.set_page_count(WIDE)
    assert asked(panel.settings()) == (1, 5)
    panel.settings().save()
    assert asked(PageNumberSettings.load()) == (1, 5)


def test_lowering_the_end_below_the_start_moves_the_start_with_it(qapp):
    """Keeping the boxes ordered has to keep the remembered pair ordered too."""
    panel = PageNumbersPanel(SHORT, _settings(start_page=4, end_page=7))
    panel.last_page_spin.setValue(2)
    assert asked(panel.settings()) == (2, 2)
    panel.set_page_count(WIDE)
    assert asked(panel.settings()) == (2, 2), "the reader's 2, not the old 4"


def test_the_last_page_stays_the_last_page_of_whatever_book_is_open(qapp):
    """`end_page == 0` means "last": the one clamp that was already remembered.

    This is the shape the explicit end needs: `_end_is_last` survived a document
    switch because it is held apart from its box, and `end_page` did not because
    it was not.
    """
    panel = _panel(start=1, end=0)
    panel.set_page_count(SHORT)
    assert panel.settings().end_page == 0
    assert panel.last_page_spin.value() == SHORT
    panel.set_page_count(WIDE)
    assert panel.last_page_spin.value() == WIDE


# ------------------------------------------------------------------ in the window
def test_a_long_books_range_survives_numbering_a_short_one(
        qapp, tmp_path, store, monkeypatch):
    """The whole story in one run: open the long book, set page 10-100, switch."""
    long_book = build_sample_pdf(tmp_path / "long.pdf", pages=WIDE)
    short_book = build_sample_pdf(tmp_path / "short.pdf", pages=SHORT)
    out = tmp_path / "numbered.pdf"

    window = MainWindow()
    window.show()
    qapp.processEvents()
    window.open_document(long_book)
    qapp.processEvents()
    panel = window.page_numbers_panel
    panel.apply_settings(_settings(start_page=10, end_page=100))

    window.open_document(short_book)
    qapp.processEvents()
    assert (panel.first_page_spin.value(), panel.last_page_spin.value()) == (8, 8), \
        "the reader is looking at eight pages"

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(out), "")))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No))
    panel.save_btn.click()
    qapp.processEvents()

    assert out.is_file()
    with fitz.open(out) as doc:
        texts = [page.get_text() for page in doc]
    assert len(texts) == SHORT
    assert "1/1" in texts[-1], "the clamped page is the one that got numbered"
    assert all("1/1" not in text for text in texts[:-1])
    assert asked(PageNumberSettings.load()) == (10, 100)

    window._close_all_tabs()
    window.close()
    window.deleteLater()
    qapp.processEvents()
