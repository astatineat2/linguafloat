"""Read only the app's data directory environment variables (64-bit Windows)."""
import ctypes as c
import json
import sys
from ctypes import wintypes as w


def data_environment(pid):
    kernel, ntdll = c.windll.kernel32, c.windll.ntdll
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.ReadProcessMemory.argtypes = [w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.c_void_p]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    class PBI(c.Structure):
        _fields_ = [("reserved", c.c_void_p), ("peb", c.c_void_p),
                    ("reserved2", c.c_void_p * 2), ("pid", c.c_void_p), ("reserved3", c.c_void_p)]
    ntdll.NtQueryInformationProcess.argtypes = [w.HANDLE, w.ULONG, c.c_void_p, w.ULONG, c.c_void_p]
    process = kernel.OpenProcess(0x410, False, pid)
    if not process:
        raise c.WinError()
    def read(address, size):
        data = c.create_string_buffer(size)
        if not kernel.ReadProcessMemory(process, address, data, size, None):
            raise c.WinError()
        return data.raw
    def pointer(address):
        return int.from_bytes(read(address, 8), "little")
    try:
        info = PBI()
        if ntdll.NtQueryInformationProcess(process, 0, c.byref(info), c.sizeof(info), None):
            raise RuntimeError("Process information unavailable")
        parameters = pointer(info.peb + 0x20)
        environment = pointer(parameters + 0x80)
        data = bytearray()
        for i in range(131072):
            data.extend(read(environment + i * 2, 2))
            if len(data) >= 4 and data[-4:] == b"\0\0\0\0":
                break
        values = {}
        for item in data.decode("utf-16-le").split("\0"):
            name, _, value = item.partition("=")
            if name.upper() in {"APPDATA", "LOCALAPPDATA", "USERPROFILE"}:
                values[name.upper()] = value
        return values
    finally:
        kernel.CloseHandle(process)


def open_data_files(pid):
    kernel, ntdll = c.windll.kernel32, c.windll.ntdll
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.DuplicateHandle.argtypes = [w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD]
    kernel.GetFileType.argtypes = [w.HANDLE]
    kernel.GetFinalPathNameByHandleW.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
    class Entry(c.Structure):
        _fields_ = [("object", c.c_void_p), ("pid", c.c_size_t), ("handle", c.c_size_t),
                    ("access", w.ULONG), ("trace", w.USHORT), ("type", w.USHORT),
                    ("attributes", w.ULONG), ("reserved", w.ULONG)]
    needed = w.ULONG()
    size = 1024 * 1024
    while True:
        data = c.create_string_buffer(size)
        status = ntdll.NtQuerySystemInformation(64, data, size, c.byref(needed))
        if status == 0:
            break
        if status != -1073741820:
            raise RuntimeError("Handle query unavailable")
        size = max(size * 2, needed.value)
    count = c.c_size_t.from_buffer(data).value
    entries = (Entry * count).from_buffer(data, 16)
    process = kernel.OpenProcess(0x40, False, pid)
    paths = set()
    try:
        for entry in entries:
            if entry.pid != pid:
                continue
            handle = w.HANDLE()
            if not kernel.DuplicateHandle(process, entry.handle, kernel.GetCurrentProcess(), c.byref(handle), 0, False, 2):
                continue
            try:
                if kernel.GetFileType(handle) != 1:
                    continue
                name = c.create_unicode_buffer(32768)
                if kernel.GetFinalPathNameByHandleW(handle, name, len(name), 0):
                    if any(s in name.value.lower() for s in ("sqlite", "config.json", "linguafloat")):
                        paths.add(name.value)
            finally:
                kernel.CloseHandle(handle)
    finally:
        kernel.CloseHandle(process)
    return sorted(paths)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data_environment(int(sys.argv[1])), ensure_ascii=False, indent=2))
    print(json.dumps(open_data_files(int(sys.argv[1])), ensure_ascii=False, indent=2))
