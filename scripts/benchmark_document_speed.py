"""Offline before/after benchmarks. Only the loopback mock API may be called."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from linguafloat.document_core import DocumentJob


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("reading", "scan", "office", "tian"), required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--after", action="store_true")
    parser.add_argument("--compare-concurrency", action="store_true")
    parser.add_argument("--compare-efficiency", action="store_true")
    parser.add_argument("--compare-relayout", action="store_true")
    parser.add_argument("--request-delay", type=float, default=2)
    args = parser.parse_args()
    folder = ROOT / ("verification-pdf-efficiency" if args.compare_efficiency or args.compare_relayout else
                     "verification-concurrency-six" if args.compare_concurrency else "verification-speed") / (args.case + ("-portable" if args.runtime else ""))
    folder.mkdir(parents=True, exist_ok=True)
    import pymupdf as fitz
    counts = []
    request_lock = threading.Lock()
    network = dict(inflight=0, peak=0)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            text = body.get("input", body.get("messages"))[-1]["content"]
            with request_lock:
                counts.append(1)
                network["inflight"] += 1
                network["peak"] = max(network["peak"], network["inflight"])
            time.sleep(args.request_delay)
            value = "文档翻译测试。保留全部文字、图片及公式。"
            if args.compare_efficiency:
                value = text.lower()
            if text.startswith("<text>"):
                from lxml import etree as ET
                root = ET.fromstring(text.encode())
                for node in root:
                    node.text = value
                value = ET.tostring(root, encoding="unicode")
            incoming = (len(json.dumps(body.get("input", body.get("messages"))).encode()) + 3) // 4 if args.compare_efficiency else 120
            outgoing = (len(value.encode()) + 3) // 4 if args.compare_efficiency else 80
            response = dict(id="r", status="completed", usage=dict(input_tokens=incoming, output_tokens=outgoing, total_tokens=incoming + outgoing),
                            output=[dict(type="message", id="m", role="assistant", content=[dict(type="output_text", text=value, annotations=[])])])
            payload = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            with request_lock:
                network["inflight"] -= 1
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    provider = dict(id="speed-test", base_url=f"http://127.0.0.1:{server.server_port}/v1", model="fixed-mock",
                    api_mode="responses", document_concurrency=6 if args.compare_efficiency else 2, document_rpm=60)
    if args.case == "reading":
        source = folder / "six-pages.pdf"
        with fitz.open() as doc:
            for i in range(3 if args.compare_efficiency else 6):
                page = doc.new_page()
                if args.compare_efficiency:
                    lines = [f"Page {i + 1} paragraph {j}: Measurements preserve equations and citations [12]." for j in range(42)]
                    page.insert_text((40, 45), "\n".join(lines), fontsize=8)
                else:
                    page.insert_text((40, 60), f"Page {i + 1}: A scientific document with unique contents and complete translation.")
            doc.save(source)
    elif args.case == "scan":
        from verify_document_pipeline import create_samples
        create_samples(folder / "samples")
        source = folder / "scan.pdf"
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=250)
            page.insert_image(page.rect, filename=str(folder / "samples/scan.png"))
            doc.save(source)
    elif args.case == "office":
        from docx import Document
        source = folder / "six-paragraphs.docx"
        doc = Document()
        for i in range(6):
            p = doc.add_paragraph()
            p.add_run(f"Paragraph {i + 1}: ").bold = True
            p.add_run("Preserve citations, equations and document styles.")
        doc.save(source)
    else:
        settings = json.loads((Path(os.environ["APPDATA"]) / "LinguaFloat/config.json").read_text(encoding="utf-8"))
        profile = next(p for p in settings["providers"] if p["id"] == settings["active_provider"])
        provider = {key: profile[key] for key in ("id", "base_url", "model", "api_mode")}
        source = Path("C:/Users/Astatine/Zotero/storage/XTKPCD2U/Tian 等 - 2025 - Design and Optimization of Bi-Planar Coils for Active Magnetic Compensation System Inside the MINI-M.pdf")
    records = []
    if args.compare_concurrency and args.case == "tian":
        parser.error("Concurrency comparison requires an offline mock-API case")
    if args.compare_efficiency and (args.case != "reading" or args.runtime):
        parser.error("Efficiency comparison uses the offline reading PDF fixture")
    if args.compare_relayout:
        if args.case != "tian" or args.runtime:
            parser.error("Re-layout comparison requires the cached Tian fixture")
        shutil.copytree(ROOT / "verification-speed/tian-portable/layout-cache", folder / "layout-cache", dirs_exist_ok=True)
    versions = (["before", "after"] if args.compare_relayout else
                ["baseline", "parse-only", "segment-only", "combined"] if args.compare_efficiency else
                ["concurrency-2", "concurrency-6"] if args.compare_concurrency else
                ["after"] if args.runtime or args.after else ["before", "after"])
    try:
        for variant in versions:
            if args.compare_concurrency:
                provider["document_concurrency"] = int(variant.rsplit("-", 1)[1])
            database = folder / (variant + "-" + uuid4().hex[:8] + ".sqlite")
            if args.case == "tian":
                with sqlite3.connect((ROOT / "verification-tian-layout-policy/jobs.sqlite3").as_uri() + "?mode=ro", uri=True) as original, sqlite3.connect(database) as copied:
                    original.backup(copied)
            for temperature in (["snapshot-relayout"] if args.compare_relayout else
                                ["cached-relayout", "snapshot-relayout"] if args.case == "tian" and variant == "after" else
                                ["cached-relayout"] if args.case == "tian" else ["cold", "warm"]):
                job = DocumentJob(str(source), str(folder / variant), provider, job_id=uuid4().hex,
                                  pdf_mode="layout" if args.case == "tian" else "reading",
                                  relayout_only=temperature != "cold", pricing=dict(input=2, output=8, multiplier=.1))
                code = ROOT / ("verification-speed/before/src" if variant == "before" else "src")
                if args.compare_relayout and variant == "before":
                    code = ROOT / "verification-pdf-efficiency/before/src"
                if args.compare_efficiency:
                    code = folder / variant / "source"
                    shutil.copytree(ROOT / "verification-pdf-efficiency/before/src", code, dirs_exist_ok=True)
                    changes = []
                    if variant in {"parse-only", "combined"}:
                        changes += ["document_pdf.py", "document_artifacts.py"]
                    if variant in {"segment-only", "combined"}:
                        changes += ["document_core.py", "document_metrics.py"]
                    for name in changes:
                        shutil.copy2(ROOT / "src/linguafloat" / name, code / "linguafloat" / name)
                if args.runtime:
                    command = [str(args.runtime / "python.exe"), "-I", "-u", str(args.runtime / "worker_entry.py")]
                else:
                    bootstrap = ("import sys,ctypes,traceback; ctypes.windll.kernel32.SetDllDirectoryW(None); "
                                 "sys.path.insert(0,sys.argv[1]); import linguafloat.document_worker as w\n"
                                 "original=w.execute\ndef execute(*a,**k):\n"
                                 " try: return original(*a,**k)\n"
                                 " except Exception:\n  traceback.print_exc(file=sys.__stderr__)\n  raise\n"
                                 "w.execute=execute\nw.main()")
                    command = [sys.executable, "-I", "-u", "-c", bootstrap, str(code)]
                count = len(counts)
                with request_lock:
                    network["peak"] = 0
                started = time.perf_counter()
                payload = dict(job=asdict(job), api_key="mock-only" if temperature == "cold" else "", database=str(database))
                diagnostic = (folder / f"{variant}-{temperature}.log").open("w", encoding="utf-8")
                process = subprocess.Popen(command, stdin=subprocess.PIPE, text=True, encoding="utf-8",
                                           stdout=subprocess.PIPE, stderr=diagnostic, creationflags=subprocess.CREATE_NO_WINDOW)
                lines = []
                reader = threading.Thread(target=lambda: lines.extend(process.stdout.readlines()), daemon=True)
                reader.start()
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
                try:
                    process.wait(timeout=240)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise
                finally:
                    process.stdin.close()
                    reader.join(5)
                    diagnostic.close()
                duration = time.perf_counter() - started
                events = [json.loads(line) for line in lines if line.startswith("{")]
                result = next((e for e in reversed(events) if e.get("type") in {"finished", "error"}), {})
                assert result.get("type") == "finished", result
                if temperature != "cold":
                    assert len(counts) == count, "A cache-only benchmark made an HTTP request"
                with sqlite3.connect(database) as db:
                    info = json.loads(db.execute("SELECT details FROM jobs WHERE id=?", (job.job_id,)).fetchone()[0])
                signature = []
                for output in result["outputs"]:
                    if Path(output).suffix == ".pdf":
                        with fitz.open(output) as doc:
                            content = "".join(p.get_text() for p in doc)
                            if args.compare_efficiency:
                                content = re.sub(r"\s+", "", content)
                            signature.append(dict(pages=len(doc), text_hash=hashlib.sha256(content.encode()).hexdigest(),
                                                  cjk=sum("\u4e00" <= c <= "\u9fff" for c in content)))
                    else:
                        from docx import Document
                        content = "\n".join(p.text for p in Document(output).paragraphs)
                        signature.append(dict(text_hash=hashlib.sha256(content.encode()).hexdigest()))
                record = dict(version=variant, cache=temperature, wall_seconds=duration, history_seconds=info.get("elapsed_seconds"),
                              peak_http_concurrency=network["peak"], mock_request_delay=args.request_delay,
                              requests=result.get("requests"), hits=result.get("cache_hits"), input_tokens=info.get("input_tokens"),
                              output_tokens=info.get("output_tokens"), signature=signature, performance=info.get("performance"),
                              outputs=result["outputs"])
                records.append(record)
                print(json.dumps({k: v for k, v in record.items() if k not in {"outputs", "signature", "performance"}}, ensure_ascii=True), flush=True)
        assert all(r["signature"] == records[0]["signature"] for r in records), "Before/after output content differs"
        (folder / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
