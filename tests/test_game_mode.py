from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRect

import linguafloat.app as app_module
import linguafloat.selection as selection_module
from linguafloat.capture import SelectionCapture
from linguafloat.config import AppSettings, ConfigStore
from linguafloat.game_mode import GameModeMonitor, normalize_game_paths
from linguafloat.hotkeys import GlobalHotkey, HotkeyError
from test_app_controller import FakeThreadPool
from test_credentials_ui import application, controller


class Windows:
    current = 100
    pids = {100: 10, 200: 20, 300: 99, 400: 40}
    paths = {10: r"C:\Games\Sample.exe", 20: r"C:\Tools\Editor.exe", 40: ""}

    def foreground(self):
        return self.current

    def process_id(self, hwnd):
        return self.pids.get(hwnd, 0)

    def path(self, pid):
        return self.paths.get(pid, "")

    def valid(self, hwnd, pid):
        return hwnd in self.pids and self.pids[hwnd] == pid


def monitor(application):
    windows = Windows()
    windows.pids = dict(Windows.pids)
    return GameModeMonitor(windows=windows, own_pid=99), windows


def test_auto_matches_full_path_and_retains_game_on_own_windows(application):
    m, w = monitor(application)
    m.configure("auto", [r"c:\GAMES\sample.EXE"])
    assert m.active and not m.allows_capture()
    w.current = 300
    m.refresh()
    assert m.active
    w.current = 200
    m.refresh()
    assert not m.active and m.allows_capture()
    m.configure("auto", [r"D:\Games\Editor.exe"])
    assert not m.active  # Same filename in another folder is not a match.


def test_game_exit_while_own_control_has_focus_restores_mode(application):
    m, w = monitor(application)
    m.configure("auto", [Windows.paths[10]])
    w.current = 300
    del w.pids[100]
    m.refresh()
    assert not m.active


def test_manual_overrides_and_removing_game_rechecks_retained_window(application):
    m, w = monitor(application)
    m.configure("off", [Windows.paths[10]])
    assert not m.active and m.allows_capture()
    m.configure("on", [])
    w.current = 200
    m.refresh()
    assert m.active
    w.current = 100
    m.configure("auto", [Windows.paths[10]])
    w.current = 300
    m.configure("auto", [])
    assert not m.active


def test_guard_blocks_selection_before_foreground_event_arrives(application):
    m, w = monitor(application)
    w.current = 200
    m.configure("auto", [Windows.paths[10]])
    w.current = 100
    assert not m.active
    assert not m.allows_capture()
    m.refresh()
    assert m.active


def test_unknown_and_transient_foreground_do_not_drop_active_protection(application):
    m, w = monitor(application)
    m.configure("auto", [Windows.paths[10]])
    w.current = 0
    m.refresh()
    assert m.active
    w.current = 400
    m.refresh()
    assert m.active and "无法读取" in m.reason
    assert not m.allows_capture()


def test_real_windows_foreground_observer_registers_and_stops(application):
    m = GameModeMonitor()
    try:
        m.configure("auto", [])
        m.start()
        assert m._hook and m.timer.isActive()
        assert isinstance(m.windows.foreground(), int)
        m.configure("on", [])
        assert not m._hook and not m.timer.isActive() and m.active
        m.configure("off", [])
        assert not m.active
    finally:
        m.stop()


def test_saving_trigger_settings_during_game_does_not_restart_any_listener(controller, monkeypatch):
    c, _, store = controller
    c._save_game_mode("on")
    def forbidden(*args, **kwargs):
        pytest.fail("Saving settings restarted a game-blocked listener")
    for obj in (c.hotkey, c.input_hotkey, c.right_click):
        monkeypatch.setattr(obj, "start", forbidden)
    # Restore the real implementation patched by the shared UI fixture.
    original = _CONFIGURE_TRIGGERS
    monkeypatch.setattr(c, "_configure_triggers", lambda *args: original(c, *args))
    c._save_input_hotkey("Ctrl+Alt+U")
    c.window.hotkey_edit.setText("Ctrl+Alt+G")
    c.window.save_button.click()
    assert store.load().hotkey == "Ctrl+Alt+G"
    assert store.load().input_hotkey == "Ctrl+Alt+U"


