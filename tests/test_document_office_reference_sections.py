from types import SimpleNamespace

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree as ET
from pptx import Presentation
from pptx.util import Inches

from linguafloat.document_core import Cancelled, DocumentError, JobControl
from linguafloat.document_office import (
    A, parse, translate_docx, translate_pptx,
)


class Translator:
    def __init__(self, job, fail=None):
        self.job = job
        self.control = JobControl()
        self.sent = []
        self.context = []
        self.fail = fail

    def set_source_context(self, blocks):
        self.context = blocks

    def translate(self, text):
        self.sent.append(text)
        if text == self.fail:
            raise DocumentError("Invalid paragraph")
        return "Translated " + text

    def request(self, messages):
        root = parse(messages[-1]["content"].encode())
        for node in root:
            node.text = self.translate(node.text or "")
        return ET.tostring(root, encoding="unicode")


def job_for(path, **kwargs):
    values = dict(source=str(path), target="zh-Hans", skip_references=True,
                  reference_boundary={}, mono=True, bilingual=True, trial=False, pages="")
    values.update(kwargs)
    return SimpleNamespace(**values)


def add_references(document):
    document.add_heading("References", level=1)
    document.add_paragraph("[1] Smith, First study, Journal (2020).")
    document.add_paragraph("[2] Jones, Second study, Journal (2021).")


def add_slide(deck, title, text):
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = title
    slide.placeholders[1].text = text
    return slide


def test_word_resumes_appendix_and_context_without_duplicating_references(tmp_path):
    path = tmp_path / "sections.docx"
    doc = Document()
    doc.add_paragraph("Body")
    add_references(doc)
    doc.add_heading("Appendix A", level=1)
    doc.add_paragraph("Supporting results")
    doc.sections[0].header.paragraphs[0].text = "Running header"
    doc.save(path)
    job = job_for(path)
    translator = Translator(job)
    outputs, warnings = translate_docx(job, translator, tmp_path, lambda _: None)
    mono, dual = map(Document, outputs)
    assert [p.text for p in mono.paragraphs] == [
        "Translated Body", "References", "[1] Smith, First study, Journal (2020).",
        "[2] Jones, Second study, Journal (2021).", "Translated Appendix A",
        "Translated Supporting results",
    ]
    assert [p.text for p in dual.paragraphs].count("References") == 1
    assert len(dual.paragraphs) == 9
    assert "Supporting results" in [block.text for block in translator.context]
    assert not any("Smith" in block.text for block in translator.context)
    assert mono.sections[0].header.paragraphs[0].text == "Translated Running header"
    assert job.reference_boundary["paragraph"] == 2
    assert not any("起保留原文" in warning for warning in warnings)


@pytest.mark.parametrize("extension", ["docx", "pptx"])
def test_office_without_references_still_translates(tmp_path, extension):
    path = tmp_path / ("plain." + extension)
    doc = Document() if extension == "docx" else Presentation()
    if extension == "docx":
        doc.add_paragraph("Ordinary body")
    else:
        add_slide(doc, "Research", "Ordinary body")
    doc.save(path)
    job = job_for(path, reference_boundary={"page": 99})
    translator = Translator(job)
    convert = translate_docx if extension == "docx" else translate_pptx
    outputs, _ = convert(job, translator, tmp_path, lambda _: None)
    assert len(outputs) == 2
    assert "Ordinary body" in translator.sent
    assert not job.reference_boundary


def test_word_failed_appendix_keeps_source_paragraph_index_and_formula(tmp_path):
    path = tmp_path / "partial.docx"
    doc = Document()
    doc.add_paragraph("Body")
    add_references(doc)
    doc.add_heading("Appendix", level=1)
    doc.add_paragraph("FAIL")
    p = doc.add_paragraph("After failure")
    math = OxmlElement("m:oMath")
    run = OxmlElement("m:r")
    value = OxmlElement("m:t")
    value.text = "x=2"
    run.append(value)
    math.append(run)
    p._p.append(math)
    doc.save(path)
    job = job_for(path)
    translator = Translator(job, fail="FAIL")
    outputs, _ = translate_docx(job, translator, tmp_path, lambda _: None)
    assert translator.local_issues[0]["paragraph"] == 6
    for output in outputs:
        result = Document(output)
        failed = [p for p in result.paragraphs if p.text == "FAIL"]
        assert len(failed) == 1
        assert failed[0]._p.find(qn("w:pPr") + "/" + qn("w:pBdr")) is not None
        assert any(p.text == "Translated After failure" for p in result.paragraphs)
        assert len(result.element.findall(".//" + qn("m:oMath"))) == 1


