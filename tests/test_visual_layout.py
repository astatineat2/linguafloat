"""Layout and interaction regressions for the shared desktop presentation."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

from linguafloat.config import AppSettings
from linguafloat.screen_ui import ScreenTranslationPanel
from linguafloat.ui import MainWindow, apply_ui_font


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    old_font = app.font()
    # Match startup: initialize the application font before constructing widgets.
    apply_ui_font(app, 14)
    instance = MainWindow(AppSettings(), "missing")
    yield app, instance
    instance.allow_close()
    instance.provider_editor.hide()
    instance.close()
    instance.deleteLater()
    app.processEvents()
    app.setFont(old_font)


def rect_in(widget, ancestor):
    return QRect(widget.mapTo(ancestor, QPoint()), widget.size())


@pytest.mark.parametrize("size", [(860, 620), (1060, 840)])
@pytest.mark.parametrize("points", [12, 14, 16])
def test_primary_actions_stay_visible_at_supported_sizes(window, size, points):
    app, w = window
    apply_ui_font(app, points)
    w.resize(*size)
    w.show()
    app.processEvents()
    assert (w.width(), w.height()) == size
    assert w.provider_list.font().pixelSize() == points
    assert w.active_model_combo.view().font().pixelSize() == points
    for page, controls, scroll in (
        (0, [w.function_save_button], w.function_scroll),
        (1, [w.save_button, w.test_connection_button, w.use_provider_button], w.config_scroll),
    ):
        w.pages.setCurrentIndex(page)
        if page == 1:
            w.open_provider_editor(w._editing_id)
            w.provider_editor.resize(*size)
        container = w.provider_editor if page == 1 else w
        app.processEvents()
        for at_bottom in (False, True):
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum() if at_bottom else 0)
            app.processEvents()
            for button in controls:
                assert button.isVisible()
                assert container.rect().contains(rect_in(button, container))
                assert button.width() >= button.minimumSizeHint().width()
            assert scroll.horizontalScrollBar().maximum() == 0
    for first, second in zip((w.active_provider_combo, w.active_model_combo),
                             (w.active_model_combo, w.active_connection_status)):
        assert not rect_in(first, w).intersects(rect_in(second, w))


def test_collapsed_diagnostics_and_test_report_are_accessible(window):
    app, w = window
    w.show()
    assert w.capture_diagnostics.isHidden()
    w.diagnostics_toggle.click()
    app.processEvents()
    assert w.capture_diagnostics.isVisible()
    w.set_self_test_result("右键取词：因游戏模式停用\nOCR：通过\nAPI：失败 <401>", error=True)
    app.processEvents()
    report = w.self_test_result.toPlainText()
    assert "因游戏模式停用" in report
    assert "失败 <401>" in report
    assert w.self_test_result.height() < 300


def test_pause_presentation_never_hides_api_error(window):
    panel = ScreenTranslationPanel()
    panel.set_status("HTTP 401: key invalid", error=True)
    panel.set_paused(True)
    assert "401" in panel.status.text()
    assert panel.status.property("state") == "error"
    panel.set_status("已暂停")
    assert panel.status.property("state") == "paused"
    panel.set_paused(False)
    panel.show_translation("Hello", "你好")
    assert panel.status.text() == "翻译完成"
    panel.close()


@pytest.mark.parametrize("size", [(860, 620), (1060, 840)])
def test_api_compatibility_controls_fit_expanded_settings(window, size):
    app, w = window
    apply_ui_font(app, 14)
    w.resize(*size)
    w.pages.setCurrentIndex(1)
    w.open_provider_editor(w._editing_id)
    w.provider_editor.resize(*size)
    w.advanced_toggle.setChecked(True)
    w.show()
    app.processEvents()
    fields = (w.output_token_limit_spin, w.chat_token_parameter_combo,
              w.instruction_role_combo, w.reasoning_effort_combo, w.thinking_mode_combo)
    assert w.config_scroll.horizontalScrollBar().maximum() == 0
    for widget in fields:
        w.config_scroll.ensureWidgetVisible(widget)
        app.processEvents()
        assert widget.isVisible()
        assert widget.width() >= widget.minimumSizeHint().width()
    for i, first in enumerate(fields):
        for second in fields[i + 1:]:
            assert not rect_in(first, w.provider_editor).intersects(rect_in(second, w.provider_editor))
