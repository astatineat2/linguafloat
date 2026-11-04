"""Exercise the real PDF pipeline with identity responses, never an API client."""
import ctypes
import json
import logging
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    ctypes.windll.kernel32.SetDllDirectoryW(None)
    sys.stdout.reconfigure(encoding="utf-8")
    logging.disable(logging.CRITICAL)
    from linguafloat.document_core import DocumentJob, DocumentTranslator, JobControl, JobStore
    from linguafloat.document_pdf import translate_layout_pdf

    source, folder = Path(sys.argv[1]), Path(sys.argv[2])
    folder.mkdir(parents=True, exist_ok=True)
    job = DocumentJob(str(source), str(folder), dict(id="offline-diagnostic", model="identity-fixture",
        api_mode="responses", base_url="https://example.invalid", document_rpm=100000,
        document_concurrency=6), pdf_mode="layout", pages=sys.argv[3] if len(sys.argv) > 3 else "")
    calls = 0
    def create(**kwargs):
        nonlocal calls
        calls += 1
        text = kwargs["input"][-1]["content"]
        if "## Here is the input:" in text:
            values = json.loads(text.split("## Here is the input:", 1)[1].strip())
            value = json.dumps([dict(id=v["id"], output=v["input"]) for v in values])
        elif text.startswith('{"fragment"'):
            value = json.loads(text)["fragment"]
        else:
            value = text.split("Now translate the following text:")[-1].split("Input:\n\n")[-1]
        return NS(output_text=value)
    previous = None
    def emit(event):
        nonlocal previous
        key = event.get("stage_key", event.get("stage"))
        if event.get("type") == "progress" and key != previous:
            previous = key
            print(event.get("message", key), flush=True)
    store = JobStore(folder / "diagnostic.sqlite3")
    report = dict(source=str(source), paid_requests=0, response_mode="identity fixture, not translation")
    try:
        translator = DocumentTranslator(job, "", store, JobControl(), emit,
            NS(responses=NS(create=create)))
        paths, warnings = translate_layout_pdf(job, translator, folder, emit)
        report.update(status="passed", outputs=paths, warnings=warnings)
    except Exception:
        report.update(status="failed", traceback=traceback.format_exc())
        print(report["traceback"], flush=True)
    finally:
        report["mock_requests"] = calls
        (folder / "diagnostic.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        store.close()
    print("Diagnostic:", report["status"], "mock requests:", calls, flush=True)
    return report["status"] != "passed"


if __name__ == "__main__":
    raise SystemExit(main())
