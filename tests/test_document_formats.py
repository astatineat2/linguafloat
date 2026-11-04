import copy
from pathlib import Path
from zipfile import ZipFile

import pytest

docx = pytest.importorskip("docx")
pptx = pytest.importorskip("pptx")
fitz = pytest.importorskip("pymupdf")
from PIL import Image
from lxml import etree as ET

from linguafloat.document_core import Cancelled, DocumentError, JobControl, JobStore
from linguafloat.document_office import A, W, parse, translate_paragraph
from linguafloat.document_pdf import render_text_pdf
from linguafloat.document_worker import execute
from test_document_core import job_for


class FakeTranslator:
    def __init__(self, job):
        self.job, self.control = job, JobControl()
        self.calls = self.hits = 0

    def translate(self, text):
        self.calls += 1
        return "译文 " + text

    def request(self, messages):
        self.calls += 1
        root = parse(messages[-1]["content"].encode())
        for node in root:
            node.text = "译文 " + (node.text or "")
        return ET.tostring(root, encoding="unicode")


def run_job(tmp_path, job, translator=None):
    events = []
    store = JobStore(tmp_path / "db.sqlite")
    try:
        translator = translator or FakeTranslator(job)
        execute(job, "unused", store, translator.control, events.append, translator)
    finally:
        store.close()
    return events[-1]


def test_docx_preserves_styles_tables_links_and_original(tmp_path):
    path = tmp_path / "sample.docx"
    document = docx.Document()
    p = document.add_paragraph()
    p.add_run("Hello ").bold = True
    p.add_run("world").italic = True
    document.add_table(rows=1, cols=1).cell(0, 0).text = "A table"
    document.sections[0].header.paragraphs[0].text = "Header"
    picture = tmp_path / "image.png"
    Image.new("RGB", (20, 20), "red").save(picture)
    document.add_picture(str(picture))
    document.save(path)
    original = path.read_bytes()
    result = run_job(tmp_path, job_for(path))
    mono, dual = map(docx.Document, result["outputs"])
    assert mono.paragraphs[0].runs[0].bold
    assert mono.paragraphs[0].runs[1].italic
    assert mono.paragraphs[0].text.count("译文") == 2
    assert dual.paragraphs[0].text == "Hello world"
    assert "译文" in dual.paragraphs[1].text
    assert len(dual.inline_shapes) == 1
    assert "译文" in mono.tables[0].cell(0, 0).text
    assert path.read_bytes() == original
    with ZipFile(path) as before, ZipFile(result["outputs"][0]) as after:
        assert before.read("word/_rels/document.xml.rels") == after.read("word/_rels/document.xml.rels")


def test_pptx_double_slides_keeps_images_and_selected_range(tmp_path):
    from pptx.util import Inches
    path = tmp_path / "sample.pptx"
    picture = tmp_path / "picture.png"
    Image.new("RGB", (30, 30), "green").save(picture)
    presentation = pptx.Presentation()
    for text in ("First page", "Second page"):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(2)).text = text
        slide.shapes.add_picture(str(picture), Inches(1), Inches(3))
    presentation.save(path)
    result = run_job(tmp_path, job_for(path, pages="2"))
    mono, dual = map(pptx.Presentation, result["outputs"])
    assert len(mono.slides) == 2 and len(dual.slides) == 3
    assert mono.slides[0].shapes[0].text == "First page"
    assert "译文" in mono.slides[1].shapes[0].text
    assert dual.slides[1].shapes[0].text == "Second page"
    assert "译文" in dual.slides[2].shapes[0].text
    assert dual.slides[2].shapes[1].image.blob == picture.read_bytes()


def test_pdf_trial_outputs_only_selected_pages_and_cjk_wraps(tmp_path):
    source = tmp_path / "paper.pdf"
    with fitz.open() as document:
        for i in range(4):
            page = document.new_page()
            page.insert_text((50, 50), f"Example research paragraph on page {i+1}.")
        document.save(source)
    result = run_job(tmp_path, job_for(source, pages="2-4", trial=True, bilingual_layout="alternating"))
    assert all(Path(path).name.startswith("试译版_") for path in result["outputs"])
    with fitz.open(result["outputs"][0]) as mono, fitz.open(result["outputs"][1]) as dual:
        assert len(mono) == 2 and len(dual) == 4
        assert "译文" in mono[0].get_text()
        assert "page 2" in dual[0].get_text()
        assert "page 3" in dual[2].get_text()
        for page in mono:
            assert all(fitz.Rect(block[:4]).x1 <= page.rect.x1 for block in page.get_text("blocks"))


