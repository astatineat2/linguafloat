"""Verify and capture a running release without displaying credentials."""
import argparse
import ctypes
from ctypes import wintypes as w
from pathlib import Path
import sys
import time

from PIL import Image
import uiautomation as auto

parser = argparse.ArgumentParser()
parser.add_argument("pid", type=int)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--version", required=True)
parser.add_argument("--page", choices=("功能设置", "配置设置", "文件翻译"), default="功能设置")
parser.add_argument("--advanced", action="store_true")
args = parser.parse_args()
user = ctypes.windll.user32
user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
user.IsWindowVisible.argtypes = [w.HWND]
user.IsIconic.argtypes = [w.HWND]
@ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
def restore_release(hwnd, _):
    pid = w.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == args.pid:
        title = ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(hwnd, title, 512)
        if "LinguaFloat" in title.value and args.version in title.value:
            if not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):
                user.ShowWindow(hwnd, 9)
    return True
deadline = time.monotonic() + 45
window = None
while time.monotonic() < deadline:
    user.EnumWindows(restore_release, 0)
    window = next((x for x in auto.GetRootControl().GetChildren()
                   if x.ProcessId == args.pid and "LinguaFloat" in x.Name and args.version in x.Name), None)
    if window:
        break
    time.sleep(.2)
assert window is not None, "Release window did not appear within 45 seconds."
assert args.version in window.Name, window.Name
names = [x.Name for x, _ in auto.WalkControl(window, maxDepth=12)]
assert all(page in names for page in ("功能设置", "配置设置", "文件翻译"))
sys.stdout.reconfigure(encoding="utf-8")
print("Verified version in title and all three page entries: " + window.Name)
tab = next(x for x, _ in auto.WalkControl(window, maxDepth=12)
           if x.Name == args.page and x.ControlTypeName == "TabItemControl")
selection = tab.GetSelectionItemPattern()
if not selection.IsSelected:
    selection.Select()
if args.advanced and args.page == "配置设置":
    control = next(x for x, _ in auto.WalkControl(window, maxDepth=15)
                   if x.Name == "高级设置" and x.ControlTypeName == "CheckBoxControl")
    pattern = control.GetTogglePattern()
    if pattern.ToggleState == 0:
        pattern.Toggle()
    names = [x.Name for x, _ in auto.WalkControl(window, maxDepth=15)]
    assert all(label in names for label in ("输出 Token 上限", "Chat 输出长度参数", "指令角色", "推理强度", "供应商思考开关"))
    print("Verified five API compatibility controls in advanced settings.")

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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    print("Captured release window: " + str(args.output))
finally:
    gdi.SelectObject(memory, previous)
    gdi.DeleteObject(bitmap)
    gdi.DeleteDC(memory)
    user.ReleaseDC(hwnd, dc)
