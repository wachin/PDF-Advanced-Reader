"""Smoke tests for the main window wiring (headless)."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QCheckBox, QDialogButtonBox, QLabel, QMenu, QMessageBox, QRadioButton,
    QToolButton, QToolTip,
)

from pdfar import APP_NAME, __version__
from pdfar.main import HIGHLIGHT_ROLE, MainWindow
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


def _yellow_pixels(image) -> int:
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if (color.red() > 220 and color.green() > 200
                    and color.blue() < 120):
                count += 1
    return count


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


def test_view_menu_toggles_pages_and_tools_panels(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        view_menu = next(a.menu() for a in win.menuBar().actions()
                         if a.text().replace("&", "") == "View")
        assert win.act_sidebar in view_menu.actions()
        assert win.act_tools_panel in view_menu.actions()
        assert win.act_sidebar.text() == "Pages Panel"
        assert win.act_tools_panel.text() == "Tools Panel"

        assert win.search_dock.isVisible()
        assert win.act_tools_panel.isChecked()

        # The dock's X hides it; the menu option follows and restores it.
        win.search_dock.close()
        qapp.processEvents()
        assert not win.search_dock.isVisible()
        assert not win.act_tools_panel.isChecked()
        win.act_tools_panel.trigger()
        qapp.processEvents()
        assert win.search_dock.isVisible()
        assert win.act_tools_panel.isChecked()

        assert win.open_document(sample_pdf)
        qapp.processEvents()
        assert win.side_dock.isVisible()
        assert win.act_sidebar.isChecked()
        win.side_dock.close()
        qapp.processEvents()
        assert not win.side_dock.isVisible()
        assert not win.act_sidebar.isChecked()
        win.act_sidebar.trigger()
        qapp.processEvents()
        assert win.side_dock.isVisible()
        assert win.act_sidebar.isChecked()

        # Closing the last document keeps the preference for the next one.
        win._close_document()
        qapp.processEvents()
        assert win.act_sidebar.isChecked()
        assert win.open_document(sample_pdf)
        qapp.processEvents()
        assert win.side_dock.isVisible()
    finally:
        win._close_document()
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

        first = win.results.item(0)
        text = first.text()
        spans = first.data(HIGHLIGHT_ROLE)
        assert [text[start:end] for start, end in spans] == [
            "alpha", "beta"]
        assert _yellow_pixels(
            win.results.viewport().grab().toImage()) > 20
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


def test_memory_level_setting_applies_and_persists(qapp, sample_pdf, big_pdf):
    """The Settings dialog exposes the preload level with visible
    descriptions: it persists, applies to every open tab and is inherited by
    documents opened afterwards."""
    settings, snapshot = _keep_settings("memory_level")
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert not hasattr(win, "mem_combo")   # moved out of the toolbar
        win.open_document(sample_pdf)
        qapp.processEvents()
        assert win.view.memory_level == "normal"

        dlg = win._build_settings_dialog()
        assert dlg.findChild(QRadioButton, "memoryNormal").isChecked()
        dlg.findChild(QRadioButton, "memoryGreedy").setChecked(True)
        dlg.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok).click()
        qapp.processEvents()
        assert win.memory_level == "greedy"
        assert win.view.memory_level == "greedy"
        assert settings.value("memory_level") == "greedy"

        # a new document inherits the chosen level
        win.open_document(big_pdf)
        qapp.processEvents()
        assert win.view.memory_level == "greedy"

        # reopening the dialog round-trips the choice; Low switches all tabs
        dlg = win._build_settings_dialog()
        assert dlg.findChild(QRadioButton, "memoryGreedy").isChecked()
        dlg.findChild(QRadioButton, "memoryLow").setChecked(True)
        dlg.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok).click()
        qapp.processEvents()
        assert win.memory_level == "low"
        for i in range(win.tabs.count()):
            assert win.tabs.widget(i).view.memory_level == "low"
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()
        _restore_settings(settings, snapshot)


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


# ------------------------------------------------------ selection tools
def test_toolbar_groups_mode_buttons_right_of_reflow(qapp):
    """Reflow, Explore, the selection split, the shape split, the stamp split,
    the measure split and the page-view menu form one contiguous group, placed
    after the navigation and zoom controls."""
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        actions = win.toolbar.actions()

        def at(item):
            """Index of an action, or of the container action of a widget."""
            for i, action in enumerate(actions):
                if action is item or win.toolbar.widgetForAction(action) is item:
                    return i
            raise AssertionError(f"{item!r} is not in the toolbar")

        assert at(win.act_explore) == at(win.act_reflow) + 1
        assert at(win.tool_split) == at(win.act_explore) + 1
        assert at(win.shape_split) == at(win.tool_split) + 1
        assert at(win.stamp_split) == at(win.shape_split) + 1
        assert at(win.measure_split) == at(win.stamp_split) + 1
        assert at(win.view_mode_button) == at(win.measure_split) + 1
        assert at(win.act_reflow) > at(win.act_zin)
    finally:
        win.close()
        win.deleteLater()


def test_toolbar_has_explore_and_split_button(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.act_explore.isCheckable()
        assert win.act_explore.isChecked() is False
        button = win.toolbar.widgetForAction(win.act_explore)
        assert isinstance(button, QToolButton)
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly

        split = win.tool_split
        assert (split.popupMode()
                == QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        assert split.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert split.defaultAction() is win.act_sel_text
        assert [a.text() for a in split.menu().actions()
                if not a.isSeparator()] == [
            "Text Selection", "Area Selection", "Table Selection", "Freehand"]
        assert win.act_sel_text.isChecked()
        assert win._tool_mode == "text"
        assert win._last_selection == "text"
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_split_button_activates_last_selection(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.open_document(sample_pdf)
        win.act_sel_area.trigger()
        assert win._tool_mode == "area"
        assert win.view.tool_mode == "area"
        assert win.tool_split.defaultAction() is win.act_sel_area
        assert win.act_sel_area.isChecked()

        win.act_explore.trigger()
        assert win._tool_mode == "browse"
        assert win.view.tool_mode == "browse"
        assert win.act_explore.isChecked()
        assert win.act_sel_area.isChecked() is False
        assert win.tool_split.defaultAction() is win.act_sel_area

        win.tool_split.defaultAction().trigger()
        assert win._tool_mode == "area"
        assert win.view.tool_mode == "area"
        assert win.act_sel_area.isChecked()
        assert win.act_explore.isChecked() is False
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_selection_mode_is_global_across_tabs(qapp, sample_pdf, tmp_path):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        import shutil
        second = tmp_path / "second.pdf"
        shutil.copy(sample_pdf, second)

        win.open_document(sample_pdf)
        win.act_sel_table.trigger()
        assert win.view.tool_mode == "table"

        win.open_document(str(second))
        assert win.tabs.count() == 2
        assert win.view.tool_mode == "table"
        assert win.act_sel_table.isChecked()

        win.tabs.setCurrentIndex(0)
        qapp.processEvents()
        assert win.view.tool_mode == "table"
        win.tabs.setCurrentIndex(1)
        qapp.processEvents()
        assert win.view.tool_mode == "table"
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_reclicking_checked_action_is_idempotent(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.open_document(sample_pdf)
        win.act_sel_area.trigger()
        win.act_sel_area.trigger()
        assert win._tool_mode == "area"
        assert win.act_sel_area.isChecked()
        assert win.tool_split.defaultAction() is win.act_sel_area
        assert win.view.tool_mode == "area"
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_reflow_disables_area_table_and_falls_back_to_text(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.open_document(sample_pdf)
        win.act_sel_area.trigger()
        win._toggle_reflow()
        assert wait_until(qapp, lambda: win.view._reflow_worker is None)

        assert win.act_sel_area.isEnabled() is False
        assert win.act_sel_table.isEnabled() is False
        assert win.act_sel_text.isEnabled() is True
        assert win.act_explore.isEnabled() is True
        assert win._tool_mode == "text"
        assert win.view.tool_mode == "text"
        assert win.tool_split.defaultAction() is win.act_sel_text
        assert ("unavailable in Reflow"
                in win.statusBar().currentMessage())

        win._toggle_reflow()
        assert wait_until(qapp, lambda: win.view._reflow_worker is None)
        assert win.act_sel_area.isEnabled() is True
        assert win.act_sel_table.isEnabled() is True
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_no_document_disables_tools(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.act_explore.isEnabled() is False
        assert win.act_sel_text.isEnabled() is False
        assert win.act_sel_area.isEnabled() is False
        assert win.act_sel_table.isEnabled() is False
        assert win.tool_split.isEnabled() is False

        win.open_document(sample_pdf)
        assert win.act_explore.isEnabled() is True
        assert win.act_sel_text.isEnabled() is True
        assert win.act_sel_area.isEnabled() is True
        assert win.act_sel_table.isEnabled() is True
        assert win.tool_split.isEnabled() is True
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_tool_icons_exist_and_tint_dark(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.open_document(sample_pdf)
        size = win.toolbar.iconSize()
        for act in (win.act_explore, win.act_sel_text,
                    win.act_sel_area, win.act_sel_table):
            assert not act.icon().isNull()
            assert act.icon().pixmap(size).size() == size

        win.act_dark_mode.setChecked(True)
        win._update_zoom_actions(False)
        qapp.processEvents()
        image = win.act_sel_area.icon().pixmap(size).toImage()
        colors = {
            (image.pixelColor(x, y).red(),
             image.pixelColor(x, y).green(),
             image.pixelColor(x, y).blue())
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 200
        }
        assert (230, 230, 230) in colors
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


# ------------------------------------------------------------ view modes
VIEW_MODE_TEXTS = [
    "Single Page",
    "Facing Pages",
    "Facing Pages (Center First Page)",
    "Overview",
    "Continuous",
]


def _page_view_menu(win):
    for menu in win.findChildren(QMenu):
        if menu.title() == "Page View":
            return menu
    return None


def test_view_menu_has_page_view_submenu(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        menu = _page_view_menu(win)
        assert menu is not None
        assert [action.text() for action in menu.actions()] == VIEW_MODE_TEXTS
        assert win.act_view_continuous.isChecked()
        assert win._view_actions["continuous"] is win.act_view_continuous
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_toolbar_view_mode_button_matches_current_mode(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        button = win.view_mode_button
        assert button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert [action.text() for action in button.menu().actions()] == \
            VIEW_MODE_TEXTS
        assert not button.icon().isNull()

        win.open_document(sample_pdf)
        win.act_view_facing.trigger()
        qapp.processEvents()
        assert win.view.view_mode == "facing"
        assert win.act_view_facing.isChecked()
        assert button.toolTip() == "Page View: Facing Pages"
        assert not button.icon().isNull()
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_view_mode_is_per_document(qapp, sample_pdf, tmp_path):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        import shutil
        second = tmp_path / "second.pdf"
        shutil.copy(sample_pdf, second)

        win.open_document(sample_pdf)
        win.act_view_facing.trigger()
        assert win.view.view_mode == "facing"

        win.open_document(str(second))
        assert win.tabs.count() == 2
        assert win.view.view_mode == "continuous"
        assert win.act_view_continuous.isChecked()

        win.tabs.setCurrentIndex(0)
        qapp.processEvents()
        assert win.view.view_mode == "facing"
        assert win.act_view_facing.isChecked()

        win.tabs.setCurrentIndex(1)
        qapp.processEvents()
        assert win.view.view_mode == "continuous"
        assert win.act_view_continuous.isChecked()
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_reclicking_view_mode_action_is_idempotent(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.open_document(sample_pdf)
        win.act_view_facing.trigger()
        win.act_view_facing.trigger()
        assert win.view.view_mode == "facing"
        assert win.act_view_facing.isChecked()
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_reflow_disables_view_mode_actions(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.open_document(sample_pdf)
        win.act_view_facing.trigger()
        win._toggle_reflow()
        assert wait_until(qapp, lambda: win.view._reflow_worker is None)

        for action in win._view_actions.values():
            assert action.isEnabled() is False
        assert win.view_mode_button.isEnabled() is False
        assert win.view.view_mode == "facing"

        win._toggle_reflow()
        assert wait_until(qapp, lambda: win.view._reflow_worker is None)
        for action in win._view_actions.values():
            assert action.isEnabled() is True
        assert win.view_mode_button.isEnabled() is True
        assert win.view.view_mode == "facing"
        assert win.act_view_facing.isChecked()
        assert win.view._rows == [[0, 1], [2]]
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_no_document_disables_view_mode_actions(qapp):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        for action in win._view_actions.values():
            assert action.isEnabled() is False
        assert win.view_mode_button.isEnabled() is False
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_view_mode_icons_exist_and_tint_dark(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.open_document(sample_pdf)
        size = win.toolbar.iconSize()
        for action in win._view_actions.values():
            assert not action.icon().isNull()
            assert action.icon().pixmap(size).size() == size

        win.act_dark_mode.setChecked(True)
        win._update_zoom_actions(False)
        qapp.processEvents()
        image = win.act_view_facing.icon().pixmap(size).toImage()
        colors = {
            (image.pixelColor(x, y).red(),
             image.pixelColor(x, y).green(),
             image.pixelColor(x, y).blue())
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 200
        }
        assert (230, 230, 230) in colors
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()
