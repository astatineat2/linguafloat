"""Offline inspection and cached replay; never uses user API credentials."""
import ctypes
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ctypes.windll.kernel32.SetDllDirectoryW(None)

import pymupdf as fitz
from linguafloat.document_core import DocumentJob, DocumentTranslator, JobControl, JobStore
from linguafloat.document_scan_layout import ScanAnalyzer, classify_page, ink_patch

FOLDER = ROOT / "verification-strict-layout"
FOLDER.mkdir(exist_ok=True)
HOBURG = Path("C:/Users/Astatine/Zotero/storage/M3FJP6NJ/Hoburg - 1995 - Principles of quasistatic magnetic shielding with cylindrical and spherical shields.pdf")


def preflight():
    store = JobStore(FOLDER / "offline.sqlite")
    t = SimpleNamespace(control=JobControl(), store=store)
    analyzer = ScanAnalyzer(t, lambda _: None)
    import hashlib
    with HOBURG.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    try:
        with fitz.open(HOBURG) as doc:
            page = doc[1]
            regions = analyzer.regions(page, classify_page(page), digest)
            for region in regions:
                if not region.get("reason"):
                    patch, reason = ink_patch(page, region)
                    region["mask_reason"] = reason
                    region["mask_ok"] = patch is not None
            (FOLDER / "hoburg-regions.json").write_text(json.dumps(regions, ensure_ascii=False, indent=2), encoding="utf-8")
            page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8)).save(FOLDER / "hoburg-source.png")
            print(json.dumps([dict(index=i, text=r["text"], reason=r.get("reason") or r.get("mask_reason"), box=r["box"])
                              for i, r in enumerate(regions)], ensure_ascii=True))
    finally:
        store.close()


