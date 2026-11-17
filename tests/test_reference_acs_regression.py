"""ACS punctuation, column continuation, and independent appendix regressions."""
import os
from pathlib import Path
import re
import socket
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_references import (
    _bibliographic_entry, _digital_lines, boundary_intersects, filter_layout_paragraphs,
    is_heading, numbered_evidence, pdf_boundary, reference_boxes, reference_page_only,
    retain_pdf_tail,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network forbidden in reference regression tests")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.mark.parametrize("text,expected", [
    ("■REFERENCES", True), ("▪ Bibliography", True), ("◆ 4. References:", True),
    ("■ References are discussed below", False), ("note REFERENCES", False),
    ("■ References ........ 10", False), ("(1) References", False),
])
def test_heading_decoration_is_narrow(text, expected):
    assert is_heading(text) == expected


@pytest.mark.parametrize("labels,expected", [
    (["(1) Dong, F.", "(2) Li, Y."], True),
    (["[1] Dong, F.", "2. Li, Y."], True),
    (["(1) Dong, F.", "(3) Li, Y."], False),
    (["(1] malformed", "(2] malformed"], False),
    (["[1) malformed", "[2) malformed"], False),
    (["(1)", "(2)"], False),
])
def test_complete_parenthesized_labels(labels, expected):
    assert numbered_evidence(labels) == expected


@pytest.mark.parametrize("text,expected", [
    ("Dong, F.; Hao, F. Microbiota activity. Gut Microbes 2020, 12, 1788899.", True),
    ("Li, Y.; Li, Q. Colonic macrophages. Theranostics 2024, 14, 2719–2735.", True),
    ("Smith, A. Research article. J. Chem. 2024, 12 (3), 15–28.", True),
    ("Smith, A. provided apparatus in 2020. https://example.org/manual", False),
    ("Smith, A. visited the Journal office in 2020.", False),
    ("Record samples in 2020, 12, 1788899.", False),
    ("Smith, A. Research. Gut Microbes 12, 1788899.", False),
])
def test_unparenthesized_year_requires_publication_volume_pages(text, expected):
    assert _bibliographic_entry(text) == expected


def _layout(monkeypatch, kind="plain text"):
    from babeldoc.docvision.doclayout import OnnxModel
    prediction = SimpleNamespace(names={0: kind}, boxes=[
        SimpleNamespace(cls=0, conf=.98, xyxy=[620, 400, 1160, 1450])])
    monkeypatch.setattr(OnnxModel, "from_pretrained",
                        lambda: SimpleNamespace(predict=lambda *a, **k: [prediction]))


def _heading(page, text, x=315, y=220):
    # The built-in CJK font carries the actual square, not a substitute dot.
    page.insert_text((x, y), text, fontname="china-s" if not text.isascii() else "helv", fontsize=12)


def _entry(page, n, x, y, *, citation=True):
    text = "Smith, A.; Brown, B. Research title." if citation else "Measure samples carefully."
    page.insert_text((x, y), f"({n}) {text}", fontsize=8)
    page.insert_text((x, y+12), "J. Chem. 2024, 12, 15–28." if citation else
                     "See https://example.org/manual in 2024.", fontsize=8)


def _document(*, heading="■ REFERENCES", citation=True, appendix="■ APPENDIX A"):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((295, 775), "7589", fontsize=8)
    page.insert_text((40, 350), "Body and https://example.org must remain translatable.", fontsize=8)
    page.insert_text((40, 365), "Further body in the left column.", fontsize=8)
    page.insert_text((315, 170), "Acknowledgments before references.", fontsize=8)
    if heading:
        _heading(page, heading)
    for n in range(1, 5):
        _entry(page, n, 315, 240+(n-1)*40, citation=citation)
    page = doc.new_page(width=600, height=800)
    page.insert_text((295, 775), "7590", fontsize=8)
    page.insert_text((40, 50), "Continuation of the preceding citation.", fontsize=8)
    for n, x, y in ((5, 40, 80), (6, 40, 120), (7, 315, 50), (8, 315, 90)):
        _entry(page, n, x, y, citation=citation)
    if appendix:
        _heading(page, appendix, y=220)
        page.insert_text((315, 250), "Independent appendix body remains translatable.", fontsize=8)
    doc.new_page(width=600, height=800).insert_text((40, 100), "Later independent body.")
    return doc


