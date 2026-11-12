"""Layout and retained shortcut actions, for the consolidated verification run."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from linguafloat.config import AppSettings
from linguafloat.ui import MainWindow
from test_credentials_ui import application


def test_shortcut_groups_have_three_equal_columns_at_normal_width(application):
    w = MainWindow(AppSettings(), "missing")
    w.resize(1060, 840); w.show(); application.processEvents()
    try:
        for row in (w.function_fields, w.input_fields):
            assert row.columns == 3
            cells = [row.grid.cellRect(0, i) for i in range(3)]
            assert max(cell.width() for cell in cells) - min(cell.width() for cell in cells) <= 2
            centers = [part.mapTo(row, QPoint(0, part.height() // 2)).y() for part in row.parts]
            assert max(centers) - min(centers) <= 2
        for row in (w.function_fields, w.input_fields):
            row.setFixedWidth(360)
        application.processEvents()
        for row in (w.function_fields, w.input_fields):
            assert row.columns < 3
            for part in row.parts:
                assert part.geometry().right() < row.width()
                assert part.width() >= part.minimumSizeHint().width()
    finally:
        w.allow_close(); w.close()


def test_shortcut_recording_cancel_trigger_and_save_signals_remain(application):
    w = MainWindow(AppSettings(), "missing")
    try:
        saves = []; functions = []
        w.input_hotkey_requested.connect(saves.append)
        w.function_save_requested.connect(lambda: functions.append(True))
        for control in (w.hotkey_edit, w.input_hotkey_edit):
            before = control.text()
            control.begin_recording()
            QTest.keyClick(control, Qt.Key.Key_Escape)
            assert control.text() == before and not control.recording
        w.function_save_button.click()
        assert saves == [] and functions == [True]
        assert not hasattr(w, "input_hotkey_save")
        w.trigger_mode_combo.setCurrentIndex(w.trigger_mode_combo.findData("right_click"))
        assert not w.hotkey_edit.isEnabled()
        w.trigger_mode_combo.setCurrentIndex(w.trigger_mode_combo.findData("both"))
        assert w.hotkey_edit.isEnabled()
    finally:
        w.allow_close(); w.close()


def test_add_menu_emits_each_provider_kind_once(application):
    w = MainWindow(AppSettings(), "missing")
    calls = []
    w.configure_provider_requested.connect(calls.append)
    try:
        for action in w.dashboard.add_button.menu().actions():
            action.trigger()
        assert calls == ["go", "no_coding", "custom"]
    finally:
        w.allow_close(); w.close()