def liu_replay():
    from linguafloat.document_artifacts import LayoutSnapshot
    from linguafloat.document_worker import execute
    evidence = ROOT / "verification-continuation-marker"
    case = json.loads((evidence / "case.json").read_text(encoding="utf-8"))
    for provider in case["providers"]:
        job = DocumentJob(case["source"], str(FOLDER / "liu"), provider,
                          job_id=uuid4().hex, pdf_mode="layout", relayout_only=True)
        if LayoutSnapshot(evidence, job, list(range(6))).path.exists():
            break
    else:
        raise RuntimeError("No matching translated snapshot")
    store = JobStore(evidence / "offline.sqlite3")
    events = []
    control = JobControl()
    t = DocumentTranslator(job, "offline-only", store, control, events.append)
    def forbidden(*args, **kwargs):
        raise AssertionError("Verification must not call any translation API")
    t.request = forbidden
    try:
        execute(job, "", store, control, events.append, t)
        event = events[-1]
        assert event["requests"] == 0
        with fitz.open(job.source) as source, fitz.open(event["outputs"][0]) as mono:
            assert len(mono) == len(source) == 6
            assert all(mono[i].rect == source[i].rect for i in range(6))
            for i in (1, 2):
                mono[i].get_pixmap(matrix=fitz.Matrix(1.8, 1.8)).save(FOLDER / f"liu-translated-{i + 1}.png")
        (FOLDER / "liu-events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(event, ensure_ascii=True))
    finally:
        store.close()


def hoburg_sample():
    from linguafloat.document_scan_layout import translate_scan_page
    regions = json.loads((FOLDER / "hoburg-regions.json").read_text(encoding="utf-8"))
    samples = {
        0: "本文给出了基于上述两种物理机制的圆柱形和球形屏蔽体的具体示例。为辨明高磁导率磁通分流和感应电流屏蔽各自的特性，首先选取仅涉及其中一种机制的示例。随后，通过两种机制同时起作用的示例，说明它们之间的相互作用。",
        7: "图1 两种不同的屏蔽几何结构。(a) 包围被屏蔽区域；磁场由远处外部导体施加。(b) 包围源导体；外部区域受到屏蔽。",
    }
    translations = {regions[i]["text"]: text for i, text in samples.items()}
    for i, region in enumerate(regions):
        if i not in samples and not region.get("reason"):
            region["reason"] = "本次离线小样未翻译此区域"
    job = DocumentJob(str(HOBURG), str(FOLDER), dict(id="offline-manual-sample", model="manual-sample",
                      base_url="https://example.invalid", api_mode="responses"), pdf_mode="layout")
    calls = []
    def create(**kwargs):
        prompt = kwargs["input"][-1]["content"]
        matches = [translation for text, translation in translations.items() if text in prompt]
        assert len(matches) == 1
        calls.append(1)
        return SimpleNamespace(output_text=matches[0])
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    store = JobStore(FOLDER / "sample-cache.sqlite")
    records = []
    try:
        with fitz.open(HOBURG) as source:
            for i in range(2):
                job.relayout_only = i == 1
                t = DocumentTranslator(job, "", store, JobControl(), lambda _: None, client)
                output, retained, placed = translate_scan_page(source, 1, "scan_ocr",
                    SimpleNamespace(regions=lambda *args: regions), "offline", t, lambda _: None)
                with output:
                    assert len(output) == 1 and output[0].rect == source[1].rect
                    assert len(placed) == 2, retained
                    figure = fitz.Rect(330, 100, 555, 400)
                    assert output[0].get_pixmap(clip=figure).samples == source[1].get_pixmap(clip=figure).samples
                    output.save(FOLDER / f"hoburg-offline-sample-{i}.pdf", garbage=4, deflate=True)
                    output[0].get_pixmap(matrix=fitz.Matrix(1.8, 1.8)).save(FOLDER / f"hoburg-offline-sample-{i}.png")
                records.append(dict(real_api_requests=0, local_mock_requests=t.calls, cached=i == 1,
                                    paragraphs=placed, untranslated=len(retained), figure_pixels_unchanged=True))
            assert records[1]["local_mock_requests"] == 0
        (FOLDER / "hoburg-sample.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(records, ensure_ascii=True))
    finally:
        store.close()


def portable():
    import subprocess
    import threading
    from dataclasses import asdict
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(1)
            payload = json.dumps(dict(id="offline", status="completed", output=[dict(
                type="message", id="sample", role="assistant", content=[dict(
                    type="output_text", text="离线测试译文，不是论文翻译。", annotations=[])])],
                usage=dict(input_tokens=10, output_tokens=10, total_tokens=20))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    runtime = ROOT / "exe/document-runtime"
    provider = dict(id="strict-layout-offline", model="offline-fixture", api_mode="responses",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1")
    records = []
    try:
        for cached in (False, True):
            before = len(calls)
            job = DocumentJob(str(HOBURG), str(FOLDER / "portable"), provider, pages="2",
                              pdf_mode="layout", relayout_only=cached, job_id=uuid4().hex)
            payload = dict(job=asdict(job), api_key="offline-fixture", database=str(FOLDER / "portable.sqlite"))
            p = subprocess.Popen([str(runtime / "python.exe"), "-I", "-u", str(runtime / "worker_entry.py")],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
            lines = []
            reader = threading.Thread(target=lambda: lines.extend(p.stdout.readlines()), daemon=True)
            reader.start()
            try:
                p.stdin.write(json.dumps(payload) + "\n")
                p.stdin.flush()
                p.wait(timeout=180)
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()
                p.stdin.close()
                reader.join(5)
                p.stdout.close()
            events = [json.loads(line) for line in lines]
            final = next(e for e in reversed(events) if e["type"] in {"finished", "error", "cancelled"})
            assert final["type"] == "finished", final
            assert final["untranslated_regions"] > 0
            assert not any(e.get("stage_key") == "scan-reading-fallback" for e in events)
            assert len(calls) == before if cached else len(calls) > before
            with fitz.open(HOBURG) as source, fitz.open(final["outputs"][0]) as mono:
                assert len(mono) == 1 and mono[0].rect == source[1].rect
                assert "离线测试译文" in mono[0].get_text().replace(" ", "")
            assert (Path(final["outputs"][0]).parent / "排版说明.txt").is_file()
            records.append(dict(cached=cached, real_api_requests=0, local_requests=len(calls) - before,
                                result=final, events=events))
        (FOLDER / "portable-result.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps([dict(cached=r["cached"], local_requests=r["local_requests"], real_api_requests=0,
                               untranslated_regions=r["result"]["untranslated_regions"]) for r in records]))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    {"preflight": preflight, "liu": liu_replay, "hoburg": hoburg_sample, "portable": portable}[sys.argv[1]]()
