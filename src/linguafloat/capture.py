from __future__ import annotations

import ctypes
import sys
import time
from collections import deque
from pynput import keyboard
from PySide6.QtCore import QMimeData, QObject, QTimer, Signal
from PySide6.QtGui import QClipboard
from linguafloat.clipboard_io import clipboard_sequence, foreground_window, read_clipboard_text

_MODIFIER_VIRTUAL_KEYS = (0x10, 0x11, 0x12, 0x5B, 0x5C)


def shortcut_modifiers_pressed(state_reader=None) -> bool:
    if sys.platform != "win32":
        return False
    reader = state_reader or ctypes.windll.user32.GetAsyncKeyState
    return any(reader(key) & 0x8000 for key in _MODIFIER_VIRTUAL_KEYS)


class SelectionCapture(QObject):
    captured = Signal(str)
    failed = Signal(str)
    diagnostic = Signal(str)

    def __init__(self, clipboard: QClipboard, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._clipboard = clipboard
        self._controller = keyboard.Controller()
        self._pending = False
        self._generation = 0
        self._saved_mime = None
        self._copy_sequence = None
        self.allowed = lambda: True
        self.history = deque(maxlen=20)

    def note(self, stage: str) -> None:
        self.history.append(stage)
        self.diagnostic.emit("\n".join(self.history))

    def _later(self, delay: int, callback) -> None:
        generation = self._generation
        def run():
            if generation != self._generation or not self._pending:
                return
            if not self.allowed():
                self.cancel()
                return
            if foreground_window() != self._source_window:
                self._finish_with_error("取词已取消：原窗口焦点已改变。")
                return
            callback()
        QTimer.singleShot(delay, run)

    def cancel(self) -> None:
        self._generation += 1
        self._pending = False
        self._saved_mime = None
        self._copy_sequence = None

    def begin(self) -> None:
        if self._pending or not self.allowed():
            return
        self._pending = True
        self._generation += 1
        self._source_window = foreground_window()
        self._release_deadline = time.monotonic() + 2.0
        self._started = time.monotonic()
        self.note("选区接口未返回文字，等待快捷键松开")
        self._later(40, self._wait_for_shortcut_release)

    def _wait_for_shortcut_release(self) -> None:
        if shortcut_modifiers_pressed():
            if time.monotonic() < self._release_deadline:
                self._later(40, self._wait_for_shortcut_release)
            else:
                self._finish_with_error("请松开快捷键后再试一次。")
            return
        try:
            self._saved_mime = self._clone_mime_data(self._clipboard.mimeData())
        except Exception:
            self._saved_mime = None
        self._later(30, self._send_copy)

    def _send_copy(self) -> None:
        self._baseline = clipboard_sequence()
        try:
            self._controller.press(keyboard.Key.ctrl_l)
            try:
                self._controller.press("c")
                self._controller.release("c")
            finally:
                self._controller.release(keyboard.Key.ctrl_l)
        except Exception:
            self._finish_with_error("无法发送复制命令，请尝试手动复制后使用剪贴板翻译。")
            return
        self.note("已发送一次复制，等待剪贴板更新")
        self._deadline = time.monotonic() + 2.5
        self._later(60, self._poll_clipboard)

    def _poll_clipboard(self) -> None:
        sequence = clipboard_sequence()
        updated = sequence != self._baseline
        if updated:
            try:
                text, kind = read_clipboard_text(self._clipboard)
            except OSError:
                text, kind = "", "busy"
            if text.strip():
                self._copy_sequence = sequence
                self._restore_clipboard()
                self._pending = False
                self.note(f"复制成功，格式 {kind}，耗时 {time.monotonic() - self._started:.2f} 秒")
                self.captured.emit(text.strip())
                return
        if time.monotonic() >= self._deadline:
            message = ("复制结果没有可读取的文字，可能是图片或对象；可使用框选识别。" if updated else
                       "复制超时：原软件未更新剪贴板。请确认正在编辑文字，或手动复制后使用剪贴板翻译。")
            self._finish_with_error(message)
            return
        self._later(80, self._poll_clipboard)

    def _finish_with_error(self, message: str) -> None:
        self.cancel()
        self.note(message)
        self.failed.emit(message)

    def _restore_clipboard(self) -> None:
        if self._saved_mime is not None and clipboard_sequence() == self._copy_sequence:
            self._clipboard.setMimeData(self._saved_mime)
        self._saved_mime = None

    @staticmethod
    def _clone_mime_data(source: QMimeData | None) -> QMimeData:
        clone = QMimeData()
        if source is not None:
            for mime_type in source.formats():
                clone.setData(mime_type, source.data(mime_type))
        return clone
