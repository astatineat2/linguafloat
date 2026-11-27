"""Startup checks shared by the frozen launcher and the Setup updater."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import time

from linguafloat.update_core import (UpdateError, check_archive, load_channel,
                                    parse_manifest, sha256_file, update_directory)


INSTALL_LOCK = '.linguafloat-setup.lock'
INCOMPLETE = '.linguafloat-setup-incomplete'
INTENT = '.linguafloat-update-intent'
RECEIPT = 'setup-receipt.json'
CRITICAL_FILES = ('LinguaFloat.exe', 'LinguaFloatUpdater.exe', 'VERSION.txt',
                  '_internal/python313.dll', '_internal/release/channel.json',
                  'document-runtime/python.exe', 'document-runtime/python313.dll',
                  'document-runtime/worker_entry.py',
                  'document-runtime/src/linguafloat/document_worker.py')


def installer_running(target: Path) -> bool:
    try:
        with (target / INSTALL_LOCK).open('rb'):
            return False
    except FileNotFoundError:
        return False
    except OSError as exc:
        if getattr(exc, 'winerror', None) in (5, 32, 33) or isinstance(exc, PermissionError):
            return True
        raise


def clear_intent(target: Path, nonce: str) -> None:
    path = target / INTENT
    try:
        if json.loads(path.read_text(encoding='utf-8')).get('nonce') == nonce:
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def startup_status(target: Path) -> str | None:
    if installer_running(target):
        return 'installing'
    if (target / INCOMPLETE).is_file():
        return 'repair'
    try:
        intent = json.loads((target / INTENT).read_text(encoding='utf-8'))
        if 0 <= time.time() - intent['created'] < 300:
            return 'installing'
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def verify_payload(target: Path, version: str) -> None:
    try:
        receipt = json.loads((target / RECEIPT).read_text(encoding='utf-8'))
        if receipt['version'] != version or set(receipt['files']) != set(CRITICAL_FILES):
            raise ValueError('receipt')
        if (target / 'VERSION.txt').read_text(encoding='utf-8').splitlines()[0] != f'LinguaFloat v{version}':
            raise ValueError('VERSION.txt')
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise UpdateError('安装记录或版本文件不完整，未启动程序。') from exc
    for relative in CRITICAL_FILES:
        try:
            if sha256_file(target / relative) == receipt['files'][relative]:
                continue
        except OSError:
            pass
        raise UpdateError(f'安装组件校验失败：{relative}。')


def cached_repair(target: Path, version: str) -> Path | None:
    root = update_directory().resolve()
    try:
        channel = load_channel()
    except UpdateError:
        return None
    for request_file in root.glob(f'v{version}-*/request.json'):
        job = request_file.parent
        if job.resolve() != job:
            continue
        try:
            request = json.loads(request_file.read_text(encoding='utf-8'))
            if Path(request['target']).resolve() != target:
                continue
            release = parse_manifest((job / 'update.json').read_bytes(), (job / 'update.json.sig').read_bytes(),
                                     channel, request['current'])
            if not release.setup or release.version != version:
                continue
            package = job / 'Setup.exe'
            check_archive(package, release)
            return package
        except (OSError, ValueError, KeyError, TypeError, UpdateError):
            continue
    return None


def health_start_allowed(target: Path, job: Path) -> bool:
    if installer_running(target) or (target / INCOMPLETE).exists():
        return False
    try:
        job = job.resolve()
        if job.parent != update_directory().resolve():
            return False
        state = json.loads((job / 'state.json').read_text(encoding='utf-8'))
        intent = json.loads((target / INTENT).read_text(encoding='utf-8'))
        return (state['phase'] == 'setup_installed' and Path(state['target']) == target
                and state['nonce'] == intent['nonce'])
    except (OSError, ValueError, TypeError, KeyError):
        return False


def offer_repair(target: Path, version: str, message: str) -> None:
    box = ctypes.windll.user32.MessageBoxW
    title = 'LinguaFloat 安装未完成'
    if box(None, f'{message}\n安装位置：{target}\n\n是否使用已验证的安装包原地修复？', title, 0x24) != 6:
        return
    try:
        if not all(part.isascii() and part.isdigit() for part in version.split('.')):
            raise ValueError('version')
        package = cached_repair(target, version)
        if package is None:
            raise UpdateError('找不到已验证的缓存安装包。')
        subprocess.Popen([str(package), '/DIR=' + str(target), '/NORESTART'], cwd=package.parent)
    except (OSError, ValueError, UpdateError):
        box(None, '无法使用缓存。请用官方 Setup 在原目录覆盖安装，不要先卸载。', title, 0x30)


def handle_blocked_start(target: Path, health_job: Path | None = None) -> bool:
    status = startup_status(target)
    if status is None:
        return False
    if status == 'installing' and health_job is not None and health_start_allowed(target, health_job):
        return False
    if status == 'repair':
        try:
            version = (target / INCOMPLETE).read_text(encoding='utf-8-sig').strip()
        except OSError:
            return True
        offer_repair(target, version, '安装尚未完成，暂时不能打开程序。')
    return True
