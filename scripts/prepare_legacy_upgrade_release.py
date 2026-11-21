"""Reuse the peer-reviewed prior release inventory, not the developer checkout contents."""
import sys
import subprocess
import zipfile

from release_tools import ROOT, REPOSITORY, sign
from linguafloat import __version__
from linguafloat.update_core import atomic_json, check_archive, load_channel, parse_manifest, sha256_file


def main(app=None, out=None):
    tag = 'v'+__version__
    previous = ROOT/'release-assets/v0.19.9'
    out = out or ROOT/'release-assets'/tag
    out.mkdir(parents=True,exist_ok=True)
    app = app or ROOT/'exe'
    pkg = out/f'LinguaFloat-{tag}-win-x64.zip'
    old = next(previous.glob('*win-x64.zip'))
    check_archive(old, parse_manifest((previous/'update.json').read_bytes(), (previous/'update.json.sig').read_bytes(), load_channel(), '0.11.0'))
    files = {p.relative_to(app).as_posix():p for p in app.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    with zipfile.ZipFile(old) as z:
        assert set(files) == set(z.namelist()), set(files).symmetric_difference(z.namelist())
    for n in ('CHANGELOG.md', 'help/本版更新.md'):
        files[n] = ROOT/'docs/public/本版更新.md'
    with zipfile.ZipFile(pkg,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for name, path in sorted(files.items()):
            z.write(path,name)
    with zipfile.ZipFile(pkg) as z:
        assert z.testzip() is None
        total = sum(i.file_size for i in z.infolist())
    base = 'https://github.com/'+REPOSITORY+'/releases'
    manifest = out/'update.json'
    atomic_json(manifest, dict(schema=1,architecture='win-x64',version=__version__,minimum_updater='0.11.0',repository=REPOSITORY,
                page=base+'/tag/'+tag,url=f'{base}/download/{tag}/{pkg.name}',size=pkg.stat().st_size,unpacked_size=total,
                sha256=sha256_file(pkg),notes=files['CHANGELOG.md'].read_text(encoding='utf-8').split('## 此前')[0],candidate_only=False))
    sig=sign(manifest.read_bytes()); (out/'update.json.sig').write_bytes(sig)
    check_archive(pkg,parse_manifest(manifest.read_bytes(),sig,load_channel(),'0.11.0'))


def sources(out=None):
    out=out or ROOT/'release-assets'/('v'+__version__)
    old=ROOT/'release-assets/v0.19.9'
    base=next(old.glob('*corresponding-source.zip'))
    checks=dict(line.split('  ',1)[::-1] for line in (old/'SHA256SUMS.txt').read_text().splitlines())
    assert sha256_file(base)==checks[base.name]
    commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    changed=set(subprocess.check_output(['git','diff','--name-only','-z','v0.19.9',commit]).decode().strip('\0').split('\0'))
    tracked=set(subprocess.check_output(['git','ls-tree','-r','--name-only','-z',commit]).decode().strip('\0').split('\0'))
    additional={'scripts/'+n+'.py' for n in ('accept_legacy_upgrade','build_legacy_repair','legacy_update_repair','prepare_legacy_upgrade_release','verify_portable_install_path')}
    with zipfile.ZipFile(base) as src,zipfile.ZipFile(out/f'LinguaFloat-v{__version__}-corresponding-source.zip','x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as dst:
        for name in sorted(set(src.namelist())|additional):
            if name.endswith('/'):continue
            key='docs/public/开发与构建.md' if name=='README.md' else name
            if name=='SOURCE-REVISION.txt':data=f'LinguaFloat v{__version__}\n{commit}\n'.encode()
            elif key in tracked:
                # Unchanged tracked objects can be taken from the preceding source package.
                data=subprocess.check_output(['git','show',commit+':'+key]) if key in changed or name in additional else src.read(name)
            elif name.startswith('source-materials/'):data=src.read(name)
            else:raise ValueError('Unexpected source entry: '+name)
            dst.writestr(name,data)


if __name__ == '__main__':
    if '--source' in sys.argv:
        sources()
    else:main()
