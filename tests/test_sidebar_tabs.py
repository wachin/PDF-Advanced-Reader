"""Sidebar tabs are icon-only; their labels appear on hover."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import QSize, Qt

from conftest import build_sample_pdf
from pdfar.main import MainWindow
from pdfar.sidebar import TAB_LABELS, TAB_ANNOTATIONS, TAB_INDEX


def build_toc_pdf(path):
    """Six pages with a five-level outline plus a second chapter."""
    doc = fitz.open()
    for p in range(6):
        page = doc.new_page(width=200, height=100)
        page.insert_text(fitz.Point(10, 30), f"Page {p + 1}", fontsize=12)
    doc.set_toc([
        [1, "Chapter One", 1],
        [2, "Section 1.1", 1],
        [3, "Topic 1.1.1", 2],
        [4, "Detail 1.1.1.1", 3],
        [5, "Subdetail 1.1.1.1.1", 4],
        [1, "Chapter Two", 5],
        [2, "Section 2.1", 6],
        [3, "Topic 2.1.1", 6],
    ])
    doc.save(path)
    doc.close()
    return str(path)


def outline_levels(tree):
    """Yield ``(item, depth)`` pairs, with top-level items at depth 1."""
    root = tree.invisibleRootItem()
    stack = [(root.child(i), 1) for i in range(root.childCount())]
    while stack:
        item, depth = stack.pop()
        yield item, depth
        stack.extend((item.child(i), depth + 1) for i in range(item.childCount()))


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    yield w
    w._close_all_tabs()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def test_sidebar_tabs_show_only_icons_with_hover_labels(win, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=1))
    tabs = win.sidebar.tabs

    assert [tabs.tabToolTip(i) for i in range(tabs.count())] == list(TAB_LABELS)
    for index in range(tabs.count()):
        assert tabs.tabText(index) == ""
        icon = tabs.tabIcon(index)
        assert not icon.isNull(), f"tab {index} has no icon"
        pixmap = icon.pixmap(QSize(18, 18))
        assert not pixmap.isNull()
        assert pixmap.size() == QSize(18, 18)


def test_sidebar_tab_icons_follow_dark_mode(win, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=1))
    sidebar = win.sidebar
    assert sidebar._icon_color == "#3c3c3c"

    saved = win.settings.value("dark_mode", False, type=bool)
    try:
        win._toggle_dark_mode()
        assert sidebar._icon_color == "#e6e6e6"
        assert not sidebar.tabs.tabIcon(0).isNull()
    finally:
        if win.view is not None and win.view.dark_mode:
            win._toggle_dark_mode()
        win.settings.setValue("dark_mode", saved)


def test_sidebar_can_shrink_for_more_document_space(win, qapp, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=1))
    sidebar = win.sidebar
    sidebar.tabs.setCurrentIndex(TAB_ANNOTATIONS)

    win.resizeDocks([win.side_dock], [160], Qt.Orientation.Horizontal)
    for _ in range(4):
        qapp.processEvents()

    assert win.side_dock.width() < 200
    assert sidebar.minimumSizeHint().width() < 200

    # The annotation controls wrap onto extra rows instead of forcing a
    # wider panel, and stay fully inside it.
    assert sidebar.ann_save_btn.y() > sidebar.ann_new_highlight.y()
    panel = sidebar.annotations_panel.rect()
    for control in (sidebar.ann_new_highlight, sidebar.ann_color_combo,
                    sidebar.ann_save_btn):
        geometry = control.geometry()
        assert geometry.width() > 0
        assert geometry.height() > 0
        assert panel.contains(geometry)


def test_outline_level_buttons_collapse_and_expand(win, tmp_path):
    win.open_document(build_toc_pdf(tmp_path / "toc.pdf"))
    sidebar = win.sidebar
    sidebar.tabs.setCurrentIndex(TAB_INDEX)
    outline = sidebar.outline
    buttons = sidebar.outline_buttons

    assert [button.text() for button in buttons] == ["T1", "T2", "T3", "T4"]
    assert all(button.isEnabled() for button in buttons)
    assert not any(button.isChecked() for button in buttons)
    assert all(item.isExpanded() for item, _depth in outline_levels(outline)
               if item.childCount())

    for level, button in enumerate(buttons, start=1):
        button.click()
        assert button.isChecked()
        assert [other.isChecked() for other in buttons] == [
            other is button for other in buttons
        ]
        for item, depth in outline_levels(outline):
            if item.childCount():
                assert item.isExpanded() == (depth < level), (
                    item.text(0), depth, level)

        # A second click releases the filter and expands everything again.
        button.click()
        assert not any(other.isChecked() for other in buttons)
        assert all(item.isExpanded() for item, _depth in outline_levels(outline)
                   if item.childCount())


def test_outline_level_buttons_switch_cleanly(win, tmp_path):
    win.open_document(build_toc_pdf(tmp_path / "toc.pdf"))
    sidebar = win.sidebar
    sidebar.tabs.setCurrentIndex(TAB_INDEX)
    buttons = sidebar.outline_buttons

    buttons[0].click()
    buttons[2].click()

    assert [button.isChecked() for button in buttons] == [False, False, True, False]
    for item, depth in outline_levels(sidebar.outline):
        if item.childCount():
            assert item.isExpanded() == (depth < 3)


def test_outline_level_buttons_disabled_without_outline(win, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=1))

    assert not any(button.isEnabled() for button in win.sidebar.outline_buttons)
