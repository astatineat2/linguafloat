import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from linguafloat.document_core import Cancelled, DocumentError, DocumentRequestError, JobControl
from linguafloat.document_failures import diagnostic, mark_pdf
from linguafloat.document_layout import LayoutPolicy
from test_document_core import job_for
from test_document_formats import FakeTranslator, run_job
from test_document_layout import engine_fixture


class PartialTranslator(FakeTranslator):
    def translate(self, text):
        if "FAIL" in text:
            raise DocumentError("[翻译响应校验失败] secret-key PRIVATE_PAPER_BODY")
        return super().translate(text)


@pytest.mark.parametrize("error", [Cancelled("cancel"), DocumentRequestError("auth"),
    PermissionError("disk"), MemoryError(), RecursionError(), ImportError()])
def test_global_errors_are_never_local(error):
    with pytest.raises(type(error)):
        diagnostic(error, "翻译")


def test_diagnostics_do_not_include_exception_text_or_locals():
    try:
        raise ValueError("secret-key PRIVATE_PAPER_BODY")
    except ValueError as error:
        report = diagnostic(error, "校验")
    raw = json.dumps(report)
    assert "secret-key" not in raw and "PRIVATE_PAPER_BODY" not in raw
    assert report["trace"]["exceptions"][0]["frames"]


@pytest.mark.parametrize("extension", ["docx", "pptx"])
def test_office_partial_outputs_keep_source_and_other_translations(tmp_path, extension):
    source = tmp_path / ("partial." + extension)
    if extension == "docx":
        from docx import Document
        document = Document()
        for text in ("FIRST", "FAIL ONE", "LAST", "FAIL TWO"):
            document.add_paragraph(text)
    else:
        from pptx import Presentation
        from pptx.util import Inches
        document = Presentation()
        for text in ("FIRST", "FAIL ONE", "LAST", "FAIL TWO"):
            slide = document.slides.add_slide(document.slide_layouts[6])
            slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = text
    document.save(source)
    original = source.read_bytes()
    job = job_for(source)
    event = run_job(tmp_path, job, PartialTranslator(job))
    assert event["type"] == "finished" and event["untranslated_regions"] == 2
    assert source.read_bytes() == original
    from zipfile import ZipFile
    for path in event["outputs"]:
        with ZipFile(path) as package:
            data = b"".join(package.read(n) for n in package.namelist() if n.endswith(".xml"))
            assert b"FAIL ONE" in data and b"FAIL TWO" in data
            assert "译文 FIRST".encode() in data and "译文 LAST".encode() in data
            assert b"CC2222" in data
    log = (Path(event["outputs"][0]).parent / "未翻译问题.jsonl").read_text(encoding="utf-8")
    assert len(log.splitlines()) == 2
    assert "secret-key" not in log and "PRIVATE_PAPER_BODY" not in log


def test_reading_pdf_failed_page_is_preserved_not_replaced_by_error_text(tmp_path):
    source = tmp_path / "reading.pdf"
    with fitz.open() as document:
        for text in ("FIRST " * 30, "FAIL " * 30, "LAST " * 30):
            document.new_page().insert_textbox(fitz.Rect(40,40,500,600), text)
        document.save(source)
    job = job_for(source)
    event = run_job(tmp_path, job, PartialTranslator(job))
    assert event["untranslated_regions"] == 1
    with fitz.open(event["outputs"][0]) as output, fitz.open(source) as original:
        assert "FAIL" in output[1].get_text()
        assert "未翻译" in output[1].get_text().replace(" ", "")
        assert len(output) == len(original)
        assert "译文" in output[0].get_text() and "译文" in output[2].get_text()


def test_layout_local_failure_removes_only_failed_paragraph():
    _, page, paragraph, _ = engine_fixture()
    policy = LayoutPolicy(JobControl())
    policy.paragraph_pages[id(paragraph)] = page
    policy.retain_error(paragraph, DocumentError("[公式占位符校验失败]"), "翻译")
    policy.retain_error(paragraph, DocumentError("again"), "翻译")
    policy.prepare_page(page)
    assert not page.pdf_paragraph and len(policy.retained_regions[0]) == 1
    assert not policy.translation_failures


def test_pdf_markers_persist_and_preserved_regions_are_not_marked(tmp_path):
    path = tmp_path / "markers.pdf"
    with fitz.open() as document:
        page = document.new_page(width=300, height=200)
        page.insert_text((40,60), "ORIGINAL CONTENT")
        region = dict(box=[35,40,190,70], coordinates="top-left", status="untranslated")
        preserved = dict(box=[35,90,190,120], coordinates="top-left", status="preserved")
        mark_pdf(page, [region, preserved], 0)
        document.save(path)
    with fitz.open(path) as document:
        assert "ORIGINAL CONTENT" in document[0].get_text()
        assert "未翻译" in document[0].get_text().replace(" ", "")
        assert region["marked"] and "marked" not in preserved
        assert any(d.get("color", (0,))[0] > .8 for d in document[0].get_drawings())


