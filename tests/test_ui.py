import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from linguafloat.config import AppSettings
from linguafloat.ui import MainWindow, TranslateActionButton, TranslationOverlay


def qt_application():
    return QApplication.instance() or QApplication([])


def test_main_window_and_right_click_button_smoke():
    application = qt_application()
    settings = AppSettings(trigger_mode="both", font_size=14)
    window = MainWindow(settings, "missing")
    action = TranslateActionButton()

    action.show_for("Hello", 300, 300)
    application.processEvents()

    assert window.trigger_mode_combo.currentData() == "both"
    assert window.self_test_button.text() == "一键测试"
    assert action.isVisible()
    assert action.global_bounds()[2] > action.global_bounds()[0]

    action.dismiss()
    window.close()


def test_translation_overlay_switches_target_language():
    application = qt_application()
    overlay = TranslationOverlay()
    requested = []
    overlay.language_requested.connect(requested.append)
    overlay.show_translation("你好", "Hello", target_language="auto")
    index = overlay.target_language_combo.findData("ja")
    overlay.target_language_combo.setCurrentIndex(index)
    application.processEvents()
    assert requested == ["ja"]
    assert overlay.target_language() == "ja"
    assert overlay.source_text == "你好"
    overlay.close()


def test_floating_translation_windows_can_be_resized():
    from linguafloat.screen_ui import ScreenTranslationPanel

    panel = ScreenTranslationPanel()
    panel.resize(500, 340)
    assert panel.size().width() == 500 and panel.size().height() == 340
    panel.close()

    overlay = TranslationOverlay()
    overlay.resize(540, 280)
    assert overlay.size().width() == 540 and overlay.size().height() == 280
    overlay.close()


def test_input_overlay_drag_and_refresh_preserve_position_and_size():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    application = qt_application()
    overlay = TranslationOverlay()
    overlay.show_translation("你好", "Hello", "en", origin="input")
    overlay.move(60, 60)
    overlay.resize(500, 280)
    application.processEvents()
    QTest.mousePress(overlay.title, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    QTest.mouseMove(overlay.title, QPoint(40, 28))
    QTest.mouseRelease(overlay.title, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert overlay.user_positioned and overlay.pos() != QPoint(60, 60)
    geometry = overlay.geometry()
    overlay.show_translation("你好", "こんにちは", "ja", origin="input")
    assert overlay.geometry() == geometry
    overlay.close()


def test_showing_right_click_button_does_not_request_translation():
    application = qt_application()
    action = TranslateActionButton()
    requested = []
    action.translate_requested.connect(requested.append)

    action.show_for("Hello", 300, 300)
    application.processEvents()
    assert requested == []

    action._activate()
    assert requested == ["Hello"]
