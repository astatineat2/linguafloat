"""Real output-derived source fixtures, with no credentials or paid requests."""
import ctypes
import json
import sys
import re
from types import SimpleNamespace as NS
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ctypes.windll.kernel32.SetDllDirectoryW(None)
import pymupdf as fitz
from linguafloat.document_scientific import reference_entries, reference_title

OUT = ROOT / "verification-pdf-defects"
OUT.mkdir(exist_ok=True)
sys.stdout.reconfigure(encoding="utf-8")


def extract():
    supplied = next((ROOT.parent / "test/s41467-024-52210-y.236e37dd96b9").glob("*.双语.pdf"))
    with fitz.open(supplied) as doc, fitz.open() as source:
        for i in (5, 6):
            p = doc[i]
            rect = fitz.Rect(0, 26, (p.rect.width - 20)/2, p.rect.height)
            with fitz.open() as cleaned:
                cleaned.insert_pdf(doc, from_page=i, to_page=i)
                page = cleaned[0]
                page.add_redact_annot(fitz.Rect(rect.x1, 0, p.rect.width, p.rect.height), fill=False)
                page.add_redact_annot(fitz.Rect(0, 0, p.rect.width, 26), fill=False)
                page.apply_redactions(images=2, graphics=1, text=0)
                out = source.new_page(width=rect.width, height=rect.height)
                out.show_pdf_page(out.rect, cleaned, 0, clip=rect)
        source.save(OUT / "nature-original-left-pages6-7.pdf", garbage=4, deflate=True)
    with fitz.open(OUT / "nature-original-left-pages6-7.pdf") as doc:
        entries = reference_entries(doc[1])
        (OUT / "reference-entries.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Recovered reference entries:", len(entries))
        for e in entries[:3]:
            print(e["number"], repr(reference_title(e["text"])))


def pipeline():
    from linguafloat.document_core import DocumentJob, DocumentTranslator, JobStore, JobControl
    from linguafloat.document_pdf import translate_layout_pdf
    folder = OUT / "native-pipeline"
    folder.mkdir(exist_ok=True)
    job = DocumentJob(str(OUT / "nature-original-left-pages6-7.pdf"), str(folder),
        dict(id="scientific-offline-v2", model="manual-fixture", api_mode="responses", base_url="https://example.invalid", document_rpm=10000),
        pdf_mode="layout")
    def translate(text):
        tokens = re.findall(r"\{v\d+\}", text)
        return "测试文字" * max(1, len(text)//30) + " ".join(tokens)
    calls = []
    def create(**kwargs):
        content = kwargs["input"][-1]["content"]
        calls.append(content)
        if "## Here is the input:" in content:
            values = json.loads(content.split("## Here is the input:", 1)[1].strip())
            value = json.dumps([dict(id=v["id"], output=translate(v["input"])) for v in values], ensure_ascii=False)
        elif content.startswith('{"fragment"'):
            value = translate(json.loads(content)["fragment"])
        else:
            value = translate(content.split("Now translate the following text:")[-1])
        return NS(output_text=value)
    store = JobStore(OUT / "native-fixture.sqlite")
    try:
        t = DocumentTranslator(job, "", store, JobControl(), lambda _: None, NS(responses=NS(create=create)))
        result = translate_layout_pdf(job, t, folder, lambda _: None)
        (OUT / "native-prompts.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Pipeline output", result[0], "mock requests", len(calls))
    finally:
        store.close()


if __name__ == "__main__":
    pipeline() if len(sys.argv) > 1 and sys.argv[1] == "pipeline" else extract()
