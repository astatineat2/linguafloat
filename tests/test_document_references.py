import json
import os
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from docx import Document
from pptx import Presentation

from linguafloat.document_core import DocumentError, JobControl, JobStore
from linguafloat.document_office import W, translate_docx, translate_pptx
from linguafloat.document_pdf import assemble_layout_pdf
from linguafloat.document_scan_layout import translate_scan_page
from linguafloat.document_references import is_heading, numbered_evidence, pdf_boundary, require_boundary
from linguafloat.document_references import filter_layout_paragraphs, retain_pdf_tail
from test_document_core import job_for
from test_document_formats import FakeTranslator
from linguafloat.document_worker import execute


@pytest.mark.parametrize("heading", ["References", "BIBLIOGRAPHY", "参考文献", "参考资料"])
def test_heading_and_citations(heading):
    assert is_heading(heading)
    assert not is_heading(f"The {heading} are discussed in [1].")
    assert numbered_evidence(["[1] One", "[2] Two"])
    assert not numbered_evidence(["[1] One", "[4] Four"])


@pytest.mark.parametrize("variant,accepted", [
    ("bibliography", True), ("ordinary_list", False), ("no_years", False),
    ("broken_numbers", False), ("figure", False), ("table", False),
    ("no_layout_evidence", False), ("body_in_same_block", False),
    ("footnotes", False), ("early_page", False),
])
def test_untitled_superscript_bibliography_needs_independent_evidence(monkeypatch, variant, accepted):
    from babeldoc.docvision.doclayout import OnnxModel
    names = {0: "plain text", 1: "figure", 2: "table"}
    label = {"figure": 1, "table": 2}.get(variant, 0)
    box = SimpleNamespace(cls=label, conf=.96, xyxy=[78, 870 if variant == "body_in_same_block" else 984, 1110, 1230])
    prediction = SimpleNamespace(names=names, boxes=[] if variant == "no_layout_evidence" else [box])
    monkeypatch.setattr(OnnxModel, "from_pretrained", lambda: SimpleNamespace(predict=lambda *a, **k: [prediction]))
    with fitz.open() as doc:
        if variant != "early_page":
            doc.new_page()
        page = doc.new_page()
        page.insert_text((40, 450), "Acknowledgments and research body must be translated.", fontsize=11)
        for index in range(1, 6):
            n = 8 if variant == "broken_numbers" and index == 3 else index
            y = 500 + (index - 1) * 25
            page.insert_text((40, y - 3), str(n), fontsize=9 if variant == "ordinary_list" else 6)
            content = (f" A. Author, Research title, Journal {index}, 123 (2020)."
                       if variant != "footnotes" else " A. Author supplied the apparatus in (2020).")
            if variant == "no_years":
                content = " An ordinary numbered instruction without citation data."
            page.insert_text((45, y), content, fontsize=9)
        if variant == "early_page":
            for _ in range(3):
                doc.new_page()
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert bool(boundary) == accepted
        if accepted:
            assert boundary["evidence"] == "untitled-superscript-citations"
            assert boundary["page"] == 1 and 485 < boundary["y"] < 500
            assert boundary["column"] == "full"


def test_real_bidinosti_untitled_references_preserve_acknowledgments():
    fixture = os.environ.get("LINGUAFLOAT_TEST_BIDINOSTI_PDF")
    if not fixture or not Path(fixture).is_file():
        pytest.skip("Set LINGUAFLOAT_TEST_BIDINOSTI_PDF to an optional local fixture")
    with fitz.open(fixture) as source:
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
        assert boundary and boundary["page"] == 14
        assert boundary["y"] == pytest.approx(540.431, abs=.02)
        assert boundary["evidence"] == "untitled-superscript-citations"
        prefix = source[14].get_text(clip=fitz.Rect(0, 0, source[14].rect.width, boundary["y"]))
        assert "ACKNOWLEDGMENTS" in prefix and "Council of Canada" in prefix


