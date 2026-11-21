"""Start a packaged app without touching the user's profile; verify its actual path."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid


def probe(target, version):
    with tempfile.TemporaryDirectory() as profile:
        root = Path(profile)
        local, roaming = root / "local", root / "roaming"
        job = local / "LinguaFloat/updates/probe"
        job.mkdir(parents=True)
        config = roaming / "LinguaFloat/config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps(dict(first_run_complete=True, update_auto_check=False,
                                          game_mode="on", providers=[])), encoding="utf-8")
        nonce = uuid.uuid4().hex
        (job / "request.json").write_text(json.dumps(dict(target=str(target), nonce=nonce)), encoding="utf-8")
        env = dict(os.environ, LOCALAPPDATA=str(local), APPDATA=str(roaming), QT_QPA_PLATFORM="offscreen")
        proc = subprocess.Popen([target / "LinguaFloat.exe", "--update-health", job], cwd=target, env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for _ in range(360):
                if (job / "healthy.json").exists() or proc.poll() is not None:
                    break
                time.sleep(.25)
            ack = json.loads((job / "healthy.json").read_text(encoding="utf-8"))
            assert (ack['pid'], ack['version'], ack['nonce'], Path(ack['target'])) == (proc.pid, version, nonce, target)
            print(f"Verified packaged startup: {version} at {target}", flush=True)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("version")
    parser.add_argument("--drive-child", action="store_true")
    args = parser.parse_args()
    source = args.bundle.resolve(strict=True)
    if args.drive_child:
        with tempfile.TemporaryDirectory(prefix="LinguaFloat 自定义测试 ", dir=source.anchor) as folder:
            shallow = Path(folder)
            shutil.copytree(source, shallow, dirs_exist_ok=True)
            probe(shallow, args.version)
    else:
        probe(source, args.version)
