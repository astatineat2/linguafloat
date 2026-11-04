"""Per-user ownership and local activation, before any translation listeners start."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QStandardPaths, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceError(RuntimeError):
    pass


def _windows_identity() -> str:
    """Use the real token user, not paths virtualized by the launching app."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        value = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            user = value.value
        finally:
            kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    return f"{user}:{session.value}"


class _WindowsMutex:
    def __init__(self, name: str):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateMutexW.restype = wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateMutexW(None, False, "Local\\" + name)
        self.owned = False
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def acquire(self) -> bool:
        result = self.api.WaitForSingleObject(self.handle, 0)
        if result in (0, 0x80):  # Acquired, or previous owner crashed.
            self.owned = True
            return True
        if result == 0x102:
            return False
        raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            if self.owned:
                self.api.ReleaseMutex(self.handle)
            self.api.CloseHandle(self.handle)
            self.handle = None
            self.owned = False


class SingleInstance(QObject):
    activation_requested = Signal()

    def __init__(self, directory: Path | None = None, parent: QObject | None = None):
        super().__init__(parent)
        if os.name == "nt" and directory is None:
            identity = _windows_identity()
        else:
            if directory is None:
                directory = Path(QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.GenericDataLocation)) / "LinguaFloat" / "instance"
            directory.mkdir(parents=True, exist_ok=True)
            identity = os.path.normcase(str(directory.resolve()))
        self.name = "LinguaFloat-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.mutex = _WindowsMutex(self.name) if os.name == "nt" else None
        self.lock = None if self.mutex else QLockFile(str(directory / "application.lock"))
        if self.lock:
            self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._activate)
        self._owned = False

    def start(self, timeout_ms: int = 15000) -> bool:
        """Return True for the owner, False after the owner acknowledges activation."""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            if self.mutex.acquire() if self.mutex else self.lock.tryLock(0):
                self._owned = True
                # The lock serializes startup, so removing a stale endpoint is safe.
                QLocalServer.removeServer(self.name)
                if self.server.listen(self.name):
                    return True
                reason = self.server.errorString()
                self.close()
                raise SingleInstanceError("无法建立程序唤醒通道：" + reason)
            if self.lock and self.lock.error() == QLockFile.LockError.PermissionError:
                raise SingleInstanceError("无法访问启动锁目录，请检查当前用户的目录权限。")
            socket = QLocalSocket()
            try:
                socket.connectToServer(self.name)
                if socket.waitForConnected(150):
                    greeting = self._read_line(socket, deadline)
                    if greeting.startswith(b"pid:") and greeting[4:].strip().isdigit():
                        if os.name == "nt":
                            ctypes.windll.user32.AllowSetForegroundWindow(int(greeting[4:]))
                        socket.write(b"activate\n")
                        socket.flush()
                        if self._read_line(socket, deadline) == b"activated\n":
                            return False
            finally:
                socket.abort()
            if time.monotonic() >= deadline:
                raise SingleInstanceError(
                    "LinguaFloat 已在运行或正在启动，但暂时没有响应。"
                    "请稍后通过托盘打开；为避免重复监听，本次不会启动第二个程序。")
            time.sleep(0.1)

    @staticmethod
    def _read_line(socket, deadline):
        while not socket.canReadLine():
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0 or socket.bytesAvailable() > 64 or not socket.waitForReadyRead(remaining):
                return b""
        return bytes(socket.readLine(64))

    def _activate(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.disconnected.connect(socket.deleteLater)
            socket.readyRead.connect(lambda connection=socket: self._activation_data(connection))
            socket.write(f"pid:{os.getpid()}\n".encode("ascii"))

    def _activation_data(self, socket):
        if socket.canReadLine():
            if bytes(socket.readLine(64)) == b"activate\n":
                self.activation_requested.emit()
                socket.write(b"activated\n")
            socket.disconnectFromServer()
        elif socket.bytesAvailable() > 64:
            socket.abort()

    def close(self):
        if self._owned:
            self.server.close()
            if self.lock:
                self.lock.unlock()
            self._owned = False
        if self.mutex:
            self.mutex.close()
