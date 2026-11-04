"""Launch the portable app repeatedly and verify the original window is restored."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import time


parser = argparse.ArgumentParser()
parser.add_argument('executable', type=Path)
parser.add_argument('--existing-pid', type=int, help='Verify an instance launched by Explorer.')
args = parser.parse_args()
executable = args.executable.resolve()
user32 = ctypes.windll.user32
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def windows(process_id):
    result = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process_id:
            title = ctypes.create_unicode_buffer(512)
            kind = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, len(title))
            user32.GetClassNameW(hwnd, kind, len(kind))
            result.append({'hwnd': hwnd, 'title': title.value, 'class': kind.value,
                           'visible': bool(user32.IsWindowVisible(hwnd))})
        return True

    user32.EnumWindows(collect, 0)
    return result


primary = None if args.existing_pid else subprocess.Popen([str(executable)], cwd=executable.parent)
primary_pid = args.existing_pid or primary.pid
deadline = time.monotonic() + 45
window = None
while time.monotonic() < deadline:
    assert primary is None or primary.poll() is None, 'Primary process exited unexpectedly'
    window = next((item for item in windows(primary_pid)
                   if 'LinguaFloat' in item['title'] and item['visible']), None)
    if window:
        break
    time.sleep(.15)
assert window, 'Primary window did not appear'
hwnd = window['hwnd']

for display_mode in (0, 6):  # Hidden-to-tray and minimized.
    if display_mode == 0:
        user32.SendMessageW(hwnd, 0x0010, 0, 0)  # Close button invokes Qt's hide-to-tray.
        deadline = time.monotonic() + 5
        while user32.IsWindowVisible(hwnd) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not user32.IsWindowVisible(hwnd)
    else:
        user32.ShowWindow(hwnd, display_mode)
        time.sleep(.2)
    duplicate = subprocess.Popen([str(executable)], cwd=executable.parent)
    assert duplicate.wait(timeout=30) == 0, 'Duplicate did not exit successfully'
    deadline = time.monotonic() + 5
    while (not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd)) and time.monotonic() < deadline:
        time.sleep(.05)
    assert user32.IsWindowVisible(hwnd), 'Original window was not restored'
    assert not user32.IsIconic(hwnd), 'Original window is still minimized'

duplicates = [subprocess.Popen([str(executable)], cwd=executable.parent) for _ in range(3)]
for duplicate in duplicates:
    assert duplicate.wait(timeout=30) == 0
assert primary is None or primary.poll() is None
current = windows(primary_pid)
tray_windows = [item for item in current if 'TrayIcon' in item['title']]
assert len(tray_windows) == 1, current
print(json.dumps({'primary_pid': primary_pid, 'secondary_launches': 5,
                  'hidden_and_minimized_restore': True, 'tray_windows': len(tray_windows)}, indent=2))
