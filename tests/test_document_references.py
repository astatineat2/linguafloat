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
        for _ in range(2):
            doc.new_page().insert_text((40, 100), "TRANSLATED BODY")
        doc.save(rendered)
    paths = assemble_layout_pdf(job, [0, 1, 2], rendered, tmp_path, {}, JobControl())
    with fitz.open(paths[0]) as mono, fitz.open(paths[1]) as dual:
        assert len(mono) == 3 and "Appendix A" in mono[2].get_text()
        assert "[1] First study" in mono[1].get_text()
        assert len(dual) == 3 and "Appendix A" in dual[2].get_text()


def test_word_boundary_keeps_reference_and_appendix_without_requests(tmp_path):
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
    mono = Document(outputs[0])
    assert translator.calls == 2
    assert mono.paragraphs[2].text == "References"
    assert mono.paragraphs[-1].text == "Appendix content"
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


def test_ppt_boundary_keeps_reference_and_appendix_slides(tmp_path):
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
    assert mono.slides[2].placeholders[1].text == "Later content"
    assert len(dual.slides) == 6
    assert translator.calls == 2


def test_missing_boundary_fails_before_translation(tmp_path):
    path = tmp_path / "paper.docx"
    doc = Document()
    doc.add_paragraph("Body text")
    doc.save(path)
    job = job_for(path, skip_references=True)
    translator = FakeTranslator(job)
    with pytest.raises(DocumentError, match="无法.*可靠确认"):
        translate_docx(job, translator, tmp_path, lambda _: None)
    assert translator.calls == 0


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
    from pathlib import Path
    candidates = list(Path("C:/Users/Astatine/Zotero/storage/3U8UUC6T").glob("*.pdf"))
    if not candidates:
        pytest.skip("User's Hobson PDF is not present")
    with fitz.open(candidates[0]) as source:
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
    assert boundary["page"] == 6
    assert boundary["column"] == "right"
    assert boundary["boxes"][0][0] > 280


def test_reading_pdf_skip_references_sends_only_prefix(tmp_path):
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
        store = JobStore(tmp_path / f"store-{skip}.sqlite")
        events = []
        try:
            execute(job, "unused", store, translator.control, events.append, translator)
        finally:
            store.close()
        counts.append(translator.calls)
        output = events[-1]
        with fitz.open(output["outputs"][0]) as mono, fitz.open(output["outputs"][1]) as dual:
            assert "Appendix A" in mono[-1].get_text()
            assert "Appendix A" in dual[-1].get_text()
            if skip:
                assert "[1] One article" in "".join(page.get_text() for page in mono)
                assert output["untranslated_regions"] == 0
                assert output["reference_boundary"]["page"] == 1
    assert counts == [3, 2]


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


def test_actual_scan_flow_preserves_tail_and_appendix(tmp_path):
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
        sent.append(text)
        return original(text)
    translator.translate = translate
    store = JobStore(tmp_path / "scan-flow.sqlite")
    events = []
    try:
        execute(job, "unused", store, translator.control, events.append, translator)
    finally:
        store.close()
    assert sent and all("Smith" not in text and "References" not in text and "Appendix" not in text for text in sent)
    assert events[-1]["untranslated_regions"] == 0
    with fitz.open(path) as source, fitz.open(events[-1]["outputs"][0]) as mono:
        assert len(mono) == len(source)
        assert mono[-1].get_pixmap().samples == source[-1].get_pixmap().samples
        clip = fitz.Rect(job.reference_boundary["boxes"][0])
        assert mono[0].get_pixmap(clip=clip).samples == source[0].get_pixmap(clip=clip).samples