def test_pdf_boundary_ignores_contents_and_preserves_appendix(tmp_path):
    path = tmp_path / "sample.pdf"
    with fitz.open() as doc:
        toc = doc.new_page()
        toc.insert_text((40, 100), "Contents")
        toc.insert_text((40, 135), "References")
        toc.insert_text((40, 155), "........ 3")
        toc.insert_text((40, 175), "Appendix ........ 4")
        body = doc.new_page()
        body.insert_text((40, 100), "Research paragraph with a citation [1].")
        body.insert_text((40, 300), "References")
        body.insert_text((40, 330), "[1] First study")
        body.insert_text((40, 348), "[2] Second study")
        doc.new_page().insert_text((40, 100), "Appendix A")
        doc.save(path)
    with fitz.open(path) as doc:
        boundary = require_boundary(pdf_boundary(doc, JobControl(), lambda _: None), "PDF")
    assert boundary["page"] == 1 and boundary["y"] > 280
    job = job_for(path, pdf_mode="layout", skip_references=True)
    job.reference_boundary = boundary
    rendered = tmp_path / "rendered.pdf"
    with fitz.open() as doc:
        for _ in range(3):
            doc.new_page().insert_text((40, 100), "TRANSLATED BODY")
        doc.save(rendered)
    paths = assemble_layout_pdf(job, [0, 1, 2], rendered, tmp_path, {}, JobControl())
    with fitz.open(paths[0]) as mono, fitz.open(paths[1]) as dual:
        assert len(mono) == 3 and "TRANSLATED BODY" in mono[2].get_text()
        assert "[1] First study" in mono[1].get_text()
        assert len(dual) == 3 and "TRANSLATED BODY" in dual[2].get_text()


def test_word_boundary_keeps_references_and_translates_appendix(tmp_path):
    path = tmp_path / "paper.docx"
    document = Document()
    document.add_paragraph("References")  # Contents entry is not a heading.
    document.add_paragraph("Research text with [1] citation.")
    document.add_heading("References", 1)
    document.add_paragraph("[1] First reference")
    document.add_paragraph("[2] Second reference")
    document.add_heading("Appendix", 1)
    document.add_paragraph("Appendix content")
    document.save(path)
    job = job_for(path, skip_references=True)
    translator = FakeTranslator(job)
    outputs, _ = translate_docx(job, translator, tmp_path, lambda _: None)
    mono, dual = map(Document, outputs)
    assert translator.calls == 4
    assert mono.paragraphs[2].text == "References"
    assert mono.paragraphs[3].text == "[1] First reference"
    assert mono.paragraphs[4].text == "[2] Second reference"
    assert mono.paragraphs[-2].text == "译文 Appendix"
    assert mono.paragraphs[-1].text == "译文 Appendix content"
    assert [p.text for p in dual.paragraphs].count("[1] First reference") == 1
    assert [p.text for p in dual.paragraphs][-2:] == ["Appendix content", "译文 Appendix content"]
    assert job.reference_boundary["paragraph"] == 3


def test_word_heading_with_auto_numbered_entries_is_reliable():
    from linguafloat.document_references import office_boundary
    from lxml import etree as ET
    root = ET.Element(f"{{{W}}}body")
    heading = ET.SubElement(root, f"{{{W}}}p")
    ppr = ET.SubElement(heading, f"{{{W}}}pPr")
    ET.SubElement(ppr, f"{{{W}}}pStyle", attrib={f"{{{W}}}val": "Heading1"})
    ET.SubElement(ET.SubElement(heading, f"{{{W}}}r"), f"{{{W}}}t").text = "References"
    paragraphs = [heading]
    for text in ("Author, Journal, 2020", "Author, Journal, 2021"):
        paragraph = ET.SubElement(root, f"{{{W}}}p")
        ppr = ET.SubElement(paragraph, f"{{{W}}}pPr")
        num = ET.SubElement(ppr, f"{{{W}}}numPr")
        ET.SubElement(num, f"{{{W}}}ilvl", attrib={f"{{{W}}}val": "0"})
        ET.SubElement(num, f"{{{W}}}numId", attrib={f"{{{W}}}val": "2"})
        ET.SubElement(ET.SubElement(paragraph, f"{{{W}}}r"), f"{{{W}}}t").text = text
        paragraphs.append(paragraph)
    assert office_boundary(paragraphs, W, structured=True) == 0


