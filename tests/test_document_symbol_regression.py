from types import SimpleNamespace as NS
import numpy as np
import pytest
from babeldoc.format.pdf.document_il import Box, Page, PdfCurve, PdfFont, PdfLine, PdfParagraph, PdfParagraphComposition, PdfStyle
from test_document_geometry import char
from linguafloat.document_geometry import attach_formula_rules


@pytest.mark.parametrize("y", [37.5, 39.0, 48.5])
def test_single_italic_variable_and_its_rule_keep_one_owner(y):
    glyph = char("a", Box(22, 40, 27, 48), 10)
    paragraph = PdfParagraph(box=Box(10, 38, 110, 50), xobj_id=0, pdf_style=PdfStyle(font_size=10),
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[glyph]))])
    glyph.xobj_id = 0
    rule = PdfCurve(box=Box(22, y, 27, y), xobj_id=0, ctm=[1, 0, 0, 1, 0, 0])
    page = Page(pdf_font=[PdfFont(font_id="body", name="Times-Italic")], pdf_paragraph=[paragraph], pdf_curve=[rule])
    assert attach_formula_rules(page) == 1
    formula = paragraph.pdf_paragraph_composition[0].pdf_formula
    assert formula.pdf_character == [glyph] and formula.pdf_curve == [rule]
    assert page.pdf_curve == [] and attach_formula_rules(page) == 0


def test_long_rule_and_regular_text_are_not_mistaken_for_variable():
    glyph = char("a", Box(22, 40, 27, 48), 10)
    paragraph = PdfParagraph(xobj_id=0, pdf_style=PdfStyle(font_size=10),
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[glyph]))])
    glyph.xobj_id = 0
    rule = PdfCurve(box=Box(22, 39, 27, 39), xobj_id=0)
    page = Page(pdf_font=[PdfFont(font_id="body", name="Times-Roman")], pdf_paragraph=[paragraph], pdf_curve=[rule])
    assert attach_formula_rules(page) == 0
    page.pdf_font[0].name = "Times-Italic"; rule.box.x2 = 120
    assert attach_formula_rules(page) == 0


def test_classifier_rotation_remaps_words_in_skewed_source_quad():
    from linguafloat.document_ocr import undo_classifier_rotation
    import cv2
    quad = np.array([[20, 10], [40, 12], [38, 112], [18, 110]], np.float32)
    expected = np.array([[20, 15], [39, 17], [38, 35], [19, 33]], np.float32)
    wrong = undo_classifier_rotation(expected, quad)
    recovered = undo_classifier_rotation(wrong, quad)
    assert np.allclose(sorted(map(tuple, recovered)), sorted(map(tuple, expected)), atol=.01)
    assert recovered[:, 1].mean() < wrong[:, 1].mean()


def test_empty_ocr_word_line_does_not_shift_later_rotation_flags():
    from linguafloat.document_ocr import DocumentRapidOCR, undo_classifier_rotation
    engine = DocumentRapidOCR.__new__(DocumentRapidOCR)
    engine.return_single_char_box = False
    engine._word_rotations = [False, False, True]
    quad = np.array([[0, 0], [100, 0], [100, 20], [0, 20]], np.float32)
    word = quad * .25
    recognized = NS(word_results=[[("one", .99, word)], [("empty", .99, None)], [("rotated", .99, word)]])
    engine.cal_rec_boxes = lambda *args: recognized
    result = engine.calc_word_boxes([], [quad] * 3, recognized)
    assert len(result) == 3 and result[1] == ()
    assert np.allclose(result[0][0][2], word)
    assert np.allclose(result[2][0][2], undo_classifier_rotation(word, quad))
    engine.text_score = .5
    filtered = engine.filter_by_text_score(NS(boxes=[quad] * 3, txts=("one", "empty", "rotated"),
                                               scores=(.1, .9, .9), word_results=result))
    assert filtered.txts == ("empty", "rotated")
    assert len(filtered.word_results) == len(filtered.txts)
    assert filtered.word_results[0] == ()
    assert np.allclose(filtered.word_results[1][0][2], undo_classifier_rotation(word, quad))


def test_graphic_context_uses_related_completed_prose_bounded_and_no_requests(tmp_path):
    from linguafloat.document_context import GraphicContext
    context = GraphicContext()
    context.remember(7, "Shield mass reduction is plotted in Fig. 4.", "图4给出了屏蔽体质量的降低情况。", "plain text")
    context.remember(3, "Unrelated cell culture in Fig. 1.", "无关的细胞培养。", "plain text")
    packet = context.packet(7, "Shield mass reduction", "Fig. 4. Shield performance.")
    assert "屏蔽体" in str(packet) and "细胞" not in str(packet)
    assert len(str(packet)) < 4000
    context.remember(7, "Shield mass reduction", "错误译文", "reference")
    assert "错误译文" not in str(context.packet(7, "Shield mass reduction", ""))
    context.remember(7, "Shield mass reduction", "图注中的质量降低。", "figure_caption")
    assert "图注中的" in str(context.packet(7, "Shield mass reduction", ""))


def test_mixed_pdf_finishes_all_body_before_graphics_and_keeps_bilingual_order(tmp_path, monkeypatch):
    import pymupdf as fitz
    from linguafloat.document_core import DocumentJob, JobControl
    from linguafloat.document_scan_layout import translate_mixed_layout_pdf
    import linguafloat.document_scan_layout as scan
    import linguafloat.document_pdf as pdf
    import linguafloat.document_graphics as graphics
    source_path = tmp_path / "mixed.pdf"
    with fitz.open() as source:
        for i in range(3):
            source.new_page().insert_text((30, 50), f"Source page {i}")
        source.save(source_path)
    job = DocumentJob(str(source_path), str(tmp_path), {}, skip_references=True,
        reference_boundary={"page_boxes": {"1": [[0, 0, 595, 842]]}, "whole_pages": [1]})
    translator = NS(job=job, control=JobControl())
    events = []

    def digital(subjob, translator, folder, emit, *, defer_graphics=False):
        assert defer_graphics
        events.append(("body", 2))
        path = folder / "digital.pdf"
        with fitz.open(source_path) as source, fitz.open() as result:
            result.insert_pdf(source, from_page=2, to_page=2)
            result.save(path)
        (folder / "layout-report.json").write_text("{}", encoding="utf-8")
        return [str(path)], []

    def scanned(source, number, *args):
        events.append(("body", number))
        fragment = fitz.open()
        fragment.insert_pdf(source, from_page=number, to_page=number)
        return fragment, [], []

    class Enhancer:
        def __init__(self, *args):
            pass

        def apply(self, source, target):
            assert events[:2] == [("body", 2), ("body", 0)]
            events.append(("graphic", source.number))

        def report(self, report):
            pass

    monkeypatch.setattr(pdf, "_translate_layout_pdf", digital)
    monkeypatch.setattr(scan, "ScanAnalyzer", lambda *args: None)
    monkeypatch.setattr(scan, "translate_scan_page", scanned)
    monkeypatch.setattr(graphics, "DocumentEnhancer", Enhancer)
    outputs, _ = translate_mixed_layout_pdf(job, translator, tmp_path, lambda _: None,
                                           [0, 1, 2], {0: "scan_ocr", 1: "digital", 2: "digital"})
    assert events == [("body", 2), ("body", 0), ("graphic", 0), ("graphic", 2)]
    for output in outputs:
        with fitz.open(output) as result:
            assert len(result) == 3
            for i, page in enumerate(result):
                assert f"Source page {i}" in page.get_text()
