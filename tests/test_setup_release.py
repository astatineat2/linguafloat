import hashlib
import json
import threading

import httpx
import pytest

from linguafloat import update_core as core


def manifest(data):
    base = 'https://github.com/example/releases/releases/'
    return dict(schema=2, version='0.21.0', minimum_updater='0.20.0', architecture='win-x64',
                repository='example/releases', size=len(data), unpacked_size=100000,
                sha256=hashlib.sha256(data).hexdigest(), page=base+'tag/v0.21.0',
                url=base+'download/v0.21.0/LinguaFloat-v0.21.0-windows-x64-Setup.exe')


def parse(raw, monkeypatch):
    monkeypatch.setattr(core, 'verify_signature', lambda *args: None)
    return core.parse_manifest(json.dumps(raw).encode(), b'sig',
                               dict(public_key={}, repository='example/releases'), '0.20.0')


def test_verified_setup_is_downloaded_as_executable(tmp_path, monkeypatch):
    data = b'MZ' + bytes(128)
    release = parse(manifest(data), monkeypatch)
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=data))) as client:
        path = core.download_release(client, release, tmp_path, lambda *args: None, threading.Event())
        assert path.name == 'Setup.exe' and path.read_bytes() == data
        path.write_bytes(b'MZ' + bytes([1])*128)
        with pytest.raises(core.UpdateError):
            core.download_release(client, release, tmp_path, lambda *args: None, threading.Event())


def test_setup_schema_rejects_wrong_asset(monkeypatch):
    raw = manifest(b'hi')
    raw['url'] = raw['url'].replace('Setup.exe', 'portable.zip')
    with pytest.raises(core.UpdateError):
        parse(raw, monkeypatch)


@pytest.mark.parametrize('exit_code,updated,should_start', [(0,True,True),(5,False,False),(0,False,False)])
def test_setup_helper_checks_outcome_before_restart(tmp_path, monkeypatch, exit_code, updated, should_start):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from linguafloat import update_install as helper
    from test_updates import install_fixture

    target, job = install_fixture(tmp_path, monkeypatch)
    data = b'MZ' + bytes(128)
    setup = job/'Setup.exe'; setup.write_bytes(data)
    release = parse(manifest(data), monkeypatch)
    monkeypatch.setattr(helper, 'parse_manifest', lambda *args: release)
    owners = []
    monkeypatch.setattr(helper, 'check_setup_target', owners.append)
    def run(cmd, cwd):
        assert cmd[0] == str(setup) and '/DIR='+str(target) in cmd and cwd == job
        if updated:
            (target/'VERSION.txt').write_text('LinguaFloat v0.21.0\n')
        return SimpleNamespace(returncode=exit_code)
    monkeypatch.setattr(helper.subprocess, 'run', run)
    launched = []
    with nullcontext() if should_start else pytest.raises(core.UpdateError):
        helper.install(job, launch=launched.append)
    assert owners[0] == target
    assert launched == ([target] if should_start else [])