def test_word_uncertain_end_does_not_swallow_plain_body(tmp_path):
    path = tmp_path / "uncertain.docx"
    doc = Document()
    add_references(doc)
    doc.add_paragraph("Unlabelled supporting discussion")
    doc.save(path)
    job = job_for(path)
    translator = Translator(job)
    _, warnings = translate_docx(job, translator, tmp_path, lambda _: None)
    assert translator.sent == ["Unlabelled supporting discussion"]
    assert any("不确定" in warning for warning in warnings)


@pytest.mark.parametrize("toc_kind", ["style", "hyperlink", "field"])
def test_word_contents_is_not_a_reference_section(tmp_path, toc_kind):
    path = tmp_path / "contents.docx"
    doc = Document()
    heading = doc.add_heading("References", level=1)
    if toc_kind == "style":
        heading._p.get_or_add_pPr().find(qn("w:pStyle")).set(qn("w:val"), "TOC1")
    elif toc_kind == "hyperlink":
        link = OxmlElement("w:hyperlink")
        link.set(qn("w:anchor"), "_Toc1")
        heading._p.append(link)
    else:
        run = OxmlElement("w:r")
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instruction = OxmlElement("w:instrText")
        instruction.text = ' TOC \\o "1-3" '
        run.extend([begin, instruction])
        heading._p.insert(1, run)
    doc.add_paragraph("[1] First topic")
    last = doc.add_paragraph("[2] Second topic")
    if toc_kind == "field":
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        last.add_run()._r.append(end)
    doc.add_heading("Methods", level=1)
    doc.add_paragraph("Body")
    doc.save(path)
    job = job_for(path)
    translator = Translator(job)
    translate_docx(job, translator, tmp_path, lambda _: None)
    assert "References" in translator.sent
    assert "[1] First topic" in translator.sent
    assert not job.reference_boundary


def test_word_reference_heading_cannot_borrow_evidence_from_appendix(tmp_path):
    path = tmp_path / "false-heading.docx"
    doc = Document()
    doc.add_heading("References", level=1)
    doc.add_paragraph("See online resources")
    doc.add_heading("Appendix", level=1)
    doc.add_paragraph("[1] First step")
    doc.add_paragraph("[2] Second step")
    doc.save(path)
    job = job_for(path)
    translator = Translator(job)
    translate_docx(job, translator, tmp_path, lambda _: None)
    assert "See online resources" in translator.sent
    assert not job.reference_boundary


def test_ppt_preserves_only_reference_paragraphs_and_resumes_appendix(tmp_path):
    path = tmp_path / "sections.pptx"
    deck = Presentation()
    add_slide(deck, "Research", "Body")
    mixed = add_slide(deck, "References", "[1] First\n[2] Second\nAppendix A\nSame slide appendix")
    mixed.shapes.add_textbox(Inches(7), Inches(1), Inches(2), Inches(2)).text = "Side discussion"
    add_slide(deck, "Supplementary material", "Later content")
    deck.save(path)
    job = job_for(path)
    translator = Translator(job)
    outputs, _ = translate_pptx(job, translator, tmp_path, lambda _: None)
    mono, dual = map(Presentation, outputs)
    assert mono.slides[1].shapes.title.text == "References"
    assert mono.slides[1].placeholders[1].text == (
        "[1] First\n[2] Second\nTranslated Appendix A\nTranslated Same slide appendix")
    assert mono.slides[1].shapes[-1].text == "Translated Side discussion"
    assert mono.slides[2].placeholders[1].text == "Translated Later content"
    assert len(dual.slides) == 6
    assert job.reference_boundary["page"] == 1
    assert not any(text.startswith("[1]") for text in translator.sent)
    assert "Later content" in [block.text for block in translator.context]


def test_ppt_textbox_reference_block_does_not_protect_other_shapes(tmp_path):
    path = tmp_path / "mixed.pptx"
    deck = Presentation()
    slide = add_slide(deck, "Research", "Main body")
    box = slide.shapes.add_textbox(Inches(1), Inches(3), Inches(5), Inches(2))
    box.text = "References\n[1] First\n[2] Second"
    original_body_pr = ET.tostring(box.text_frame._txBody.find(f"{{{A}}}bodyPr"))
    deck.save(path)
    job = job_for(path)
    translator = Translator(job)
    outputs, _ = translate_pptx(job, translator, tmp_path, lambda _: None)
    mono = Presentation(outputs[0])
    assert mono.slides[0].shapes.title.text == "Translated Research"
    assert mono.slides[0].placeholders[1].text == "Translated Main body"
    assert mono.slides[0].shapes[-1].text == box.text
    assert ET.tostring(mono.slides[0].shapes[-1].text_frame._txBody.find(f"{{{A}}}bodyPr")) == original_body_pr


