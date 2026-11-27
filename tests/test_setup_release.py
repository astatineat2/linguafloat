import hashlib
import json
import threading

import httpx
import pytest

from linguafloat import update_core as core


def test_startup_gate_retains_failed_install_after_lock_released(tmp_path, monkeypatch):
    from linguafloat import setup_gate

    target = tmp_path / 'installed'
    other = tmp_path / 'another-install'
    target.mkdir()
    other.mkdir()
    (target / setup_gate.INCOMPLETE).write_text('0.23.5')
    monkeypatch.setattr(setup_gate, 'installer_running', lambda path: path == target)
    assert setup_gate.startup_status(target) == 'installing'
    assert setup_gate.startup_status(other) is None
    monkeypatch.setattr(setup_gate, 'installer_running', lambda path: False)
    assert setup_gate.startup_status(target) == 'repair'
    (target / setup_gate.INCOMPLETE).unlink()
    assert setup_gate.startup_status(target) is None


def test_stale_handoff_from_terminated_updater_does_not_lock_startup(tmp_path, monkeypatch):
    from linguafloat import setup_gate

    monkeypatch.setattr(setup_gate.time, 'time', lambda: 600)
    (tmp_path / setup_gate.INTENT).write_text(json.dumps(dict(nonce='handoff', created=298)))
    assert setup_gate.startup_status(tmp_path) is None
    (tmp_path / setup_gate.INTENT).write_text(json.dumps(dict(nonce='handoff', created=599)))
    assert setup_gate.startup_status(tmp_path) == 'installing'
    (tmp_path / setup_gate.INCOMPLETE).write_text('0.23.5')
    assert not setup_gate.health_start_allowed(tmp_path, tmp_path / 'untrusted-job')


def test_install_receipt_checks_components_not_only_version(tmp_path):
    from linguafloat import setup_gate

    version = '0.23.5'
    for path in setup_gate.CRITICAL_FILES:
        f = tmp_path / path
        f.parent.mkdir(exist_ok=True, parents=True)
        f.write_text(path)
    (tmp_path / 'VERSION.txt').write_text(f'LinguaFloat v{version}\n')
    receipt = {p: core.sha256_file(tmp_path / p) for p in setup_gate.CRITICAL_FILES}
    (tmp_path / setup_gate.RECEIPT).write_text(json.dumps(dict(version=version, files=receipt)))
    setup_gate.verify_payload(tmp_path, version)
    (tmp_path / 'LinguaFloatUpdater.exe').write_text('wrong version')
    with pytest.raises(core.UpdateError, match='LinguaFloatUpdater.exe'):
        setup_gate.verify_payload(tmp_path, version)


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


@pytest.mark.parametrize('exit_code,updated,ack,phase', [
    (0, True, True, 'committed'), (5, True, False, 'setup_failed'),
    (0, False, False, 'setup_failed'), (0, True, False, 'restart_failed'),
])
def test_setup_helper_checks_outcome_before_restart(tmp_path, monkeypatch, exit_code, updated, ack, phase):
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
    monkeypatch.setattr(helper, 'check_setup_target', lambda *args: owners.append(args))
    def run(cmd, cwd):
        assert cmd[0] == str(setup) and '/DIR='+str(target) in cmd and cwd == job
        if updated:
            (target/'VERSION.txt').write_text('LinguaFloat v0.21.0\n')
        return SimpleNamespace(returncode=exit_code)
    monkeypatch.setattr(helper.subprocess, 'run', run)
    launched = []
    def verify(path, version):
        if not updated:
            raise core.UpdateError('bad payload')
    def launch(path, directory):
        launched.append((path, directory))
        if ack:
            core.atomic_json(job / 'healthy.json', dict(nonce='fixture', target=str(path), version=release.version, pid=5))
        return SimpleNamespace(pid=5, poll=lambda: None if ack else 1)
    monkeypatch.setattr(helper, 'verify_payload', verify)
    with nullcontext() if ack else pytest.raises(core.UpdateError):
        helper.install(job, launch=launch, health_timeout=1)
    assert owners[0] == (target,)
    assert launched == ([(target, job)] if phase in {'committed', 'restart_failed'} else [])
    assert json.loads((job / 'state.json').read_text())['phase'] == phase
