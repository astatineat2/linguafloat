from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

import linguafloat.capture as capture_module
import linguafloat.clipboard_io as clipboard_module
from linguafloat.capture import SelectionCapture
from linguafloat.config import ConfigStore
from linguafloat.resize import resized_rect
from linguafloat.screen_ui import RegionOutline
from linguafloat.provider_tools import address_suggestion
from test_credentials_ui import application, controller
from test_app_controller import FakeThreadPool
from linguafloat.selection import RightClickMonitor
from pynput.mouse import Button


def test_editing_provider_does_not_activate_it_and_save_preserves_metadata(controller):
    c, _, store = controller
    w = c.window
    w.provider_combo.setCurrentIndex(1)
    w.favorite_check.setChecked(True)
    w.set_models(["m1", "m2"])
    w._favorite_models = ["m2"]
    w.timeout_spin.setValue(45)
    w.retries_spin.setValue(1)
    w.save_button.click()
    saved = store.load()
    assert saved.active_provider == "test"
    p = next(p for p in saved.providers if p.id == "second")
    assert p.favorite and p.models == ["m1", "m2"] and p.favorite_models == ["m2"]
    assert (p.timeout, p.retries) == (45, 1)
    assert not w.is_dirty()
    w.use_provider_button.click()
    assert c.settings.active_provider == "second"


def test_tabs_and_active_switch_preserve_unrelated_draft(controller):
    c, _, _ = controller
    w = c.window
    w.model_edit.setText("draft-model")
    w.pages.setCurrentIndex(1)
    w.pages.setCurrentIndex(0)
    c._select_provider("second")
    assert w._editing_id == "test" and w.model_edit.text() == "draft-model"
    assert w.is_dirty()


def test_cancel_provider_switch_retains_editor(controller, monkeypatch):
    c, _, _ = controller
    w = c.window
    w.model_edit.setText("draft")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    w.provider_combo.setCurrentIndex(1)
    assert w._editing_id == "test" and w.model_edit.text() == "draft"


def test_function_save_does_not_discard_invalid_provider_draft(controller):
    c, _, _ = controller
    w = c.window
    w.base_url_edit.setText("invalid draft")
    c._save_function_settings()
    assert w.base_url_edit.text() == "invalid draft" and w.is_dirty()
    assert c.settings.active_profile().base_url == "https://example.com/v1"