def test_ppt_boundary_keeps_references_and_translates_appendix_slides(tmp_path):
    path = tmp_path / "slides.pptx"
    deck = Presentation()
    for title, lines in (("Research", ["Body text"]),
                         ("References", ["[1] First", "[2] Second"]),
                         ("Appendix", ["Later content"])):
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = "\n".join(lines)
    deck.save(path)
    job = job_for(path, skip_references=True)
    translator = FakeTranslator(job)
    outputs, _ = translate_pptx(job, translator, tmp_path, lambda _: None)
    mono, dual = map(Presentation, outputs)
    assert job.reference_boundary["page"] == 1
    assert mono.slides[1].shapes.title.text == "References"
    assert mono.slides[1].placeholders[1].text == "[1] First\n[2] Second"
    assert mono.slides[2].shapes.title.text == "译文 Appendix"
    assert mono.slides[2].placeholders[1].text == "译文 Later content"
    assert len(dual.slides) == 6
    assert dual.slides[4].placeholders[1].text == "Later content"
    assert dual.slides[5].placeholders[1].text == "译文 Later content"
    assert translator.calls == 4


def test_word_missing_boundary_continues_translation(tmp_path):
    path = tmp_path / "paper.docx"
    doc = Document()
    doc.add_paragraph("Body text")
    doc.save(path)
    job = job_for(path, skip_references=True)
    translator = FakeTranslator(job)
    outputs, warnings = translate_docx(job, translator, tmp_path, lambda _: None)
    assert translator.calls == 1
    assert Document(outputs[0]).paragraphs[0].text == "译文 Body text"
    assert not job.reference_boundary
    assert any("未可靠识别参考文献范围" in warning for warning in warnings)


def test_scanned_boundary_skips_regions_without_api(tmp_path):
    source_path = tmp_path / "scan.pdf"
    with fitz.open() as source:
        page = source.new_page(width=300, height=400)
        page.insert_text((30, 70), "Body")
        page.insert_text((30, 220), "References")
        page.insert_text((30, 240), "[1] First")
        page.insert_text((30, 260), "[2] Second")
        source.save(source_path)
    job = job_for(source_path, skip_references=True)
    job.reference_boundary = dict(page=0, y=210, column="full", boxes=[[0, 210, 300, 400]])
    translator = FakeTranslator(job)
    regions = [dict(box=[30, 215, 180, 270], text="References [1] First [2] Second", reason="")]
    analyzer = SimpleNamespace(regions=lambda *_: regions)
    with fitz.open(source_path) as source:
        result, retained, placed = translate_scan_page(source, 0, "scan_image", analyzer, "digest",
                                                       translator, lambda _: None)
        with result:
            assert "References" in result[0].get_text()
    assert not retained and not placed and translator.calls == 0


def test_actual_ocr_finds_scanned_references_and_ignores_figure(tmp_path):
    path = tmp_path / "raster.pdf"
    with fitz.open() as digital, fitz.open() as scanned:
        page = digital.new_page(width=595, height=842)
        page.insert_text((45, 110), "Research text with citations [1] and [2].", fontsize=15)
        page.insert_text((45, 440), "References", fontsize=18)
        page.insert_text((45, 475), "[1] Smith, Research article, Journal (2021).", fontsize=14)
        page.insert_text((45, 506), "[2] Brown, Another article, Journal (2022).", fontsize=14)
        raster = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
        out = scanned.new_page(width=595, height=842)
        out.insert_image(out.rect, stream=raster)
        scanned.save(path)
    with fitz.open(path) as source:
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
    assert boundary and boundary["page"] == 0 and boundary["scanned"]
    assert 410 < boundary["y"] < 445


def test_tail_replacement_does_not_remove_crossing_image():
    from PIL import Image
    import io
    with fitz.open() as original, fitz.open() as rendered:
        original.new_page(width=300, height=400).draw_rect(fitz.Rect(0, 210, 300, 400), fill=(1, 0, 0))
        page = rendered.new_page(width=300, height=400)
        image = Image.new("RGB", (300, 400), (0, 200, 0))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page.insert_image(page.rect, stream=buffer.getvalue())
        boundary = dict(boxes=[[0, 210, 300, 400]])
        retain_pdf_tail(page, original[0], boundary)
        pixels = page.get_pixmap()
        assert pixels.pixel(100, 100) == (0, 200, 0)
        assert pixels.pixel(100, 300) == (255, 0, 0)


