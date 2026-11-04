"""Exercise the compiled update assistant with a deliberately non-executable test app."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from linguafloat.update_core import atomic_json, update_directory
from release_tools import sign


def main():
    identity = "native-rollback-" + uuid.uuid4().hex[:12]
    target = ROOT / ".test-update-native" / identity / "app"
    target.mkdir(parents=True)
    shutil.copy2(Path(os.environ["WINDIR"]) / "System32/where.exe", target / "LinguaFloat.exe")
    original = hashlib.sha256((target / "LinguaFloat.exe").read_bytes()).hexdigest()
    (target / "VERSION.txt").write_text("LinguaFloat v0.11.0\n", encoding="utf-8")
    job = update_directory() / identity
    job.mkdir(parents=True)
    data = {"LinguaFloat.exe": b"intentionally invalid test executable", "LinguaFloatUpdater.exe": b"fixture",
            "VERSION.txt": b"LinguaFloat v0.11.1\n", "_internal/release/channel.json": b"{}",
            "document-runtime/python.exe": b"fixture", "document-runtime/worker_entry.py": b"fixture"}
    archive = job / "package.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name, content in data.items():
            z.writestr(name, content)
    value = {"schema": 1, "version": "0.11.1", "minimum_updater": "0.11.0", "architecture": "win-x64",
             "repository": "astatineat2/linguafloat-releases", "size": archive.stat().st_size,
             "unpacked_size": sum(map(len, data.values())), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
             "url": "https://github.com/astatineat2/linguafloat-releases/releases/download/v0.11.1/test-only.zip",
             "page": "https://github.com/astatineat2/linguafloat-releases/releases/tag/v0.11.1"}
    payload = json.dumps(value).encode()
    (job / "update.json").write_bytes(payload)
    (job / "update.json.sig").write_bytes(sign(payload))
    atomic_json(job / "request.json", {"target": str(target.resolve()), "current": "0.11.0", "parent_pid": 999999,
                                       "nonce": uuid.uuid4().hex})
    helper = job / "LinguaFloatUpdater.exe"
    shutil.copy2(ROOT / "app-dist/LinguaFloat/LinguaFloatUpdater.exe", helper)
    environment = dict(os.environ, APPDATA=str(target.parent / "isolated-data"))
    process = subprocess.Popen([str(helper), "--job", str(job)], cwd=job, env=environment, creationflags=0x08000000)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and not (job / "error.json").exists() and process.poll() is None:
        time.sleep(.2)
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "rolled_back", state
    assert hashlib.sha256((target / "LinguaFloat.exe").read_bytes()).hexdigest() == original
    report = {"compiled_helper": True, "signed_test_package": True, "expected_failure": "invalid test executable",
              "result": "old executable restored", "journal": str(job), "helper_pid": process.pid}
    output = ROOT / "verification-v0.11.0/native-rollback.json"
    atomic_json(output, report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