def test_models_result_for_changed_form_is_ignored(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    c._provider_tools("models")
    task = c.thread_pool.tasks[0]
    c.window.base_url_edit.setText("https://changed.example/v1")
    task.signals.finished.emit(task.token, ["wrong-site-model"], "")
    assert "wrong-site-model" not in c.window._model_choices
    assert c.window.fetch_models_button.isEnabled()


def test_paused_screen_result_only_writes_cache_and_never_updates_ui(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    reader = c.screen_reader
    reader.active_scope = c.settings.active_profile().cache_scope
    reader.state.running = True
    reader._observe("Hello")
    reader._observe("Hello")
    task = c.thread_pool.tasks[0]
    assert task.allowed()
    reader.panel.show_translation("existing", "保留译文")
    reader.pause()
    activity = c.window.activity.toPlainText()
    assert not task.allowed()
    task.signals.succeeded.emit(task.cache_scope, "Hello", "新译文")
    assert reader.panel.translation.toPlainText() == "保留译文"
    assert reader.panel.status.text() == "已暂停" or reader.panel.status.text() == "未选择区域"
    assert c.window.activity.toPlainText() == activity
    assert c.cache.get(task.cache_scope, "Hello") == "新译文"
    assert reader.state.pending is None


def test_cancelled_queued_screen_request_does_not_restart_after_pause(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    reader = c.screen_reader
    reader.active_scope = c.settings.active_profile().cache_scope
    reader.state.running = True
    reader._observe("Hello")
    reader._observe("Hello")
    task = c.thread_pool.tasks[0]
    reader.pause()
    task.signals.cancelled.emit(task.cache_scope, "Hello", "auto")
    assert len(c.thread_pool.tasks) == 1
    assert reader.state.pending is None


def test_paused_screen_error_is_silent(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    reader = c.screen_reader
    reader.active_scope = c.settings.active_profile().cache_scope
    reader.state.running = True
    reader._observe("Hello")
    reader._observe("Hello")
    task = c.thread_pool.tasks[0]
    reader.pause()
    status = reader.panel.status.text()
    activity = c.window.activity.toPlainText()
    task.signals.failed.emit(task.cache_scope, "Hello", "old HTTP 401")
    assert reader.panel.status.text() == status
    assert c.window.activity.toPlainText() == activity


def test_provider_switch_invalidates_ordinary_response_and_preserves_screen_pause(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    c._translate("Hello")
    task = c.thread_pool.tasks[0]
    c.screen_reader.pause()
    c.screen_reader.panel.show_translation("old", "保留")
    c._select_provider("second")
    task.signals.succeeded.emit(task.cache_scope, "Hello", "obsolete")
    assert not c.overlay.isVisible()
    assert not c.screen_reader.state.running
    assert c.screen_reader.panel.translation.toPlainText() == "保留"


@pytest.mark.parametrize("edge,delta,expected", [
    ("left", QPoint(-20, 0), QRect(80, 100, 220, 100)),
    ("right", QPoint(20, 0), QRect(100, 100, 220, 100)),
    ("top", QPoint(0, -20), QRect(100, 80, 200, 120)),
    ("bottom", QPoint(0, 20), QRect(100, 100, 200, 120)),
])
def test_four_edge_geometry_preserves_other_edges(edge, delta, expected):
    assert resized_rect(QRect(100, 100, 200, 100), edge, delta, QRect(0, 0, 1000, 800), QSize(48, 32)) == expected


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_panel_actual_side_drag_changes_only_requested_dimension(controller, application, edge):
    panel = controller[0].screen_reader.panel
    panel.setGeometry(150, 150, 360, 250)
    panel.show()
    application.processEvents()
    old = panel.geometry()
    handle = panel.resize_edges[edge]
    point = handle.rect().center()
    delta = {"left": QPoint(-20, 0), "right": QPoint(20, 0), "top": QPoint(0, -20), "bottom": QPoint(0, 20)}[edge]
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(handle, point + delta)
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=point)
    assert panel.geometry() != old
    assert panel.height() == old.height() if edge in {"left", "right"} else panel.width() == old.width()
    panel.hide()


@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_outline_side_hit_areas_are_resizable(application, edge):
    outline = RegionOutline()
    outline.show_region(QRect(100, 100, 200, 100))
    points = {"left": QPoint(1, 40), "right": QPoint(outline.width()-1, 40),
              "top": QPoint(25, 1), "bottom": QPoint(25, outline.height()-1)}
    assert outline._corner_at(points[edge]) == edge
    assert not outline.mask().contains(outline.rect().center())
    outline.close()


def test_capture_never_reads_stale_clipboard(application, monkeypatch):
    capture = SelectionCapture(application.clipboard())
    capture._baseline = 10
    capture._deadline = time.monotonic() - 1
    errors = []
    capture.failed.connect(errors.append)
    monkeypatch.setattr(capture_module, "clipboard_sequence", lambda: 10)
    monkeypatch.setattr(capture_module, "read_clipboard_text", lambda *a: pytest.fail("read stale clipboard"))
    capture._poll_clipboard()
    assert errors and "超时" in errors[0]


def test_capture_accepts_delayed_format_without_extra_copy(application, monkeypatch):
    capture = SelectionCapture(application.clipboard())
    capture._baseline = 10
    capture._deadline = time.monotonic() + 10
    capture._started = time.monotonic()
    captured = []
    capture.captured.connect(captured.append)
    monkeypatch.setattr(capture_module, "clipboard_sequence", lambda: 11)
    monkeypatch.setattr(capture_module, "read_clipboard_text", lambda *a: ("First line\nSecond line", "html"))
    capture._poll_clipboard()
    assert captured == ["First line\nSecond line"]
    assert not capture._pending


@pytest.mark.parametrize("suffix,raw,expected", [
    ("HTML Format", b"Version:0.9\r\n<html><!--StartFragment--><p>Hello</p><p>World</p><!--EndFragment--></html>", "Hello\nWorld"),
    ("Rich Text Format", br"{\rtf1\ansi First line\par Second line}", "First line\nSecond line"),
])
def test_native_rich_clipboard_formats(application, monkeypatch, suffix, raw, expected):
    monkeypatch.setattr(clipboard_module, "_native_format", lambda number: raw if number == clipboard_module.ctypes.windll.user32.RegisterClipboardFormatW(suffix) else b"")
    text, kind = clipboard_module.read_clipboard_text(application.clipboard())
    assert text.strip() == expected
    assert kind in {"html", "rtf"}


@pytest.mark.parametrize("raw,expected", [
    ("relay.example/custom/responses", "https://relay.example/custom"),
    ("https://relay.example/v1/v1", "https://relay.example/v1"),
    ("https://relay.example/custom", "https://relay.example/custom"),
])
def test_address_suggestions_preserve_custom_prefixes(raw, expected):
    assert address_suggestion(raw) == expected


def test_mouse_hook_accepts_new_pynput_injected_argument(application, monkeypatch):
    monitor = RightClickMonitor()
    monitor._active = True
    events = []
    monkeypatch.setattr(monitor, "_schedule_selection_probe", lambda x, y: events.append((x, y)))
    monitor._on_click(120, 180, Button.left, False, False)
    monitor._on_click(130, 190, Button.left, False, True)
    assert events == [(120, 180), (130, 190)]


def test_failed_configuration_save_restores_previous_key_and_keeps_draft(controller, monkeypatch):
    c, secrets, store = controller
    c.window.api_key_edit.selectAll()
    c.window.api_key_edit.insert("replacement")
    c.window.model_edit.setText("draft-model")
    def fail_save(*args):
        raise OSError("test disk failure")
    monkeypatch.setattr(store, "save", fail_save)
    c.window.save_button.click()
    assert secrets["test"] == "dummy-stored-key"
    assert c.settings.active_profile().model == "test-model"
    assert c.window.is_dirty()


def test_model_list_can_be_requested_before_choosing_model(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    c.window.model_edit.clear()
    c._provider_tools("models")
    assert len(c.thread_pool.tasks) == 1


def test_oversized_moved_panel_is_repositioned_outside_new_region(controller, application):
    c, _, _ = controller
    reader = c.screen_reader
    screen = application.primaryScreen()
    area = screen.availableGeometry()
    reader.panel.resize(min(700, area.width()), min(600, area.height()))
    reader.panel.user_moved = True
    region = QRect(area.left()+20, area.top()+20, min(400, area.width()-320), 100)
    assert reader.start_region(screen, region)
    assert not reader.panel.geometry().intersects(region)
    assert area.contains(reader.panel.geometry())
    reader.stop()


def test_empty_unicode_clipboard_uses_rich_text_fallback(application, monkeypatch):
    html_id = clipboard_module.ctypes.windll.user32.RegisterClipboardFormatW("HTML Format")
    formats = {13: b"\0\0", html_id: b"<p>Fallback text</p>"}
    monkeypatch.setattr(clipboard_module, "_native_format", lambda number: formats.get(number, b""))
    assert clipboard_module.read_clipboard_text(application.clipboard()) == ("Fallback text", "html")


def test_import_preview_does_not_expose_secret_and_offers_duplicate_choices(controller):
    from linguafloat.import_ui import ImportPreview
    c, _, _ = controller
    profile = c.settings.active_profile()
    preview = ImportPreview([profile], {profile.id: profile}, c.window)
    choice = preview.choices[0][1]
    assert [choice.itemData(i) for i in range(choice.count())] == ["add", "update", "skip"]
    assert preview.operations() == [(profile, "add")]
    preview.close()


def test_unsaved_key_test_does_not_mark_saved_active_configuration_as_passed(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    c.window.api_key_edit.selectAll()
    c.window.api_key_edit.insert("draft-key")
    header = c.window.active_connection_status.text()
    c.window.test_connection_button.click()
    task = c.thread_pool.tasks[0]
    task.signals.succeeded.emit(task.cache_scope, "Hello.", "Test reply")
    assert "连接成功" in c.window.connection_status.text()
    assert c.window.active_connection_status.text() == header
