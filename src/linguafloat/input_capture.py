from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from linguafloat.selection import MAX_SELECTION_LENGTH, normalize_selection, selected_text_from_control


def foreground_process_id() -> int:
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    process = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(process))
    return process.value


def text_from_input(control, own_process_id: int, *, allowed=lambda: True) -> str:
    if not allowed():
        return ""
    if control is None or control.ProcessId == own_process_id:
        raise ValueError("请先切换到需要翻译的输入框。")
    # Inspect the focused control and its nearby ancestors before reading any value.
    ancestors = []
    current = control
    for _ in range(8):
        if not allowed():
            return ""
        if current is None:
            break
        if current.IsPassword:
            raise ValueError("密码输入框不能取词。")
        if getattr(current, "ControlType", 0) == 50004:
            name = getattr(current, "Name", "").lower()
            if any(label in name for label in ("password", "api key", "api_key", "secret", "密钥", "密码")):
                raise ValueError("敏感输入框不能取词。")
        ancestors.append(current)
        try:
            current = current.GetParentControl()
        except Exception:
            break
    candidate = selected_text_from_control(control, 0, 0, own_process_id=own_process_id, allowed=allowed)
    if candidate is not None:
        return candidate.text
    for current in ancestors[:3]:
        if not allowed():
            return ""
        if getattr(current, "ControlType", 0) != 50004:
            continue
        value_pattern = current.GetPattern(10002)
        if value_pattern is not None:
            if value_pattern.IsReadOnly:
                continue
            return normalize_selection(value_pattern.Value)
        text_pattern = current.GetPattern(10014)
        if text_pattern is not None:
            document = text_pattern.DocumentRange
            if document.GetAttributeValue(40015) is False:
                return normalize_selection(document.GetText(MAX_SELECTION_LENGTH + 1))
    raise ValueError("无法直接读取这个输入框，请先选中要翻译的文字，再按输入翻译快捷键。")


class InputCaptureSignals(QObject):
    succeeded = Signal(int, str)
    failed = Signal(int, str)


class InputCaptureTask(QRunnable):
    def __init__(self, token: int, source_process: int, own_process: int, *, allowed=None) -> None:
        super().__init__()
        self.token, self.source_process, self.own_process = token, source_process, own_process
        self.signals = InputCaptureSignals()
        self.allowed = allowed or (lambda: True)

    @Slot()
    def run(self) -> None:
        if not self.allowed():
            return
        try:
            from uiautomation import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                if not self.allowed():
                    return
                control = auto.GetFocusedControl()
                if control is None or control.ProcessId != self.source_process:
                    raise ValueError("输入焦点已改变，请回到原输入框后重试。")
                if not self.allowed():
                    return
                text = text_from_input(control, self.own_process, allowed=self.allowed)
                if not text:
                    raise ValueError("当前输入框没有可翻译的文字。")
        except ValueError as exc:
            self.signals.failed.emit(self.token, str(exc))
        except Exception:
            self.signals.failed.emit(self.token, "无法读取输入内容，请选中文字后重试。")
        else:
            if self.allowed():
                self.signals.succeeded.emit(self.token, text)