@pytest.mark.parametrize("error", [DocumentError("empty"), ValueError("invalid layout")])
def test_scan_keeps_local_failed_region_and_continues(tmp_path, monkeypatch, error):
    from test_document_strict_layout import scan_fixture
    import linguafloat.document_scan_layout as scan
    source, region = scan_fixture(tmp_path)
    job = job_for(source, pdf_mode="layout")
    translator = PartialTranslator(job)
    calls = []
    def translate(text):
        calls.append(text)
        if len(calls) == 1:
            raise error
        return "译文"
    translator.translate = translate
    # Both regions are known, safe fixtures; first fails before any page mutation.
    analyzer = SimpleNamespace(regions=lambda *a: [region, dict(region)])
    with fitz.open(source) as original:
        result, retained, placed = scan.translate_scan_page(original, 0, "scan_image", analyzer, "fixture", translator, lambda _: None)
        result.close()
    assert len(calls) == 2 and len(retained) == 1 and len(placed) == 1


def test_ocr_failure_preserves_entire_source_page(tmp_path):
    from test_document_strict_layout import scan_fixture
    from linguafloat.document_scan_layout import translate_scan_page
    source, _ = scan_fixture(tmp_path)
    translator = PartialTranslator(job_for(source))
    def fail(*args):
        raise ValueError("private text")
    with fitz.open(source) as original:
        result, retained, placed = translate_scan_page(original, 0, "scan_image", SimpleNamespace(regions=fail), "fixture", translator, lambda _: None)
        with result:
            assert result[0].get_pixmap().samples == original[0].get_pixmap().samples
        assert retained[0]["box"] == list(original[0].rect) and not placed


def test_cancel_during_office_is_not_published(tmp_path):
    from docx import Document
    source = tmp_path / "cancel.docx"
    doc = Document()
    doc.add_paragraph("Hello")
    doc.save(source)
    job = job_for(source)
    translator = PartialTranslator(job)
    def cancel(text):
        raise Cancelled("stop")
    translator.translate = cancel
    with pytest.raises(Cancelled):
        run_job(tmp_path, job, translator)
    assert not list(Path(job.output_dir).glob("cancel.*"))


def test_authentication_failure_stops_subsequent_requests(tmp_path):
    import httpx
    import openai
    from linguafloat.document_core import DocumentTranslator, JobStore
    calls = []
    def create(**kwargs):
        calls.append(1)
        raise openai.AuthenticationError("invalid", response=httpx.Response(401,
            request=httpx.Request("POST", "https://example.invalid")), body={})
    store = JobStore(tmp_path / "auth.sqlite")
    try:
        translator = DocumentTranslator(job_for(tmp_path / "x.pdf"), "", store, JobControl(), lambda _: None,
            SimpleNamespace(responses=SimpleNamespace(create=create)))
        for text in ("First", "Second", "Third"):
            with pytest.raises(DocumentRequestError):
                translator.translate(text)
        assert len(calls) == 1
    finally:
        store.close()


def test_local_failure_is_not_cached_and_can_recover_after_restart(tmp_path):
    from linguafloat.document_core import DocumentTranslator, JobStore
    job = job_for(tmp_path / "x.pdf")
    path = tmp_path / "restart.sqlite"
    store = JobStore(path)
    empty = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: SimpleNamespace(output_text="")))
    translator = DocumentTranslator(job, "", store, JobControl(), lambda _: None, empty)
    with pytest.raises(DocumentError):
        translator.translate("Original")
    assert store.get(translator.cache_key(translator.messages("Original"))) is None
    store.close()
    store = JobStore(path)
    valid = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: SimpleNamespace(output_text="译文")))
    try:
        translator = DocumentTranslator(job, "", store, JobControl(), lambda _: None, valid)
        assert translator.translate("Original") == "译文"
        assert translator.translate("Original") == "译文"
        assert translator.calls == 1 and translator.hits == 1
    finally:
        store.close()


def test_layout_failure_retains_region_without_aborting_other_paragraphs(monkeypatch):
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
    engine, page, paragraph, _ = engine_fixture()
    policy = LayoutPolicy(JobControl())
    policy.paragraph_pages[id(paragraph)] = page
    def fail(*args):
        raise ValueError("bad font geometry")
    monkeypatch.setattr(Typesetting, "render_paragraph", fail)
    with policy:
        engine.render_paragraph(paragraph, page, {})
    assert policy.retained_regions[0][0]["stage"] == "排版"
    assert not paragraph.pdf_paragraph_composition
