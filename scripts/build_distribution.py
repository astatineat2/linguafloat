"""Snapshot the complete released bundle; build a Windows-compatible ZIP.

No dependency collection, upgrades, pruning, or application execution occurs here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import time
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = {
    "LinguaFloat.exe", "LinguaFloatUpdater.exe", "_internal", "document-runtime",
    "LICENSES", "help", "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md",
    "CHANGELOG.md", "VERSION.txt",
}
REQUIRED = (
    "LinguaFloat.exe", "LinguaFloatUpdater.exe", "_internal/python313.dll",
    "document-runtime/python.exe", "document-runtime/worker_entry.py",
    "document-runtime/src/linguafloat/document_worker.py", "LICENSE",
    "THIRD_PARTY_NOTICES.md", "VERSION.txt",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(directory: Path) -> list[dict]:
    rows = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Unexpected linked path: {path.relative_to(directory)}")
        if path.is_file():
            rows.append(dict(path=path.relative_to(directory).as_posix(),
                             bytes=path.stat().st_size, sha256=digest(path)))
    return rows


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_bundle(source: Path) -> str:
    names = {p.name for p in source.iterdir()}
    if names != TOP_LEVEL:
        raise ValueError(f"Review payload boundary before building: extra={names-TOP_LEVEL}, missing={TOP_LEVEL-names}")
    for relative in REQUIRED:
        if not (source / relative).is_file():
            raise ValueError(f"Missing required payload: {relative}")
    version_text = (source / "VERSION.txt").read_text(encoding="utf-8-sig")
    match = re.search(r"^LinguaFloat v(\d+\.\d+\.\d+)\s*$", version_text, re.M)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not match or match[1] != version:
        raise ValueError("The payload version must match the current application source")
    # These exact user-data names require review, not automatic removal. Dependency
    # resources such as certifi/cacert.pem and package tests are deliberately retained.
    private_names = {".env", "config.json", "credentials.json", "jobs.sqlite3", "history.sqlite3"}
    for path in source.rglob("*"):
        if path.is_file() and path.name.casefold() in private_names:
            raise ValueError(f"Review possible user data: {path.relative_to(source)}")
    return match[1]


def theme_assets(payload: Path, target: Path, version: str) -> None:
    from PIL import Image, ImageDraw, ImageFont
    icon = payload / "_internal/photo/app.ico"
    if not icon.is_file():
        icon = ROOT / "photo/app.ico"
    shutil.copy2(icon, target / "app.ico")
    image = Image.new("RGB", (328, 628), "#F6F6F9")
    draw = ImageDraw.Draw(image)
    logo = Image.open(icon).convert("RGBA").resize((112, 112), Image.Resampling.LANCZOS)
    image.paste(logo, (38, 60), logo)
    font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 32)
    small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 22)
    draw.text((38, 202), "LinguaFloat", font=font, fill="#1C2238")
    draw.text((38, 250), "v" + version, font=small, fill="#626980")
    image.save(target / "wizard.bmp")
    small_image = Image.new("RGB", (64, 64), "white")
    logo = logo.resize((48, 48), Image.Resampling.LANCZOS)
    small_image.paste(logo, (8, 8), logo)
    small_image.save(target / "wizard-small.bmp")


def make_distribution(source: Path, output: Path, build: int, level: int) -> None:
    source, output = source.resolve(), output.resolve()
    if output == source or source in output.parents:
        raise ValueError("Output must be outside the original portable bundle")
    version = check_bundle(source)
    if output.exists():
        raise FileExistsError("Use a new distribution directory; existing artifacts are never overwritten")
    output.mkdir(parents=True)
    started = time.monotonic()
    print("Reading complete source inventory...", flush=True)
    rows = inventory(source)
    total = sum(r["bytes"] for r in rows)
    if shutil.disk_usage(output).free < total * 4 + 512 * 1024**2:
        raise OSError("Insufficient free space for snapshot, archives and validation")
    write_json(output / "payload-manifest.json", dict(version=version, files=rows, bytes=total,
        count=len(rows), source_read_only=True, generated_utc=datetime.now(timezone.utc).isoformat()))
    payload = output / "build-input/LinguaFloat"
    print(f"Snapshotting {len(rows)} files, {total} bytes...", flush=True)
    shutil.copytree(source, payload)
    if inventory(payload) != rows:
        raise ValueError("Snapshot differs from original bundle")
    installer = output / "build-input/installer"
    installer.mkdir()
    theme_assets(payload, installer, version)
    script_dir = Path(__file__).parent / "installer"
    for name in ("LinguaFloat.iss", "README.md"):
        shutil.copy2(script_dir / name, installer / name)
    (installer / "Build.iss").write_text(
        f'#define AppVersion "{version}"\n#define DistributionBuild "{build}"\n'
        '#define PayloadDir "..\\LinguaFloat"\n#define DistOutput "..\\.."\n'
        '#include "LinguaFloat.iss"\n', encoding="utf-8-sig")
    shutil.copy2(Path(__file__), output / "build-input/build_distribution.py")
    shutil.copy2(Path(__file__).parent / "build_installer.ps1", output / "build-input/build_installer.ps1")
    archive = output / f"LinguaFloat-v{version}-windows-x64-portable-build{build}.zip"
    print(f"Compressing ZIP (DEFLATE level {level})...", flush=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=level,
                         allowZip64=True, strict_timestamps=False) as zipped:
        for index, row in enumerate(rows):
            zipped.write(payload / row["path"], "LinguaFloat/" + row["path"])
            if index and index % 5000 == 0:
                print(f"ZIP: {index}/{len(rows)}", flush=True)
    write_json(output / "build-summary.json", dict(version=version, distribution_build=build,
        payload_files=len(rows), payload_bytes=total, zip=archive.name,
        zip_bytes=archive.stat().st_size, zip_sha256=digest(archive), compression="ZIP DEFLATE",
        compression_level=level, build_seconds=round(time.monotonic()-started, 2),
        source_directory=str(source), source_unchanged=inventory(source) == rows,
        longest_relative_path=max(len(r["path"]) for r in rows)))
    (output / "SHA256SUMS.txt").write_text(f"{digest(archive)}  {archive.name}\n", encoding="ascii")
    print(json.dumps(dict(output=str(output), files=len(rows), bytes=total,
                         zip_bytes=archive.stat().st_size), ensure_ascii=False), flush=True)


def verify_archive(archive: Path, manifest: Path, extract: Path | None) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    rows = data["files"]
    expected = {"LinguaFloat/" + r["path"]: r for r in rows}
    if extract:
        extract.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as zipped:
        if set(zipped.namelist()) != set(expected) or len(zipped.infolist()) != len(expected):
            raise ValueError("ZIP inventory mismatch")
        for entry in zipped.infolist():
            row = expected[entry.filename]
            if entry.file_size != row["bytes"]:
                raise ValueError("ZIP file size mismatch: " + entry.filename)
            hasher = hashlib.sha256()
            destination = extract / entry.filename if extract else None
            if destination:
                destination.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(entry) as stream:
                if destination:
                    with destination.open("xb") as out:
                        while chunk := stream.read(1024 * 1024):
                            hasher.update(chunk)
                            out.write(chunk)
                else:
                    while chunk := stream.read(1024 * 1024):
                        hasher.update(chunk)
            if hasher.hexdigest() != row["sha256"]:
                raise ValueError("ZIP content mismatch: " + entry.filename)
    if extract and inventory(extract / "LinguaFloat") != rows:
        raise ValueError("Extracted payload differs")
    print(json.dumps(dict(status="passed", zip_sha256=digest(archive),
                          checked_files=len(rows), extracted=bool(extract))), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--source", type=Path, default=ROOT / "exe")
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--build", type=int, default=1)
    build_parser.add_argument("--level", type=int, default=6, choices=range(1, 10))
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--archive", type=Path, required=True)
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--extract", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        make_distribution(args.source, args.output, args.build, args.level)
    else:
        verify_archive(args.archive, args.manifest, args.extract)
