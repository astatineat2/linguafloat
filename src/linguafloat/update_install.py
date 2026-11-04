"""Out-of-process, recoverable replacement of a complete portable installation."""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from linguafloat.update_core import (UpdateError, atomic_json, check_archive, load_channel,
                                    parse_manifest, update_directory)


def load_request(directory: Path) -> dict:
    root = update_directory().resolve()
    directory = directory.resolve()
    if directory.parent != root or directory.is_symlink():
        raise UpdateError("更新工作目录无效。")
    value = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    target = Path(value["target"])
    if not target.is_absolute() or target.is_symlink() or target.resolve() != target or len(target.parts) < 3:
        raise UpdateError("程序安装目录无效。")
    return value


@contextmanager
def installation_lock(target: Path):
    import msvcrt
    path = target.parent / ("." + target.name + "-update.lock")
    with path.open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise UpdateError("另一个更新助手正在处理此目录，请勿重复安装。") from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def process_alive(pid: int) -> bool:
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    api.OpenProcess.restype = ctypes.c_void_p
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 5:
            raise UpdateError("无法确认运行中程序已退出，更新已停止。")
        return False
    try:
        return api.WaitForSingleObject(handle, 0) == 258
    finally:
        api.CloseHandle(handle)


def running_in(target: Path) -> bool:
    """Query paths in-process; no account credentials or command lines are logged."""
    from ctypes import wintypes as w

    class Entry(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD), ("heap", ctypes.c_size_t),
                    ("module", w.DWORD), ("threads", w.DWORD), ("parent", w.DWORD),
                    ("priority", w.LONG), ("flags", w.DWORD), ("name", w.WCHAR * 260)]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    api.CreateToolhelp32Snapshot.restype = w.HANDLE
    api.Process32FirstW.argtypes = api.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    api.OpenProcess.restype = w.HANDLE
    api.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    api.CloseHandle.argtypes = [w.HANDLE]
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise UpdateError("无法检查程序文件占用。")
    entry = Entry()
    entry.size = ctypes.sizeof(entry)
    try:
        more = api.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.pid != os.getpid() and entry.name.lower() in {"linguafloat.exe", "python.exe", "pythonw.exe"}:
                process = api.OpenProcess(0x1000, False, entry.pid)
                if process:
                    try:
                        text, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
                        if api.QueryFullProcessImageNameW(process, 0, text, ctypes.byref(length)):
                            path = Path(text.value).resolve()
                            if path == target / "LinguaFloat.exe" or target in path.parents:
                                return True
                    finally:
                        api.CloseHandle(process)
            more = api.Process32NextW(snapshot, ctypes.byref(entry))
        return False
    finally:
        api.CloseHandle(snapshot)


def backup_data(directory: Path) -> None:
    source = Path(os.environ["APPDATA"]) / "LinguaFloat"
    backup = directory / "data-backup"
    backup.mkdir(exist_ok=True)
    for name in ("config.json", "documents/jobs.sqlite3"):
        path = source / name
        if path.is_file():
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".sqlite3":
                with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(destination) as dst:
                    src.backup(dst)
            else:
                shutil.copy2(path, destination)


def restore_data(directory: Path) -> None:
    destination = Path(os.environ["APPDATA"]) / "LinguaFloat"
    for name in ("config.json", "documents/jobs.sqlite3"):
        path = directory / "data-backup" / name
        if path.is_file():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".sqlite3":
                # All application processes are stopped before restoring the migration snapshot.
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(target) + suffix)
                    if sidecar.exists():
                        sidecar.rename(directory / (target.name + suffix + ".failed"))
            shutil.copy2(path, target)


def launch_application(target: Path, directory: Path | None = None):
    command = [str(target / "LinguaFloat.exe")]
    if directory:
        command += ["--update-health", str(directory)]
    return subprocess.Popen(command, cwd=target, creationflags=0x08000000)


def rollback(directory: Path, state: dict, *, relaunch=True) -> None:
    target, backup = Path(state["target"]), Path(state["backup"])
    if running_in(target):
        raise UpdateError("新版仍在运行，未覆盖程序或数据。请退出后运行更新恢复助手。")
    if backup.is_dir():
        if target.exists():
            failed = target.parent / "LinguaFloat-backups" / ("failed-" + directory.name)
            failed.parent.mkdir(exist_ok=True)
            if failed.exists():
                raise UpdateError("回退目录已存在，需要人工检查；旧版备份仍保留。")
            target.rename(failed)
        backup.rename(target)
        restore_data(directory)
    state["phase"] = "rolled_back"
    atomic_json(directory / "state.json", state)
    if relaunch and (target / "LinguaFloat.exe").is_file():
        launch_application(target)