def test_engine_filter_preserves_left_column_and_excludes_tail():
    from babeldoc.format.pdf.document_il import Box, Cropbox, PdfParagraph, Page
    body = PdfParagraph(box=Box(25, 50, 270, 500), unicode="Research body")
    reference = PdfParagraph(box=Box(320, 80, 565, 450), unicode="[1] Reference entry")
    page = Page(page_number=2, cropbox=Cropbox(Box(0, 0, 595, 842)), pdf_paragraph=[body, reference])
    boundary = dict(page=2, boxes=[[297, 300, 595, 842]])
    filter_layout_paragraphs(page, boundary)
    assert page.pdf_paragraph == [body]
    page.page_number = 3
    filter_layout_paragraphs(page, boundary)
    assert not page.pdf_paragraph


def test_real_two_column_boundary():
    fixture = os.environ.get("LINGUAFLOAT_TEST_HOBSON_PDF")
    if not fixture or not Path(fixture).is_file():
        pytest.skip("Set LINGUAFLOAT_TEST_HOBSON_PDF to an optional local fixture")
    with fitz.open(fixture) as source:
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
    assert boundary["page"] == 6
    assert boundary["column"] == "right"
    assert boundary["boxes"][0][0] > 280


@pytest.mark.parametrize("variant,conflict", [
    ("repeated_header", False),
    ("unique_header", True),
    ("different_position", True),
    ("body_above_heading", True),
    ("body_crossing_heading", True),
    ("repeated_body", False),
])
def test_column_evidence_only_excludes_confirmed_margin_headers(monkeypatch, variant, conflict):
    from babeldoc.docvision.doclayout import OnnxModel
    from linguafloat.document_references import (
        _digital_lines, _is_repeated_margin_line, boundary_intersects,
    )
    model = SimpleNamespace(predict=lambda *a, **k: [SimpleNamespace(names={}, boxes=[])])
    monkeypatch.setattr(OnnxModel, "from_pretrained", lambda: model)
    with fitz.open() as document:
        for index in range(2):
            page = document.new_page()
            if variant != "unique_header" or index == 1:
                y = 135 if variant == "repeated_body" else 35
                x = 460 if variant == "different_position" and not index else 500
                page.insert_text((x, y), "A. Author et al", fontsize=9)
            page.insert_text((40, 100), "Research paragraph above the references.")
            if index == 1:
                if variant in {"body_above_heading", "body_crossing_heading"}:
                    page.insert_text((340, 160), "Other column research text.")
                if variant == "body_crossing_heading":
                    page.insert_text((340, 340), "Research continues below the heading.")
                page.insert_text((40, 300), "References")
                page.insert_text((40, 330), "[1] First study")
                page.insert_text((40, 350), "[2] Second study")
        if conflict:
            boundary = pdf_boundary(document, JobControl(), lambda _: None)
            if variant in {"body_above_heading", "body_crossing_heading"}:
                assert boundary["uncertain_pages"] == [1]
            else:
                assert boundary["page"] == 1
        else:
            boundary = pdf_boundary(document, JobControl(), lambda _: None)
            assert boundary["page"] == 1 and boundary["column"] == "full"
            assert boundary["boxes"][0][0] == 0
        if variant == "repeated_body":
            rows = [row for row in _digital_lines(document[1]) if row["text"] == "A. Author et al"]
            assert len(rows) == 1
            assert not _is_repeated_margin_line(document, 1, rows[0])
            assert not boundary_intersects(boundary, 1, rows[0]["box"])


def test_real_jia_header_does_not_block_references_and_prefix_is_kept():
    from babeldoc.format.pdf.document_il import Box, Cropbox, PdfParagraph, Page
    fixture = os.environ.get("LINGUAFLOAT_TEST_JIA_PDF")
    if not fixture or not Path(fixture).is_file():
        pytest.skip("Set LINGUAFLOAT_TEST_JIA_PDF to an optional local fixture")
    with fitz.open(fixture) as source:
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
        assert boundary["page"] == 6 and boundary["column"] == "full"
        assert boundary["y"] == pytest.approx(208.926, abs=.01)
        height = source[6].rect.height
        paragraphs = []
        for block in source[6].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                x0, y0, x1, y1 = line["bbox"]
                paragraphs.append(PdfParagraph(box=Box(x0, height-y1, x1, height-y0),
                    unicode="".join(span["text"] for span in line["spans"])))
        page = Page(page_number=6, cropbox=Cropbox(Box(0, 0, source[6].rect.width, height)),
                    pdf_paragraph=paragraphs)
        filter_layout_paragraphs(page, boundary)
        kept = "\n".join(p.unicode for p in page.pdf_paragraph)
        assert "Acknowledgments" in kept and "ORCID" in kept
        assert "Z-L Jia et al" in kept  # Exclusion is for column evidence only.
        assert "References" not in kept
        assert not any(line.lstrip().startswith(("[1]", "[2]", "[39]")) for line in kept.splitlines())


