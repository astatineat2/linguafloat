import json

import pymupdf as fitz

from linguafloat.document_core import JobControl
from linguafloat.document_pdf import translate_reading_pdf
from linguafloat.document_references import pdf_boundary, reference_boxes, reference_page_only
from test_document_core import job_for
from test_document_formats import FakeTranslator


def fixed_start(monkeypatch, **values):
    import linguafloat.document_references as references
    start = dict(page=0, y=90, boxes=[[0, 90, 600, 800]], title="References",
                 scanned=False, column="full", uncertain_pages=[])
    start.update(values)
    monkeypatch.setattr(references, "_pdf_start", lambda *args: dict(start))


def test_wrapped_citation_keyword_does_not_end_bibliography(monkeypatch):
    fixed_start(monkeypatch)
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 105), "References")
        page.insert_text((40, 135), "[1] Smith, A long article title")
        page.insert_text((40, 150), "Supplementary methods in scientific research.")
        page.insert_text((40, 180), "[2] Jones, Another article.")
        page.insert_text((40, 250), "Appendix A", fontsize=14)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["end"]["y"] > 220
        assert any(fitz.Rect(b).contains(page.search_for("Supplementary methods")[0])
                   for b in reference_boxes(boundary, 0))


def test_unconfirmed_following_pages_are_not_all_references(monkeypatch):
    fixed_start(monkeypatch)
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 105), "References")
        page.insert_text((40, 135), "[1] First article")
        page.insert_text((40, 165), "[2] Second article")
        doc.new_page(width=600, height=800).insert_text((40, 100), "Experimental details and more body text.")
        doc.new_page(width=600, height=800).insert_text((40, 100), "Further results.")
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert not reference_boxes(boundary, 1)
        assert not reference_boxes(boundary, 2)
        assert not reference_page_only(boundary, 1)
        assert boundary["uncertain_end"]["page"] == 1


def test_numbered_appendix_heading_ends_bibliography(monkeypatch):
    fixed_start(monkeypatch)
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 105), "References")
        page.insert_text((40, 135), "[1] First article")
        page.insert_text((40, 165), "[2] Second article")
        page.insert_text((40, 250), "1. Appendix A", fontsize=14)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["end"]["page"] == 0
        assert not any(fitz.Rect(b).intersects(page.search_for("Appendix")[0])
                       for b in reference_boxes(boundary, 0))


def test_left_column_appendix_unprotects_entire_following_right_column(monkeypatch):
    fixed_start(monkeypatch, column="both", boxes=[[0, 90, 280, 800], [280, 40, 600, 800]])
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 105), "References")
        page.insert_text((40, 135), "[1] First article")
        page.insert_text((40, 165), "[2] Second article")
        page.insert_text((40, 250), "Appendix A", fontsize=14)
        page.insert_text((320, 80), "Appendix continuation in right column")
        page.insert_text((320, 110), "More supporting results")
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["end"]["column"] == "left"
        assert all(box[2] <= 280 for box in reference_boxes(boundary, 0))
        assert not any(fitz.Rect(b).intersects(page.search_for("right column")[0])
                       for b in reference_boxes(boundary, 0))


def test_reading_right_appendix_does_not_move_left_body_after_references(tmp_path, monkeypatch):
    fixed_start(monkeypatch, column="right", boxes=[[240, 90, 600, 800]])
    path = tmp_path / "columns.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((30, 400), "LEFT BODY BELOW APPENDIX")
        page.insert_text((250, 105), "References")
        page.insert_text((250, 135), "[1] First article")
        page.insert_text((250, 165), "[2] Second article")
        page.insert_text((250, 250), "Appendix A", fontsize=14)
        page.insert_text((250, 280), "RIGHT APPENDIX BODY")
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["end"]["column"] == "right"
        assert boundary["end"]["split_x"] == 240
        doc.save(path)
    job = job_for(path, skip_references=True)
    job.reference_boundary = boundary
    translator = FakeTranslator(job)
    sent = []
    translate = translator.translate
    def capture(text):
        sent.append(text)
        return translate(text)
    translator.translate = capture
    outputs, _ = translate_reading_pdf(job, translator, tmp_path, lambda _: None)
    assert len(sent) == 2
    assert any("LEFT BODY" in text and "APPENDIX BODY" not in text for text in sent)
    assert any("RIGHT APPENDIX BODY" in text and "LEFT BODY" not in text for text in sent)
    with fitz.open(outputs[0]) as mono:
        assert "LEFT BODY" in mono[0].get_text()
        assert "[1] First article" in mono[1].get_text()
        assert "RIGHT APPENDIX BODY" in mono[2].get_text()


def test_reading_uncertain_reference_page_is_reported_not_silently_skipped(tmp_path):
    path = tmp_path / "uncertain.pdf"
    with fitz.open() as doc:
        doc.new_page(width=600, height=800).insert_text((40, 100), "Body and references overlap")
        doc.save(path)
    job = job_for(path, skip_references=True)
    job.reference_boundary = dict(page=0, boxes=[[0, 0, 600, 800]],
                                  page_boxes={"0": [[0, 0, 600, 800]]}, uncertain_pages=[0])
    translator = FakeTranslator(job)
    translate_reading_pdf(job, translator, tmp_path, lambda _: None)
    assert translator.calls == 0
    report = json.loads((tmp_path / "layout-report.json").read_text(encoding="utf-8"))
    assert report["untranslated_regions"]["0"][0]["stage"] == "参考文献边界"


def test_positioned_ocr_failure_keeps_reading_page_and_continues(tmp_path, monkeypatch):
    from linguafloat.document_pdf import DocumentOcr
    path = tmp_path / "positioned-ocr.pdf"
    with fitz.open() as doc:
        doc.new_page(width=600, height=800).insert_text((40, 100), "First page")
        doc.new_page(width=600, height=800).insert_text((40, 100), "Second page body")
        doc.save(path)
    monkeypatch.setattr("linguafloat.document_pdf.needs_page_ocr", lambda page, text: page.number == 0)
    def recognize(self, page, positioned=False):
        if positioned:
            raise ValueError("positioned recognition failed")
        return "First page"
    monkeypatch.setattr(DocumentOcr, "recognize", recognize)
    job = job_for(path, skip_references=True)
    job.reference_boundary = dict(page=0, boxes=[[0, 200, 600, 800]],
                                  page_boxes={"0": [[0, 200, 600, 800]]})
    translator = FakeTranslator(job)
    outputs, _ = translate_reading_pdf(job, translator, tmp_path, lambda _: None)
    assert translator.calls == 1
    with fitz.open(outputs[0]) as mono:
        assert "First page" in mono[0].get_text()
        assert "Second page body" in mono[-1].get_text()


def test_worker_retains_uncertain_end_in_final_warnings(tmp_path, monkeypatch):
    from linguafloat.document_core import JobStore
    from linguafloat.document_worker import execute
    fixed_start(monkeypatch)
    path = tmp_path / "uncertain-end.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 105), "References")
        page.insert_text((40, 135), "[1] First article")
        page.insert_text((40, 165), "[2] Second article")
        doc.new_page(width=600, height=800).insert_text((40, 100), "Unlabelled supporting body text")
        doc.save(path)
    job = job_for(path, pdf_mode="reading", skip_references=True)
    translator = FakeTranslator(job)
    events = []
    store = JobStore(tmp_path / "worker.sqlite")
    try:
        execute(job, "unused", store, translator.control, events.append, translator)
    finally:
        store.close()
    assert events[-1]["type"] == "finished"
    assert any("原始第 2 页起未确认参考文献续页范围" in warning for warning in events[-1]["warnings"])
