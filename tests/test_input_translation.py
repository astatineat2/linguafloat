from types import SimpleNamespace

import pytest

import linguafloat.app as app_module
from linguafloat.input_capture import text_from_input
from linguafloat.translation import translation_scope
from test_app_controller import FakeThreadPool
from test_credentials_ui import application, controller
from test_selection import FakePattern, FakeRange


class InputControl:
    ProcessId = 42
    IsPassword = False
    ControlType = 50004
    Name = "Message"

    def __init__(self, value="你好", selection="", readonly=False, parent=None):
        self.parent = parent
        self.patterns = {
            10002: SimpleNamespace(Value=value, IsReadOnly=readonly),
            10014: FakePattern([FakeRange(selection)]),
        }

    def GetPattern(self, pattern_id):
        return self.patterns.get(pattern_id)

    def GetParentControl(self):
        return self.parent


def test_input_reads_selected_text_before_entire_edit():
    assert text_from_input(InputControl(value="whole document", selection="selected"), 99) == "selected"


def test_input_without_selection_reads_only_editable_field():
    assert text_from_input(InputControl(), 99) == "你好"
    with pytest.raises(ValueError):
        text_from_input(InputControl(readonly=True), 99)
    document = InputControl(value="entire page")
    document.ControlType = 50030
    with pytest.raises(ValueError):
        text_from_input(document, 99)


@pytest.mark.parametrize("field", ["password", "secret", "own", "parent", "length"])
def test_input_blocks_sensitive_fields_and_overlong_text(field):
    control = InputControl()
    if field == "password":
        control.IsPassword = True
    elif field == "secret":
        control.Name = "API key"
    elif field == "own":
        control.ProcessId = 99
    elif field == "parent":
        parent = InputControl()
        parent.IsPassword = True
        control = InputControl(selection="private", parent=parent)
    elif field == "length":
        control = InputControl(value="文" * 5001)
    with pytest.raises(ValueError):
        text_from_input(control, 99)


def begin_input(c, monkeypatch, text="你好", language="en"):
    c.settings.input_language = language
    monkeypatch.setattr(app_module, "foreground_process_id", lambda: 42)
    c._on_input_hotkey()
    capture = c.thread_pool.tasks[-1]
    capture.signals.succeeded.emit(capture.token, text)
    return c.thread_pool.tasks[-1]


def test_global_input_hotkey_translates_and_copies_without_pasting(controller, application, monkeypatch):
    c, _, store = controller
    c.thread_pool = FakeThreadPool()
    application.clipboard().setText("previous")
    task = begin_input(c, monkeypatch, language="ko")
    assert task.target_language == "ko"
    assert application.clipboard().text() == "previous"
    c._on_input_hotkey()
    assert len(c.thread_pool.tasks) == 2
    task.signals.succeeded.emit(task.cache_scope, task.source_text, "안녕하세요")
    assert application.clipboard().text() == "안녕하세요"
    assert c.overlay.translation.toPlainText() == "안녕하세요"
    assert c._input_pending is None
    assert "你好" not in store.path.read_text(encoding="utf-8")


def test_target_language_has_separate_cache_and_repeat_reuses_it(controller, application, monkeypatch):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    english = begin_input(c, monkeypatch, language="en")
    english.signals.succeeded.emit(english.cache_scope, "你好", "Hello")
    japanese = begin_input(c, monkeypatch, language="ja")
    assert japanese is not english and japanese.target_language == "ja"
    japanese.signals.succeeded.emit(japanese.cache_scope, "你好", "こんにちは")
    count = len(c.thread_pool.tasks)
    begin_input(c, monkeypatch, language="en")
    assert len(c.thread_pool.tasks) == count + 1  # Capture only; no new API task.
    assert application.clipboard().text() == "Hello"
    assert c.cache.get(c.settings.active_profile().cache_scope, "你好") is None


@pytest.mark.parametrize("change", ["language", "provider", "pause"])
def test_obsolete_input_result_does_not_copy(controller, application, monkeypatch, change):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    task = begin_input(c, monkeypatch)
    application.clipboard().setText("keep clipboard")
    if change == "language":
        c.settings.input_language = "ja"
    elif change == "provider":
        c.settings.active_provider = "second"
    else:
        c._set_paused(True)
    task.signals.succeeded.emit(task.cache_scope, "你好", "Hello")
    assert application.clipboard().text() == "keep clipboard"


def test_input_failure_keeps_clipboard_and_allows_retry(controller, application, monkeypatch):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    task = begin_input(c, monkeypatch)
    application.clipboard().setText("keep clipboard")
    task.signals.failed.emit(task.cache_scope, "你好", "failed")
    assert application.clipboard().text() == "keep clipboard"
    assert c._input_pending is None
    begin_input(c, monkeypatch)
    assert len(c.thread_pool.tasks) == 4


def test_input_timeout_discards_late_capture(controller, monkeypatch):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    monkeypatch.setattr(app_module, "foreground_process_id", lambda: 42)
    c._on_input_hotkey()
    task = c.thread_pool.tasks[0]
    c._input_capture_timeout(task.token)
    task.signals.succeeded.emit(task.token, "你好")
    assert len(c.thread_pool.tasks) == 1
    assert c._input_pending is None and c._input_capture_token is None


def test_language_selection_persists_and_hotkeys_cannot_collide(controller, monkeypatch):
    c, _, store = controller
    calls = []
    monkeypatch.setattr(c.input_hotkey, "start", lambda value, callback: calls.append(value))
    c.window.input_language_combo.setCurrentIndex(c.window.input_language_combo.findData("ja"))
    assert store.load().input_language == "ja"
    c._save_input_hotkey("Ctrl+Alt+T")
    assert store.load().input_hotkey == "Ctrl+Alt+Y"
    c._save_input_hotkey("Ctrl+Alt+U")
    assert store.load().input_hotkey == "Ctrl+Alt+U"
