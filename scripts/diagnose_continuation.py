"""Export only a failed job's non-secret metadata and cached layout for offline QA."""
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import asdict
from uuid import uuid4
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "verification-continuation-marker"
sys.path.insert(0, str(ROOT / "src"))


def replay():
    import ctypes
    ctypes.windll.kernel32.SetDllDirectoryW(None)
    if "--portable" in sys.argv:
        sys.path.insert(0, str(ROOT / "exe/document-runtime/src"))
    import pymupdf
    from linguafloat.document_core import DocumentJob, DocumentTranslator, JobStore, JobControl
    from linguafloat.document_artifacts import LayoutSnapshot
    from linguafloat.document_layout import LayoutPolicy
    from linguafloat.document_worker import execute
    case = json.loads((FOLDER / "case.json").read_text(encoding="utf-8"))
    with pymupdf.open(case["source"]) as pdf:
        pages = list(range(len(pdf)))
    for provider in case["providers"]:
        job = DocumentJob(case["source"], str(FOLDER / "outputs"), provider,
                          job_id=uuid4().hex, pdf_mode="layout", relayout_only=True)
        if LayoutSnapshot(FOLDER, job, pages).path.exists():
            break
    else:
        raise RuntimeError("No matching complete translated snapshot")
    records = []
    original = LayoutPolicy.defer
    def inspect(policy, engine, paragraph, page, units):
        record = dict(page=page.page_number + 1, box=asdict(paragraph.box), text=paragraph.unicode,
                      size=paragraph.pdf_style.font_size)
        records.append(record)
        try:
            return original(policy, engine, paragraph, page, units)
        except Exception as exc:
            record["error"] = str(exc)
            raise
    LayoutPolicy.defer = inspect
    store = JobStore(FOLDER / "offline.sqlite3")
    control = JobControl()
    events = []
    translator = DocumentTranslator(job, "offline-only", store, control, events.append)
    def forbidden(*args, **kwargs):
        raise RuntimeError("Network disabled: cached layout required")
    translator.request = forbidden
    label = sys.argv[2] if len(sys.argv) > 2 else "replay"
    try:
        execute(job, "", store, control, events.append, translator)
    except Exception as exc:
        events.append(dict(type="error", message=str(exc)))
    finally:
        store.close()
        (FOLDER / (label + ".json")).write_text(json.dumps(dict(deferred=records, events=events),
                     ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(events[-1], ensure_ascii=True))


def export():
    FOLDER.mkdir(exist_ok=True)
    base = Path(os.environ["APPDATA"]) / "LinguaFloat"
    with sqlite3.connect((base / "documents/jobs.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("select source,status,details from jobs order by updated desc limit 10").fetchall()
    records = [dict(source=s, status=state, message=json.loads(d).get("message", "")) for s, state, d in rows]
    (FOLDER / "recent.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = next(r for r in records if "续页标记" in r["message"])
    settings = json.loads((base / "config.json").read_text(encoding="utf-8"))
    providers = [{k: p.get(k) for k in ("id", "base_url", "model", "api_mode")} for p in settings["providers"]]
    (FOLDER / "case.json").write_text(json.dumps(dict(source=failed["source"], providers=providers)), encoding="utf-8")
    snapshots = sorted((base / "documents/layout-cache").glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    (FOLDER / "layout-cache").mkdir(exist_ok=True)
    for path in snapshots[:4]:
        shutil.copy2(path, FOLDER / "layout-cache" / path.name)


if __name__ == "__main__":
    if "--replay" in sys.argv:
        replay()
        sys.exit()
    try:
        export()
        (FOLDER / "export-status.txt").write_text("ok", encoding="utf-8")
    except Exception as exc:
        FOLDER.mkdir(exist_ok=True)
        (FOLDER / "export-status.txt").write_text(type(exc).__name__ + ": " + str(exc), encoding="utf-8")
        raise