def test_ppt_continuation_stops_at_new_slide_title_even_for_numbered_body(tmp_path):
    path = tmp_path / "continued.pptx"
    deck = Presentation()
    add_slide(deck, "References", "[1] Smith, Journal (2020).\n[2] Jones, Journal (2021).")
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(2)).text = (
        "[3] Brown, More research, Journal (2022).\n[4] Green, Final research, Journal (2023).")
    add_slide(deck, "Methods", "[1] First step\n[2] Second step")
    deck.save(path)
    job = job_for(path)
    translator = Translator(job)
    translate_pptx(job, translator, tmp_path, lambda _: None)
    assert not any("Journal" in text for text in translator.sent)
    assert "[1] First step" in translator.sent


def test_ppt_contents_and_selected_appendix_do_not_require_reference_page(tmp_path):
    path = tmp_path / "selection.pptx"
    deck = Presentation()
    add_slide(deck, "Contents", "References\n[1] First topic\n[2] Second topic")
    add_slide(deck, "References", "[1] First\n[2] Second")
    add_slide(deck, "Appendix", "Selected appendix")
    deck.save(path)
    job = job_for(path, pages="1,3")
    translator = Translator(job)
    outputs, _ = translate_pptx(job, translator, tmp_path, lambda _: None)
    assert "[1] First topic" in translator.sent
    assert "Selected appendix" in translator.sent
    assert job.reference_boundary["page"] == 1
    assert len(Presentation(outputs[1]).slides) == 5


def test_word_trial_counts_translated_appendix_paragraphs(tmp_path):
    path = tmp_path / "trial.docx"
    doc = Document()
    add_references(doc)
    doc.add_heading("Appendix", level=1)
    for index in range(12):
        doc.add_paragraph(f"Body {index}")
    doc.save(path)
    job = job_for(path, trial=True)
    translator = Translator(job)
    translate_docx(job, translator, tmp_path, lambda _: None)
    assert translator.sent == ["Appendix"] + [f"Body {index}" for index in range(7)]
    assert len(translator.context) == 8


@pytest.mark.parametrize("extension", ["docx", "pptx"])
def test_cancel_after_references_does_not_publish(tmp_path, extension):
    path = tmp_path / ("cancel." + extension)
    doc = Document() if extension == "docx" else Presentation()
    if extension == "docx":
        add_references(doc)
        doc.add_heading("Appendix", level=1)
    else:
        add_slide(doc, "References", "[1] First\n[2] Second")
        add_slide(doc, "Appendix", "Body")
    doc.save(path)
    job = job_for(path)
    translator = Translator(job)
    def cancel(text):
        raise Cancelled("stop")
    translator.translate = cancel
    convert = translate_docx if extension == "docx" else translate_pptx
    with pytest.raises(Cancelled):
        convert(job, translator, tmp_path, lambda _: None)
    assert not list(tmp_path.glob("cancel.*." + extension))


@pytest.mark.parametrize("extension", ["docx", "pptx"])
def test_disabling_skip_translates_reference_entries(tmp_path, extension):
    path = tmp_path / ("all." + extension)
    doc = Document() if extension == "docx" else Presentation()
    if extension == "docx":
        add_references(doc)
    else:
        add_slide(doc, "References", "[1] First\n[2] Second")
    doc.save(path)
    job = job_for(path, skip_references=False)
    translator = Translator(job)
    convert = translate_docx if extension == "docx" else translate_pptx
    convert(job, translator, tmp_path, lambda _: None)
    assert "References" in translator.sent
    assert any(text.startswith("[1]") for text in translator.sent)
    assert not job.reference_boundary


