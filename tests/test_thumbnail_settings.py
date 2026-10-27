"""Tests for thumbnail appearance settings."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSize, QSettings
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialogButtonBox, QSpinBox,
)

from conftest import build_sample_pdf
from pdfar.main import MainWindow
from pdfar.sidebar import ThumbnailSettings
from pdfar.sidebar import THUMB_MAX_WIDTH, THUMB_MIN_WIDTH


@pytest.fixture(autouse=True)
def reset_thumbnail_settings(qapp):
    ThumbnailSettings().save()
    yield
    ThumbnailSettings().save()


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    yield w
    w._close_all_tabs()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def test_settings_load_clamps_values(qapp):
    settings = QSettings("PDFAR", "PDFAR")
    settings.setValue("thumbnail_width", THUMB_MAX_WIDTH + 500)
    settings.setValue("thumbnail_border_width", 9)
    settings.setValue("thumbnail_shadow", False)

    loaded = ThumbnailSettings.load()

    assert loaded.width == THUMB_MAX_WIDTH
    assert loaded.border_width == 3
    assert loaded.shadow is False


def test_sidebar_uses_saved_settings(win, tmp_path):
    ThumbnailSettings(width=160, border_width=2, shadow=False).save()
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=3))

    sidebar = win.sidebar
    assert sidebar._thumb_settings == ThumbnailSettings(160, 2, False)
    assert sidebar.thumbs.iconSize() == QSize(160, int(160 * 1.414))
    assert sidebar.thumbs.delegate.settings == sidebar._thumb_settings


def test_apply_settings_rescales_thumbnail_jobs(win, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    sidebar = win.sidebar
    old_zoom = sidebar._thumb_zoom[0]

    updated = ThumbnailSettings(width=180, border_width=3, shadow=False)
    win._apply_thumbnail_settings(updated)

    assert sidebar._thumb_settings == updated
    assert sidebar.thumbs.iconSize().width() == 180
    assert sidebar.thumbs.delegate.settings == updated
    assert sidebar._thumb_zoom[0] == pytest.approx(old_zoom * 180 / 140)
    assert sidebar.thumbs.item(0).icon().isNull()


def test_inline_size_controls(win, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    sidebar = win.sidebar
    win.sidebar.tabs.setCurrentIndex(0)

    assert sidebar.thumb_size_label.text() == "140 px"

    sidebar.thumb_plus.click()
    assert sidebar._thumb_settings.width == 148
    assert sidebar.thumb_size_label.text() == "148 px"
    assert ThumbnailSettings.load().width == 148

    sidebar.thumb_minus.click()
    assert sidebar._thumb_settings.width == 140

    sidebar.apply_thumbnail_settings(
        ThumbnailSettings(width=THUMB_MAX_WIDTH, border_width=1, shadow=True))
    assert not sidebar.thumb_plus.isEnabled()
    assert sidebar.thumb_minus.isEnabled()

    sidebar.apply_thumbnail_settings(
        ThumbnailSettings(width=THUMB_MIN_WIDTH, border_width=1, shadow=True))
    assert sidebar.thumb_plus.isEnabled()
    assert not sidebar.thumb_minus.isEnabled()


def test_inline_size_updates_all_tabs(win, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "one.pdf", pages=3))
    win.open_document(build_sample_pdf(tmp_path / "two.pdf", pages=3))

    win.sidebar.thumb_plus.click()

    for i in range(win.tabs.count()):
        assert win.tabs.widget(i).sidebar._thumb_settings.width == 148


def test_settings_dialog_saves_and_applies(win, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=3))

    dlg = win._build_settings_dialog()
    size = dlg.findChild(QSpinBox)
    border = dlg.findChild(QComboBox)
    shadow = dlg.findChild(QCheckBox, "thumbnailShadow")

    size.setValue(168)
    border.setCurrentIndex(1)
    shadow.setChecked(False)
    dlg.findChild(QDialogButtonBox).button(
        QDialogButtonBox.StandardButton.Ok).click()

    expected = ThumbnailSettings(width=168, border_width=2, shadow=False)
    assert win.sidebar._thumb_settings == expected
    assert ThumbnailSettings.load() == expected
    assert size.minimum() == THUMB_MIN_WIDTH
    assert size.maximum() == THUMB_MAX_WIDTH