def install(directory: Path, *, recover=False, launch=launch_application, health_timeout=60) -> None:
    request = load_request(directory)
    target = Path(request["target"])
    with installation_lock(target):
        state_path = directory / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        if recover:
            if state.get("phase") not in {"committed", "rolled_back", "prepared"} and state.get("backup"):
                if healthy(directory, state):
                    state["phase"] = "committed"
                    atomic_json(state_path, state)
                else:
                    rollback(directory, state)
            return
        if state.get("phase") not in (None, "prepared"):
            raise UpdateError("此更新已处理或需要恢复，不可重复安装。")
        deadline = time.monotonic() + 180
        while process_alive(int(request["parent_pid"])):
            if time.monotonic() >= deadline:
                raise UpdateError("等待主程序退出超时，当前安装未更改。")
            time.sleep(.25)
        if running_in(target):
            raise UpdateError("程序或文档后台仍在使用安装目录，请安全退出后重试。")
        release = parse_manifest((directory / "update.json").read_bytes(),
                                 (directory / "update.json.sig").read_bytes(), load_channel(), request["current"])
        if release.version == request["current"]:
            raise UpdateError("不允许重复安装当前版本。")
        from packaging.version import Version
        if Version(release.version) <= Version(request["current"]):
            raise UpdateError("不允许通过更新安装旧版本。")
        if (target / "VERSION.txt").read_text(encoding="utf-8").splitlines()[0] != f"LinguaFloat v{request['current']}":
            raise UpdateError("当前目录版本已变化，请重新检查更新。")
        archive = directory / "package.zip"
        check_archive(archive, release)
        if shutil.disk_usage(target.parent).free < release.unpacked_size + 128 * 1024**2:
            raise UpdateError("安装盘空间不足，当前程序未更改。")
        staging = target.parent / (".LinguaFloat-stage-" + directory.name)
        backup = target.parent / "LinguaFloat-backups" / (f"v{request['current']}-" + directory.name)
        if staging.exists() or backup.exists():
            raise UpdateError("暂存或备份目录已存在，需要先恢复上次更新。")
        staging.mkdir()
        state = {"phase": "extracting", "target": str(target), "backup": str(backup), "version": release.version,
                 "nonce": request["nonce"], "staging": str(staging)}
        atomic_json(state_path, state)
        try:
            import zipfile
            with zipfile.ZipFile(archive) as package:
                package.extractall(staging)  # Every member was checked against Windows path rules above.
            backup_data(directory)
            backup.parent.mkdir(exist_ok=True)
            state["phase"] = "swapping"
            atomic_json(state_path, state)
            target.rename(backup)
            staging.rename(target)
            state["phase"] = "starting"
            atomic_json(state_path, state)
            process = launch(target, directory)
            state["child_pid"] = process.pid
            atomic_json(state_path, state)
            deadline = time.monotonic() + health_timeout
            while time.monotonic() < deadline:
                if healthy(directory, state):
                    state["phase"] = "committed"
                    atomic_json(state_path, state)
                    return
                if process.poll() is not None:
                    break
                time.sleep(.25)
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            raise UpdateError("新版未通过启动检查，正在回退旧版。")
        except Exception:
            # Keep failed files and the transaction journal for diagnosis; never remove user outputs.
            if backup.is_dir():
                rollback(directory, state)
            else:
                state["phase"] = "rolled_back"
                atomic_json(state_path, state)
                launch(target)
            raise


def healthy(directory: Path, state: dict) -> bool:
    try:
        value = json.loads((directory / "healthy.json").read_text(encoding="utf-8"))
        return (value["nonce"] == state["nonce"] and value["version"] == state["version"]
                and Path(value["target"]).resolve() == Path(state["target"]).resolve()
                and (not state.get("child_pid") or value["pid"] == state["child_pid"]))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def mark_healthy(directory: Path, version: str, target: Path) -> None:
    request = load_request(directory)
    if Path(request["target"]).resolve() != target.resolve():
        raise UpdateError("启动检查目录不符。")
    atomic_json(directory / "healthy.json", {"nonce": request["nonce"], "version": version,
                                             "target": str(target), "pid": os.getpid()})


def pending_recovery(target: Path) -> Path | None:
    for state_path in update_directory().glob("*/state.json"):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if (state.get("phase") in {"extracting", "swapping", "starting"}
                    and Path(state["target"]).resolve() == target.resolve()):
                return state_path.parent
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    try:
        install(args.job, recover=args.recover)
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, (UpdateError, OSError)) else "更新助手遇到错误，旧版备份及恢复记录仍保留。"
        try:
            state = json.loads((args.job / "state.json").read_text(encoding="utf-8"))
            if state.get("phase") == "rolled_back":
                message = "更新未完成，已恢复旧版程序。\n原因：" + message
            else:
                message = "更新未完成。\n原因：" + message
        except (OSError, ValueError):
            message = "更新未完成，未确认替换成功。\n原因：" + message
        message += "\n恢复记录：" + str(args.job) + "\n请保留备份，检查原因后重试；不要混用不同版本依赖。"
        try:
            atomic_json(args.job / "error.json", {"message": message})
        except OSError:
            pass
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, "LinguaFloat 更新未完成", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
