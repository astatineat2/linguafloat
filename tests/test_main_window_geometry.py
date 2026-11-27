import ctypes as ct
from ctypes import wintypes as wt

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from linguafloat.config import AppSettings
from linguafloat.ui import MainWindow


def test_windows_maximize_fills_work_area_and_restore_keeps_normal_frame():
    app = QApplication.instance() or QApplication([])
    if app.platformName() != 'windows':
        pytest.skip('Requires the native Windows window manager')

    class MonitorInfo(ct.Structure):
        _fields_ = [('size', wt.DWORD), ('monitor', wt.RECT), ('work', wt.RECT), ('flags', wt.DWORD)]

    user32 = ct.windll.user32
    user32.MonitorFromWindow.argtypes = (wt.HWND, wt.DWORD)
    user32.MonitorFromWindow.restype = wt.HANDLE
    user32.GetMonitorInfoW.argtypes = (wt.HANDLE, ct.POINTER(MonitorInfo))
    window = MainWindow(AppSettings(), '')
    try:
        window.show()
        QTest.qWait(100)
        handle = wt.HWND(int(window.winId()))
        normal = window.frameGeometry()
        info = MonitorInfo(ct.sizeof(MonitorInfo))
        assert user32.GetMonitorInfoW(user32.MonitorFromWindow(handle, 2), ct.byref(info))
        expected = (info.work.left, info.work.top, info.work.right, info.work.bottom)
        for _ in range(2):
            user32.SendMessageW(handle, 0x0112, 0xF030, 0)  # WM_SYSCOMMAND / SC_MAXIMIZE
            QTest.qWait(100)
            assert window.isMaximized()
            bounds = wt.RECT()
            assert ct.windll.dwmapi.DwmGetWindowAttribute(handle, 9, ct.byref(bounds), ct.sizeof(bounds)) == 0
            actual = (bounds.left, bounds.top, bounds.right, bounds.bottom)
            assert actual == pytest.approx(expected, abs=2), (actual, expected)
            user32.SendMessageW(handle, 0x0112, 0xF120, 0)  # SC_RESTORE
            QTest.qWait(100)
            assert not window.isMaximized()
            assert window.frameGeometry() == normal
    finally:
        window.allow_close()
        window.close()
