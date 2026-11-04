from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from PySide6.QtGui import QTextDocument


def foreground_window() -> int:
    if sys.platform != "win32":
        return 0
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = wintypes.HWND
    return user.GetForegroundWindow() or 0


def clipboard_sequence() -> int:
    return ctypes.windll.user32.GetClipboardSequenceNumber() if sys.platform == "win32" else 0


def _native_format(format_id: int) -> bytes:
    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.restype = ctypes.c_size_t
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    handle = user.GetClipboardData(format_id)
    if not handle:
        return b""
    size = kernel.GlobalSize(handle)
    if not size or size > 4 * 1024 * 1024:
        return b""
    pointer = kernel.GlobalLock(handle)
    if not pointer:
        return b""
    try:
        return ctypes.string_at(pointer, size)
    finally:
        kernel.GlobalUnlock(handle)


def read_clipboard_text(clipboard) -> tuple[str, str]:
    """Read native formats first; Qt may cache content it previously owned."""
    if sys.platform != "win32":
        return clipboard.text(), "text"
    user = ctypes.windll.user32
    if not user.OpenClipboard(None):
        raise OSError("剪贴板正被其他程序占用")
    try:
        raw = _native_format(13)
        if raw:
            text = raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]
            if text.strip():
                return text, "unicode"
        user.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        html = _native_format(user.RegisterClipboardFormatW("HTML Format"))
        rtf = _native_format(user.RegisterClipboardFormatW("Rich Text Format"))
    finally:
        user.CloseClipboard()
    if html:
        value = html.decode("utf-8", errors="replace").rstrip("\0")
        start, end = value.find("<!--StartFragment-->"), value.find("<!--EndFragment-->")
        value = value[start + 20:end] if start >= 0 and end > start else value[value.find("<"):]
        document = QTextDocument()
        document.setHtml(value)
        if document.toPlainText().strip():
            return document.toPlainText(), "html"
    if rtf:
        from striprtf.striprtf import rtf_to_text
        try:
            return rtf_to_text(rtf.rstrip(b"\0").decode("latin-1"), errors="replace"), "rtf"
        except (ValueError, IndexError):
            return "", "invalid-rtf"
    return "", "non-text"
