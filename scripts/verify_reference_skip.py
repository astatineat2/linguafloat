"""Create an offline PDF example of the reference-tail output rule."""
from pathlib import Path
import argparse
import json
import re
import sys
from uuid import uuid4
from types import SimpleNamespace
from dataclasses import replace

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from linguafloat.document_core import JobControl
from linguafloat.document_pdf import assemble_layout_pdf
from linguafloat.document_references import pdf_boundary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    folder = ROOT / "output" / "pdf" / "reference-skip-v0.10.0"
    folder.mkdir(parents=True, exist_ok=True)
    source_path = folder / "reference-skip-source.pdf"
    translated_path = folder / "offline-rendered.pdf"
    with fitz.open() as source:
        for text in ("Research article body and citation [1].",
                     "Second page body with results and discussion."):
            page = source.new_page(width=595, height=842)
            page.insert_text((45, 95), text, fontsize=13)
            if args.engine:
                assert page.insert_textbox(fitz.Rect(45, 120, 550, 400),
                    "The magnetic shielding experiment evaluates measurement precision and field stability. " * 14, fontsize=11) >= 0
        page = source[-1]
        page.insert_text((45, 450), "References", fontsize=15)
        page.insert_text((45, 480), "[1] First paper title. 2022.", fontsize=11)
        page.insert_text((45, 501), "[2] Second paper title. 2023.", fontsize=11)
        appendix = source.new_page(width=595, height=842)
        appendix.insert_text((45, 100), "Appendix A - original data.", fontsize=13)
        if args.engine:
            assert appendix.insert_textbox(fitz.Rect(45, 130, 550, 750),
                "Appendix supporting material and methods with additional measurement data. " * 40, fontsize=11) >= 0
        source.save(source_path)
    with fitz.open() as translated:
        for label in ("OFFLINE TRANSLATED BODY - PAGE 1", "OFFLINE TRANSLATED BODY - PAGE 2"):
            translated.new_page(width=595, height=842).insert_text((45, 95), label, fontsize=13)
        translated.save(translated_path)
    with fitz.open(source_path) as source:
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
    if boundary is None:
        raise RuntimeError("Sample reference boundary was not detected")
    from linguafloat.document_core import DocumentJob
    job = DocumentJob(source=str(source_path), output_dir=str(folder), provider={},
                      pdf_mode="layout", skip_references=True, reference_boundary=boundary)
    paths = assemble_layout_pdf(job, [0, 1, 2], translated_path, folder, {}, JobControl())
    with fitz.open(paths[0]) as mono, fitz.open(paths[1]) as dual:
        assert len(mono) == 3 and len(dual) == 3
        assert "Appendix A" in mono[-1].get_text()
        assert "Appendix A" in dual[-1].get_text()
        assert "[1] First paper" in mono[1].get_text()
    translated_path.unlink()
    print("Boundary:", boundary)
    print("Offline examples:", *paths)
    if args.engine:
        from linguafloat.document_core import DocumentTranslator, JobStore
        from linguafloat.document_worker import execute
        comparisons = []
        verification_run = uuid4().hex[:10]
        for skip in (False, True):
            requests = []
            def translated(text):
                return ("磁屏蔽实验用于测量精度与磁场稳定性。" * max(1, len(text)//60)
                        + " ".join(re.findall(r"\{[vV]\d+\}", text)))
            def create(**kwargs):
                prompt = kwargs["input"][-1]["content"]
                requests.append(prompt)
                value = translated(prompt)
                if "## Here is the input:" in prompt:
                    paragraphs = json.loads(prompt.split("## Here is the input:", 1)[1].strip())
                    value = json.dumps([dict(id=p["id"], output=translated(p["input"])) for p in paragraphs], ensure_ascii=False)
                elif prompt.startswith('{"fragments"'):
                    fragments = json.loads(prompt)["fragments"]
                    value = json.dumps([dict(id=p["id"], output="测试译文。") for p in fragments], ensure_ascii=False)
                return SimpleNamespace(output_text=value, usage=SimpleNamespace(input_tokens=30, output_tokens=10, total_tokens=40))
            job = DocumentJob(str(source_path), str(folder / "engine"),
                              dict(id="offline", base_url="http://127.0.0.1:1/v1", model="offline", api_mode="responses", retries=0,
                                   document_concurrency=2, document_rpm=10000),
                              job_id=uuid4().hex, pdf_mode="layout", skip_references=skip)
            client = SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None)
            store = JobStore(folder / f"engine-{verification_run}-{skip}.sqlite3")
            control, events = JobControl(), []
            def emit(event):
                events.append(event)
                if event.get("type") in {"finished", "progress"}:
                    print(json.dumps({k:v for k,v in event.items() if k in {"type","stage","message","requests"}}, ensure_ascii=False), flush=True)
            translator = DocumentTranslator(job, "offline", store, control, emit, client=client)
            try:
                execute(job, "offline", store, control, emit, translator=translator)
                if skip:
                    assert not any("First paper title" in p or "Appendix A - original data" in p for p in requests)
                    assert events[-1]["untranslated_regions"] == 0
                comparisons.append(dict(skip=skip, requests=len(requests), usage=translator.token_usage,
                                        boundary=job.reference_boundary, outputs=events[-1]["outputs"]))
                if skip:
                    def forbidden(**kwargs):
                        raise AssertionError("Re-layout must never call the translation API")
                    relayout = replace(job, job_id=uuid4().hex, relayout_only=True)
                    relayout_client = SimpleNamespace(responses=SimpleNamespace(create=forbidden), close=lambda: None)
                    local = DocumentTranslator(relayout, "", store, control, emit, client=relayout_client)
                    execute(relayout, "", store, control, emit, translator=local)
                    assert local.calls == 0
                    comparisons.append(dict(skip=True, relayout_only=True, requests=local.calls,
                                            cache_hits=local.hits, outputs=events[-1]["outputs"]))
            finally:
                (folder / f"requests-{skip}.json").write_text(json.dumps(requests, ensure_ascii=False, indent=2), encoding="utf-8")
                store.close()
        (folder / "engine-comparison.json").write_text(json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(comparisons, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