def test_long_translation_paginates_without_truncation(tmp_path):
    output = tmp_path / "long.pdf"
    render_text_pdf("很长的翻译段落。" * 1200 + "ENDMARKER", output, 1)
    with fitz.open(output) as document:
        assert len(document) > 1
        assert "ENDMARKER" in document[-1].get_text()


def test_scan_text_cache_reuses_ocr_and_invalidates_changed_file(tmp_path, monkeypatch):
    from linguafloat.document_pdf import DocumentOcr
    source = tmp_path / "scan.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(source)
    recognized = []
    def recognize(self, page):
        recognized.append(page.number)
        return "Recognized text from scanned page"
    monkeypatch.setattr(DocumentOcr, "recognize", recognize)
    store = JobStore(tmp_path / "cache.sqlite")
    try:
        for index in range(3):
            if index == 2:
                with fitz.open(source) as doc:
                    doc.set_metadata({"title": "Changed input"})
                    doc.saveIncr()
            job = job_for(source)
            job.job_id = str(index)
            translator = FakeTranslator(job)
            translator.store = store
            execute(job, "unused", store, translator.control, lambda _: None, translator)
        assert recognized == [0, 0]
    finally:
        store.close()


def test_mixed_document_only_ocr_scanned_pages_with_digital_header(tmp_path, monkeypatch):
    from linguafloat.document_pdf import DocumentOcr
    source = tmp_path / "mixed.pdf"
    picture = tmp_path / "scan.png"
    Image.new("RGB", (500, 700), "white").save(picture)
    with fitz.open() as doc:
        doc.new_page().insert_text((30, 30), "Digital page: enough text to extract directly.")
        scan = doc.new_page()
        scan.insert_image(fitz.Rect(10, 60, 580, 830), filename=str(picture))
        scan.insert_text((30, 30), "Digital header above a scanned document body")
        doc.save(source)
    seen = []
    def recognize(self, page):
        seen.append(page.number)
        return "Digital header and scanned body text recognized together."
    monkeypatch.setattr(DocumentOcr, "recognize", recognize)
    result = run_job(tmp_path, job_for(source))
    assert result["type"] == "finished"
    assert seen == [1]


def searchable_scan(tmp_path):
    source = tmp_path / "searchable.pdf"
    picture = tmp_path / "background.png"
    Image.new("RGB", (500, 700), "white").save(picture)
    with fitz.open() as doc:
        for mode in (0, 3):
            page = doc.new_page()
            page.insert_image(page.rect, filename=str(picture))
            page.insert_text((30, 40), "Existing OCR layer with complete searchable text.\n" * 16,
                             fontsize=9, render_mode=mode)
        doc.save(source)
    return source


def test_searchable_scan_preserves_layout_and_reports_unverified_text(tmp_path, monkeypatch):
    import linguafloat.document_pdf as pdf
    from linguafloat.document_scan_layout import ScanAnalyzer
    source = searchable_scan(tmp_path)
    with fitz.open(source) as doc:
        assert not pdf.has_scanned_text_layer(doc[0])
        assert pdf.has_scanned_text_layer(doc[1])
    def forbidden(*args, **kwargs):
        pytest.fail("Scans must not enter the digital engine or reading fallback")
    monkeypatch.setattr(pdf, "_translate_layout_pdf", forbidden)
    monkeypatch.setattr(pdf, "translate_reading_pdf", forbidden)
    monkeypatch.setattr(ScanAnalyzer, "regions", lambda self, page, *args: [
        dict(box=list(page.rect), text=page.get_text(), reason="隐藏文字层与可见文字不一致")])
    job = job_for(source, pdf_mode="layout", pages="2")
    result = run_job(tmp_path, job)
    assert result["type"] == "finished"
    assert any("隐藏文字层" in text for text in result["warnings"])
    with fitz.open(result["outputs"][0]) as translated:
        assert "Existing OCR layer" in translated[0].get_text()


def test_scan_classification_respects_selected_pages(tmp_path, monkeypatch):
    import linguafloat.document_pdf as pdf
    source = searchable_scan(tmp_path)
    with fitz.open(source) as doc:
        doc.delete_page(0)
        doc.new_page(pno=0).insert_text((40, 40), "Digital text, no scanned image")
        doc.saveIncr()
    expected = (["sentinel.pdf"], [])
    monkeypatch.setattr(pdf, "_translate_layout_pdf", lambda *args: expected)
    job = job_for(source, pdf_mode="layout", pages="1")
    assert pdf.translate_layout_pdf(job, FakeTranslator(job), tmp_path, lambda _: None) == expected


