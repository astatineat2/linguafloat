"""Regressions for the November 18 review and Bifido boundary failures."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_references import (
    boundary_intersects, filter_layout_paragraphs, pdf_boundary,
    reference_boxes, retain_pdf_tail,
)


@pytest.fixture(autouse=True)
def local_layout(monkeypatch):
    from babeldoc.docvision.doclayout import OnnxModel
    prediction = SimpleNamespace(names={}, boxes=[])
    monkeypatch.setattr(OnnxModel, "from_pretrained",
                        lambda: SimpleNamespace(predict=lambda *a, **k: [prediction]))


def _citation(page, name, x, y):
    page.insert_text((x, y), f"{name}, A., & Brown, B. (2024). Microbial metabolites.", fontsize=7)
    page.insert_text((x + 12, y + 9), "Journal of Food Science, 12, 123-129. https://doi.org/example", fontsize=7)


def _review(right_x=308, leading_body=False):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    for i, text in enumerate(("Author contributions", "Funding", "Conflicts of interest",
                              "Appendix A. Supplementary data", "Data availability")):
        page.insert_text((38, 70 + i * 45), text, fontsize=8, fontname="hebo")
        page.insert_text((50, 85 + i * 45), "Independent prose to translate.", fontsize=8)
    page.insert_text((38, 340), "References", fontsize=8, fontname="hebo")
    _citation(page, "Able", 38, 360)
    _citation(page, "Baker", 38, 390)
    page.insert_text((38, 720), "Dodd, D., & Smith, A. (2017). A bacterial pathway", fontsize=7)
    page.insert_text((right_x + 12, 60), "Independent prose must not be protected." if leading_body else
                     "produces metabolites. Nature, 551, 648-652.", fontsize=7)
    _citation(page, "Ehrlich", right_x, 80)
    _citation(page, "Fang", right_x, 110)
    # Dense, overlapping line bboxes reproduce the old restoration expansion.
    for y in range(140, 725, 8):
        page.insert_text((right_x + 12, y), "Bibliographic continuation of the cited publication.", fontsize=7)
    return doc


@pytest.mark.parametrize("right_x", [308, 330])
def test_author_year_start_follows_columns_and_keeps_independent_prose(right_x):
    with _review(right_x) as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary and boundary["column"] == "both"
        for text in ("Dodd", "produces metabolites", "Ehrlich", "Fang", "Able", "Baker"):
            assert all(boundary_intersects(boundary, 0, box) for box in doc[0].search_for(text))
        for box in doc[0].search_for("Independent prose"):
            assert not boundary_intersects(boundary, 0, box)
        _assert_filter_and_restore(doc[0], boundary)


def test_author_year_evidence_cannot_swallow_unrelated_right_column_prefix():
    with _review(leading_body=True) as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary is None or not boundary_intersects(
            boundary, 0, doc[0].search_for("Independent prose must")[0])


def _paragraph(rect, text, height):
    from babeldoc.format.pdf.document_il import Box, PdfParagraph
    return PdfParagraph(box=Box(rect.x0, height - rect.y1, rect.x1, height - rect.y0), unicode=text)


def _assert_filter_and_restore(source, boundary):
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page
    height = source.rect.height
    prose = source.search_for("Independent prose to translate.")
    paragraphs = [_paragraph(box, "Independent prose to translate.", height) for box in prose]
    right = fitz.Rect(308, 53, 570, 730)
    paragraphs.append(_paragraph(right, "Bibliography", height))
    page = Page(page_number=source.number, cropbox=Cropbox(Box(0, 0, 600, height)),
                pdf_paragraph=paragraphs)
    errors = []
    filter_layout_paragraphs(page, boundary, lambda *args: errors.append(args))
    assert not errors
    assert [p.unicode for p in page.pdf_paragraph] == ["Independent prose to translate."] * len(prose)
    with fitz.open() as rendered:
        target = rendered.new_page(width=600, height=height)
        target.insert_text((50, 85), "TRANSLATED PROSE", fontsize=8)
        before = target.get_pixmap(clip=fitz.Rect(30, 50, 290, 300), matrix=fitz.Matrix(2, 2)).samples
        retain_pdf_tail(target, source, dict(boxes=reference_boxes(boundary, source.number)))
        assert target.get_pixmap(clip=fitz.Rect(30, 50, 290, 300), matrix=fitz.Matrix(2, 2)).samples == before
        for box in reference_boxes(boundary, source.number):
            clip = fitz.Rect(box) + (1, 1, -1, -1)
            assert target.get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2)).samples == source.get_pixmap(
                clip=clip, matrix=fitz.Matrix(2, 2)).samples


def _publisher_document(*, repeat=True, margin_y=763):
    from test_reference_batch_continuations import _bifido
    doc = _bifido(footer=False, fragment=False)
    for n in range(len(doc)):
        if repeat or n == 0:
            doc[n].insert_text((42, 34), "Journal of Microbiology", fontsize=12)
            doc[n].insert_text((480, 34), "Articles", fontsize=12)
            doc[n].insert_text((42, margin_y), "Journal of Microbiology | Volume 6 | 2021 | 100-120 | publisher.example", fontsize=7)
    return doc


def test_repeated_publisher_furniture_crossing_gutter_is_preserved_without_error():
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page
    with _publisher_document() as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary and boundary["end"]["page"] == 2
        for n in (0, 2):
            source = doc[n]
            furniture = [fitz.Rect(42, 21, 550, 38), fitz.Rect(42, 755, 338, 765)]
            paragraphs = [_paragraph(b, "Publisher metadata", source.rect.height) for b in furniture]
            text = "Methods body" if n == 0 else "We thank"
            prose = source.search_for(text)[0]
            paragraphs.append(_paragraph(prose, text, source.rect.height))
            page = Page(page_number=n, cropbox=Cropbox(Box(0, 0, source.rect.width, source.rect.height)),
                        pdf_paragraph=paragraphs)
            errors = []
            filter_layout_paragraphs(page, boundary, lambda *args: errors.append(args))
            assert not errors
            assert [p.unicode for p in page.pdf_paragraph] == [text]
            with fitz.open() as rendered:
                target = rendered.new_page(width=source.rect.width, height=source.rect.height)
                target.insert_text((prose.x0, prose.y1), "TRANSLATED PROSE", fontsize=7)
                retain_pdf_tail(target, source, dict(boxes=reference_boxes(boundary, n)))
                assert "TRANSLATED PROSE" in target.get_text()
                for box in furniture:
                    assert target.get_pixmap(clip=box).samples == source.get_pixmap(clip=box).samples


@pytest.mark.parametrize("repeat,margin_y", [(False, 763), (True, 600)])
def test_unique_or_body_text_is_not_promoted_to_publisher_metadata(repeat, margin_y):
    with _publisher_document(repeat=repeat, margin_y=margin_y) as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert not boundary.get("publisher_boxes", {}).get("0") if not repeat else all(
            box[1] < 100 or box[1] > 740 for box in boundary.get("publisher_boxes", {}).get("0", []))


def test_restore_does_not_expand_up_an_overlapping_column():
    with fitz.open() as source, fitz.open() as output:
        page = source.new_page(width=300, height=400)
        target = output.new_page(width=300, height=400)
        for y in range(40, 330, 8):
            page.insert_text((30, y), "Close-set source line", fontsize=8)
        target.insert_text((30, 55), "Translated body", fontsize=8)
        body = fitz.Rect(25, 35, 160, 65)
        before = target.get_pixmap(clip=body).samples
        retain_pdf_tail(target, page, dict(boxes=[[0, 240, 300, 400]]))
        assert target.get_pixmap(clip=body).samples == before
        refs = fitz.Rect(0, 250, 300, 400)
        assert target.get_pixmap(clip=refs).samples == page.get_pixmap(clip=refs).samples


def test_reference_projection_does_not_leave_hidden_searchable_prose():
    with fitz.open() as source, fitz.open() as output:
        page = source.new_page(width=300, height=400)
        target = output.new_page(width=300, height=400)
        page.insert_text((25, 30), "Journal running header", fontsize=8)
        page.insert_text((25, 100), "Source body must not leak", fontsize=8)
        page.insert_text((25, 260), "[1] Original reference", fontsize=8)
        target.insert_text((25, 100), "Translated body", fontsize=8)
        target.insert_text((25, 260), "Broken translated reference", fontsize=8)
        retain_pdf_tail(target, page, dict(boxes=[[0, 10, 300, 40], [0, 240, 300, 400]]))
        text = target.get_text()
        assert "Translated body" in text
        assert "[1] Original reference" in text
        assert "Source body must not leak" not in text
        assert "Broken translated reference" not in text


@pytest.mark.parametrize("folder,pages", [("e99ed9afc949", [18]), ("71a7dc6a4c8e", [12, 14])])
def test_saved_partial_reference_pages(monkeypatch, folder, pages):
    root = os.environ.get("LINGUAFLOAT_TEST_REFERENCE_EVIDENCE")
    if not root:
        pytest.skip("Set LINGUAFLOAT_TEST_REFERENCE_EVIDENCE to the saved six-quality evidence directory")
    from babeldoc.docvision.doclayout import OnnxModel
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page
    from linguafloat.document_references import _digital_lines
    model = Path.home() / ".cache/babeldoc/models/doclayout_yolo_docstructbench_imgsz1024.onnx"
    monkeypatch.setattr(OnnxModel, "from_pretrained", lambda: OnnxModel(str(model)))
    evidence = Path(root) / folder
    task = json.loads((evidence / "task.json").read_text(encoding="utf8"))
    issues = [json.loads(line) for line in next(evidence.glob("*.jsonl")).read_text(encoding="utf8").splitlines()]
    with fitz.open(task["source"]) as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["page"] == pages[0] and not boundary.get("uncertain_end")
        assert boundary["whole_pages"] == ([19, 20] if pages == [18] else [13])
        if pages == [12, 14]:
            assert boundary["end"]["page"] == 14 and boundary["end"]["column"] == "right"
        for n in pages:
            page = doc[n]
            h = page.rect.height
            body, refs = [], []
            for row in _digital_lines(page):
                rect = fitz.Rect(row["box"])
                x, y = rect.tl
                if n == 18 and 50 < y < 750:
                    dest = body if x < 298 and y < 338 else refs
                elif (n == 12 and 55 < y < 735 and (x < 290 or y < 375)
                      or n == 14 and x > 300 and 271.5 <= y < 750):
                    dest = body
                else:
                    continue
                dest.append((rect, row["text"]))
            for issue in issues:
                if issue["page"] == n + 1 and issue["stage"] == "参考文献边界":
                    x0, y0, x1, y1 = issue["box"]
                    refs.append((fitz.Rect(x0, h-y1, x1, h-y0), issue["id"]))
            assert body and len(refs) == {18: 136, 12: 1, 14: 2}[n]
            assert all(not boundary_intersects(boundary, n, rect) for rect, _ in body)
            assert all(boundary_intersects(boundary, n, rect) for rect, _ in refs)
            il = Page(page_number=n, cropbox=Cropbox(Box(0, 0, page.rect.width, h)),
                      pdf_paragraph=[_paragraph(r, text, h) for r, text in body + refs])
            filter_layout_paragraphs(il, boundary)
            assert [p.unicode for p in il.pdf_paragraph] == [text for _, text in body]
            with fitz.open() as output:
                target = output.new_page(width=page.rect.width, height=h)
                marker = body[0][0]
                target.insert_text((marker.x0, marker.y1-2), "TRANSLATED", fontsize=7)
                def pixels(p, clip):
                    return p.get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2)).samples
                before = pixels(target, marker)
                boxes = reference_boxes(boundary, n)
                retain_pdf_tail(target, page, dict(boxes=boxes))
                assert pixels(target, marker) == before
                for box in boxes:
                    clip = fitz.Rect(box) + (1, 1, -1, -1)
                    assert pixels(target, clip) == pixels(page, clip)
