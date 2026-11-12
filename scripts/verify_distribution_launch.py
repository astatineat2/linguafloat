"""Launch a bundle with a fixed, isolated, credential-free acceptance profile.

Use only after the user's ordinary instance is safely exited. This helper does
not change single-instance handling, drive Windows UI, or terminate processes.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--bundle', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--first-run', action='store_true')
args = parser.parse_args()
bundle = args.bundle.resolve()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
config_directory = output / 'profile/roaming/LinguaFloat'
config_directory.mkdir(parents=True)
config = dict(update_auto_check=False, first_run_complete=not args.first_run,
    hotkey='Ctrl+Shift+F11', input_hotkey='Ctrl+Shift+F12', game_mode='on',
    active_provider='distribution-offline-only', providers=[dict(id='distribution-offline-only',
        name='分发验证（隔离配置）', base_url='http://127.0.0.1:9/v1',
        model='offline-test', api_mode='chat_completions', env_key='')])
(config_directory / 'config.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
environment = dict(os.environ, APPDATA=str(output / 'profile/roaming'),
                   LOCALAPPDATA=str(output / 'profile/local'))
environment.pop('QT_QPA_PLATFORM', None)
environment.pop('QT_SCALE_FACTOR', None)
process = subprocess.Popen([str(bundle / 'LinguaFloat.exe')], cwd=bundle, env=environment)
record = dict(pid=process.pid, executable=str(bundle / 'LinguaFloat.exe'),
    first_run=args.first_run,
    isolated_profile=True, auto_update_disabled=True, translation_started=False,
    note='Process launch is not proof of a visible window. Verify its actual window and version separately.')
(output / 'launch.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(record, ensure_ascii=True))
