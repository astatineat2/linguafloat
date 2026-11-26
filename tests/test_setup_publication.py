import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import github_publish
import release_tools


def test_prepare_only_setup_and_source(tmp_path, monkeypatch):
    version = release_tools.__version__
    name = f'LinguaFloat-v{version}-windows-x64-Setup.exe'
    dist = tmp_path / 'dist'; dist.mkdir()
    data = b'MZfixture'
    (dist/name).write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (dist/'SHA256SUMS.txt').write_text(digest + '  ' + name + '\n')
    (dist/'build-summary.json').write_text(json.dumps(dict(format='setup', version=version, payload_bytes=4096)))
    docs = tmp_path/'docs/public'; docs.mkdir(parents=True)
    (docs/'本版更新.md').write_text('notes')
    monkeypatch.setattr(release_tools, 'ROOT', tmp_path)
    monkeypatch.setattr(release_tools, 'write_source', lambda path: path.write_bytes(b'source fixture'))
    monkeypatch.setattr(release_tools, 'sign', lambda payload: b'signature fixture')
    monkeypatch.setattr(release_tools.subprocess, 'check_output', lambda *a, **kw: 'test-revision')
    release_tools.prepare(dist, candidate=True)
    folder = tmp_path/'release-assets'/('v'+version)
    value = json.loads((folder/'update.json').read_text(encoding='utf-8'))
    assert value['schema'] == 2 and value['sha256'] == digest and value['url'].endswith('/'+name)
    assert {p.name for p in folder.glob('*.zip')} == {f'LinguaFloat-v{version}-corresponding-source.zip'}
    assert value['candidate_only'] and not json.loads((folder/'LOCAL-RELEASE-REVIEW.json').read_text())['public_release_ready']
    monkeypatch.setattr(github_publish, 'ROOT', tmp_path)
    with pytest.raises(SystemExit, match='未上传'):
        github_publish.publish(None)
