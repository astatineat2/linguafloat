"""Bounded live verification. Credentials pass only through child stdin."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")


def launch():
    from dataclasses import asdict
    import subprocess
    from linguafloat.config import ConfigStore, CredentialStore
    settings = ConfigStore().load()
    profile = settings.active_profile()
    payload = dict(provider=asdict(profile), key=CredentialStore().get_api_key(profile.id, profile.env_key))
    if not payload["key"]:
        raise RuntimeError("Current profile has no saved credential")
    child = subprocess.Popen([str(ROOT / ".document-runtime/Scripts/python.exe"), __file__, "worker", sys.argv[-1]],
                             stdin=subprocess.PIPE, text=True, encoding="utf-8", cwd=ROOT)
    child.communicate(json.dumps(payload))
    raise SystemExit(child.returncode)


def worker():
    import ctypes
    import sqlite3
    import threading
    import time
    from types import SimpleNamespace as NS
    import openai
    ctypes.windll.kernel32.SetDllDirectoryW(None)
    from linguafloat.document_core import DocumentJob, DocumentTranslator, JobControl, JobStore, DocumentError
    from linguafloat.document_pdf import translate_layout_pdf
    from linguafloat.document_metrics import PerformanceLog
    settings = json.load(sys.stdin)
    folder = ROOT / "verification-pdf-defects/live"
    folder.mkdir(exist_ok=True)
    database = folder / "verification.sqlite3"
    if not database.exists():
        original = Path(os.environ["APPDATA"]) / "LinguaFloat/documents/jobs.sqlite3"
        with sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(database) as dst:
            src.backup(dst)
    # Reuse the verified local OCR cache without touching the user's database.
    with sqlite3.connect(ROOT / "verification-scan-coverage/analysis.sqlite") as src, sqlite3.connect(database) as dst:
        dst.executemany("INSERT OR IGNORE INTO cache VALUES (?,?)", src.execute("SELECT key,value FROM cache"))
    store = JobStore(database)
    provider = settings["provider"]
    provider["retries"] = 1
    provider["document_concurrency"] = min(6, provider.get("document_concurrency", 2))
    control, lock = JobControl(), threading.Lock()
    calls = 0
    client = openai.OpenAI(api_key=settings.pop("key"), base_url=provider["base_url"], timeout=120, max_retries=0)
    def bounded(method):
        def create(**kwargs):
            nonlocal calls
            with lock:
                if calls >= 110:
                    raise DocumentError("Live verification request cap reached")
                calls += 1
                print("Live request", calls, flush=True)
            return method(**kwargs)
        return create
    safe = NS(responses=NS(create=bounded(client.responses.create)),
              chat=NS(completions=NS(create=bounded(client.chat.completions.create))))
    sources = {
        "hoburg": (next(Path("C:/Users/Astatine/Zotero/storage/M3FJP6NJ").glob("*.pdf")), "2"),
        "nature": (ROOT / "verification-pdf-defects/nature-original-left-pages6-7.pdf", ""),
    }
    name = sys.argv[-1]
    cache_only = name.endswith("-cached")
    name = name.removesuffix("-cached")
    source, pages = sources[name]
    output = folder / name
    output.mkdir(exist_ok=True)
    started = time.perf_counter()
    job = DocumentJob(str(source), str(output), provider, pages=pages, pdf_mode="layout", request_timeout=120, relayout_only=cache_only)
    emit = PerformanceLog(control, lambda event: None)
    translator = DocumentTranslator(job, "", store, control, emit, safe)
    report = dict(source=str(source), pages=pages or "1-2 (original 6-7)", model=provider["model"])
    try:
        paths, warnings = translate_layout_pdf(job, translator, output, emit)
        report.update(outputs=[str(p) for p in paths], warnings=warnings, status="generated")
        print("Generated", name, flush=True)
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        # Known application errors are sanitized; do not dump SDK request objects.
        if isinstance(exc, DocumentError):
            report["error"] = str(exc)
        print("Verification failed", type(exc).__name__, flush=True)
    finally:
        report.update(requests=calls, cache_hits=translator.hits, tokens=translator.token_usage,
                      seconds=round(time.perf_counter()-started, 2))
        (folder / (name + "-result.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        store.close()
        client.close()


if __name__ == "__main__":
    launch() if sys.argv[1] == "launch" else worker()
