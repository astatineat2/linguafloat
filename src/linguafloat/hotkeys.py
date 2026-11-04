from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
from itertools import count
import sys
from typing import Callable

from pynput import keyboard
from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication


class HotkeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HotkeySpec:
    display: str
    pynput: str


_MODIFIERS = {
    "ctrl": ("Ctrl", "<ctrl>"),
    "control": ("Ctrl", "<ctrl>"),
    "alt": ("Alt", "<alt>"),
    "shift": ("Shift", "<shift>"),
    "win": ("Win", "<cmd>"),
    "windows": ("Win", "<cmd>"),
    "meta": ("Win", "<cmd>"),
}
_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Win")


def parse_hotkey(value: str) -> HotkeySpec:
    parts = [part.strip().lower() for part in value.split("+") if part.strip()]
    if not parts:
        raise HotkeyError("快捷键不能为空。")

    modifiers: dict[str, str] = {}
    regular_keys: list[str] = []
    for part in parts:
        if part in _MODIFIERS:
            display, pynput_name = _MODIFIERS[part]
            modifiers[display] = pynput_name
        else:
            regular_keys.append(part)

    if not modifiers:
        raise HotkeyError("快捷键至少需要 Ctrl、Alt、Shift 或 Win 中的一个修饰键。")
    if len(regular_keys) != 1:
        raise HotkeyError("快捷键必须包含且只包含一个普通按键。")

    key = regular_keys[0]
    if len(key) == 1 and key.isalnum():
        display_key = key.upper()
        pynput_key = key.lower()
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        display_key = key.upper()
        pynput_key = f"<{key}>"
    else:
        raise HotkeyError("普通按键仅支持字母、数字或 F1-F12。")

    ordered = [name for name in _MODIFIER_ORDER if name in modifiers]
    display = "+".join([*ordered, display_key])
    pynput_value = "+".join([*(modifiers[name] for name in ordered), pynput_key])
    return HotkeySpec(display=display, pynput=pynput_value)


_HOTKEY_IDS = count(0x6000)


class WindowsHotkey(QAbstractNativeEventFilter):
    def __init__(self, spec: HotkeySpec, callback: Callable[[], None]) -> None:
        super().__init__()
        self.callback = callback
        self.application = QCoreApplication.instance()
        self.identifier = next(_HOTKEY_IDS)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        parts = spec.display.split("+")
        modifiers = 0x4000  # MOD_NOREPEAT
        for name in parts[:-1]:
            modifiers |= {"Ctrl": 0x0002, "Alt": 0x0001, "Shift": 0x0004, "Win": 0x0008}[name]
        key = parts[-1]
        vk = 0x70 + int(key[1:]) - 1 if len(key) > 1 and key.startswith("F") else ord(key)
        if not self.user32.RegisterHotKey(None, self.identifier, modifiers, vk):
            raise HotkeyError(f"无法注册快捷键 {spec.display}，可能已被其他软件占用。")
        self.application.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if msg.message == 0x0312 and msg.wParam == self.identifier:
            self.callback()
            return True, 0
        return False, 0

    def stop(self) -> None:
        self.application.removeNativeEventFilter(self)
        self.user32.UnregisterHotKey(None, self.identifier)


class GlobalHotkey:
    def __init__(self, *, native: bool = False) -> None:
        self.use_native = native
        self._listener: keyboard.Listener | WindowsHotkey | None = None
        self._generation = 0

    def start(self, value: str, callback: Callable[[], None]) -> HotkeySpec:
        spec = parse_hotkey(value)
        self.stop()
        generation = self._generation
        def activate():
            if generation == self._generation:
                callback()
        if self.use_native and sys.platform == "win32" and QCoreApplication.instance() is not None:
            self._listener = WindowsHotkey(spec, activate)
            return spec
        try:
            hotkey = keyboard.HotKey(keyboard.HotKey.parse(spec.pynput), activate)
            listener = keyboard.Listener(
                on_press=lambda key: hotkey.press(canonical_hotkey_key(listener, key)),
                on_release=lambda key: hotkey.release(canonical_hotkey_key(listener, key)),
            )
            listener.start()
        except Exception as exc:
            raise HotkeyError(f"无法注册快捷键 {spec.display}。") from exc
        self._listener = listener
        return spec

    def stop(self) -> None:
        self._generation += 1
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


def canonical_hotkey_key(listener, key):
    # Windows IMEs may report Ctrl+Alt+letter as a VK with no character.
    vk = getattr(key, "vk", None)
    if sys.platform == "win32" and vk is not None and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
        return keyboard.KeyCode.from_char(chr(vk).lower())
    return listener.canonical(key)
