"""Capture a specific app window without capturing another covering window."""
import ctypes
from ctypes import wintypes as w
from pathlib import Path
import sys

from PIL import Image
import uiautomation as auto

pid, output = int(sys.argv[1]), Path(sys.argv[2])
window = next(x for x in auto.GetRootControl().GetChildren() if x.ProcessId == pid and "LinguaFloat" in x.Name)
for name in ("文件翻译", "历史记录"):
    next(x for x, _ in auto.WalkControl(window, maxDepth=12)
         if x.Name == name and x.ControlTypeName == "TabItemControl").GetSelectionItemPattern().Select()

user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
user.GetWindowDC.argtypes, user.GetWindowDC.restype = [w.HWND], w.HDC
user.ReleaseDC.argtypes = [w.HWND, w.HDC]
user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [w.HDC], w.HDC
gdi.CreateCompatibleBitmap.argtypes, gdi.CreateCompatibleBitmap.restype = [w.HDC, ctypes.c_int, ctypes.c_int], w.HBITMAP
gdi.SelectObject.argtypes, gdi.SelectObject.restype = [w.HDC, w.HANDLE], w.HANDLE
gdi.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT]
gdi.DeleteObject.argtypes, gdi.DeleteDC.argtypes = [w.HANDLE], [w.HDC]

class Header(ctypes.Structure):
    _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                ("planes", w.WORD), ("bits", w.WORD), ("compression", w.DWORD),
                ("image_size", w.DWORD), ("xppm", w.LONG), ("yppm", w.LONG),
                ("used", w.DWORD), ("important", w.DWORD)]

hwnd = window.NativeWindowHandle
rect = w.RECT()
user.GetWindowRect(hwnd, ctypes.byref(rect))
width, height = rect.right - rect.left, rect.bottom - rect.top
dc = user.GetWindowDC(hwnd)
memory = gdi.CreateCompatibleDC(dc)
bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
previous = gdi.SelectObject(memory, bitmap)
try:
    if not user.PrintWindow(hwnd, memory, 2):
        raise RuntimeError("PrintWindow failed")
    gdi.SelectObject(memory, previous)
    data = ctypes.create_string_buffer(width * height * 4)
    header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, len(data), 0, 0, 0, 0)
    if not gdi.GetDIBits(memory, bitmap, 0, height, data, ctypes.byref(header), 0):
        raise RuntimeError("GetDIBits failed")
    image = Image.frombytes("RGB", (width, height), data.raw, "raw", "BGRX")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    print("Captured app history window")
finally:
    gdi.SelectObject(memory, previous)
    gdi.DeleteObject(bitmap)
    gdi.DeleteDC(memory)
    user.ReleaseDC(hwnd, dc)