_CONFIGURE_TRIGGERS = app_module.AppController._configure_triggers


def test_config_migrates_and_round_trips_game_settings(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppSettings(game_mode="auto", game_paths=[r"C:\Games\Test.exe"]))
    loaded = store.load()
    assert loaded.game_mode == "auto" and loaded.game_paths == [r"C:\Games\Test.exe"]
    assert AppSettings().game_mode == "off"
    assert normalize_game_paths([None, "test.exe", "C:\\a.txt", r"C:\Games\Test.exe", r"c:\games\test.EXE"]) == [r"C:\Games\Test.exe"]


def test_entering_game_stops_all_hooks_and_never_starts_screen_reader(controller, monkeypatch):
    c, _, _ = controller
    calls = []
    for obj, label in [(c.hotkey, "selection"), (c.input_hotkey, "input"), (c.right_click, "mouse")]:
        monkeypatch.setattr(obj, "stop", lambda name=label: calls.append(name))
    c._save_game_mode("on")
    assert set(calls) == {"selection", "input", "mouse"}
    assert not c.screen_reader.state.running and not c.screen_reader.timer.isActive()
    assert c.game_actions["on"].isChecked()
    assert "仅保留区域翻译" in c.window.status_label.text()


def test_game_rejects_all_ordinary_entry_points_before_reading(controller, monkeypatch):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    def forbidden(*args, **kwargs):
        pytest.fail("Game mode performed an ordinary capture")
    monkeypatch.setattr(app_module, "read_current_selection", forbidden)
    monkeypatch.setattr(c.capture, "begin", forbidden)
    monkeypatch.setattr(app_module, "foreground_process_id", forbidden)
    c._save_game_mode("on")
    for _ in range(8):
        c._on_hotkey()
        c._on_input_hotkey()
        c._show_right_click_action("Hello", 20, 20)
        c._translate("Hello")
        c._submit_translation("你好", "overlay_input", target_language="en")
    assert not c.thread_pool.tasks and not c.action_button.isVisible() and not c.overlay.isVisible()


