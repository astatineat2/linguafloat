"""Locally install the signed candidate through the compiled helper, after user exit.

The first v0.10 -> v0.11 bootstrap is a local publisher operation, not a GitHub update.
No candidate release metadata is made publicly installable.
"""
from pathlib import Path
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from linguafloat import __version__
from linguafloat.update_core import atomic_json, check_archive, load_channel, parse_manifest, update_directory
from linguafloat.update_install import running_in
from release_tools import sign


def history_count():
    path = Path(os.environ["APPDATA"]) / "LinguaFloat/documents/jobs.sqlite3"
    if not path.exists():
        return None
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                for name in names if name != "sqlite_sequence"}


def main():
    target = ROOT / "exe"
    if running_in(target):
        raise SystemExit("Current installation is running. Exit normally first; no task was interrupted.")
    candidate = ROOT / "release-assets" / ("v" + __version__)
    value = json.loads((candidate / "update.json").read_text(encoding="utf-8"))
    current = (target / "VERSION.txt").read_text(encoding="utf-8").splitlines()[0].split("v", 1)[1]
    if current != "0.10.0" or value["version"] != __version__:
        raise SystemExit("This one-time local verification expects the original v0.10.0 installation.")
    value["minimum_updater"] = "0.10.0"
    value["candidate_only"] = False
    value["notes"] = "Local publisher bootstrap verification only; not uploaded to GitHub."
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    signature = sign(payload)
    release = parse_manifest(payload, signature, load_channel(), current)
    archive = candidate / f"LinguaFloat-v{__version__}-win-x64.zip"
    check_archive(archive, release)
    before = history_count()
    job = update_directory() / ("bootstrap-" + uuid.uuid4().hex[:12])
    job.mkdir(parents=True)
    (job / "update.json").write_bytes(payload)
    (job / "update.json.sig").write_bytes(signature)
    shutil.copy2(archive, job / "package.zip")
    helper = job / "LinguaFloatUpdater.exe"
    shutil.copy2(ROOT / "app-dist/LinguaFloat/LinguaFloatUpdater.exe", helper)
    atomic_json(job / "request.json", {"target": str(target.resolve()), "current": current,
                                      "parent_pid": 0, "nonce": uuid.uuid4().hex})
    process = subprocess.Popen([str(helper), "--job", str(job)], cwd=job, creationflags=0x08000000)
    print("Compiled helper started for local bootstrap verification.", flush=True)
    deadline = time.monotonic() + 180
    state = {}
    while time.monotonic() < deadline:
        path = job / "state.json"
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        if state.get("phase") in {"committed", "rolled_back"} or (job / "error.json").exists():
            break
        time.sleep(.25)
    after = history_count()
    result = {"compiled_helper": True, "source": "local signed candidate", "public_github_test": False,
              "phase": state.get("phase"), "version": __version__, "target": str(target),
              "journal": str(job), "history_counts_before": before, "history_counts_after": after,
              "counts_preserved": before == after, "helper_pid": process.pid}
    atomic_json(ROOT / "verification-v0.11.0/bootstrap-install.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if state.get("phase") != "committed" or before != after:
        raise SystemExit("Bootstrap not fully verified; inspect retained journal and backup.")


if __name__ == "__main__":
    main()
