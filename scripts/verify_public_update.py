"""Verify an anonymous public download and install with the existing native helper.

No release mutation, credential lookup, translation call, or task termination.
"""
from pathlib import Path
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from linguafloat import __version__
from linguafloat.update_core import (atomic_json, check_latest, download_release, fetch_bytes,
                                    load_channel, parse_manifest, sha256_file, update_directory)
from linguafloat.update_install import running_in


def network_step(action):
    for attempt in range(3):
        try:
            return action()
        except httpx.HTTPError as exc:
            print(f"Network interruption ({type(exc).__name__}), attempt {attempt + 1}/3.", flush=True)
            if attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))


def data_snapshot():
    directory = Path(os.environ["APPDATA"]) / "LinguaFloat"
    config = directory / "config.json"
    result = {"config_sha256": sha256_file(config) if config.is_file() else None, "rows": {}}
    database = directory / "documents/jobs.sqlite3"
    if database.is_file():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                     if r[0] != "sqlite_sequence"]
            result["rows"] = {n: db.execute('SELECT COUNT(*) FROM "' + n.replace('"', '""') + '"').fetchone()[0]
                              for n in names}
    return result


def main():
    target = (ROOT / "exe").resolve()
    current = (target / "VERSION.txt").read_text(encoding="utf-8").splitlines()[0].removeprefix("LinguaFloat v")
    if current == __version__:
        raise SystemExit("The fixed installation is already current; refusing to reinstall.")
    output = ROOT / ("verification-v" + __version__)
    output.mkdir(exist_ok=True)
    job = update_directory() / ("public-v" + __version__ + "-" + uuid.uuid4().hex[:10])
    job.mkdir(parents=True)
    atomic_json(output / "public-job.json", {"directory": str(job)})
    result = {"authenticated": False, "paid_api": False, "from_version": current,
              "to_version": __version__, "job": str(job), "target": str(target)}
    channel = load_channel()
    with httpx.Client(timeout=httpx.Timeout(90, connect=15), follow_redirects=True,
                      headers={"User-Agent": "LinguaFloat/" + __version__, "Accept-Encoding": "identity"}) as client:
        release = network_step(lambda: check_latest(client, channel, current, job / "release-cache.json"))
        if release is None or release.version != __version__:
            raise SystemExit("The expected public stable version is not available; no installation changed.")
        # Use exact tagged assets for the install journal, not a mutable local candidate.
        base = release.url.rsplit("/", 1)[0] + "/"
        payload = network_step(lambda: fetch_bytes(client, base + "update.json"))
        signature = network_step(lambda: fetch_bytes(client, base + "update.json.sig", limit=4096))
        assert parse_manifest(payload, signature, channel, current) == release
        (job / "update.json").write_bytes(payload)
        (job / "update.json.sig").write_bytes(signature)
        last = [-1]
        def progress(done, total):
            step = int(done / total * 10)
            if step != last[0]:
                print(f"Public download: {step * 10}%", flush=True)
                last[0] = step
        archive = network_step(lambda: download_release(client, release, job, progress, threading.Event()))
        result.update({"signature_verified": True, "complete_package_downloaded": True,
                       "bytes": archive.stat().st_size, "sha256": sha256_file(archive),
                       "archive_validated": True})
        assert network_step(lambda: check_latest(client, channel, __version__, job / "current-check.json")) is None
        result["same_version_check_correct"] = True
    atomic_json(output / "public-download.json", result)
    if running_in(target):
        raise SystemExit("The application or document runtime is running. Download verified; exit normally before installation.")
    result["before"] = data_snapshot()
    helper = job / "LinguaFloatUpdater.exe"
    shutil.copy2(target / "LinguaFloatUpdater.exe", helper)
    result["installed_helper_sha256"] = sha256_file(helper)
    atomic_json(job / "request.json", {"target": str(target), "current": current,
                                      "parent_pid": 0, "nonce": uuid.uuid4().hex})
    process = subprocess.Popen([str(helper), "--job", str(job)], cwd=job, creationflags=0x08000000)
    result["helper_pid"] = process.pid
    print("Compiled installed helper started using the public signed package.", flush=True)
    deadline = time.monotonic() + 240
    previous = None
    state = {}
    while time.monotonic() < deadline:
        path = job / "state.json"
        if path.is_file():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        phase = state.get("phase")
        if phase != previous:
            print("Install phase: " + str(phase), flush=True)
            previous = phase
        if phase in {"committed", "rolled_back"} or (job / "error.json").exists():
            break
        time.sleep(.25)
    result.update({"phase": state.get("phase"), "after": data_snapshot(),
                   "healthy": (job / "healthy.json").is_file(), "backup": state.get("backup")})
    result["data_preserved"] = result["before"] == result["after"]
    result["installed_version"] = (target / "VERSION.txt").read_text(encoding="utf-8").splitlines()[0]
    atomic_json(output / "public-install.json", result)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    if result["phase"] != "committed" or not result["data_preserved"]:
        raise SystemExit("Public update not fully verified; inspect retained journal and backup.")


if __name__ == "__main__":
    main()