@pytest.mark.parametrize("leave_game", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_old_requests_never_show_or_copy_but_shared_screen_target_survives(controller, application, monkeypatch, leave_game, failure):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    delivered = []
    monkeypatch.setattr(c.screen_reader, "translated", lambda *args, **kw: delivered.append(args))
    c._translate("Hello")
    task = c.thread_pool.tasks[-1]
    c._translate_screen("Hello", 17)
    assert len(c.thread_pool.tasks) == 1
    c._input_pending = (1, c.settings.active_profile().cache_scope, "en")
    c._submit_translation("你好", "input", 1, "en")
    input_task = c.thread_pool.tasks[-1]
    c._save_game_mode("on")
    if leave_game:
        c._save_game_mode("off")
    application.clipboard().setText("keep this")
    for pending in (task, input_task):
        signal = pending.signals.failed if failure else pending.signals.succeeded
        signal.emit(pending.cache_scope, pending.source_text, "result")
    assert delivered == [(17, "Hello", "result")]
    assert not c.overlay.isVisible()
    assert application.clipboard().text() == "keep this"


def test_pending_capture_and_queued_shortcut_cannot_revive_after_game(controller, monkeypatch):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    monkeypatch.setattr(app_module, "foreground_process_id", lambda: 42)
    c._on_input_hotkey()
    task = c.thread_pool.tasks[0]
    generation = c._ordinary_generation
    c._save_game_mode("on")
    c._save_game_mode("off")
    task.run()  # Cancelled task must return before importing/reading UIA.
    task.signals.succeeded.emit(task.token, "stale")
    c._on_hotkey(generation)
    c._on_input_hotkey(generation)
    assert len(c.thread_pool.tasks) == 1


def test_queued_api_work_is_cancelled_before_creating_client(controller, monkeypatch):
    import linguafloat.translation as translation_module
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    c._translate("Hello")
    task = c.thread_pool.tasks[0]
    c._save_game_mode("on")
    monkeypatch.setattr(translation_module, "OpenAITranslator", lambda *args, **kwargs: pytest.fail("Cancelled work called API"))
    task.run()
    assert not c._inflight_requests and not c._request_targets


def test_screen_joining_cancelled_work_is_resubmitted(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    c._translate("Hello")
    task = c.thread_pool.tasks[0]
    c._save_game_mode("on")
    assert not task.allowed()
    reader = c.screen_reader
    reader.active_scope = c.settings.active_profile().cache_scope
    reader.state.running = True
    reader.state.text = "Hello"
    reader.state.observations = 2
    reader.state.pending = (5, "Hello", reader.state.generation)
    c._translate_screen("Hello", 5)
    task.signals.cancelled.emit(task.cache_scope, "Hello", "auto")
    assert len(c.thread_pool.tasks) == 2
    assert c.thread_pool.tasks[1].allowed()


def test_pause_intent_and_trigger_edits_survive_game_transitions(controller, monkeypatch):
    c, _, store = controller
    starts = []
    monkeypatch.setattr(c, "_configure_triggers", lambda *args: starts.append(args))
    c._set_paused(True)
    c._save_game_mode("on")
    c._save_input_hotkey("Ctrl+Alt+U")
    c.window.trigger_mode_combo.setCurrentIndex(c.window.trigger_mode_combo.findData("right_click"))
    c.window.save_button.click()
    c._save_game_mode("off")
    assert c.paused and not starts
    assert store.load().input_hotkey == "Ctrl+Alt+U"
    c._set_paused(False)
    assert starts == [(c.settings.hotkey, "right_click")]


def test_restoration_failure_is_visible_and_does_not_pause_ocr(controller, monkeypatch):
    c, _, _ = controller
    c.screen_reader.state.running = True
    c._save_game_mode("on")
    def fail(*args):
        raise HotkeyError("Ctrl+Alt+Y 已被其他软件占用")
    monkeypatch.setattr(c, "_configure_triggers", fail)
    c._save_game_mode("off")
    assert "已被其他软件占用" in c.window.status_label.text()
    assert c.screen_reader.state.running and not c.paused
    assert not c._ordinary_allowed()
    monkeypatch.setattr(c, "_configure_triggers", lambda *args: None)
    c.window.pause_button.click()
    assert c._ordinary_allowed() and "正在监听" in c.window.status_label.text()


def test_game_self_test_reports_intentional_disable_and_still_checks_ocr_api(controller, monkeypatch):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    monkeypatch.setattr(c.screen_reader.pool, "start", c.thread_pool.start)
    c._save_game_mode("on")
    c._run_self_test()
    c.thread_pool.tasks[0].signals.succeeded.emit(0, b"", "Hello")
    task = c.thread_pool.tasks[1]
    task.signals.succeeded.emit(task.cache_scope, task.source_text, "你好")
    report = c.window.self_test_result.toPlainText()
    assert report.count("因游戏模式停用") == 3
    assert "OCR：通过" in report and "API：通过" in report and "未通过" not in report


@pytest.mark.parametrize("attribute", ["input_hotkey", "hotkey"])
def test_native_input_shortcut_is_released_in_game(controller, attribute):
    c, _, _ = controller
    hotkey = getattr(c, attribute)
    other = GlobalHotkey(native=True)
    try:
        hotkey.start("Ctrl+Alt+Shift+F10", lambda: None)
        with pytest.raises(HotkeyError):
            other.start("Ctrl+Alt+Shift+F10", lambda: None)
        c._save_game_mode("on")
        other.start("Ctrl+Alt+Shift+F10", lambda: None)
        assert hotkey._listener is None
    finally:
        other.stop()
        hotkey.stop()


def test_clipboard_delayed_copy_is_cancelled_and_cannot_join_new_capture(application, monkeypatch):
    import linguafloat.capture as capture_module
    callbacks = []
    monkeypatch.setattr(capture_module.QTimer, "singleShot", lambda delay, fn: callbacks.append(fn))
    monkeypatch.setattr(capture_module, "shortcut_modifiers_pressed", lambda: False)
    c = SelectionCapture(application.clipboard())
    copies = []
    c._controller = SimpleNamespace(press=lambda key: copies.append(key), release=lambda key: None)
    application.clipboard().setText("original")
    c.begin()
    callbacks.pop(0)()  # Sentinel written, copy not yet sent.
    c.cancel()
    assert application.clipboard().text() == "original"
    c.begin()
    callbacks.pop(0)()  # Previous session's pending Ctrl+C.
    assert not copies
    c.cancel()


def test_cancel_does_not_overwrite_new_clipboard_content(application, monkeypatch):
    import linguafloat.capture as capture_module
    callbacks = []
    monkeypatch.setattr(capture_module.QTimer, "singleShot", lambda delay, fn: callbacks.append(fn))
    monkeypatch.setattr(capture_module, "shortcut_modifiers_pressed", lambda: False)
    c = SelectionCapture(application.clipboard())
    c.begin()
    callbacks.pop(0)()
    application.clipboard().setText("user copied this")
    c.cancel()
    assert application.clipboard().text() == "user copied this"


def test_stopped_right_click_listener_and_delayed_probe_never_read_selection(application, monkeypatch):
    callbacks = []
    class Listener:
        def __init__(self, **handlers):
            self.handlers = handlers
        def start(self):
            pass
        def stop(self):
            pass
    monkeypatch.setattr(selection_module, "ensure_uiautomation_available", lambda: None)
    monkeypatch.setattr(selection_module.mouse, "Listener", Listener)
    monkeypatch.setattr(selection_module.keyboard, "Listener", Listener)
    monkeypatch.setattr(selection_module.threading, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: callbacks.append(target)))
    monkeypatch.setattr(selection_module, "read_selection_at", lambda *args, **kw: pytest.fail("Unexpected selection read"))
    m = selection_module.RightClickMonitor()
    m.start()
    old_mouse = m._mouse_listener
    m._schedule_selection_probe(10, 10)
    old_session = m._session
    emitted = []
    m.selection_found.connect(lambda *args: emitted.append(args))
    m.stop()
    m.start()
    callbacks[0]()
    old_mouse.handlers["on_click"](10, 10, selection_module.mouse.Button.right, True)
    m._found.emit(old_session, "stale", 10, 10)
    assert not emitted
    m.stop()


def test_game_preserves_region_refresh_resize_move_and_pause(controller, application):
    c, _, _ = controller
    reader = c.screen_reader
    screen = application.primaryScreen()
    bounds = screen.availableGeometry()
    region = QRect(bounds.x() + 30, bounds.y() + 30, 150, 100)
    try:
        assert reader.start_region(screen, region)
        c._save_game_mode("on")
        assert reader.state.running and (reader.timer.isActive() or reader.settle_timer.isActive())
        reader._outline_move_started()
        moved = region.adjusted(20, 20, 60, 40)
        reader._outline_move_finished(moved)
        assert reader.region == moved and reader.state.running
        reader.panel.user_moved = True
        reader._outline_move_started()
        reader._outline_move_finished(region)
        assert reader.region == region and reader.state.running
        reader.toggle_pause()
        assert not reader.state.running
        c._save_game_mode("off")
        assert not reader.state.running
        reader.toggle_pause()
        assert reader.state.running
    finally:
        reader.stop()
