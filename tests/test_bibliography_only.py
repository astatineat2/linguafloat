from types import SimpleNamespace

import pymupdf as fitz
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_references import pdf_boundary, boundary_intersects, reference_page_only, retain_pdf_tail


@pytest.fixture
def local_layout(monkeypatch):
    from babeldoc.docvision.doclayout import OnnxModel
    model = SimpleNamespace(predict=lambda *a, **k: [SimpleNamespace(names={}, boxes=[])])
    monkeypatch.setattr(OnnxModel, "from_pretrained", lambda: model)


def test_equation_number_is_not_a_second_body_column(local_layout):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 100), "Scientific body paragraph.")
        page.insert_text((510, 100), "(9)", fontsize=9)
        page.insert_text((40, 300), "References")
        page.insert_text((40, 330), "[1] A. Author, Journal (2020).")
        page.insert_text((40, 350), "[2] B. Author, Journal (2021).")
        appendix = doc.new_page()
        appendix.insert_text((40, 90), "Appendix A")
        appendix.insert_text((40, 130), "Supplementary methods must be translated.")
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["page"] == 0 and not boundary.get("uncertain_pages")
        assert boundary_intersects(boundary, 0, [40, 320, 300, 360])
        assert not boundary_intersects(boundary, 1, [40, 80, 400, 150])
        assert not reference_page_only(boundary, 1)


def test_references_end_on_same_page_preserve_original_pixels(local_layout):
    with fitz.open() as doc, fitz.open() as rendered:
        page = doc.new_page()
        for y, text in [(100, "Research body"), (250, "References"),
                        (280, "[1] First reference"), (300, "[2] Second reference"),
                        (400, "Appendix A"), (430, "Methods for the appendix")]:
            page.insert_text((40, y), text)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["end"]["page"] == 0
        assert not boundary_intersects(boundary, 0, [40, 420, 300, 435])
        target = rendered.new_page()
        target.insert_text((40, 100), "Translated body")
        target.insert_text((40, 430), "Translated appendix")
        retain_pdf_tail(target, page, dict(boxes=boundary["page_boxes"]["0"]))
        clip = fitz.Rect(0, 245, page.rect.width, 340)
        assert target.get_pixmap(clip=clip).samples == page.get_pixmap(clip=clip).samples
        assert "Translated appendix" in target.get_text()


def test_unknown_column_conflict_retains_only_uncertain_page(local_layout):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((340, 150), "Other column body must not be lost.")
        page.insert_text((40, 300), "References")
        page.insert_text((40, 330), "[1] First study")
        page.insert_text((40, 350), "[2] Second study")
        doc.new_page().insert_text((40, 100), "Appendix A")
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["uncertain_pages"] == [0]
        assert boundary["page_boxes"]["0"] == [[0, 0, doc[0].rect.width, doc[0].rect.height]]
        assert not reference_page_only(boundary, 1)


def test_reading_appendix_request_and_order(local_layout, tmp_path):
    from test_document_core import job_for
    from test_document_formats import FakeTranslator
    from linguafloat.document_pdf import translate_reading_pdf
    source = tmp_path / "source.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        for y, text in [(80, "Research body"), (200, "References"), (230, "[1] First study"),
                        (250, "[2] Second study"), (400, "Appendix A"), (430, "Extra methods")]:
            page.insert_text((40, y), text)
        doc.save(source)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
    job = job_for(source, skip_references=True)
    job.reference_boundary = boundary
    translator = FakeTranslator(job)
    paths, _ = translate_reading_pdf(job, translator, tmp_path, lambda _: None)
    with fitz.open(paths[0]) as mono:
        assert len(mono) == 3
        assert "[1] First study" in mono[1].get_text()
        assert translator.calls == 2
