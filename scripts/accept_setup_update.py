"""Local Windows acceptance for a complete candidate Setup and the published updater.

Use an idle desktop. The installed application's files are never modified; its
uninstall entry is parked and restored while the spare install is tested.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import winreg

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from linguafloat.setup_gate import INCOMPLETE, INSTALL_LOCK, INTENT, verify_payload

BASE = r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
PRODUCT = '{7AB27613-3319-49B5-A2EB-796458D9C0F6}_is1'
FLAGS = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOCLOSEAPPLICATIONS']
REG_VIEW = winreg.KEY_WOW64_64KEY


def registration(key=PRODUCT):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BASE + '\\' + key, 0, winreg.KEY_READ | REG_VIEW) as entry:
            return tuple(winreg.QueryValueEx(entry, k)[0] for k in ('InstallLocation', 'DisplayVersion', 'UninstallString'))
    except FileNotFoundError:
        return None


def rename(handle, name, new):
    call = ctypes.windll.advapi32.RegRenameKey
    call.argtypes = (wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR)
    call.restype = wintypes.LONG
    if code := call(int(handle), name, new):
        raise ctypes.WinError(code)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def wait_for(test, seconds, reason):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if value := test():
            return value
        time.sleep(.05)
    raise RuntimeError(reason)


def apps():
    return [p for p in psutil.process_iter(['name', 'exe']) if (p.info['name'] or '').casefold() == 'linguafloat.exe']


def snapshot_shortcuts():
    # Inno can use the actual Known Folder despite the test process's APPDATA.
    buffer = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 2, None, 0, buffer) != 0:
        raise RuntimeError('Could not locate the Windows Programs folder')
    menu = Path(buffer.value) / 'LinguaFloat'
    paths = (menu / 'LinguaFloat.lnk', menu / '卸载 LinguaFloat.lnk')
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def restore_shortcuts(snapshot):
    for path, data in snapshot.items():
        if data is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def run(old_setup, new_setup, metadata, signature, output, scenario):
    if apps():
        raise SystemExit('The app is running. No acceptance files or registration changed.')
    sys.path.insert(0, str(ROOT / 'src'))
    from linguafloat.update_core import load_channel, parse_manifest
    old_version = old_setup.name.split('-')[1].removeprefix('v')
    original = parse_manifest((old_setup.parent / 'update.json').read_bytes(),
                              (old_setup.parent / 'update.json.sig').read_bytes(), load_channel(), old_version)
    assert original.version == old_version and original.sha256 == digest(old_setup)
    new = parse_manifest(metadata.read_bytes(), signature.read_bytes(), load_channel(), old_version)
    if new.sha256 != digest(new_setup):
        raise RuntimeError('Candidate package differs from the signed manifest')
    root = Path(tempfile.mkdtemp(prefix='setup-accept-', dir=output)).resolve()
    target = root / 'Isolated Install'
    exe = target / 'LinguaFloat.exe'
    guid = uuid.uuid4().hex
    group = 'LinguaFloat QA ' + guid
    parked = PRODUCT + '.saved-' + guid
    local = root / 'Local'
    roaming = root / 'Roaming'
    user_dir = roaming / 'LinguaFloat'
    user_dir.mkdir(parents=True)
    config = user_dir / 'config.json'
    config.write_text(json.dumps({'update_auto_check': False, 'first_run_complete': True}))
    history = user_dir / 'documents/jobs.sqlite3'
    from linguafloat.document_core import JobStore
    import sqlite3
    from contextlib import closing
    JobStore(history)
    with closing(sqlite3.connect(history)) as db:
        db.execute('create table qa_evidence (value text)')
        db.execute("insert into qa_evidence values ('keep me')")
        db.commit()
    env = dict(os.environ, LOCALAPPDATA=str(local), APPDATA=str(roaming), QT_QPA_PLATFORM='offscreen')
    job = local / 'LinguaFloat/updates' / ('v' + new.version + '-' + new.sha256[:12])
    job.mkdir(parents=True)
    shutil.copyfile(metadata, job / 'update.json')
    shutil.copyfile(signature, job / 'update.json.sig')
    try:
        os.link(new_setup, job / 'Setup.exe')
    except OSError:
        shutil.copyfile(new_setup, job / 'Setup.exe')
    actual = registration()
    shortcuts = snapshot_shortcuts() if actual else {}
    original_exe = Path(actual[0]) / exe.name if actual else None
    original_hash = digest(original_exe) if original_exe else None
    print('Evidence:', root, flush=True)

    def stop_test_apps():
        for p in apps():
            if p.info['exe'] and Path(p.info['exe']).resolve() == exe:
                p.terminate()  # Only the disposable isolated QA process.
                p.wait(15)

    def command(package, log):
        return [str(package), *FLAGS, '/TASKS=!desktopicon', '/GROUP=' + group,
                '/DIR=' + str(target), '/LOG=' + str(root / log)]

    def uninstall():
        if entry := registration():
            assert Path(entry[0]).resolve() == target
            tool = Path(entry[2].strip('"')).resolve(strict=True)
            assert tool.parent == target
            subprocess.run([str(tool), *FLAGS], env=env, check=True, timeout=180)

    def installed(expected):
        entry = registration()
        assert Path(entry[0]).resolve() == target and entry[1] == expected

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BASE, 0, winreg.KEY_WRITE | REG_VIEW) as parent:
        if actual:
            rename(parent, PRODUCT, parked)
        try:
            subprocess.run(command(old_setup, 'install-old.log'), env=env, check=True, timeout=240)
            installed(old_version)
            request = dict(target=str(target), current=old_version, parent_pid=0, nonce=guid)
            (job / 'request.json').write_text(json.dumps(request))
            before = subprocess.Popen([str(exe), '--update-health', str(job)], cwd=target, env=env)
            ack = job / 'healthy.json'
            wait_for(ack.is_file, 45, 'Old app did not open')
            old_health = json.loads(ack.read_text())
            assert old_health['target'] == str(target) and old_health['version'] == old_version
            stop_test_apps()
            before.wait(20)
            ack.unlink()
            config_hash = digest(config)
            baseline = dict(phase='prepared', target=str(target))
            (job / 'state.json').write_text(json.dumps(baseline))
            updater = job / 'LinguaFloatUpdater.exe'
            if scenario == 'normal-new-helper':
                shutil.copyfile(new_setup.parent / 'build-input/LinguaFloat' / updater.name, updater)
                (target / INTENT).write_text(json.dumps(dict(nonce=guid, created=time.time())))
            else:
                shutil.copyfile(target / updater.name, updater)
            request['parent_pid'] = old_health['pid']
            (job / 'request.json').write_text(json.dumps(request))

            attempts = []
            if scenario == 'failure':
                # Exclusive write/delete access denied only for the last file in the real installer.
                create = ctypes.windll.kernel32.CreateFileW
                create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                create.restype = wintypes.HANDLE
                close = ctypes.windll.kernel32.CloseHandle
                close.argtypes = [wintypes.HANDLE]
                lock = create(str(target / 'VERSION.txt'), 0x80000000, 1, None, 3, 0x80, None)
                assert lock not in (None, ctypes.c_void_p(-1).value)
                try:
                    failure = subprocess.run(command(job / 'Setup.exe', 'injected-failure.log'), env=env, timeout=240)
                finally:
                    close(lock)
                assert failure.returncode == 5, failure.returncode
                assert (target / INCOMPLETE).is_file()
                log = (root / 'injected-failure.log').read_text(encoding='utf-8-sig')
                assert log.index(r'\LinguaFloat.exe') < log.index('Defaulting to Abort')
                assert 'DeleteFile' in log and r'\VERSION.txt' in log
                assert not apps()
                subprocess.run(command(job / 'Setup.exe', 'repair.log'), env=env, check=True, timeout=240)
                assert not (target / INCOMPLETE).exists()
                installed(new.version)
                verify_payload(target, new.version)
                with closing(sqlite3.connect(history)) as db:
                    assert db.execute('select value from qa_evidence').fetchone() == ('keep me',)
                assert digest(config) == config_hash
                print(json.dumps(dict(scenario=scenario, setup_exit=failure.returncode,
                                      original_path=str(exe), recovery='passed')), flush=True)
                return

            helper = subprocess.Popen([str(updater), '--job', str(job)], cwd=job, env=env)
            try:
                if scenario == 'reclick':
                    log = job / 'setup.log'
                    wait_for(lambda: log.is_file() and b'Starting the installation process.' in log.read_bytes(),
                             50, 'Setup did not start replacing files')
                    for _ in range(240):
                        if not (target / INCOMPLETE).exists():
                            break
                        try:
                            launch = subprocess.Popen([str(exe)], cwd=target, env=env)
                        except FileNotFoundError:
                            attempts.append('entry temporarily unavailable')
                        else:
                            assert launch.wait(8) == 0, 'An early click did not exit cleanly'
                            attempts.append('candidate refused early launch')
                        time.sleep(.25)
                    assert attempts
                assert helper.wait(260) == 0
            finally:
                if helper.poll() is None:
                    raise RuntimeError('Update assistant remains active; do not change its target until it exits')
            state = json.loads((job / 'state.json').read_text())
            assert state['phase'] == 'committed' and state['version'] == new.version
            after = wait_for(lambda: next((p for p in apps() if p.info['exe'] and Path(p.info['exe']) == exe), None),
                             40, 'Candidate did not automatically start in the original directory')
            time.sleep(3)
            assert after.is_running() and after.pid != old_health['pid']
            installed(new.version)
            verify_payload(target, new.version)
            assert not any((target / marker).exists() for marker in (INCOMPLETE, INSTALL_LOCK, INTENT))
            with closing(sqlite3.connect(history)) as db:
                assert db.execute('select value from qa_evidence').fetchone() == ('keep me',)
            assert digest(config) == config_hash
            print(json.dumps(dict(scenario=scenario, from_version=old_version, to_version=new.version,
                                  path_before=old_health['target'], path_after=after.info['exe'],
                                  early_launch_counts={a: attempts.count(a) for a in set(attempts)})), flush=True)
        finally:
            try:
                stop_test_apps()
                uninstall()
            finally:
                if actual:
                    if remaining := registration():
                        if Path(remaining[0]).resolve() != target:
                            raise RuntimeError(f'Unexpected concurrent registration; untouched original is at {parked}')
                        rename(parent, PRODUCT, PRODUCT + '.qa-failed-' + guid)
                    rename(parent, parked, PRODUCT)
                    assert registration() == actual and digest(original_exe) == original_hash
                    restore_shortcuts(shortcuts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('old_setup', 'new_setup', 'metadata', 'signature', 'output'):
        parser.add_argument(arg, type=Path)
    parser.add_argument('scenario', choices=('normal', 'normal-new-helper', 'reclick', 'failure'))
    run(**{k: v.resolve() if isinstance(v, Path) else v for k, v in vars(parser.parse_args()).items()})
