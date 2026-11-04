import base64
import hashlib
import io
import json
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from linguafloat import update_core as core
from linguafloat import update_install as installer


@pytest.fixture
def signed():
    value = json.loads((Path(__file__).parent / "fixtures/update-signature.json").read_text())
    return base64.b64decode(value["payload"]), value["signature"].encode(), {
        "repository": "example/releases", "public_key": value["public_key"]}


def test_windows_cng_accepts_real_signature_and_rejects_tampering(signed):
    payload, signature, channel = signed
    release = core.parse_manifest(payload, signature, channel, "0.11.0")
    assert release.version == "0.12.0"
    with pytest.raises(core.UpdateError, match="签名"):
        core.parse_manifest(payload.replace(b"0.12.0", b"0.13.0"), signature, channel, "0.11.0")
    with pytest.raises(core.UpdateError):
        core.parse_manifest(payload, b"not-base64", channel, "0.11.0")


@pytest.mark.parametrize("field,value", [("architecture", "win-arm64"), ("schema", 9), ("candidate_only", True),
    ("version", "0.12.0rc1"), ("minimum_updater", "5.0.0"), ("size", -1), ("repository", "other/repo"),
    ("url", "https://evil.invalid/release.zip"), ("page", "https://github.com/example/releases/releases/tag/v0.99.0")])
def test_invalid_or_inapplicable_metadata_is_never_installed(signed, monkeypatch, field, value):
    payload, signature, channel = signed
    raw = json.loads(payload)
    raw[field] = value
    monkeypatch.setattr(core, "verify_signature", lambda *a: None)
    with pytest.raises(core.UpdateError):
        core.parse_manifest(json.dumps(raw).encode(), signature, channel, "0.11.0")


def test_check_downloads_signed_metadata_and_uses_conditional_cache(signed, tmp_path):
    payload, signature, channel = signed
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "api.github.com":
            if request.headers.get("If-None-Match") == '"fixture"':
                return httpx.Response(304)
            assets = [{"name": n, "browser_download_url": f"https://github.com/example/releases/releases/download/v0.12.0/{n}"}
                      for n in ("update.json", "update.json.sig")]
            return httpx.Response(200, json={"tag_name": "v0.12.0", "assets": assets}, headers={"ETag": '"fixture"'})
        return httpx.Response(200, content=signature if request.url.path.endswith(".sig") else payload)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        cache = tmp_path / "cache.json"
        first = core.check_latest(client, channel, "0.11.0", cache)
        assert first.version == "0.12.0"
        assert core.check_latest(client, channel, "0.11.0", cache) == first
        assert len(calls) == 4
        assert core.check_latest(client, channel, "0.13.0", cache) is None


@pytest.mark.parametrize("status", [403, 429, 404, 500])
def test_failed_checks_are_not_reported_as_latest(signed, tmp_path, status):
    channel = signed[2]
    count = []
    def respond(request):
        count.append(1)
        return httpx.Response(status, headers={"Retry-After": "90"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(core.UpdateError):
            core.check_latest(client, channel, "0.11.0", tmp_path / "cache.json", now=100)
        if status in (403, 429):
            with pytest.raises(core.UpdateError, match="限流"):
                core.check_latest(client, channel, "0.11.0", tmp_path / "cache.json", now=110)
            assert len(count) == 1


def package(tmp_path, *, extra=None, version="0.12.0"):
    data = {"LinguaFloat.exe": b"test executable", "LinguaFloatUpdater.exe": b"test helper", "VERSION.txt": f"LinguaFloat v{version}\n".encode(),
            "document-runtime/python.exe": b"test runtime", "document-runtime/worker_entry.py": b"# fixture", "_internal/release/channel.json": b"{}"}
    if extra:
        data.update(extra)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in data.items():
            info = zipfile.ZipInfo(name)
            info.filename = name  # Preserve malicious Windows backslashes in the actual ZIP member.
            archive.writestr(info, content)
    payload = stream.getvalue()
    release = core.Release(version, f"https://github.com/example/releases/releases/download/v{version}/program.zip", len(payload),
                           sum(len(v) for v in data.values()), hashlib.sha256(payload).hexdigest(), "", "", "v" + version)
    path = tmp_path / "test-package.zip"
    path.write_bytes(payload)
    return path, release


@pytest.mark.parametrize("name", ["../escape", "C:/escape", "..\\escape", "NUL", "space. /x", "file:stream", "linguafloat.EXE"])
def test_archive_rejects_windows_path_escapes_and_aliases(tmp_path, name):
    path, release = package(tmp_path, extra={name: b"unsafe"})
    with pytest.raises(core.UpdateError):
        core.check_archive(path, release)


def test_download_resume_and_revalidate_complete_archive(tmp_path):
    path, release = package(tmp_path)
    data = path.read_bytes()
    target = tmp_path / "download"
    target.mkdir()
    (target / "package.part").write_bytes(data[:50])
    def respond(request):
        assert request.headers["Range"] == "bytes=50-"
        return httpx.Response(206, content=data[50:], headers={"Content-Range": f"bytes 50-{len(data)-1}/{len(data)}"})
    progress = []
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = core.download_release(client, release, target, lambda *p: progress.append(p), threading.Event())
    assert result.read_bytes() == data
    assert progress[-1] == (len(data), len(data))


def test_cancelled_download_keeps_partial_and_does_not_replace_program(tmp_path):
    path, release = package(tmp_path)
    event = threading.Event()
    event.set()
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=path.read_bytes()))) as client:
        with pytest.raises(core.UpdateError, match="暂停"):
            core.download_release(client, release, tmp_path / "download", lambda *a: None, event)
    assert not (tmp_path / "download/package.zip").exists()


