import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from linguafloat.screen_ui import ScreenTranslationPanel
from test_credentials_ui import application, controller


def test_long_static_translation_settles_and_preserves_text(application, monkeypatch):
    panel = ScreenTranslationPanel()
    calls = []
    original = panel.translation.setHtml
    monkeypatch.setattr(panel.translation, "setHtml", lambda text: (calls.append(text), original(text))[-1])
    text = "磁场测量结果保持不变，单位和变量 B 均保留。" * 35
    try:
        panel.show()
        panel.show_blocks([("source", text, "done")] * 5)
        QTest.qWait(150)
        settled = len(calls)
        QTest.qWait(200)
        assert len(calls) == settled <= 2
        panel.show_blocks([("source", text, "done")] * 5)
        panel.resize(330, 250)
        QTest.qWait(100)
        assert len(calls) == settled
        assert "".join(panel.translation.toPlainText().split()) == "".join((text * 5).split())
    finally:
        panel.hide(); panel.deleteLater()


def test_function_save_commits_both_shortcuts_together(controller, monkeypatch):
    c, _, store = controller
    registrations = []
    monkeypatch.setattr(c, "_configure_triggers", lambda key, mode: registrations.append((key, c.settings.input_hotkey)))
    c.window.hotkey_edit.setText("Ctrl+Shift+F8")
    c.window.input_hotkey_edit.setText("Ctrl+Shift+F9")
    c.window.function_save_button.click()
    saved = store.load()
    assert (saved.hotkey, saved.input_hotkey) == ("Ctrl+Shift+F8", "Ctrl+Shift+F9")
    assert registrations and all(pair == (saved.hotkey, saved.input_hotkey) for pair in registrations)
    assert not hasattr(c.window, "input_hotkey_save")


def test_function_save_conflict_or_write_error_restores_both(controller, monkeypatch):
    c, _, store = controller
    shared_settings = c.settings
    previous = (c.settings.hotkey, c.settings.input_hotkey)
    monkeypatch.setattr(c, "_configure_triggers", lambda *a: None)
    c.window.hotkey_edit.setText("Ctrl+Shift+F8")
    c.window.input_hotkey_edit.setText("Ctrl+Shift+F8")
    c._save_function_settings()
    assert (c.settings.hotkey, c.settings.input_hotkey) == previous
    monkeypatch.setattr(store, "save", lambda _: (_ for _ in ()).throw(OSError("test disk failure")))
    c.window.hotkey_edit.setText("Ctrl+Shift+F8")
    c.window.input_hotkey_edit.setText("Ctrl+Shift+F9")
    c._save_function_settings()
    assert (c.settings.hotkey, c.settings.input_hotkey) == previous
    assert (c.window.hotkey_edit.text(), c.window.input_hotkey_edit.text()) == previous
    assert c.settings is shared_settings and c.documents.settings is shared_settings
