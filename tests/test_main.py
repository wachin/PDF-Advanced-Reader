"""Smoke tests for the main window wiring (headless)."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QCheckBox, QDialogButtonBox, QLabel, QMessageBox, QToolButton, QToolTip,
)

from pdfar import APP_NAME, __version__
from pdfar.main import MainWindow
from pdfar.search import SearchHit


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """QMessageBox would block the offscreen test run forever."""
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


def wait_until(app, predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_open_document_wires_everything(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(sample_pdf) is True
        assert win.view is not None
        assert win.view.page_count == 3
        assert win.page_label.text() == "/ 3"
        assert win.page_spin.maximum() == 3
        assert win.sidebar is not None
        assert win.sidebar.thumbs.count() == 3

        # pages really render through the whole MainWindow path
        assert wait_until(qapp, lambda: win.view.cache.get(0, win.view.zoom, win.view.rotation) is not None)
        assert win.sidebar.thumbs.count() == 3

        # navigation updates the page spinner
        win._goto(2)
        qapp.processEvents()
        assert win.page_spin.value() == 3
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_properties_dialog_builds_all_tabs(qapp, sample_pdf, monkeypatch):
    from PyQt6.QtWidgets import QDialog, QLabel, QTabWidget

    win = MainWindow()
    win.show()
    qapp.processEvents()
    seen = {}

    def fake_exec(dlg):
        tabs = dlg.findChild(QTabWidget)
        seen["titles"] = [tabs.tabText(i) for i in range(tabs.count())]
        seen["texts"] = [label.text() for label in dlg.findChildren(QLabel)]
        return 0

    try:
        assert win.open_document(sample_pdf)
        monkeypatch.setattr(QDialog, "exec", fake_exec)
        win._show_properties()
        assert seen["titles"] == ["General", "Fonts", "Security", "File Info", "Metadata"]
        assert "Page Size:" in seen["texts"]
        assert "3" in seen["texts"]
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_window_starts_with_ui_visible(qapp):
    """Regression: a previously-saved Qt windowState could leave the toolbar
    and docks hidden, so the window opened looking completely empty."""
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.toolbar.isVisible()
        assert win.tabs.isVisible()
        assert win.search_dock.isVisible()
        # the central tab area is the main document pane
        assert win.tabs.count() == 0
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_search_through_the_dock(qapp, sample_pdf):
    """1.x search never ran at all (the worker was never started)."""
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(sample_pdf) is True
        win.query_edit.setText("alpha beta")
        win.mode.setCurrentText("AND")
        win.start_search()

        assert wait_until(qapp, lambda: win.search_thread is not None and not win.search_thread.isRunning())
        assert wait_until(qapp, lambda: win.results.count() > 0)
        assert win.results.count() == 2          # pages 2 and 3 of the sample
        assert win.view._hit_flat, "search highlights should be set on the view"
        assert win.hit_label.text().startswith("Hit")
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_result_click_jumps_to_that_pages_first_hit(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(sample_pdf) is True
        win.query_edit.setText("alpha beta")
        win.mode.setCurrentText("AND")
        win.start_search()

        assert wait_until(qapp, lambda: win.search_thread is not None
                          and not win.search_thread.isRunning())
        assert win.results.count() == 2
        assert win.view.current_page == 1

        first_index = win.view.first_hit_index(2)
        win._on_result_clicked(win.results.item(1))

        assert win.view.current_page == 2
        assert win.hit_label.text() == (
            f"Hit {first_index + 1} / {len(win.view._hit_flat)}")
        assert win.results.currentRow() == 1
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_invalid_regex_reports_error(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(sample_pdf) is True
        win.query_edit.setText("[invalid")
        win.mode.setCurrentText("REGEX")
        win.start_search()

        assert "Invalid regex" in win.hit_label.text()
        assert win.results.count() == 0
        assert win.view._hit_flat == []
        assert win.search_thread is None or not win.search_thread.isRunning()
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_stale_search_results_are_ignored(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(sample_pdf) is True
        win._search_generation = 7
        win._on_search_done(
            [SearchHit(page=0, snippet="stale", rects=[(0, 0, 1, 1)])], 6)

        assert win.results.count() == 0
        assert win.view._hit_flat == []
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_search_mode_items_explain_themselves(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        expected = (
            ("AND", "every word"),
            ("OR", "any of the words"),
            ("PHRASE", "exact phrase"),
            ("REGEX", "regular expression"),
        )
        assert win.mode.count() == len(expected)
        for index, (name, needle) in enumerate(expected):
            assert win.mode.itemText(index) == name
            tip = win.mode.itemData(index, Qt.ItemDataRole.ToolTipRole)
            assert tip.startswith(name) and needle in tip.lower()
            assert tip.count("Example:") >= 2
            assert "\n\n" in tip, "examples should be split into paragraphs"

        assert win._mode_tooltips.delay_ms == 1000

        def wait_for_tooltip():
            deadline = time.time() + 1.2
            while time.time() < deadline:
                qapp.processEvents()
                time.sleep(0.01)

        # hovering the closed selector explains the mode it currently shows
        QTest.mouseMove(win.mode, win.mode.rect().center())
        qapp.processEvents()
        assert win._mode_tooltips._timer.isActive()
        assert not QToolTip.isVisible()
        wait_for_tooltip()
        assert QToolTip.isVisible()
        assert QToolTip.text().startswith("AND")
        assert "Example:" in QToolTip.text()

        # hovering an option in the open menu explains that option
        win.mode.showPopup()
        qapp.processEvents()
        view = win.mode.view()
        QTest.mouseMove(view.viewport(),
                        view.visualRect(view.model().index(2, 0)).center())
        qapp.processEvents()
        assert win._mode_tooltips._timer.isActive()
        assert not QToolTip.isVisible()

        wait_for_tooltip()
        assert QToolTip.isVisible()
        assert QToolTip.text().startswith("PHRASE")

        win.mode.hidePopup()
        qapp.processEvents()
        assert not QToolTip.isVisible()
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_about_dialog_content(qapp):
    win = MainWindow()
    try:
        dlg = win._build_about_dialog()
        labels = dlg.findChildren(QLabel)
        rich = next(l for l in labels if "Copyright" in l.text())
        text = rich.text()
        assert "© 2026 Washington Indacochea Delgado" in text
        assert "mailto:linuxfrontier@proton.me" in text
        assert "linuxfrontier@proton.me" in text
        assert "GPL3" in text
        assert "https://github.com/wachin/PDF-Advanced-Reader" in text
        assert "Technologies used" in text
        assert "Python 3" in text and "PyQt6" in text and "PyMuPDF" in text
        assert "A fast, feature-rich PDF reader" in text
        assert f"Version {__version__}" in text
        assert rich.openExternalLinks()
        assert (rich.textInteractionFlags()
                != Qt.TextInteractionFlag.NoTextInteraction)
        icons = [l for l in labels if not l.pixmap().isNull()]
        assert icons, "about dialog should show the program icon"
        assert icons[0].pixmap().width() >= 128
        dlg.deleteLater()
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_toolbar_icon_actions(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        for act, tip in ((win.act_prev, "Previous page (PgUp)"),
                         (win.act_next, "Next page (PgDown)"),
                         (win.act_rotl, "Rotate left"),
                         (win.act_rotr, "Rotate right")):
            assert not act.icon().isNull()
            assert act.icon().pixmap(win.toolbar.iconSize()).size() == win.toolbar.iconSize()
            assert act.toolTip() == tip
            button = win.toolbar.widgetForAction(act)
            assert isinstance(button, QToolButton)
            assert (button.toolButtonStyle()
                    == Qt.ToolButtonStyle.ToolButtonIconOnly)
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_zoom_preset_and_rotation_actions(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        presets = [win.zoom_combo.itemText(i)
                   for i in range(win.zoom_combo.count())]
        assert presets == ["Fit Page", "Fit Width",
                           "50%", "62.5%", "75%", "87.5%",
                           "100%", "112.5%", "125%", "137.5%",
                           "150%", "162.5%", "175%", "200%",
                           "250%", "300%", "350%", "400%"]

        win.open_document(sample_pdf)
        win.zoom_combo.setCurrentText("150%")
        qapp.processEvents()
        assert abs(win.view.zoom - 1.5) < 1e-6
        assert win.zoom_label.text() == "150%"

        win.zoom_combo.setCurrentText("62.5%")
        qapp.processEvents()
        assert abs(win.view.zoom - 0.625) < 1e-6
        assert win.zoom_label.text() == "62.5%"
        assert "62.5%" in win.status_right.text()

        win._rotate(90)
        qapp.processEvents()
        assert win.view.rotation == 90
        win._rotate(90)
        qapp.processEvents()
        assert win.view.rotation == 180
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_zoom_step_presets_and_ctrl_wheel_option(qapp, sample_pdf):
    settings = QSettings(APP_NAME, APP_NAME)
    old_step = settings.value("zoom_step")
    old_wheel = settings.value("ctrl_wheel_zoom_step")
    settings.setValue("zoom_step", 0.10)
    settings.setValue("ctrl_wheel_zoom_step", True)
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        labels = [win.zoom_step_combo.itemText(i)
                  for i in range(win.zoom_step_combo.count())]
        assert labels == ["2.5%", "5%", "7.5%", "10%", "12.5%",
                          "15%", "17.5%", "20%", "22.5%", "25%"]
        assert win.zoom_step_combo.currentText() == "10%"
        assert win.act_ctrl_wheel_step.isCheckable()
        assert win.act_ctrl_wheel_step.isChecked()

        assert win.open_document(sample_pdf) is True
        win.zoom_combo.setCurrentText("100%")
        assert win.view.zoom == pytest.approx(1.0)
        win._zoom_in()
        assert win.view.zoom == pytest.approx(1.10)
        assert win.view.zoom_step == pytest.approx(0.10)
        assert win.view.ctrl_wheel_step is True

        win.zoom_step_combo.setCurrentText("5%")
        assert win.zoom_step == pytest.approx(0.05)
        assert win.view.zoom_step == pytest.approx(0.05)
        assert win.settings.value("zoom_step", type=float) == pytest.approx(0.05)
        win._zoom_in()
        assert win.view.zoom == pytest.approx(1.15)

        win.act_ctrl_wheel_step.trigger()
        assert win.ctrl_wheel_step is False
        assert win.view.ctrl_wheel_step is False
        assert win.settings.value("ctrl_wheel_zoom_step", type=bool) is False
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()
        for key, old in (("zoom_step", old_step),
                         ("ctrl_wheel_zoom_step", old_wheel)):
            if old is None:
                settings.remove(key)
            else:
                settings.setValue(key, old)


def test_recent_files_menu(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(sample_pdf) is True
        actions = win.menu_recent.actions()
        assert any(a.data() == sample_pdf for a in actions), "opened file should be in recent list"
        act = next(a for a in actions if a.data() == sample_pdf)
        act.trigger()
        qapp.processEvents()
        assert win.doc_path == sample_pdf
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_open_missing_file_is_rejected(qapp, tmp_path):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(str(tmp_path / "does-not-exist.pdf")) is False
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_memory_level_combo_applies_and_persists(qapp, sample_pdf, big_pdf):
    """The toolbar Memory combo applies to the active view, persists the
    choice, and is inherited by documents opened afterwards."""
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.open_document(sample_pdf)
        qapp.processEvents()
        assert win.view.memory_level == "normal"
        assert win.mem_combo.currentText() == "Normal"

        win.mem_combo.setCurrentText("Greedy")
        qapp.processEvents()
        assert win.memory_level == "greedy"
        assert win.view.memory_level == "greedy"
        assert win.settings.value("memory_level") == "greedy"

        # a new document inherits the chosen level
        win.open_document(big_pdf)
        qapp.processEvents()
        assert win.view.memory_level == "greedy"
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def _keep_settings(*keys):
    """Snapshot QSettings keys so a test can put them back afterwards."""
    settings = QSettings(APP_NAME, APP_NAME)
    return settings, {key: settings.value(key) for key in keys}


def _restore_settings(settings, snapshot):
    for key, value in snapshot.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)


def test_first_launch_opens_maximized(qapp):
    settings, snapshot = _keep_settings("geometry", "window_maximized")
    try:
        settings.remove("geometry")
        settings.remove("window_maximized")
        win = MainWindow()
        try:
            win.show_initial()
            qapp.processEvents()
            assert win.isMaximized()
        finally:
            win.close()
            win.deleteLater()
            qapp.processEvents()
    finally:
        _restore_settings(settings, snapshot)


def test_window_position_and_size_are_remembered(qapp):
    settings, snapshot = _keep_settings("geometry", "window_maximized")
    try:
        settings.remove("geometry")
        settings.setValue("window_maximized", False)
        win = MainWindow()
        win.showNormal()
        win.resize(640, 520)
        win.move(60, 70)
        qapp.processEvents()
        expected_size = win.size()
        expected_pos = win.pos()
        win.close()
        win.deleteLater()
        qapp.processEvents()

        again = MainWindow()
        assert again._start_maximized is False
        again.show_initial()
        qapp.processEvents()
        try:
            assert not again.isMaximized()
            assert again.size() == expected_size
            assert again.pos() == expected_pos
        finally:
            again.close()
            again.deleteLater()
            qapp.processEvents()
    finally:
        _restore_settings(settings, snapshot)


def test_unmaximized_startup_undoes_a_saved_maximized_state(qapp):
    settings, snapshot = _keep_settings("geometry", "window_maximized")
    try:
        settings.remove("geometry")
        settings.setValue("window_maximized", True)
        win = MainWindow()
        win.showNormal()
        win.resize(640, 520)
        win.move(60, 70)
        qapp.processEvents()
        expected_size = win.size()
        expected_pos = win.pos()
        win.showMaximized()
        qapp.processEvents()
        assert win.isMaximized()
        win.close()
        win.deleteLater()
        qapp.processEvents()

        settings.setValue("window_maximized", False)
        again = MainWindow()
        again.show_initial()
        qapp.processEvents()
        try:
            assert not again.isMaximized()
            assert again.size() == expected_size
            assert again.pos() == expected_pos
        finally:
            again.close()
            again.deleteLater()
            qapp.processEvents()
    finally:
        _restore_settings(settings, snapshot)


def test_settings_dialog_controls_maximized_startup(qapp):
    settings, snapshot = _keep_settings("window_maximized")
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        settings.setValue("window_maximized", True)
        win.showMaximized()
        qapp.processEvents()
        assert win.isMaximized()

        dlg = win._build_settings_dialog()
        box = dlg.findChild(QCheckBox, "windowStartMaximized")
        assert box is not None and box.isChecked()
        box.setChecked(False)
        dlg.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok).click()
        qapp.processEvents()
        assert settings.value("window_maximized", True, type=bool) is False
        assert not win.isMaximized()

        dlg = win._build_settings_dialog()
        box = dlg.findChild(QCheckBox, "windowStartMaximized")
        assert box is not None and not box.isChecked()
        box.setChecked(True)
        dlg.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok).click()
        qapp.processEvents()
        assert settings.value("window_maximized", True, type=bool) is True
        assert win.isMaximized()
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
        _restore_settings(settings, snapshot)
