"""Sidebar tabs are icon-only; their labels appear on hover."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSize

from conftest import build_sample_pdf
from pdfar.main import MainWindow
from pdfar.sidebar import TAB_LABELS


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