@pytest.mark.parametrize("appendix", ["■ APPENDIX A", "▪ SUPPORTING INFORMATION", "Appendix A"])
@pytest.mark.parametrize("heading", ["■ REFERENCES", "References"])
def test_parenthesized_bibliography_is_bounded_and_preserves_columns(monkeypatch, appendix, heading):
    _layout(monkeypatch)
    with _document(appendix=appendix, heading=heading) as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary and boundary["page"] == 0 and boundary["column"] == "right"
        assert boundary["end"]["page"] == 1 and boundary["end"]["column"] == "right"
        assert not boundary.get("uncertain_pages")
        assert not boundary.get("uncertain_end")
        for number in range(1, 9):
            p = 0 if number <= 4 else 1
            assert boundary_intersects(boundary, p, doc[p].search_for(f"({number})")[0])
        assert boundary_intersects(boundary, 1, doc[1].search_for("Continuation")[0])
        for p, text in ((0, "Body and"), (0, "Acknowledgments"), (1, "Independent appendix")):
            assert not boundary_intersects(boundary, p, doc[p].search_for(text)[0])
        assert not reference_page_only(boundary, 1)
        assert reference_boxes(boundary, 2) == []


@pytest.mark.parametrize("heading", ["", "■ REFERENCES", "References"])
def test_parenthesized_instructions_and_urls_are_not_bibliographies(monkeypatch, heading):
    _layout(monkeypatch)
    with _document(heading=heading, citation=False) as doc:
        assert pdf_boundary(doc, JobControl(), lambda _: None) is None


@pytest.mark.parametrize("citation", [False, True])
@pytest.mark.parametrize("count", [1, 4])
def test_continuing_numbers_require_publication_evidence(monkeypatch, citation, count):
    _layout(monkeypatch)
    with _document() as doc:
        doc.delete_pages(1, 2)
        page = doc.new_page(width=600, height=800)
        for n in range(5, 5+count):
            _entry(page, n, 40, 100 + (n-5)*40, citation=citation)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary and boundary["page"] == 0
        assert bool(reference_boxes(boundary, 1)) == citation
        if not citation:
            assert boundary["uncertain_end"]["page"] == 1


@pytest.mark.parametrize("kind", ["figure", "table", "abandon"])
def test_decorated_title_inside_graphic_is_not_a_section(monkeypatch, kind):
    _layout(monkeypatch, kind)
    with _document() as doc:
        assert pdf_boundary(doc, JobControl(), lambda _: None) is None


def test_parenthesized_separate_label_joins_only_same_baseline():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 100), "(1)", fontsize=8)
        page.insert_text((70, 100), "Smith, A. Article.", fontsize=8)
        page.insert_text((40, 140), "(2)", fontsize=8)
        page.insert_text((315, 140), "Other column body.", fontsize=8)
        rows = _digital_lines(page)
        assert any(r["text"].startswith("(1)") and "Smith" in r["text"] for r in rows)
        assert not any("(2)" in r["text"] and "Other column" in r["text"] for r in rows)


def test_untitled_parenthesized_citations_require_real_publication_evidence(monkeypatch):
    from babeldoc.docvision.doclayout import OnnxModel
    prediction = SimpleNamespace(names={0: "plain text"}, boxes=[
        SimpleNamespace(cls=0, conf=.98, xyxy=[620, 462.8, 1160, 780])])
    monkeypatch.setattr(OnnxModel, "from_pretrained",
                        lambda: SimpleNamespace(predict=lambda *a, **k: [prediction]))
    with _document(heading="") as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary and boundary["evidence"] == "untitled-numbered-citations"
        assert boundary["column"] == "right"
        assert not boundary_intersects(boundary, 0, doc[0].search_for("Body and")[0])