def test_reading_pdf_skip_references_translates_prefix_and_appendix(tmp_path):
    source_path = tmp_path / "reading.pdf"
    with fitz.open() as source:
        source.new_page().insert_text((40, 100), "Research body before references.")
        page = source.new_page()
        page.insert_text((40, 100), "More research body text.")
        page.insert_text((40, 300), "References")
        page.insert_text((40, 325), "[1] One article")
        page.insert_text((40, 345), "[2] Another article")
        source.new_page().insert_text((40, 100), "Appendix A data and description.")
        source.save(source_path)
    counts = []
    for skip in (False, True):
        job = job_for(source_path, pdf_mode="reading", skip_references=skip,
                      bilingual_layout="alternating")
        job.job_id = f"comparison-{skip}"
        translator = FakeTranslator(job)
        sent = []
        original_translate = translator.translate
        def capture(text):
            sent.append(text)
            return original_translate(text)
        translator.translate = capture
        store = JobStore(tmp_path / f"store-{skip}.sqlite")
        events = []
        try:
            execute(job, "unused", store, translator.control, events.append, translator)
        finally:
            store.close()
        counts.append(translator.calls)
        assert any("Appendix A" in text for text in sent)
        if skip:
            assert all("[1] One article" not in text and "[2] Another article" not in text
                       and "References" not in text for text in sent)
        output = events[-1]
        with fitz.open(output["outputs"][0]) as mono, fitz.open(output["outputs"][1]) as dual:
            assert "Appendix A" in mono[-1].get_text()
            assert "Appendix A" in dual[-1].get_text()
            if skip:
                assert "[1] One article" in "".join(page.get_text() for page in mono)
                assert output["untranslated_regions"] == 0
                assert output["reference_boundary"]["page"] == 1
    assert counts == [3, 3]


def test_running_reference_header_is_not_a_chapter(tmp_path):
    path = tmp_path / "headers.pdf"
    with fitz.open() as document:
        for index in range(2):
            page = document.new_page()
            page.insert_text((40, 45), "References")
            page.insert_text((40, 140), "[1] A numbered example in the body")
            page.insert_text((40, 165), "[2] Another numbered example in the body")
            if index:
                page.insert_text((40, 400), "References")
                page.insert_text((40, 430), "[1] First reference")
                page.insert_text((40, 455), "[2] Second reference")
        document.save(path)
    with fitz.open(path) as document:
        boundary = pdf_boundary(document, JobControl(), lambda _: None)
    assert boundary["page"] == 1 and boundary["y"] > 380


