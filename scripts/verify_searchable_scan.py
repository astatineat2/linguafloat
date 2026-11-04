"""Offline real-file regression. Echoes source text, never produces a real translation."""
import json
import subprocess
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from linguafloat.document_core import DocumentJob

folder = ROOT / "verification-searchable-scan"
folder.mkdir(exist_ok=True)
source = Path("C:/Users/Astatine/Zotero/storage/M3FJP6NJ/Hoburg - 1995 - Principles of quasistatic magnetic shielding with cylindrical and spherical shields.pdf")
calls = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = body["input"][-1]["content"]
        calls.append(text)
        value = "OFFLINE TEST - SOURCE TEXT, NOT TRANSLATED\n" + text
        result = dict(id="test", status="completed", usage=dict(input_tokens=100, output_tokens=100, total_tokens=200),
                      output=[dict(type="message", id="test-message", role="assistant",
                                   content=[dict(type="output_text", text=value, annotations=[])])])
        data = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    import pymupdf as fitz
    from linguafloat.document_pdf import has_scanned_text_layer
    with fitz.open(source) as pdf:
        classified = [has_scanned_text_layer(page) for page in pdf]
    assert classified == [True] * 6
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    provider = dict(id="offline-scan-check", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model="offline-echo", api_mode="responses", document_concurrency=2)
    runtime = ROOT / "exe/document-runtime"
    results = []
    try:
        for cached in (False, True):
            job = DocumentJob(str(source), str(folder / "outputs"), provider, job_id=uuid4().hex,
                              pdf_mode="layout", relayout_only=cached)
            payload = dict(job=asdict(job), api_key="offline-test", database=str(folder / "test.sqlite3"))
            process = subprocess.Popen([str(runtime / "python.exe"), "-I", "-u", str(runtime / "worker_entry.py")],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
            lines = []
            reader = threading.Thread(target=lambda: lines.extend(process.stdout.readlines()), daemon=True)
            reader.start()
            before = len(calls)
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
            try:
                process.wait(timeout=180)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdin.close()
                reader.join(5)
                process.stdout.close()
            events = [json.loads(line) for line in lines]
            final = next(e for e in reversed(events) if e["type"] in {"finished", "error", "cancelled"})
            assert final["type"] == "finished", final
            assert not any(e.get("stage") == "OCR 中" for e in events), "Existing OCR layer was ignored"
            assert any(e.get("stage_key") == "scan-reading-fallback" for e in events)
            assert any("隐藏文字层" in w for w in final["warnings"])
            assert len(calls) == before if cached else len(calls) > before
            for path in final["outputs"]:
                with fitz.open(path) as pdf:
                    text = "".join(page.get_text() for page in pdf)
                    assert "OFFLINE TEST" in text and len(pdf) >= 6
                    if "双语" in path:
                        assert all(page.rect.width > page.rect.height for page in pdf)
            results.append(dict(cache_only=cached, local_requests=len(calls) - before, real_api_requests=0,
                                existing_ocr_reused=True, result=final))
        (folder / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps([dict(cache_only=r["cache_only"], local_requests=r["local_requests"],
                               real_api_requests=0, status="passed") for r in results]))
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