def test_original_walnut_boundary_filter_and_source_pixels(monkeypatch):
    fixture = os.environ.get("LINGUAFLOAT_TEST_WALNUT_PDF")
    if not fixture or not Path(fixture).is_file():
        pytest.skip("Set LINGUAFLOAT_TEST_WALNUT_PDF to the original local PDF")
    # This optional integration test uses the real installed detector, never a download.
    from babeldoc.assets.assets import get_cache_file_path, verify_file, DOCLAYOUT_YOLO_DOCSTRUCTBENCH_IMGSZ1024ONNX_SHA3_256
    model = get_cache_file_path("doclayout_yolo_docstructbench_imgsz1024.onnx", "models")
    assert verify_file(model, DOCLAYOUT_YOLO_DOCSTRUCTBENCH_IMGSZ1024ONNX_SHA3_256), "Local model required"
    # Avoid the asset resolver's Windows asyncio loopback socket and download
    # fallback; construction and inference still use the real verified model.
    monkeypatch.setattr("babeldoc.docvision.doclayout.get_doclayout_onnx_model_path", lambda: model)
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page, PdfParagraph
    with fitz.open(fixture) as source, fitz.open() as rendered:
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
        assert boundary and boundary["page"] == 9 and boundary["column"] == "right"
        assert set(boundary["page_boxes"]) == {"9", "10"}
        assert boundary["y"] == pytest.approx(209.895, abs=.02)
        assert reference_page_only(boundary, 10)
        assert not boundary.get("uncertain_pages") and not boundary.get("uncertain_end")
        labels = []
        for number in (9, 10):
            page = source[number]
            rows = _digital_lines(page)
            paragraphs = []
            for row in rows:
                x0, y0, x1, y1 = row["box"]
                # Source lines stand in for engine text paragraphs here; page
                # folios are layout furniture, not translation paragraphs.
                if not (row["text"].strip().isdigit() and y0 >= page.rect.height * .94):
                    paragraphs.append(PdfParagraph(box=Box(x0, page.rect.height-y1, x1, page.rect.height-y0),
                                                   unicode=row["text"]))
                if label := re.match(r"^\((\d{1,2})\) ", row["text"]):
                    labels.append(int(label.group(1)))
                    assert boundary_intersects(boundary, number, row["box"])
            il_page = Page(page_number=number, cropbox=Cropbox(Box(0, 0, page.rect.width, page.rect.height)),
                           pdf_paragraph=paragraphs)
            filter_layout_paragraphs(il_page, boundary)
            kept = "\n".join(p.unicode for p in il_page.pdf_paragraph)
            assert not any(re.match(r"^\(\d{1,2}\) ", p.unicode) for p in il_page.pdf_paragraph)
            if number == 9:
                assert "ACKNOWLEDGMENTS" in kept and "ASSOCIATED CONTENT" in kept
            else:
                assert not kept
            target = rendered.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_text((40, 100), "TRANSLATED PREFIX")
            retain_pdf_tail(target, page, dict(boxes=reference_boxes(boundary, number)))
            for box in reference_boxes(boundary, number):
                clip = fitz.Rect(box) + (1, 1, -1, -1)
                assert target.get_pixmap(clip=clip).samples == page.get_pixmap(clip=clip).samples
            if number == 9:
                assert "TRANSLATED PREFIX" in target.get_text()
        assert labels == list(range(1, 41))

        # Controlled addition in memory: the original has no following appendix.
        # A new independent right-column section must not be swallowed by the
        # otherwise full-page continuation, nor may its following page be skipped.
        _heading(source[10], "■ APPENDIX A", x=319, y=620)
        source[10].insert_text((319, 650), "Independent appendix body.", fontsize=9)
        source.new_page().insert_text((40, 100), "Following appendix page.")
        with_appendix = pdf_boundary(source, JobControl(), lambda _: None)
        assert with_appendix["end"]["page"] == 10
        assert with_appendix["end"]["column"] == "right"
        assert not reference_page_only(with_appendix, 10)
        assert reference_boxes(with_appendix, 11) == []
        assert not boundary_intersects(with_appendix, 10, source[10].search_for("Independent appendix")[0])
        assert boundary_intersects(with_appendix, 10, source[10].search_for("(40)")[0])
