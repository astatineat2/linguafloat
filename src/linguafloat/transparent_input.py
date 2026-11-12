"""Live, zero-tint Windows glass which still receives normal mouse input.

Layered, zero-alpha Qt windows are hit-test transparent on Windows. DWM glass
uses a normal HWND instead: no global mouse hook, screen copy or alpha tint.
"""
import sys
import ctypes
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget, QApplication


class TransparentInputWindow(QWidget):
    def __init__(self, parent=None, flags=Qt.WindowType.Widget):
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.glass_ready = QApplication.platformName() != "windows"
        self.capture_excluded = False
        if QApplication.platformName() != "windows":
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            return
        self._enable_glass()

    def _enable_glass(self):
        class BlurBehind(ctypes.Structure):
            _fields_ = [("flags", wintypes.DWORD), ("enable", wintypes.BOOL),
                        ("region", wintypes.HRGN), ("transition", wintypes.BOOL)]
        dwm = ctypes.WinDLL("dwmapi")
        gdi = ctypes.WinDLL("gdi32")
        gdi.CreateRectRgn.restype = wintypes.HRGN
        gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        dwm.DwmEnableBlurBehindWindow.argtypes = [wintypes.HWND, ctypes.POINTER(BlurBehind)]
        region = gdi.CreateRectRgn(0, 0, -1, -1)
        try:
            settings = BlurBehind(3, True, region, False)
            self.glass_ready = dwm.DwmEnableBlurBehindWindow(int(self.winId()), ctypes.byref(settings)) == 0
        finally:
            gdi.DeleteObject(region)
        # Older Windows interpret 0x11 as WDA_MONITOR (black); never use there.
        if sys.getwindowsversion().build >= 19041:
            user = ctypes.WinDLL("user32")
            user.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
            self.capture_excluded = bool(user.SetWindowDisplayAffinity(int(self.winId()), 0x11))

    @staticmethod
    def clear_glass(painter, rect):
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(rect, QColor(0, 0, 0, 0))
        painter.restore()