def install_fixture(tmp_path, monkeypatch):
    target = tmp_path / "app"
    target.mkdir()
    (target / "VERSION.txt").write_text("LinguaFloat v0.11.0\n")
    (target / "LinguaFloat.exe").write_text("old")
    root = tmp_path / "updates"
    job = root / "transaction"
    job.mkdir(parents=True)
    archive, release = package(tmp_path)
    archive.rename(job / "package.zip")
    (job / "update.json").write_text("{}")
    (job / "update.json.sig").write_text("ignored by isolated installer fixture")
    core.atomic_json(job / "request.json", {"target": str(target.resolve()), "current": "0.11.0", "parent_pid": 999999, "nonce": "fixture"})
    monkeypatch.setattr(installer, "update_directory", lambda: root)
    monkeypatch.setattr(installer, "process_alive", lambda p: False)
    monkeypatch.setattr(installer, "running_in", lambda p: False)
    monkeypatch.setattr(installer, "parse_manifest", lambda *a: release)
    monkeypatch.setattr(installer, "load_channel", lambda: {})
    monkeypatch.setenv("APPDATA", str(tmp_path / "data"))
    monkeypatch.setattr(installer, "launch_application", lambda *a: None)
    return target, job


def test_complete_install_preserves_data_and_old_bundle(tmp_path, monkeypatch):
    target, job = install_fixture(tmp_path, monkeypatch)
    config = tmp_path / "data/LinguaFloat/config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"keep": true}')
    def launch(path, directory):
        core.atomic_json(directory / "healthy.json", {"version": "0.12.0", "nonce": "fixture", "target": str(path), "pid": 123})
        return SimpleNamespace(pid=123, poll=lambda: None)
    installer.install(job, launch=launch, health_timeout=1)
    state = json.loads((job / "state.json").read_text())
    assert state["phase"] == "committed"
    assert (target / "VERSION.txt").read_text().startswith("LinguaFloat v0.12.0")
    assert (Path(state["backup"]) / "LinguaFloat.exe").read_text() == "old"
    assert config.read_text() == '{"keep": true}'


def test_failed_start_rolls_back_program_and_pre_migration_data(tmp_path, monkeypatch):
    target, job = install_fixture(tmp_path, monkeypatch)
    config = tmp_path / "data/LinguaFloat/config.json"
    config.parent.mkdir(parents=True)
    config.write_text("old data")
    def launch(path, directory):
        config.write_text("incompatible migration")
        return SimpleNamespace(pid=123, poll=lambda: 1)
    with pytest.raises(core.UpdateError, match="启动检查"):
        installer.install(job, launch=launch, health_timeout=.1)
    assert (target / "LinguaFloat.exe").read_text() == "old"
    assert config.read_text() == "old data"
    assert json.loads((job / "state.json").read_text())["phase"] == "rolled_back"


def test_running_document_process_blocks_replacement(tmp_path, monkeypatch):
    target, job = install_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(installer, "running_in", lambda p: True)
    with pytest.raises(core.UpdateError, match="后台"):
        installer.install(job)
    assert (target / "LinguaFloat.exe").read_text() == "old"


def test_recovery_handles_interrupted_directory_swap(tmp_path, monkeypatch):
    target, job = install_fixture(tmp_path, monkeypatch)
    backup = tmp_path / "backup"
    target.rename(backup)
    core.atomic_json(job / "state.json", {"phase": "swapping", "target": str(target), "backup": str(backup)})
    installer.install(job, recover=True)
    assert (target / "LinguaFloat.exe").read_text() == "old"


def test_first_run_and_update_preferences_migrate_without_resetting_settings(tmp_path):
    from linguafloat.config import ConfigStore
    store = ConfigStore(tmp_path / "config.json")
    assert not store.load().first_run_complete
    store.path.write_text('{"document_output_dir":"D:/Results","update_auto_check":false}')
    settings = store.load()
    assert settings.first_run_complete and not settings.update_auto_check
    assert settings.document_output_dir == "D:/Results"
    store.save(settings)
    assert store.load() == settings
