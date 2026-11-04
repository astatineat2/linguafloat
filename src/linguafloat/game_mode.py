from __future__ import annotations

import ctypes
from ctypes import wintypes
import ntpath
import os
import threading

from PySide6.QtCore import QObject, QTimer, Signal, Slot


GAME_MODES = {"auto": "自动", "on": "始终开启", "off": "关闭"}


def normalize_game_paths(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        path = ntpath.normpath(value.strip())
        key = ntpath.normcase(path)
        if not ntpath.isabs(path) or not key.endswith(".exe") or key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


class ForegroundWindows:
    def __init__(self) -> None:
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.IsWindow.argtypes = [wintypes.HWND]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.callback_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                              wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
        self.user.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                                            self.callback_type, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        self.user.SetWinEventHook.restype = wintypes.HANDLE
        self.user.UnhookWinEvent.argtypes = [wintypes.HANDLE]

    def foreground(self) -> int:
        return self.user.GetForegroundWindow() or 0

    def process_id(self, hwnd: int) -> int:
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def valid(self, hwnd: int, pid: int) -> bool:
        return bool(hwnd and self.user.IsWindow(hwnd) and self.process_id(hwnd) == pid)

    def path(self, pid: int) -> str:
        handle = self.kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                return buffer.value
            return ""
        finally:
            self.kernel.CloseHandle(handle)


class GameModeMonitor(QObject):
    changed = Signal(bool, str)
    foreground_changed = Signal()

    def __init__(self, parent=None, *, windows=None, own_pid=None) -> None:
        super().__init__(parent)
        self.windows = windows or ForegroundWindows()
        self.own_pid = own_pid or os.getpid()
        self.mode = "off"
        self.paths: set[str] = set()
        self.active = False
        self.reason = "游戏模式：关闭"
        self.blocked = threading.Event()
        self._last_external = (0, 0)
        self._cached_identity = (0, 0)
        self._cached_path = ""
        self._hook = None
        self._callback = None
        self._running = False
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.refresh)
        self.foreground_changed.connect(self.refresh)

    def start(self) -> None:
        self._running = True
        self.configure(self.mode, list(self.paths))

    def _start_watch(self) -> None:
        if self.timer.isActive():
            return
        # OUTOFCONTEXT receives foreground notifications without injecting into games.
        self._callback = self.windows.callback_type(lambda *args: self.foreground_changed.emit())
        self._hook = self.windows.user.SetWinEventHook(3, 3, None, self._callback, 0, 0, 0)
        self.timer.start()  # Also catches exited windows and covers hook failure.

    def _stop_watch(self) -> None:
        self.timer.stop()
        if self._hook:
            self.windows.user.UnhookWinEvent(self._hook)
            self._hook = None
        self._callback = None

    def stop(self) -> None:
        self._running = False
        self.blocked.set()
        self._stop_watch()

    def configure(self, mode: str, paths: list[str]) -> None:
        self.mode = mode
        self.paths = {ntpath.normcase(p) for p in normalize_game_paths(paths)}
        if self._running and mode == "auto":
            self._start_watch()
        else:
            self._stop_watch()
        self.refresh()

    def _identity(self) -> tuple[int, int]:
        hwnd = self.windows.foreground()
        return hwnd, self.windows.process_id(hwnd) if hwnd else 0

    def _path(self, hwnd: int, pid: int) -> str:
        if (hwnd, pid) != self._cached_identity or not self._cached_path:
            self._cached_identity = (hwnd, pid)
            self._cached_path = ntpath.normcase(self.windows.path(pid)) if pid else ""
        return self._cached_path

    @Slot()
    def refresh(self) -> None:
        if self.mode == "on":
            self._set_active(True, "游戏模式：始终开启")
            return
        if self.mode == "off":
            self._set_active(False, "游戏模式：关闭")
            return
        hwnd, pid = self._identity()
        if not hwnd:
            return  # Transient foreground loss during Alt+Tab.
        if pid == self.own_pid:
            hwnd, pid = self._last_external
            if not self.windows.valid(hwnd, pid):
                self._set_active(False, "游戏模式：自动，当前无游戏")
                return
        else:
            self._last_external = hwnd, pid
        path = self._path(hwnd, pid)
        if not path:
            self._set_active(self.active, "游戏模式：自动，无法读取前台程序路径，保持当前状态")
            return
        matched = path in self.paths
        reason = f"游戏模式：自动，已启用（{ntpath.basename(path)}）" if matched else "游戏模式：自动，当前为普通模式"
        self._set_active(matched, reason)

    def _set_active(self, active: bool, reason: str) -> None:
        self.blocked.set() if active else self.blocked.clear()
        if (active, reason) != (self.active, self.reason):
            self.active, self.reason = active, reason
            self.changed.emit(active, reason)

    def allows_capture(self) -> bool:
        if self.blocked.is_set():
            return False
        if self.mode != "auto":
            return self.mode != "on"
        # Close the event-delivery gap before any selection read or simulated copy.
        hwnd, pid = self._identity()
        if not hwnd or pid == self.own_pid:
            return False
        path = ntpath.normcase(self.windows.path(pid))
        return bool(path and path not in self.paths)