def test_actual_scan_flow_preserves_references_and_translates_appendix(tmp_path):
    path = tmp_path / "scan-with-references.pdf"
    with fitz.open() as digital, fitz.open() as scan:
        page = digital.new_page()
        page.insert_text((45, 110), "The experiment measures magnetic field stability.", fontsize=14)
        page.insert_text((45, 440), "References", fontsize=18)
        page.insert_text((45, 475), "[1] Smith, Research article, Journal (2021).", fontsize=14)
        page.insert_text((45, 506), "[2] Brown, Another article, Journal (2022).", fontsize=14)
        digital.new_page().insert_text((45, 110), "Appendix supporting data.", fontsize=14)
        for page in digital:
            out = scan.new_page(width=page.rect.width, height=page.rect.height)
            out.insert_image(out.rect, stream=page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png"))
        scan.save(path)
    job = job_for(path, pdf_mode="layout", skip_references=True)
    translator = FakeTranslator(job)
    sent = []
    original = translator.translate
    def translate(text):
        sent.append((translator.location, text))
        return original(text)
    translator.translate = translate
    store = JobStore(tmp_path / "scan-flow.sqlite")
    events = []
    try:
        execute(job, "unused", store, translator.control, events.append, translator)
    finally:
        store.close()
    assert sent and all("Smith" not in text and "Brown" not in text and "References" not in text
                        for _, text in sent)
    assert any(location.startswith("扫描 PDF 第 2 页，") and text.strip() for location, text in sent)
    result = events[-1]
    assert result["type"] == "finished"
    folder = Path(result["outputs"][0]).parent
    report = json.loads((folder / "layout-report.json").read_text(encoding="utf-8"))
    assert any(region["page"] == 2 for region in report["paragraphs"])
    assert result["reference_boundary"]["end"]["page"] == 1
    regions = [(int(page) + 1, region) for page, items in report["untranslated_regions"].items()
               for region in items]
    log_path = folder / "未翻译问题.jsonl"
    issues = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()] if log_path.is_file() else []
    assert result["untranslated_regions"] == len(regions) == len(issues)
    assert {issue["id"] for issue in issues} == {region["id"] for _, region in regions}
    with fitz.open(path) as source, fitz.open(result["outputs"][0]) as mono:
        assert len(mono) == len(source)
        assert "译文" in "".join(mono[1].get_text().split())
        for number, page in enumerate(mono, 1):
            markers = [drawing for drawing in page.get_drawings()
                       if drawing.get("color") == pytest.approx((.85, .05, .05), abs=.001)]
            assert len(markers) == sum(page_number == number for page_number, _ in regions)
        for page, region in regions:
            issue = next(item for item in issues if item["id"] == region["id"])
            assert region["status"] == issue["status"] == "untranslated"
            assert region["marked"] and issue["marked"]
            assert issue["page"] == page
            assert issue["box"] == region["box"]
            assert issue["reason"] == region["reason"] and region["reason"]
            assert region["id"] in "".join(mono[page - 1].get_text().split())
            outline = (fitz.Rect(region["box"]) + (-1, -1, 1, 1)) & mono[page - 1].rect
            assert any(tuple(drawing["rect"]) == pytest.approx(tuple(outline), abs=.01)
                       and drawing.get("color") == pytest.approx((.85, .05, .05), abs=.001)
                       for drawing in mono[page - 1].get_drawings())
        for box in job.reference_boundary["page_boxes"]["0"]:
            clip = fitz.Rect(box)
            assert mono[0].get_pixmap(clip=clip).samples == source[0].get_pixmap(clip=clip).samples


def test_controlled_scan_recognition_sends_complete_appendix_after_references(tmp_path):
    path = tmp_path / "controlled-appendix.pdf"
    appendix_text = "Appendix A\nComplete supporting explanation."
    regions = []
    with fitz.open() as digital, fitz.open() as scan:
        for texts in (("References", "[1] First study", "[2] Second study"), appendix_text.splitlines()):
            page = digital.new_page(width=400, height=260)
            lines = []
            for index, text in enumerate(texts):
                page.insert_text((40, 100 + index * 30), text, fontsize=12)
                lines.append(dict(box=list(page.search_for(text)[0]), text=text,
                                  confidence=1, horizontal=True))
            box = fitz.Rect(lines[0]["box"])
            for line in lines[1:]:
                box |= fitz.Rect(line["box"])
            regions.append(dict(box=list(box + (-2, -2, 2, 2)), text="\n".join(texts),
                                lines=lines, reason="", label="plain text"))
            raster = page.get_pixmap(matrix=fitz.Matrix(3, 3)).tobytes("png")
            target = scan.new_page(width=400, height=260)
            target.insert_image(target.rect, stream=raster)
        scan.save(path)
    job = job_for(path, pdf_mode="layout", skip_references=True)
    job.reference_boundary = dict(page=0, boxes=[[0, 70, 400, 260]], whole_pages=[],
                                  page_boxes={"0": [[0, 70, 400, 260]], "1": [[0, 0, 400, 80]]},
                                  end=dict(page=1, y=80, column="full", split_x=None))
    translator = FakeTranslator(job)
    sent = []
    def translate(text):
        sent.append(text)
        return "附录译文"
    translator.translate = translate
    analyzer = SimpleNamespace(regions=lambda page, *_: [regions[page.number]])
    with fitz.open(path) as source:
        for number in range(2):
            output, retained, placed = translate_scan_page(
                source, number, "scan_image", analyzer, "controlled", translator, lambda _: None)
            with output:
                assert not retained
                if number == 0:
                    assert not placed and not sent
                    assert output[0].get_pixmap().samples == source[0].get_pixmap().samples
                else:
                    assert sent == [appendix_text]
                    assert len(placed) == 1 and placed[0]["page"] == 2
                    assert placed[0]["text"] == "附录译文"
                    assert "附录译文" in output[0].get_text().replace(" ", "")
