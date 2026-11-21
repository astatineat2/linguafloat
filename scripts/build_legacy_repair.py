"""Build and independently sign the v0.11 updater repair executable and payload."""
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from release_tools import ROOT, sign
from linguafloat import __version__
from linguafloat.update_core import load_channel, sha256_file, verify_signature


def main():
    stage = ROOT / '.scratch/repair-build'
    stage.mkdir(parents=True, exist_ok=True)
    out = ROOT / 'release-assets' / ('v' + __version__)
    out.mkdir(exist_ok=True)
    payload = stage / 'LinguaFloatUpdater.exe'
    shutil.copy2(ROOT / 'exe' / payload.name, payload)
    known = {}
    for version in ('0.11.0', '0.11.1'):
        directory = ROOT / 'release-assets' / ('v' + version)
        from linguafloat.update_core import check_archive, parse_manifest
        release = parse_manifest((directory / 'update.json').read_bytes(), (directory / 'update.json.sig').read_bytes(), load_channel(), version)
        path = next(directory.glob('*win-x64.zip'))
        check_archive(path, release)
        with zipfile.ZipFile(path) as package:
            digest = lambda n: hashlib.sha256(package.read(n)).hexdigest()
            known[digest('LinguaFloat.exe')] = dict(version=version, helper=digest(payload.name))
    manifest = stage / 'repair.json'
    manifest.write_text(json.dumps({'version': __version__, 'from': known, 'sha256': sha256_file(payload)}), encoding='utf-8')
    sig = stage / 'repair.json.sig'
    sig.write_bytes(sign(manifest.read_bytes()))
    verify_signature(manifest.read_bytes(), sig.read_bytes(), load_channel()['public_key'])
    name = 'LinguaFloat-Repair-v011'
    cmd = [sys.executable, '-m', 'PyInstaller', '--onefile', '--windowed', '--noupx', '--noconfirm', '--name', name,
           '--specpath', stage, '--workpath', stage/'work', '--distpath', out, '--paths', ROOT/'src',
           '--icon', ROOT/'photo/app.ico', '--add-data', f'{ROOT / "release/channel.json"};release']
    for p in (manifest, sig, payload):
        cmd.extend(['--add-data', f'{p};repair'])
    subprocess.run(cmd+[ROOT/'scripts/legacy_update_repair.py'], check=True)
    exe = out / (name + '.exe')
    # Sign the digest as a small, inspectable detached attestation.
    proof = out / (name + '.sha256')
    proof.write_bytes(f'{sha256_file(exe)}  {exe.name}\n'.encode('ascii'))
    (out/(proof.name+'.sig')).write_bytes(sign(proof.read_bytes()))


if __name__ == '__main__':
    main()