def test_upstream_scanned_error_never_switches_mode_and_cancel_propagates(tmp_path, monkeypatch):
    import linguafloat.document_pdf as pdf
    from babeldoc.babeldoc_exception.BabelDOCException import ScannedPDFError
    source = searchable_scan(tmp_path)
    with fitz.open(source) as doc:
        doc.delete_page(0)
        doc.new_page(pno=0).insert_text((40, 40), "Digital text, no scanned image")
        doc.saveIncr()
    job = job_for(source, pdf_mode="layout", pages="1")
    def scanned(*args):
        raise ScannedPDFError("upstream error")
    monkeypatch.setattr(pdf, "_translate_layout_pdf", scanned)
    monkeypatch.setattr(pdf, "translate_reading_pdf", lambda *args: pytest.fail("Mode changed"))
    with pytest.raises(DocumentError, match="判定不一致"):
        run_job(tmp_path, job)
    def cancelled(*args):
        raise Cancelled("cancelled")
    monkeypatch.setattr(pdf, "_translate_layout_pdf", cancelled)
    with pytest.raises(Cancelled):
        pdf.translate_layout_pdf(job, FakeTranslator(job), tmp_path, lambda _: None)


def test_searchable_scan_relayout_never_fills_missing_translations_via_api(tmp_path, monkeypatch):
    from linguafloat.document_core import DocumentTranslator
    from linguafloat.document_scan_layout import ScanAnalyzer
    source = searchable_scan(tmp_path)
    job = job_for(source, pdf_mode="layout", pages="2", relayout_only=True)
    store = JobStore(tmp_path / "no-api.sqlite")
    translator = DocumentTranslator(job, "not-a-key", store, JobControl(), lambda _: None)
    def forbidden(*args, **kwargs):
        pytest.fail("Cache-only fallback must not call the API")
    monkeypatch.setattr(translator.client.responses, "create", forbidden)
    monkeypatch.setattr(ScanAnalyzer, "regions", lambda self, page, *args: [
        dict(box=list(page.rect), text=page.get_text(), reason="隐藏文字层与可见文字不一致")])
    try:
        execute(job, "", store, translator.control, lambda _: None, translator)
        assert translator.calls == 0
    finally:
        store.close()


def test_scanned_error_is_explained_in_chinese(tmp_path):
    from babeldoc.babeldoc_exception.BabelDOCException import ScannedPDFError
    from linguafloat.document_worker import describe_failure
    reason = describe_failure(ScannedPDFError("private upstream body"), job_for(tmp_path / "paper.pdf"), "解析中")
    assert "隐藏的 OCR 文字层" in reason and "阅读版" in reason
    assert "ScannedPDFError" not in reason and "private upstream body" not in reason


@pytest.mark.parametrize("extension", ["docx", "pptx"])
def test_trial_names_are_distinct_and_history_matches_outputs(tmp_path, extension):
    import json
    from pptx.util import Inches
    source = tmp_path / ("sample." + extension)
    if extension == "docx":
        document = docx.Document()
        document.add_paragraph("Hello")
    else:
        document = pptx.Presentation()
        slide = document.slides.add_slide(document.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(2)).text = "Hello"
    document.save(source)
    original = source.read_bytes()
    trial_job = job_for(source, trial=True)
    trial_job.job_id = "trial"
    full_job = job_for(source)
    full_job.job_id = "full"
    trial = run_job(tmp_path, trial_job)
    full = run_job(tmp_path, full_job)
    for path in map(Path, trial["outputs"]):
        assert path.name.startswith("试译版_")
        assert path.parent.name.startswith("试译版_")
        assert path.is_file()
    assert all(not Path(path).name.startswith("试译版_") for path in full["outputs"])
    assert source.read_bytes() == original
    store = JobStore(tmp_path / "db.sqlite")
    try:
        info = json.loads(next(row[4] for row in store.history() if row[0] == "trial"))
        assert info["outputs"] == trial["outputs"] and info["trial"]
    finally:
        store.close()


def test_side_by_side_maps_continuations_to_source_page(tmp_path):
    source = tmp_path / "paper.pdf"
    with fitz.open() as document:
        for number in range(3):
            page = document.new_page(width=420 + number * 20, height=600)
            page.insert_text((40, 40), f"SOURCE-{number + 1} This is a source paragraph.")
        document.save(source)
    job = job_for(source, pages="2-3")
    translator = FakeTranslator(job)
    translator.translate = lambda text: (("很长的段落。" * 1600 + "END-TWO") if "SOURCE-2" in text else "END-THREE")
    result = run_job(tmp_path, job, translator)
    with fitz.open(result["outputs"][1]) as document:
        assert len(document) > 2
        for page in list(document)[:-1]:
            assert "SOURCE-2" in page.get_text()
            assert "Original 2" in page.get_text()
            assert page.rect.width > page.rect.height
        assert "END-TWO" in document[-2].get_text()
        assert "SOURCE-3" in document[-1].get_text()
        assert "END-THREE" in document[-1].get_text()