def test_word_auto_numbered_references_and_second_reference_section(tmp_path):
    path = tmp_path / "multiple.docx"
    doc = Document()
    doc.add_heading("References", level=1)
    for text in ("Smith, First article, 2020", "Jones, Second article, 2021"):
        paragraph = doc.add_paragraph(text)
        numbering = OxmlElement("w:numPr")
        num_id = OxmlElement("w:numId")
        num_id.set(qn("w:val"), "1")
        numbering.append(num_id)
        paragraph._p.get_or_add_pPr().append(numbering)
    doc.add_heading("Appendix A", level=1)
    doc.add_paragraph("Appendix text")
    add_references(doc)
    doc.add_heading("Supplementary information", level=1)
    doc.add_paragraph("Supplementary text")
    doc.save(path)
    job = job_for(path)
    translator = Translator(job)
    translate_docx(job, translator, tmp_path, lambda _: None)
    assert translator.sent == ["Appendix A", "Appendix text", "Supplementary information", "Supplementary text"]
    assert len(job.reference_boundary["paragraphs"]) == 6


def test_word_invalid_appendix_response_discards_cache_and_marks_original_index(tmp_path):
    path = tmp_path / "cached.docx"
    doc = Document()
    add_references(doc)
    doc.add_heading("Appendix", level=1)
    paragraph = doc.add_paragraph()
    paragraph.add_run("First span ")
    paragraph.add_run("second span").bold = True
    doc.add_paragraph("Following paragraph")
    doc.save(path)
    job = job_for(path)
    translator = Translator(job)
    discarded = []
    translator.store = SimpleNamespace(discard=discarded.append)
    translator.cache_key = lambda messages: "cached-appendix"
    translator.request = lambda messages: "<text><r id='wrong'>Invalid</r></text>"
    outputs, _ = translate_docx(job, translator, tmp_path, lambda _: None)
    assert discarded == ["cached-appendix"]
    assert translator.local_issues[0]["paragraph"] == 5
    for path in outputs:
        paragraphs = Document(path).paragraphs
        assert [p.text for p in paragraphs].count("First span second span") == 1
        assert any(p.text == "Translated Following paragraph" for p in paragraphs)


def test_ppt_failed_same_slide_appendix_keeps_paragraph_number(tmp_path):
    path = tmp_path / "partial.pptx"
    deck = Presentation()
    add_slide(deck, "References", "[1] First\n[2] Second\nAppendix\nFAIL\nFollowing paragraph")
    deck.save(path)
    job = job_for(path)
    translator = Translator(job, fail="FAIL")
    outputs, _ = translate_pptx(job, translator, tmp_path, lambda _: None)
    assert translator.local_issues[0]["paragraph"] == 5
    mono = Presentation(outputs[0])
    paragraphs = mono.slides[0].placeholders[1].text_frame.paragraphs
    assert paragraphs[3].text == "FAIL"
    assert b"CC2222" in ET.tostring(paragraphs[3]._p)
    assert paragraphs[4].text == "Translated Following paragraph"


def test_ppt_reference_title_can_use_next_slide_evidence(tmp_path):
    path = tmp_path / "title-page.pptx"
    deck = Presentation()
    title = deck.slides.add_slide(deck.slide_layouts[0])
    title.shapes.title.text = "References"
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(2)).text = "[1] First\n[2] Second"
    add_slide(deck, "Appendix", "After references")
    deck.save(path)
    job = job_for(path)
    translator = Translator(job)
    translate_pptx(job, translator, tmp_path, lambda _: None)
    assert translator.sent == ["Appendix", "After references"]
    assert job.reference_boundary["page"] == 0


def test_ppt_clear_end_keeps_wrapped_reference_before_appendix(tmp_path):
    path = tmp_path / "wrapped.pptx"
    deck = Presentation()
    add_slide(deck, "References", "[1] First\nWrapped citation text\n[2] Second\nAppendix\nAfter references")
    deck.save(path)
    job = job_for(path)
    translator = Translator(job)
    translate_pptx(job, translator, tmp_path, lambda _: None)
    assert translator.sent == ["Appendix", "After references"]


def test_ppt_reference_title_does_not_claim_separate_numbered_body_box(tmp_path):
    path = tmp_path / "side-list.pptx"
    deck = Presentation()
    slide = add_slide(deck, "References", "[1] First\n[2] Second")
    side = slide.shapes.add_textbox(Inches(7), Inches(1), Inches(2), Inches(2))
    side.text = "[1] First instruction\n[2] Second instruction"
    deck.save(path)
    job = job_for(path)
    translator = Translator(job)
    outputs, _ = translate_pptx(job, translator, tmp_path, lambda _: None)
    mono = Presentation(outputs[0])
    assert mono.slides[0].placeholders[1].text == "[1] First\n[2] Second"
    assert mono.slides[0].shapes[-1].text == (
        "Translated [1] First instruction\nTranslated [2] Second instruction")
