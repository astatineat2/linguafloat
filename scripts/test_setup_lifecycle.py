"""Exercise the production Inno template with a small fixture and a unique user registration."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
import winreg


def run(compiler, assets):
    guid = str(uuid.uuid4()).upper()
    group = 'LinguaFloat-QA-' + guid
    key = rf'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{{guid}}}_is1'
    def registration():
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as entry:
            return [winreg.QueryValueEx(entry, v)[0] for v in ('InstallLocation','DisplayVersion','UninstallString')]
    with tempfile.TemporaryDirectory(prefix='lf-setup-qa-') as tmp:
        root = Path(tmp)
        target = root / '中文 QA' / 'Installed'
        payload = root / 'payload'
        payload.mkdir()
        for name in ('app.ico', 'wizard.bmp', 'wizard-small.bmp'):
            shutil.copyfile(assets / name, root / name)
        template = Path(__file__).parent / 'installer/LinguaFloat.iss'
        shutil.copyfile(template, root / template.name)
        shutil.copyfile(Path(os.environ['WINDIR'])/'System32/whoami.exe', payload/'LinguaFloat.exe')
        (payload/'runtime.dat').write_bytes(b'QA runtime fixture')
        flags = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/TASKS=!desktopicon', '/GROUP='+group]
        marker = target / 'my-paper.pdf'
        data = b'%PDF-reader-owned'
        for index, version in enumerate(('0.20.0','0.20.1')):
            (payload/'VERSION.txt').write_text(version)
            (root/'QA.iss').write_text(f'#define ProductGuid "{guid}"\n#define MenuGroup "{group}"\n#define AppVersion "{version}"\n'
                '#define DistributionBuild "1"\n#define PayloadDir "payload"\n#define DistOutput "."\n#include "LinguaFloat.iss"\n', encoding='utf-8-sig')
            subprocess.run([compiler,'/Q',root/'QA.iss'], check=True, timeout=120)
            setup = root / f'LinguaFloat-v{version}-windows-x64-Setup.exe'
            if not index:
                foreign = root/'unrelated'; foreign.mkdir(); (foreign/'my-paper.pdf').write_bytes(data)
                attempt = subprocess.run([setup,*flags,'/DIR='+str(foreign)],timeout=60)
                assert attempt.returncode and list(foreign.iterdir()) == [foreign/'my-paper.pdf']
            subprocess.run([setup,*flags,'/DIR='+str(target)],check=True,timeout=120)
            location, installed, uninstall = registration()
            assert Path(location) == target and installed == version and (target/'VERSION.txt').read_text() == version
            if not index:
                marker.write_bytes(data)
            assert marker.read_bytes() == data
        uninstaller = Path(uninstall.strip('"'))
        assert uninstaller.is_file() and uninstaller.parent == target
        subprocess.run([uninstaller,*flags[:3]],check=True,timeout=120)
        try:
            registration()
        except FileNotFoundError:
            pass
        else:
            raise AssertionError('Uninstall registration survived')
        assert not (target/'LinguaFloat.exe').exists() and marker.read_bytes() == data
        print(json.dumps(dict(qa_guid=guid, setup_versions=['0.20.0','0.20.1'],
            nonempty_blocked=True, installed_in_place=True, uninstalled=True, pdf_preserved=True)))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compiler',required=True,type=Path)
    p.add_argument('--assets',required=True,type=Path)
    args = p.parse_args()
    run(args.compiler,args.assets)