def test_side_by_side_preserves_rotated_source_and_blank_page(tmp_path):
    from linguafloat.document_pdf import append_bilingual
    with fitz.open() as source, fitz.open() as translated, fitz.open() as dual:
        page = source.new_page(width=300, height=500)
        page.insert_text((40, 40), "ROTATED-SOURCE")
        page.set_rotation(90)
        source.new_page(width=300, height=500)
        translated.new_page().insert_text((40, 40), "TRANSLATION")
        append_bilingual(dual, source, 0, translated, "side_by_side")
        append_bilingual(dual, source, 1, translated, "side_by_side")
        assert dual[0].rect.width == 500 + translated[0].rect.width + 20
        assert "ROTATED-SOURCE" in dual[0].get_text()
        assert "TRANSLATION" in dual[1].get_text()


def test_publish_failure_never_reports_finished(tmp_path, monkeypatch):
    import shutil
    source = tmp_path / "source.docx"
    document = docx.Document()
    document.add_paragraph("Hello")
    document.save(source)
    job = job_for(source)
    translator = FakeTranslator(job)
    events = []
    store = JobStore(tmp_path / "failure.sqlite")
    def fail(*args, **kwargs):
        raise PermissionError("synthetic output failure")
    monkeypatch.setattr(shutil, "copy2", fail)
    try:
        with pytest.raises(PermissionError):
            execute(job, "unused", store, translator.control, events.append, translator)
    finally:
        store.close()
    assert not any(event["type"] == "finished" for event in events)
    assert all(event.get("value", 0) != 100 for event in events)
    assert not list(Path(job.output_dir).glob("*"))


def test_layout_rejects_missing_text_and_unreadable_shrinking():
    from types import SimpleNamespace
    from linguafloat.document_pdf import check_layout_paragraph
    paragraph = SimpleNamespace(unicode="Translated text", scale=.49,
                                pdf_style=SimpleNamespace(font_size=12))
    with pytest.raises(DocumentError, match="保留版式模式未改变"):
        check_layout_paragraph(paragraph, [object()], 3)
    paragraph.scale = 1
    with pytest.raises(DocumentError, match="保留版式模式未改变"):
        check_layout_paragraph(paragraph, [])
    check_layout_paragraph(paragraph, [object()])


def test_six_point_boundary_and_layout_error_details():
    from types import SimpleNamespace
    from linguafloat.document_pdf import check_layout_paragraph
    paragraph = SimpleNamespace(unicode="<style id='1'>关键词</style>", scale=.85,
                                pdf_style=SimpleNamespace(font_size=8.9663))
    check_layout_paragraph(paragraph, [object()], 1)
    paragraph.pdf_style.font_size = 12
    paragraph.scale = .5
    check_layout_paragraph(paragraph, [object()], 1)
    paragraph.scale = .499
    with pytest.raises(DocumentError) as caught:
        check_layout_paragraph(paragraph, [object()], 5)
    text = str(caught.value)
    for expected in ("原始第 5 页", "5.9880pt", "6pt", "关键词", "行距", "输出状态"):
        assert expected in text
    assert "<style" not in text
    with pytest.raises(DocumentError, match="文字未绘制") as caught:
        check_layout_paragraph(paragraph, [], 2)
    assert "原始第 2 页" in str(caught.value)
    assert "字号过小" not in str(caught.value)
    paragraph.pdf_style.font_size = 5
    paragraph.scale = 1
    check_layout_paragraph(paragraph, [object()], 1)
    paragraph.pdf_style.font_size = 7.9702
    paragraph.scale = .7
    with pytest.raises(DocumentError, match="字号过小"):
        check_layout_paragraph(paragraph, [object()], 2)


def test_cancel_does_not_publish_outputs(tmp_path):
    source = tmp_path / "source.docx"
    document = docx.Document()
    document.add_paragraph("Hello")
    document.save(source)
    job = job_for(source)
    translator = FakeTranslator(job)

    def cancel(text):
        translator.control.cancelled.set()
        return "你好"

    translator.translate = cancel
    with pytest.raises(Cancelled):
        run_job(tmp_path, job, translator)
    assert not list(Path(job.output_dir).glob("*"))


def test_broken_inline_markers_fail_without_corrupting_package(tmp_path):
    paragraph = ET.fromstring(f'<w:p xmlns:w="{W}"><w:r><w:t>Hello</w:t></w:r><w:r><w:t>world</w:t></w:r></w:p>')
    translator = FakeTranslator(job_for(tmp_path / "source.docx"))
    translator.request = lambda _: "<text><r id='0'>missing span</r></text>"
    with pytest.raises(DocumentError, match="格式标记"):
        translate_paragraph(paragraph, W, translator)
    assert "".join(paragraph.itertext()) == "Helloworld"
