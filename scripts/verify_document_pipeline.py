"""End-to-end worker/HTTP/format checks with synthetic documents and a local API."""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from linguafloat.document_core import DocumentJob, JobStore, billing_display


def create_samples(folder):
    from docx import Document
    from pptx import Presentation
    from pptx.util import Inches
    from PIL import Image, ImageDraw, ImageFont
    import pymupdf as fitz
    folder.mkdir(parents=True, exist_ok=True)
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("Hello ").bold = True
    paragraph.add_run("document translation.")
    document.save(folder / "sample.docx")
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(2)).text = "Hello document translation."
    presentation.save(folder / "sample.pptx")
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 70), "Document Translation", fontsize=22)
        page.insert_textbox(fitz.Rect(50, 110, 540, 450),
            "This study evaluates document translation with a local desktop application. "
            "The original documents remain unchanged. Translation results are saved in separate files. "
            "The process preserves figures, tables and mathematical equations where possible.", fontsize=12)
        pdf.save(folder / "sample.pdf")
    image = Image.new("RGB", (1200, 500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 40)
    draw.text((70, 100), "Hello document translation.", font=font, fill="black")
    draw.text((70, 180), "This is a scanned page.", font=font, fill="black")
    image.save(folder / "scan.png")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--layout", action="store_true")
    parser.add_argument("--trial", action="store_true")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--long-layout", action="store_true")
    parser.add_argument("--folder", default="verification")
    parser.add_argument("--concurrency", type=int, choices=range(1, 7), default=2)
    args = parser.parse_args()
    folder = ROOT / args.folder
    create_samples(folder / "samples")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = body.get("input", body.get("messages"))
            text = messages[-1]["content"]
            calls.append((self.path, text))
            result = "你好，文档翻译。原始文件保持不变。" * (100 if args.long_layout else 1)
            if text.startswith("<text>"):
                from lxml import etree as ET
                root = ET.fromstring(text.encode())
                for element in root:
                    element.text = "翻译文本"
                result = ET.tostring(root, encoding="unicode")
            elif "## Here is the input:" in text:
                values = json.loads(text.split("## Here is the input:", 1)[1].strip())
                result = json.dumps([{"id": item["id"], "output": "文档翻译测试。" * (100 if args.long_layout else 1)} for item in values], ensure_ascii=False)
            if self.path.endswith("/responses"):
                response = {"id": "resp_test", "object": "response", "created_at": 1, "status": "completed",
                            "usage": {"input_tokens": 120, "output_tokens": 80, "total_tokens": 200},
                            "output": [{"type": "message", "id": "msg_test", "role": "assistant", "status": "completed",
                                        "content": [{"type": "output_text", "text": result, "annotations": []}]}]}
            else:
                response = {"id": "chat_test", "object": "chat.completion", "created": 1, "model": "test",
                            "usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
                            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": result}}]}
            payload = json.dumps(response, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = []
    try:
        for mode in ("responses", "chat_completions"):
            for name in (["sample.pdf"] if args.layout else ["sample.docx", "sample.pptx", "sample.pdf", "scan.png"]):
                job = DocumentJob(str(folder / "samples" / name), str(folder / "outputs"),
                    {"id": "local-test", "base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "test", "api_mode": mode,
                     "document_concurrency": args.concurrency},
                    job_id=uuid4().hex, pdf_mode="layout" if args.layout else "reading", trial=args.trial,
                    pricing={"input": 2, "output": 8, "multiplier": .1})
                if args.runtime:
                    command = [str(args.runtime / "python.exe"), "-I", "-u", str(args.runtime / "worker_entry.py")]
                else:
                    entry = "document_worker_debug.py" if args.trace else "document_worker_entry.py"
                    command = [sys.executable, "-I", "-u", str(ROOT / "scripts" / entry)]
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                process.stdin.write((json.dumps({"job": asdict(job), "api_key": "synthetic-test-key",
                    "database": str(folder / "verification.sqlite3")}) + "\n").encode())
                process.stdin.flush()
                events = queue.Queue()

                def reader():
                    for line in process.stdout:
                        events.put(json.loads(line))
                    events.put(None)

                thread = threading.Thread(target=reader, daemon=True)
                thread.start()
                final = None
                try:
                    while True:
                        event = events.get(timeout=600 if args.layout else 90)
                        if event is None:
                            break
                        if event["type"] in {"finished", "error", "cancelled"}:
                            final = event
                            print(json.dumps({"file": name, "mode": mode, "terminal": event["type"]}), flush=True)
                            break
                        elif event["type"] == "progress":
                            print(json.dumps({"file": name, "mode": mode, **event}, ensure_ascii=True), flush=True)
                    process.wait(timeout=10)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    process.stdin.close()
                    process.stdout.close()
                if not final or final["type"] != "finished":
                    raise RuntimeError(f"{name} {mode}: {final}")
                assert len(final["outputs"]) == 2
                for output in map(Path, final["outputs"]):
                    assert output.name.startswith("试译版_") == args.trial
                    assert output.parent.name.startswith("试译版_") == args.trial
                store = JobStore(folder / "verification.sqlite3")
                try:
                    record = next(row for row in store.history() if row[0] == job.job_id)
                    details = json.loads(record[4])
                    assert record[2] == "completed"
                    assert details["total_tokens"] == final["requests"] * 200
                    assert details["unreported_requests"] == 0
                    assert details["elapsed_seconds"] >= 0
                    assert details["file_size"] == Path(job.source).stat().st_size
                    assert details["outputs"] == final["outputs"]
                    assert details["pricing"] == job.pricing
                    assert billing_display(details)[2] == f"${final['requests'] * .00088:.6f}"
                    assert billing_display(details)[3] == f"${final['requests'] * .000088:.6f}"
                finally:
                    store.close()
                assert all(Path(path).stat().st_size > 100 for path in final["outputs"])
                if Path(final["outputs"][0]).suffix == ".pdf":
                    import pymupdf as fitz
                    with fitz.open(final["outputs"][0]) as translated:
                        assert any("翻译" in page.get_text() for page in translated), "Output lacks translated text"
                        if args.long_layout:
                            assert len(translated) > 1, "Long translations did not create continuation pages"
                            assert any("Additional translations" in page.get_text() and "[P1-" in page.get_text()
                                       for page in translated)
                results.append({"file": name, "mode": mode, "outputs": final["outputs"], "requests": final["requests"]})
        if not args.layout:
            assert any("scanned page" in text for _, text in calls), "OCR text did not reach translation API"
        (folder / ("layout-report.json" if args.layout else "pipeline-report.json")).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"PASS: {len(results)} worker jobs, {len(calls)} local HTTP requests; real OCR enabled", flush=True)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
