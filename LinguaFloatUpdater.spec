# Minimal standalone assistant; it must run outside the installation it replaces.
from pathlib import Path
project = Path(SPECPATH)
a = Analysis([str(project / 'scripts/updater_entry.py')], pathex=[str(project / 'src')],
             datas=[(str(project / 'release/channel.json'), 'release')],
             binaries=[], hiddenimports=[], hookspath=[], runtime_hooks=[],
             excludes=['PySide6', 'openai', 'rapidocr', 'numpy', 'onnxruntime', 'httpx'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='LinguaFloatUpdater', console=False,
          debug=False, strip=False, upx=False, icon=str(project / 'photo/app.ico'))
