"""Unheaded, annotated bibliographies with PDF labels on separate lines."""
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_references import (
    _bibliographic_entry, _digital_lines, boundary_intersects, filter_layout_paragraphs, pdf_boundary,
    reference_boxes, reference_page_only, retain_pdf_tail,
)


@pytest.mark.parametrize("text,expected", [
    ("Šmejkal, L., González-Hernández, R. Spin order. Sci. Adv. 6, eaaz8809 (2020).", True),
    ("A. Author, Spin order. Phys. Rev. X 12, 031042 (2022).", True),
    ("Smith, L. supplied the apparatus in (2020).", False),
    ("Smith, L. Spin order. Newton 1, 100162.", False),
    ("Measure samples from (2020) and compare Journal measurements.", False),
])
def test_citation_evidence_requires_author_year_and_publication(text, expected):
    assert _bibliographic_entry(text) == expected


@pytest.mark.parametrize("body_x,body_y,joined", [(56, 100, True), (310, 100, False), (56, 112, False)])
def test_separate_label_only_joins_nearby_same_baseline(body_x, body_y, joined):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 100), "1.", fontsize=6)
        page.insert_text((body_x, body_y), "Smith, L. Spin order.", fontsize=6)
        rows = _digital_lines(page)
        assert any("1." in r["text"] and "Smith" in r["text"] for r in rows) == joined


def _entry(page, number, x, y, *, split_label=True, citation=True, note=False):
    label = f"{number}."
    text = "Smith, L., Brown, J. & Jones, T. Spin symmetry."
    if not citation:
        text = "Measure the sample and record the temperature."
    if split_label:
        page.insert_text((x, y), label, fontsize=6)
        page.insert_text((x + 16, y), text, fontsize=6)
    else:
        page.insert_text((x, y), label + "  " + text, fontsize=6)
    publication = "Sci. Adv. 6, eaaz8809 (2020)." if number == 3 else "Newton 1, 100162 (2025)."
    page.insert_text((x + 16, y + 9), publication if citation else "Repeat carefully.", fontsize=6)
    if note:
        page.insert_text((x + 16, y + 18), "Supplementary analysis establishes this result.",
                         fontsize=6, fontname="hebo")


def _layout(monkeypatch, variant="normal"):
    from babeldoc.docvision.doclayout import OnnxModel
    regions = [(0, [38, 363.5, 245, 520]), (0, [308, 43.5, 570, 200])]
    if variant == "figure":
        regions[0] = (1, regions[0][1])
    elif variant == "table":
        regions[0] = (2, regions[0][1])
    elif variant == "crossing_body":
        regions[0] = (0, [38, 290, 245, 520])
    elif variant == "missing":
        regions = []
    prediction = SimpleNamespace(names={0: "plain text", 1: "figure", 2: "table"},
        boxes=[SimpleNamespace(cls=cls, conf=.96, xyxy=[v * 2 for v in box]) for cls, box in regions])
    monkeypatch.setattr(OnnxModel, "from_pretrained", lambda: SimpleNamespace(predict=lambda *a, **k: [prediction]))


def _document(*, split_label=True, citation=True, gap=False, following=True):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((40, 300), "Research body above the bibliography.", fontsize=9)
    for n in range(1, 9):
        x, y = (40, 370 + (n - 1) * 35) if n <= 4 else (310, 50 + (n - 5) * 35)
        _entry(page, n + (1 if gap and n >= 5 else 0), x, y,
               split_label=split_label, citation=citation, note=n in (1, 6))
    if following:
        page = doc.new_page(width=600, height=800)
        for n in range(9, 17):
            x, y = (40, 50 + (n - 9) * 35) if n <= 12 else (310, 50 + (n - 13) * 35)
            _entry(page, n, x, y, split_label=split_label, note=n == 10)
        page.insert_text((310, 240), "Acknowledgements We thank our colleagues.", fontsize=6)
        page.insert_text((310, 255), "Supporting discussion must be translated.", fontsize=6)
        doc.new_page(width=600, height=800).insert_text((40, 100), "Appendix A: Experimental details")
    return doc


@pytest.mark.parametrize("split_label", [True, False])
def test_unheaded_columns_protect_annotations_and_resume_following_sections(monkeypatch, split_label):
    _layout(monkeypatch)
    with _document(split_label=split_label) as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary is not None
        assert boundary["page"] == 0 and boundary["column"] == "both"
        assert boundary["end"]["page"] == 1 and boundary["end"]["column"] == "right"
        for i in (0, 1):
            for text in ("Smith", "Newton", "Supplementary analysis"):
                assert all(boundary_intersects(boundary, i, rect) for rect in doc[i].search_for(text))
        assert not boundary_intersects(boundary, 0, doc[0].search_for("Research body")[0])
        assert not boundary_intersects(boundary, 1, doc[1].search_for("Acknowledgements")[0])
        assert not reference_page_only(boundary, 1)
        assert reference_boxes(boundary, 2) == []


@pytest.mark.parametrize("variant", ["figure", "table", "missing", "crossing_body", "ordinary_list", "gap"])
def test_unheaded_columns_require_citations_sequence_and_layout(monkeypatch, variant):
    _layout(monkeypatch, variant)
    with _document(citation=variant != "ordinary_list", gap=variant == "gap", following=False) as doc:
        assert pdf_boundary(doc, JobControl(), lambda _: None) is None


def test_unheaded_columns_filter_engine_paragraphs_and_preserve_pixels(monkeypatch):
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page, PdfParagraph
    _layout(monkeypatch)
    with _document() as source, fitz.open() as rendered:
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
        assert boundary is not None
        for i in (0, 1):
            paragraphs = []
            for text in ("Research body", "Smith", "Supplementary analysis", "Acknowledgements"):
                for rect in source[i].search_for(text):
                    paragraphs.append(PdfParagraph(box=Box(rect.x0, 800-rect.y1, rect.x1, 800-rect.y0), unicode=text))
            page = Page(page_number=i, cropbox=Cropbox(Box(0, 0, 600, 800)), pdf_paragraph=paragraphs)
            filter_layout_paragraphs(page, boundary)
            assert [p.unicode for p in page.pdf_paragraph] == (["Research body"] if i == 0 else ["Acknowledgements"])
            target = rendered.new_page(width=600, height=800)
            target.insert_text((40 if i == 0 else 310, 300 if i == 0 else 240), "Translated body or appendix", fontsize=6)
            retain_pdf_tail(target, source[i], dict(boxes=reference_boxes(boundary, i)))
            for box in reference_boxes(boundary, i):
                clip = fitz.Rect(box) + (1, 1, -1, -1)
                assert target.get_pixmap(clip=clip).samples == source[i].get_pixmap(clip=clip).samples
            assert "Translated body or appendix" in target.get_text()
