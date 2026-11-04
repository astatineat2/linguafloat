"""The publisher must safely resume a draft without replacing immutable assets."""
import importlib.util
import json
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
    monkeypatch.setattr(update_core, "parse_manifest", lambda *args: object())
    monkeypatch.setattr(update_core, "check_archive", lambda *args: None)
    monkeypatch.setattr(update_core, "load_channel", lambda: {})
    folder = tmp_path / "release-assets" / ("v" + module.__version__)
    folder.mkdir(parents=True)
    (folder / "LOCAL-RELEASE-REVIEW.json").write_text('{"public_release_ready": true}')
    names = [f"LinguaFloat-v{module.__version__}-win-x64.zip",
             f"LinguaFloat-v{module.__version__}-corresponding-source.zip", "update.json", "update.json.sig"]
    for name in names:
        (folder / name).write_bytes(b"fixture:" + name.encode())
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
            return httpx.Response(200, json={"html_url": "https://github.com/example/release"})
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
