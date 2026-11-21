"""Isolated Windows release acceptance, using only publisher-authenticated packages."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from linguafloat.update_core import atomic_json, check_archive, load_channel, parse_manifest, sha256_file
from legacy_update_repair import running_apps


def wait(fn, timeout=120):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        value=fn()
        if value:return value
        time.sleep(.25)
    raise RuntimeError(f'timed out: {fn}')


def popup(text, button, proc):
    # Only interact with this test process tree's native message boxes. PyInstaller
    # onefile has a child process, so match the unique title and verify parent PID.
    from ctypes import wintypes as w
    api=ctypes.windll.user32
    def find():
        hwnd=api.FindWindowW('#32770',text)
        if not hwnd:return
        owner=w.DWORD();api.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        import psutil
        if owner.value!=proc.pid and proc.pid not in [p.pid for p in psutil.Process(owner.value).parents()]:return
        child=api.GetDlgItem(hwnd,button)
        if child:api.PostMessageW(child,0xF5,0,0);return True
    wait(find,45)


def signed(version):
    folder=ROOT/'release-assets'/('v'+version); p=next(folder.glob('*win-x64.zip'))
    r=parse_manifest((folder/'update.json').read_bytes(),(folder/'update.json.sig').read_bytes(),load_channel(),'0.11.0')
    check_archive(p,r);return folder,p,r


def run(version, shallow=True, ui=False, rollback=False):
    import psutil
    old,oldzip,_=signed(version);new,pkg,r=signed('0.19.10');exe='LinguaFloat.exe'
    if any(p.name().casefold()==exe.casefold() for p in psutil.process_iter()):raise RuntimeError('Close existing LinguaFloat before isolated acceptance')
    with tempfile.TemporaryDirectory(prefix='LF 旧版 '+version+' ',dir=ROOT.anchor if shallow else ROOT/'.scratch') as raw, tempfile.TemporaryDirectory() as pf:
        target=Path(raw).resolve();profile=Path(pf).resolve();assert profile.drive!=target.drive
        local=profile/'local';roam=profile/'roam'; cfg=roam/'LinguaFloat/config.json';cfg.parent.mkdir(parents=True)
        atomic_json(cfg,dict(first_run_complete=True,update_auto_check=False,game_mode='on',providers=[]));original=cfg.read_bytes()
        history=cfg.parent/'documents/jobs.sqlite3';history.parent.mkdir();
        with sqlite3.connect(history) as db:db.execute('CREATE TABLE qa_history(value TEXT)');db.execute("INSERT INTO qa_history VALUES ('kept')")
        db.close();other=profile/'second copy';other.mkdir();
        with zipfile.ZipFile(oldzip) as z:z.extractall(target);z.extractall(other)
        def inventory(t):return {p.relative_to(t).as_posix():sha256_file(p) for p in t.rglob('*') if p.is_file()}
        untouched=inventory(other)
        job=local/'LinguaFloat/updates'/('v'+r.version+'-'+r.sha256[:12]);job.mkdir(parents=True)
        for n in ('update.json','update.json.sig'):shutil.copy2(new/n,job/n)
        shutil.copyfile(pkg,job/'package.zip');env=dict(os.environ,LOCALAPPDATA=str(local),APPDATA=str(roam),QT_QPA_PLATFORM='windows' if ui else 'offscreen',QT_ACCESSIBILITY='1'); nonce=uuid.uuid4().hex
        proc=None;child=None;helper=None;repair=None;backup=None
        try:
            proc=subprocess.Popen([target/exe],cwd=profile,env=env);wait(lambda: (target/'VERSION.txt').is_file() and proc.poll() is None)
            wait(lambda: running_apps()==(proc.pid,target/exe));time.sleep(5)
            if proc.poll() is not None:raise RuntimeError('Old app failed to start')
            args=dict(target=str(target),current=version,parent_pid=proc.pid,nonce=nonce)
            atomic_json(job/'request.json',args);atomic_json(job/'state.json',dict(target=str(target),phase='prepared'))
            shutil.copy2(target/'LinguaFloatUpdater.exe',job)
            if shallow:
                helper=subprocess.Popen([job/'LinguaFloatUpdater.exe','--job',job],env=env,cwd=job)
                wait(lambda:(job/'error.json').exists());assert '程序安装目录无效' in (job/'error.json').read_text(encoding='utf-8')
                popup('LinguaFloat 更新未完成',1,helper);helper.wait(10);assert helper.returncode!=0
                (job/'error.json').unlink()
                repair=subprocess.Popen([new/'LinguaFloat-Repair-v011.exe'],env=env,cwd=profile)
                popup('LinguaFloat · v0.11 更新修复',6,repair);popup('LinguaFloat · v0.11 更新修复',1,repair)
                assert repair.wait(15)==0 and sha256_file(target/'LinguaFloatUpdater.exe')==sha256_file(ROOT/'exe/LinguaFloatUpdater.exe')
                shutil.copy2(target/'LinguaFloatUpdater.exe',job)
            if ui:
                import uiautomation as auto
                def invoke(names,timeout=45):
                    def button():
                        for win in auto.GetRootControl().GetChildren():
                            if win.ProcessId!=proc.pid:continue
                            for c,_ in auto.WalkControl(win,maxDepth=12):
                                if c.ControlTypeName=='ButtonControl' and c.Name in names and c.IsEnabled:return c
                    wait(button,timeout).GetInvokePattern().Invoke()
                invoke({'帮助与更新','帮助 · 有更新'});invoke({'检查更新'});invoke({'下载更新'},100);invoke({'安装并重启'},100);invoke({'Yes','&Yes','是','是(Y)','是(&Y)'})
                wait(lambda:proc.poll() is not None);nonce=json.loads((job/'request.json').read_text(encoding='utf-8'))['nonce']
            else:
                helper=subprocess.Popen([job/'LinguaFloatUpdater.exe','--job',job],env=env,cwd=job)
                proc.terminate();proc.wait(15)
            if rollback:
                def started():
                    s=json.loads((job/'state.json').read_text(encoding='utf-8'))
                    return s if s.get('phase')=='starting' and s.get('child_pid') else None
                state=wait(started);child=psutil.Process(state['child_pid']);assert Path(child.exe())==target/exe
                child.terminate();child.wait(10);wait(lambda:(job/'error.json').exists());popup('LinguaFloat 更新未完成',1,helper)
                state=json.loads((job/'state.json').read_text(encoding='utf-8'));assert state['phase']=='rolled_back'
                assert sha256_file(target/exe)==untouched[exe] and cfg.read_bytes()==original and inventory(other)==untouched
                with sqlite3.connect(history) as db:assert db.execute('SELECT value FROM qa_history').fetchall()==[('kept',)]
                db.close();atomic_json(new/'rollback-011.json',dict(phase=state['phase'],old_exe_restored=True,history_preserved=True,config_preserved=True));return
            wait(lambda:(job/'error.json').exists() or json.loads((job/'state.json').read_text(encoding='utf-8')).get('phase')=='committed',240)
            if (job/'error.json').exists():raise RuntimeError((job/'error.json').read_text(encoding='utf-8'))
            state=json.loads((job/'state.json').read_text(encoding='utf-8'));h=json.loads((job/'healthy.json').read_text(encoding='utf-8'))
            child=psutil.Process(h['pid']);assert Path(child.exe())==target/exe and h['pid']==state['child_pid'] and h['nonce']==nonce and h['version']==r.version and state['phase']=='committed'
            backup=Path(state['backup']);assert (backup/'VERSION.txt').read_text().splitlines()[0]=='LinguaFloat v'+version
            assert cfg.read_bytes()==original and inventory(other)==untouched
            with sqlite3.connect(history) as db:assert db.execute('SELECT value FROM qa_history').fetchall()==[('kept',)]
            db.close();row=dict(from_version=version,to_version=r.version,package_sha256=r.sha256,old_pid=proc.pid,new_pid=child.pid,
                     old_exe=str(target/exe),new_exe=child.exe(),restarted_in_place=True,configuration_preserved=True,history_preserved=True,other_unchanged=True,
                     old_helper_rejected=shallow,used_repair=shallow,cache_drive=local.drive,gui=ui,transport='public GUI check, signed cached ZIP' if ui else 'signed release files staged; helper handoff simulated, no GUI download click')
            atomic_json(new/f'accept-{version}-{int(shallow)}-{int(ui)}.json',row);print(json.dumps(row,ensure_ascii=False),flush=True)
        finally:
            for p in (repair,helper,proc):
                if p is not None and p.poll() is None:
                    for c in psutil.Process(p.pid).children(recursive=True): c.kill()
                    p.kill();p.wait()
            for p in psutil.process_iter(['exe']):
                if p.info['exe'] and Path(p.info['exe'])==target/exe:p.terminate();p.wait(20)
            if backup and backup.parent==target.parent/'LinguaFloat-backups' and backup.name==f'v{version}-'+job.name and backup.is_dir():shutil.rmtree(backup)
            (target.parent/('.'+target.name+'-update.lock')).unlink(missing_ok=True)


if __name__=='__main__':run(sys.argv[1], '--nested' not in sys.argv, '--gui' in sys.argv, '--rollback' in sys.argv)
