"""Corresponding sources must retain paths used by their own build scripts."""
import importlib.util
from pathlib import Path


def test_source_archive_retains_rebuild_and_test_inputs(monkeypatch, tmp_path):
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("source_release_test", root / "scripts/release_tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = set()

    class Archive:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, source, name):
            names.add(str(name).replace("\\", "/"))

        def writestr(self, name, content):
            names.add(name)

    monkeypatch.setattr(module.zipfile, "ZipFile", Archive)
    module.write_source(tmp_path / "source.zip")
    assert {
        "docs/public/首次使用指南.md", "docs/public/开发与构建.md",
        "scripts/build_distribution.py", "scripts/build_installer.ps1",
        "scripts/installer/LinguaFloat.iss", "scripts/installer/README.md",
        "tests/fixtures/update-signature.json", "source-materials/source-lock.json",
    } <= names
    assert not any("__pycache__" in n or n.endswith((".key", ".pem", ".pyc")) for n in names)
