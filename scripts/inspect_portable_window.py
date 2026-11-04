"""Inspect/show only the specified portable app process and capture its window."""
import argparse
import ctypes
from ctypes import wintypes
import json
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("pid", type=int)
parser.add_argument("--show", action="store_true")
parser.add_argument("--capture", type=Path)
args = parser.parse_args()
user32 = ctypes.windll.user32
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
windows = []


@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
def inspect(hwnd, _):
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if process_id.value != args.pid:
        return True
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, len(title))
    if args.show and "LinguaFloat" in title.value:
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    windows.append({"hwnd": hwnd, "title": title.value, "visible": bool(user32.IsWindowVisible(hwnd)),
                    "rect": [rect.left, rect.top, rect.right, rect.bottom]})
    return True


user32.EnumWindows(inspect, 0)
print(json.dumps(windows, ensure_ascii=True))
if args.capture:
    from PIL import ImageGrab
    target = next((w for w in windows if "LinguaFloat" in w["title"] and w["visible"]), None)
    if not target:
        raise SystemExit("No visible LinguaFloat window found")
    time.sleep(.5)
    args.capture.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=tuple(target["rect"]), all_screens=True).save(args.capture)
