"""The publisher must safely resume a draft without replacing immutable assets."""
import importlib.util
import json
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest

from linguafloat import update_core


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / "scripts/github_publish.py"
    spec = importlib.util.spec_from_file_location("release_publisher_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "documents", lambda client: None)
    setup = f"LinguaFloat-v{module.__version__}-windows-x64-Setup.exe"
    url = f"https://github.com/astatineat2/linguafloat-releases/releases/download/v{module.__version__}/{setup}"
    monkeypatch.setattr(update_core, "parse_manifest", lambda *args: SimpleNamespace(setup=True, version=module.__version__, url=url))
    monkeypatch.setattr(update_core, "check_archive", lambda *args: None)
    monkeypatch.setattr(update_core, "load_channel", lambda: {})
    folder = tmp_path / "release-assets" / ("v" + module.__version__)
    folder.mkdir(parents=True)
    (folder / "LOCAL-RELEASE-REVIEW.json").write_text('{"public_release_ready": true}')
    names = [setup,
             f"LinguaFloat-v{module.__version__}-corresponding-source.zip", "update.json", "update.json.sig"]
    for name in names:
        (folder / name).write_bytes(b"fixture:" + name.encode())
    (folder / 'SETUP-ACCEPTANCE.json').write_text(json.dumps(dict(from_version='0.20.0',
        to_version=module.__version__, package_sha256=update_core.sha256_file(folder / names[0]),
        clean_install=True, uninstalled=True, user_files_preserved=True,
        restarted_in_place=True, configuration_preserved=True)))
    (folder / "SHA256SUMS.txt").write_text("".join(update_core.sha256_file(folder / n) + "  " + n + "\n" for n in names))
    names.append("SHA256SUMS.txt")
    docs = tmp_path / "docs/public"
    docs.mkdir(parents=True)
    (docs / "本版更新.md").write_text("Reviewed fixture", encoding="utf-8")
    assets = [{"name": n, "state": "uploaded", "size": (folder / n).stat().st_size,
               "digest": "sha256:" + update_core.sha256_file(folder / n)} for n in names]
    return module, assets


@pytest.mark.parametrize("corrupt", [False, True])
def test_resume_only_publishes_matching_complete_draft(publisher, corrupt):
    module, assets = publisher
    calls = []
    if corrupt:
        assets[0]["digest"] = "sha256:" + "0" * 64
    def respond(request):
        calls.append(request)
        if request.url.path.endswith("/assets"):
            return httpx.Response(200, json=assets)
        if request.method == "PATCH":
            assert json.loads(request.content)['make_latest'] == 'true'
            return httpx.Response(200, json={"html_url": "https://github.com/example/release"})
        if request.url.path.endswith('/latest'):
            return httpx.Response(200, json={'id':123,'tag_name':'v'+module.__version__} if any(r.method=='PATCH' for r in calls) else {'id':1,'tag_name':'v0.11.1'})
        return httpx.Response(200, json=[{"id": 123, "tag_name": "v" + module.__version__, "draft": True,
                                         "upload_url": "https://uploads.github.com/repos/example/releases/123/assets{?name}"}])
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if corrupt:
            with pytest.raises(SystemExit, match="不匹配"):
                module.publish(client)
        else:
            module.publish(client)
    assert not any(r.method in {"POST", "DELETE"} for r in calls)
    assert sum(r.method == "PATCH" for r in calls) == (0 if corrupt else 1)


def test_repository_creation_keeps_development_private(publisher):
    module, _ = publisher
    created = []
    def respond(request):
        if request.method == "GET":
            return httpx.Response(404)
        body = json.loads(request.content)
        created.append(body)
        return httpx.Response(201, json={"private": body["private"],
            "html_url": "https://github.com/astatineat2/" + body["name"]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        module.ensure_repositories(client)
    assert [(b["name"], b["private"]) for b in created] == [
        ("linguafloat", True), ("linguafloat-releases", False)]


@pytest.mark.parametrize('mode', ['absent', 'different_package', 'unverified_restart', 'no_uninstall'])
def test_no_stable_promotion_without_matching_upgrade_evidence(publisher, mode):
    m, _ = publisher
    file = m.ROOT / 'release-assets' / ('v'+m.__version__) / 'SETUP-ACCEPTANCE.json'
    data = json.loads(file.read_text())
    if mode == 'absent':
        file.unlink()
    else:
        data.update({'different_package': {'package_sha256': '0' * 64},
                     'unverified_restart': {'restarted_in_place': False},
                     'no_uninstall': {'uninstalled': False}}[mode])
        file.write_text(json.dumps(data))
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('must stop before contacting GitHub'))) as client:
        with pytest.raises(SystemExit, match='更新验收记录'):
            m.publish(client)
