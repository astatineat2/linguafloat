"""One-time repair for the official Windows v0.11.0 / v0.11.1 portable updater."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from linguafloat.update_core import UpdateError, load_channel, sha256_file, verify_signature
from linguafloat.update_install import check_portable_folder, installation_lock


def running_apps():
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    query = ("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
             "$apps=@(Get-Process -Name LinguaFloat -ErrorAction SilentlyContinue | "
             "Where-Object SessionId -eq (Get-Process -Id $PID).SessionId | Select-Object Id,Path); "
             "ConvertTo-Json -Compress -InputObject $apps")
    raw = subprocess.check_output([shell, '-NoProfile', '-NonInteractive', '-Command', query],
                                  creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
    rows = json.loads(raw.decode('utf-8-sig'))
    if len(rows) != 1 or not rows[0]['Path']:
        raise UpdateError('请只打开要更新的那一份 LinguaFloat，再运行修复工具。没有修改任何程序。')
    return rows[0]['Id'], Path(rows[0]['Path']).resolve(strict=True)


def repair(resources, discover, confirm):
    manifest = (resources / 'repair.json').read_bytes()
    verify_signature(manifest, (resources / 'repair.json.sig').read_bytes(), load_channel()['public_key'])
    info = json.loads(manifest)
    pid, exe = discover()
    target = exe.parent
    check_portable_folder(target)
    original = sha256_file(exe)
    old = info['from'].get(original)
    if not old or exe.name.lower() != 'linguafloat.exe':
        raise UpdateError('这不是受支持的公开版 v0.11.0 或 v0.11.1，无需使用此工具。')
    helper = target / 'LinguaFloatUpdater.exe'
    payload = resources / helper.name
    if sha256_file(payload) != info['sha256']:
        raise UpdateError('修复组件校验失败，未更改安装目录。')
    if not confirm(f"当前版本：v{old['version']}\n安装位置：{target}\n升级目标：v{info['version']}\n\n只修复这份程序的更新助手，保留配置和历史。继续？"):
        return False
    with installation_lock(target):
        if discover() != (pid, exe) or sha256_file(exe) != original:
            raise UpdateError('运行中的程序发生变化，已停止。')
        current = sha256_file(helper)
        if current == info['sha256']:
            return True
        if current != old['helper']:
            raise UpdateError('现有更新助手与公开版不符，未覆盖。')
        with tempfile.NamedTemporaryFile(prefix='.lf-repair-', dir=target, delete=False) as tmp:
            staging = Path(tmp.name)
        try:
            shutil.copyfile(payload, staging)
            if sha256_file(staging) != info['sha256']:
                raise UpdateError('写入的更新组件不完整，未覆盖。')
            os.replace(staging, helper)
        finally:
            staging.unlink(missing_ok=True)
    return True


def main():
    box = ctypes.windll.user32.MessageBoxW
    title = 'LinguaFloat · v0.11 更新修复'
    try:
        if repair(Path(sys._MEIPASS) / 'repair', running_apps, lambda msg: box(None, msg, title, 0x124) == 6):
            box(None, '更新助手已就绪。回到旧版「帮助与更新」，依次点击「检查更新」「下载更新」「安装并重启」。', title, 0x40)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, UpdateError) as exc:
        box(None, str(exc), title, 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
