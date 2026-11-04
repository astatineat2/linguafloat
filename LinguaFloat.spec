# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_all
from pathlib import Path
import sys

project_src = str(Path(SPECPATH) / 'src')
sys.path[:] = [project_src] + [p for p in sys.path if not (Path(p) / 'linguafloat' / 'app.py').is_file()]

datas = [('photo', 'photo'), ('release/channel.json', 'release')]
binaries = []
hiddenimports = ['rapidocr.inference_engine.onnxruntime.main']
datas += collect_data_files('qtawesome')
datas += collect_data_files('rapidocr')
tmp_ret = collect_all('keyring')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('uiautomation')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['src/linguafloat/__main__.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Package-local OpenSSL/ICU binaries retain their destinations; generic root
# copies shadow Qt/Windows dependencies on this build host.
a.binaries = [entry for entry in a.binaries if entry[0].lower() not in
              {'icudt78.dll', 'icuuc.dll', 'libcrypto-3-x64.dll', 'libssl-3-x64.dll'}]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LinguaFloat',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['photo/app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LinguaFloat',
)
