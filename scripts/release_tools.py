"""Prepare signed release assets locally. Uploading is a separate explicit command."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata as metadata
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from linguafloat import __version__
from linguafloat.update_core import atomic_json, sha256_file

REPOSITORY = "astatineat2/linguafloat-releases"


def key_path():
    return Path(os.environ["LOCALAPPDATA"]) / "LinguaFloatPublisher" / "signing-key.pem"


def init_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        # Tighten the directory ACL before writing a publishing secret.
        if os.name == "nt":
            who = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], encoding="utf-8", errors="replace")
            import csv
            sid = next(csv.reader([who.strip()]))[1]
            subprocess.run(["icacls", str(path.parent), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
                           check=True, capture_output=True)
        with path.open("xb") as out:
            out.write(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    private = serialization.load_pem_private_key(path.read_bytes(), password=None)
    numbers = private.public_key().public_numbers()
    public = {"n": format(numbers.n, "x"), "e": numbers.e}
    fingerprint = hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()[:16]
    atomic_json(ROOT / "release/channel.json", {"repository": REPOSITORY, "architecture": "win-x64",
                                                "key_id": fingerprint, "public_key": public})
    print("Public update channel prepared. Publishing private key stays outside the project: " + str(path))


def sign(payload: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    private = serialization.load_pem_private_key(key_path().read_bytes(), password=None)
    channel = json.loads((ROOT / "release/channel.json").read_text(encoding="utf-8"))
    if format(private.public_key().public_numbers().n, "x") != channel["public_key"]["n"]:
        raise SystemExit("Publishing key does not match the trusted update channel.")
    return base64.b64encode(private.sign(payload, padding.PKCS1v15(), hashes.SHA256())) + b"\n"


def write_source(output: Path):
    """An allowlist, not a copy of the working directory or development Git history."""
    inputs = [(ROOT / "src", "src"), (ROOT / "tests", "tests"), (ROOT / "photo", "photo"), (ROOT / "release", "release"),
              (ROOT / "LICENSES", "LICENSES"), (ROOT / "docs/public", "docs"),
              (ROOT / "source-materials", "source-materials")]
    fixed = ["pyproject.toml", "LinguaFloat.spec", "LinguaFloatUpdater.spec", "LICENSE", "THIRD_PARTY_NOTICES.md",
             "app-requirements.lock.txt", "document-requirements.lock.txt"]
    build_scripts = ["build_documents.ps1", "build.ps1", "check_build.py", "prepare_document_runtime.py",
                     "prepare_assets.py", "document_worker_entry.py", "updater_entry.py", "collect_licenses.py",
                     "publish_portable.py", "release_tools.py", "github_publish.py", "complete_release_sources.py",
                     "finalize_release_inventory.py"]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("SOURCE-REVISION.txt", "LinguaFloat v" + __version__ + "\n" +
                         subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() + "\n")
        for source, label in inputs:
            for path in sorted(source.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix.lower() not in {".pyc", ".pem", ".key", ".part"}:
                    archive.write(path, str(Path(label) / path.relative_to(source)))
        for name in fixed:
            archive.write(ROOT / name, name)
        for name in build_scripts:
            archive.write(ROOT / "scripts" / name, "scripts/" + name)
        archive.write(ROOT / "docs/public/开发与构建.md", "README.md")


def prepare(bundle: Path, *, candidate=False):
    bundle = bundle.resolve()
    if (bundle / "VERSION.txt").read_text(encoding="utf-8").splitlines()[0] != f"LinguaFloat v{__version__}":
        raise SystemExit("The bundle version must match the source version.")
    if not (bundle / "LinguaFloatUpdater.exe").is_file():
        raise SystemExit("Complete update assistant is missing.")
    approvals_path = ROOT / "release/approvals.json"
    approvals = json.loads(approvals_path.read_text(encoding="utf-8")) if approvals_path.exists() else {}
    required = ("artwork_redistribution", "license_inventory_reviewed", "corresponding_source_complete", "isolated_profile_verified")
    missing = [name for name in required if approvals.get(name) is not True]
    if missing and not candidate:
        raise SystemExit("Public release prerequisites are incomplete: " + ", ".join(missing))
    output = ROOT / "release-assets" / ("v" + __version__)
    output.mkdir(parents=True, exist_ok=True)
    # Never silently replace an immutable release asset.
    package = output / f"LinguaFloat-v{__version__}-win-x64.zip"
    if package.exists():
        raise SystemExit("Release archive already exists. Review/remove the local candidate explicitly or increment the version.")
    total = 0
    with zipfile.ZipFile(package, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_symlink():
                raise SystemExit("A portable bundle must not contain symbolic links.")
            if path.is_file():
                archive.write(path, path.relative_to(bundle).as_posix())
                total += path.stat().st_size
    source = output / f"LinguaFloat-v{__version__}-corresponding-source.zip"
    write_source(source)
    notes = (ROOT / "docs/public/本版更新.md").read_text(encoding="utf-8")
    value = {"schema": 1, "version": __version__, "minimum_updater": "0.11.0", "architecture": "win-x64",
             "repository": REPOSITORY, "url": f"https://github.com/{REPOSITORY}/releases/download/v{__version__}/{package.name}",
             "page": f"https://github.com/{REPOSITORY}/releases/tag/v{__version__}", "size": package.stat().st_size,
             "unpacked_size": total, "sha256": sha256_file(package), "notes": notes,
             "candidate_only": bool(missing)}
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    (output / "update.json").write_bytes(payload)
    (output / "update.json.sig").write_bytes(sign(payload))
    assets = [package, source, output / "update.json", output / "update.json.sig"]
    (output / "SHA256SUMS.txt").write_text("".join(sha256_file(p) + "  " + p.name + "\n" for p in assets), encoding="ascii")
    atomic_json(output / "LOCAL-RELEASE-REVIEW.json", {"public_release_ready": not missing,
                "missing": missing, "version": __version__, "private_repository": "astatineat2/linguafloat",
                "public_repository": REPOSITORY,
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()})
    print(json.dumps({"output": str(output), "zip_MiB": round(package.stat().st_size / 1024**2, 1),
                      "unpacked_bytes": total, "public_release_ready": not missing, "missing": missing}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-key")
    pack = sub.add_parser("prepare")
    pack.add_argument("--bundle", type=Path, default=ROOT / "exe")
    pack.add_argument("--candidate", action="store_true", help="Prepare locally; do not represent incomplete licensing/QA as public-ready.")
    args = parser.parse_args()
    if args.command == "init-key":
        init_key()
    else:
        prepare(args.bundle, candidate=args.candidate)


if __name__ == "__main__":
    main()
