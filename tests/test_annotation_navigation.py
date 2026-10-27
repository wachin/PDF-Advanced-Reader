"""F10-3f: jumping from a comment row to the comment on the page.

The sidebar has listed annotations since F10-3a, but activating a row only moved
to their *page* — on a 540-page book with a highlight halfway down, that is a
search of its own.  Here a row scrolls the annotation into view and the view
draws around it, so the user can see which one the panel meant.

Two things are worth pinning down.  The outline is geometry, not pixels, so it is
tested as geometry through ``_annotation_outline_rects()`` at zoom and under
rotation; and a selection is stored as an annotation *id*, because the document
can lose that annotation from under the view (an undo, a revert) and a ring drawn
around whatever now shares its position would be a lie.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QTreeWidgetItem

from conftest import build_sample_pdf
from pdfar.geometry import map_rect

#: Where the sample document's annotations go, in PDF space.
HL_PAGE1 = (20.0, 20.0, 120.0, 34.0)


@pytest.fixture
def win(qapp):
    from pdfar.main import MainWindow
    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    w._close_all_tabs(confirm=False)
    w.close()
    w.deleteLater()
    qapp.processEvents()


@pytest.fixture
def open_tab(qapp, win, tmp_path):
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=3,
                               width=200, height=100))
    assert win.open_document(path)
    qapp.processEvents()
    return win._active_tab()


@pytest.fixture
def view(open_tab):
    return open_tab.view


def widget_rect_for(view, page, rect):
    """Where *rect* is on the screen, the way the outline helper must report it."""
    w0, h0 = view.page_sizes[page]
    x0, y0, x1, y1 = map_rect(rect, w0, h0, view.rotation)
    px, py = view._page_origin(page)
    z = view.zoom
    return QRect(round(px + x0 * z - view.horizontalScrollBar().value()),
                 round(py + y0 * z - view.verticalScrollBar().value()),
                 round((x1 - x0) * z), round((y1 - y0) * z))


def highlight_on(view, page, rect=HL_PAGE1):
    made = view.annotation_manager.add_highlight(page, [rect])
    assert made is not None
    return made


def annotation_rows(sidebar):
    """The (id, item) pairs the annotations tree currently shows."""
    tree = sidebar.annotations_tree
    out = []
    for i in range(tree.topLevelItemCount()):
        node = tree.topLevelItem(i)
        for j in range(node.childCount()):
            child = node.child(j)
            out.append((child.data(0, Qt.ItemDataRole.UserRole), child))
    return out


# ------------------------------------------------------------------ the jump
def test_selecting_an_annotation_brings_it_into_view(view, qapp):
    """The point of the row is the annotation, not the page it is somewhere on."""
    hl = highlight_on(view, 2)
    view.goto_page(0)
    qapp.processEvents()
    assert view.current_page == 0

    assert view.select_annotation(hl.id) is True
    qapp.processEvents()

    assert view.current_page == 2
    outlines = view._annotation_outline_rects(2)
    assert len(outlines) == 1
    assert view.viewport().rect().contains(outlines[0]), \
        f"{outlines[0]} is not inside {view.viewport().rect()}"


def test_the_outline_is_where_the_annotation_is(view, qapp):
    """The rectangle is the annotation's own, mapped through zoom and rotation."""
    hl = highlight_on(view, 1)
    for zoom, rotation in ((1.0, 0), (2.0, 0), (1.5, 90), (0.9, 180), (3.0, 270)):
        view.set_zoom(zoom)
        view.set_rotation(rotation)
        assert view.select_annotation(hl.id) is True
        qapp.processEvents()
        assert view._annotation_outline_rects(1) == [
            widget_rect_for(view, 1, HL_PAGE1)], f"zoom {zoom} rotation {rotation}"
    view.set_rotation(0)


def test_a_highlight_outlines_every_quad_it_covers(view, qapp):
    """A selection of two lines is one annotation with two rectangles."""
    made = view.annotation_manager.add_highlight(
        0, [(20.0, 20.0, 150.0, 30.0), (20.0, 40.0, 90.0, 50.0)])
    qapp.processEvents()

    assert view.select_annotation(made.id) is True
    assert len(view._annotation_outline_rects(0)) == 2
    assert view._annotation_outline_rects(1) == []


def test_a_note_is_outlined_where_its_icon_is(view, qapp):
    note = view.annotation_manager.add_note(0, (60.0, 40.0), "here")
    qapp.processEvents()

    assert view.select_annotation(note.id) is True
    assert view._annotation_outline_rects(0) == [
        widget_rect_for(view, 0, note.rects[0])]


def test_selecting_nothing_leaves_no_outline(view, qapp):
    hl = highlight_on(view, 0)
    view.select_annotation(hl.id)
    qapp.processEvents()

    view.clear_annotation_selection()
    qapp.processEvents()

    assert view._annotation_outline_rects(0) == []


def test_the_outline_moves_to_whatever_is_selected_next(view, qapp):
    first = highlight_on(view, 0, (20.0, 20.0, 60.0, 30.0))
    second = highlight_on(view, 1, (30.0, 50.0, 90.0, 60.0))
    view.select_annotation(first.id)
    qapp.processEvents()

    assert view.select_annotation(second.id) is True
    qapp.processEvents()

    assert view._annotation_outline_rects(0) == []
    assert len(view._annotation_outline_rects(1)) == 1


def test_an_annotation_that_is_gone_draws_nothing(view, qapp):
    """The id is the selection, so an undo cannot leave a ring around a stranger."""
    hl = highlight_on(view, 0)
    view.select_annotation(hl.id)
    qapp.processEvents()
    assert len(view._annotation_outline_rects(0)) == 1

    assert view.undo_edit() is True
    qapp.processEvents()

    assert view._annotation_outline_rects(0) == []


def test_selecting_an_annotation_that_is_not_there_says_why(view, qapp):
    seen = []
    view.transient_message.connect(seen.append)

    assert view.select_annotation("never-here") is False
    qapp.processEvents()

    assert seen and "not in this document" in seen[-1]
    assert view._annotation_outline_rects(0) == []


# ------------------------------------------------------------- the row drives it
def test_activating_a_sidebar_row_jumps_to_its_annotation(view, open_tab, qapp):
    hl = highlight_on(view, 2)
    qapp.processEvents()
    node = dict(annotation_rows(open_tab.sidebar))[hl.id]

    view.goto_page(0)
    qapp.processEvents()
    open_tab.sidebar.annotations_tree.itemActivated.emit(node, 0)
    qapp.processEvents()

    assert view.current_page == 2
    assert len(view._annotation_outline_rects(2)) == 1


def test_a_row_without_an_annotation_still_goes_to_its_page(open_tab, qapp):
    """Only a comment row can select a comment; a page row has nothing to ring."""
    seen = []
    open_tab.sidebar.goto_page_requested.connect(seen.append)
    item = QTreeWidgetItem(["Page 2"])
    item.setData(0, Qt.ItemDataRole.UserRole + 1, 1)

    open_tab.sidebar._on_annotation_activated(item, 0)
    qapp.processEvents()

    assert seen == [1]


# ------------------------------------------------------------- letting go again
def test_clicking_the_page_lets_go_of_the_outline(view, qapp):
    """A ring the user cannot get rid of is a bug they have to live with."""
    hl = highlight_on(view, 0)
    view.select_annotation(hl.id)
    qapp.processEvents()
    assert view._annotation_outline_rects(0)

    where = QPointF(view.viewport().mapToGlobal(QPoint(40, 40)))
    view.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(40, 40), where,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    qapp.processEvents()

    assert view._annotation_outline_rects(0) == []
