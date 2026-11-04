import pytest

from linguafloat.hotkeys import HotkeyError, parse_hotkey


def test_parse_default_hotkey():
    spec = parse_hotkey("ctrl + alt + t")

    assert spec.display == "Ctrl+Alt+T"
    assert spec.pynput == "<ctrl>+<alt>+t"


def test_parse_function_key():
    spec = parse_hotkey("Shift+F8")

    assert spec.display == "Shift+F8"
    assert spec.pynput == "<shift>+<f8>"


@pytest.mark.parametrize("value", ["T", "Ctrl+Alt", "Ctrl+Alt+T+Y", "Ctrl+Space"])
def test_reject_invalid_hotkeys(value):
    with pytest.raises(HotkeyError):
        parse_hotkey(value)
from types import SimpleNamespace
from pynput import keyboard
from linguafloat.hotkeys import canonical_hotkey_key


def test_windows_ime_virtual_key_hotkey_and_release_are_recognized():
    calls = []
    listener = SimpleNamespace(canonical=lambda key: key)
    hotkey = keyboard.HotKey(keyboard.HotKey.parse("<ctrl>+<alt>+y"), lambda: calls.append(True))
    keys = [keyboard.Key.ctrl, keyboard.Key.alt, keyboard.KeyCode.from_vk(0x59)]
    for key in keys:
        hotkey.press(canonical_hotkey_key(listener, key))
    hotkey.press(canonical_hotkey_key(listener, keys[-1]))
    assert calls == [True]
    for key in reversed(keys):
        hotkey.release(canonical_hotkey_key(listener, key))
    for key in keys:
        hotkey.press(canonical_hotkey_key(listener, key))
    assert calls == [True, True]


def test_windows_native_shortcut_receives_hotkey_and_releases_registration():
    import os
    from pathlib import Path
    import subprocess
    import sys
    # Native WM_HOTKEY needs the Windows Qt dispatcher, whereas the other UI
    # tests intentionally use an isolated offscreen clipboard.
    result = subprocess.run(
        [sys.executable, "-c", "import runpy, sys; runpy.run_path(sys.argv[1])['_exercise_native_hotkey']()", str(Path(__file__).resolve())],
        env={**os.environ, "QT_QPA_PLATFORM": "windows"}, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise_native_hotkey():
    import ctypes
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
    from linguafloat.hotkeys import GlobalHotkey, HotkeyError
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.setWindowTitle("LinguaFloat shortcut test")
    window.show()
    QTest.qWait(150)
    calls = []
    binding = GlobalHotkey(native=True)
    other = GlobalHotkey(native=True)
    try:
        binding.start("Ctrl+Alt+Shift+F11", lambda: calls.append(True))
        with pytest.raises(HotkeyError):
            other.start("Ctrl+Alt+Shift+F11", lambda: None)
        for vk in (17, 18, 16, 0x7A):
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        for vk in (0x7A, 16, 18, 17):
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        QTest.qWait(500)
        assert calls == [True]
        binding.stop()
        other.start("Ctrl+Alt+Shift+F11", lambda: None)
    finally:
        binding.stop()
        other.stop()
        window.close()
