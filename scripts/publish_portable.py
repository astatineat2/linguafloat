"""Publish a verified complete bundle; archive previous releases without deleting data."""
import argparse
import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--source", type=Path, required=True)
args = parser.parse_args()
source = args.source.resolve()
destination = ROOT / "exe"
if source == destination or ROOT not in source.parents:
    raise SystemExit("Publication source must be a separate build directory in this project.")
for name in ("LinguaFloat.exe", "_internal", "document-runtime/python.exe", "document-runtime/worker_entry.py"):
    if not (source / name).exists():
        raise SystemExit(f"Incomplete portable bundle: {name}")
subprocess.run([sys.executable, str(ROOT / "scripts/check_build.py"), "--build-dir",
                str(ROOT / "build-documents/LinguaFloat"), "--dist-dir", str(source)], check=True)
if sys.platform == "win32":
    command = "Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('LinguaFloat.exe','python.exe','pythonw.exe') } | Select-Object ExecutablePath | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, check=True)
    raw = result.stdout.decode("utf-8", errors="replace").strip()
    processes = json.loads(raw) if raw else []
    if isinstance(processes, dict):
        processes = [processes]
    for process in processes:
        path = process.get("ExecutablePath")
        if path and (Path(path).resolve() == destination / "LinguaFloat.exe" or destination in Path(path).resolve().parents):
            raise SystemExit("The installed bundle is running. Exit LinguaFloat before publishing; no files were replaced.")
sys.path.insert(0, str(ROOT / "src"))
from linguafloat import __version__
if destination.exists():
    if destination.is_symlink() or destination.resolve() != destination:
        raise SystemExit("Unexpected publication destination.")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = ROOT / "build-backups" / f"before-v{__version__}-{stamp}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    destination.rename(backup)
    print(f"Previous complete bundle retained: {backup}")
try:
    shutil.copytree(source, destination)
    (destination / "VERSION.txt").write_text(f"LinguaFloat v{__version__}\nLaunch: exe/LinguaFloat.exe\n", encoding="utf-8")
    shutil.copy2(ROOT / "docs/public/首次使用指南.md", destination / "README.md")
    shutil.copy2(ROOT / "docs/public/本版更新.md", destination / "CHANGELOG.md")
except Exception:
    print("Publication failed; incomplete bundle was retained for diagnosis. Previous bundle is in build-backups.")
    raise
print(f"Published v{__version__}: {destination / 'LinguaFloat.exe'}")
