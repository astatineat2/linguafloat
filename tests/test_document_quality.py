"""Scientific quality contracts; fixtures contain no personal documents or keys."""
from types import SimpleNamespace as NS

import pytest
import pymupdf as fitz
from lxml import etree as ET

from linguafloat.document_core import DocumentError, JobControl
from linguafloat.document_office import W, translate_paragraph
from linguafloat.document_scan_layout import fit_translation


def test_hidden_ocr_font_cannot_change_visible_scan_typesetting():
    region = dict(box=[40, 40, 400, 120], lines=[dict(box=[42, 45, 250, 55])])
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((42, 58), "Hidden layer", fontsize=30, render_mode=3)
        fitted = fit_translation(page, region, "A short scientific sentence.", "en", JobControl())
        assert fitted is not None
        assert fitted[1]["size"] == pytest.approx(12.5)


@pytest.mark.parametrize("reply", [
    '<text>lost text<r id="0">A</r><r id="1">B</r></text>',
    '<text><r id="0">A</r>lost text<r id="1">B</r></text>',
    '<text><r id="0"></r><r id="1"></r></text>',
    '<text><r id="0">Sample </r><r id="1">grew.</r></text>',
])
def test_office_rejects_unassigned_or_missing_scientific_text(reply):
    p = ET.fromstring(f'<p xmlns="{W}"><r><t>Sample </t></r><r><t>grew by 12.5%.</t></r></p>')
    original = ET.tostring(p)
    translator = NS(job=NS(target="zh-Hans"), request=lambda _: reply)
    with pytest.raises(DocumentError):
        translate_paragraph(p, W, translator)
    assert ET.tostring(p) == original


def test_document_definitions_context_boundaries_and_ambiguity():
    from linguafloat.document_context import SourceBlock as B, SourceContext, CONTEXT_LIMIT
    import json
    context = SourceContext([
        B("Bose-Einstein condensate (BEC) is the defined system.", section="Physics"),
        B("The BEC response does not imply instability.", section="Physics"),
        B("Figure 1: unrelated caption.", "figure_caption", "Physics"),
        B("Deoxyribonucleic acid (DNA) was measured.", section="Biology"),
        B("DNA abundance decreased by 12.5%.", section="Biology"),
        B("Excluded reference with endoplasmic reticulum (ER).", "references"),
    ])
    packet = context.packet("The BEC response does not imply instability.")
    assert packet["document_definitions"] == [dict(abbreviation="BEC", source_definition="Bose-Einstein condensate")]
    assert "preceding_source" in packet and "following_source" not in packet
    dna = context.packet("DNA abundance decreased by 12.5%.")
    assert dna["document_definitions"][0]["source_definition"] == "Deoxyribonucleic acid"
    assert not context.packet("HER2 was measured.")
    assert "ER" not in context.definitions
    ambiguous = SourceContext([B("Endoplasmic reticulum (ER)."), B("Estrogen receptor (ER).")])
    assert ambiguous.packet("The ER response.")["document_definitions"] == [dict(abbreviation="ER", ambiguous=True)]
    long = SourceContext([B("x" * 5000), B("target"), B("y" * 5000)])
    assert len(json.dumps(long.packet("target"), ensure_ascii=False)) <= CONTEXT_LIMIT


def test_context_is_in_cache_identity_and_existing_request_budget(tmp_path):
    import json
    from concurrent.futures import ThreadPoolExecutor
    from linguafloat.document_context import SourceBlock as B
    from linguafloat.document_core import DocumentTranslator, JobStore
    from test_document_core import job_for
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return NS(output_text="内质网反应。")
    store = JobStore(tmp_path / "quality.sqlite")
    try:
        t = DocumentTranslator(job_for(tmp_path / "a.pdf"), "", store, JobControl(), lambda _: None,
                               NS(responses=NS(create=create)))
        t.set_source_context([B("Endoplasmic reticulum (ER) is defined here."), B("The ER response.")])
        original = t.messages("The ER response.")
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert len(set(pool.map(t.translate, ["The ER response."] * 4))) == 1
        assert len(calls) == 1 and calls[0]["input"][-1] == original[-1]
        packet = json.loads(calls[0]["input"][-2]["content"])["reference_only"]
        assert packet["document_definitions"][0]["source_definition"] == "Endoplasmic reticulum"
        assert len(original) == 2  # input is never mutated
        digest = t.cache_key(original)
        t.set_source_context([B("Estrogen receptor (ER) is defined here."), B("The ER response.")])
        assert t.cache_key(original) != digest
        t.job.relayout_only = True
        with pytest.raises(DocumentError, match="缓存不完整"):
            t.translate("The ER response.")
        assert len(calls) == 1
    finally:
        store.close()


def test_scan_line_join_preserves_hyphens_without_evidence():
    from linguafloat.document_scan_layout import join_ocr_lines
    def join(*texts):
        return join_ocr_lines([dict(text=t) for t in texts])
    assert join("The long-", "term response.") == "The long-term response."
    assert join("The inter\u00ad", "action.") == "The interaction."
    assert join("We measure inter-", "actions. These interactions matter.") == "We measure interactions. These interactions matter."


def test_scientific_number_guard_allows_equivalent_not_missing_quantities():
    from linguafloat.document_context import scientific_quantities
    assert scientific_quantities("12.50% at -0.05") == scientific_quantities("12.5％，−0.050")
    assert scientific_quantities("1e-3") == scientific_quantities("0.001")
    assert scientific_quantities("12.5%") != scientific_quantities("12.5")


def test_office_invalid_response_is_not_reused(tmp_path):
    from linguafloat.document_core import DocumentTranslator, JobStore
    from test_document_core import job_for
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return NS(output_text='<text><r id="0">样本</r><r id="1">增加。</r></text>')
    store = JobStore(tmp_path / "invalid.sqlite")
    try:
        t = DocumentTranslator(job_for(tmp_path / "a.docx"), "", store, JobControl(), lambda _: None,
                               NS(responses=NS(create=create)))
        p = ET.fromstring(f'<p xmlns="{W}"><r><t>Sample </t></r><r><t>grew by 12.5%.</t></r></p>')
        for _ in range(2):
            with pytest.raises(DocumentError):
                translate_paragraph(p, W, t)
        assert len(calls) == 2
    finally:
        store.close()


def test_equation_line_container_keeps_superscript_attached():
    from babeldoc.format.pdf.document_il import (Box, PdfCharacter, PdfFormula,
        PdfLine, PdfParagraphComposition as Part, PdfStyle)
    from linguafloat.document_scientific import merge_formula_runs, composition_chars
    glyphs = [PdfCharacter(char_unicode=c, box=Box(10 + i * 5, 20, 15 + i * 5, 30),
                           pdf_style=PdfStyle(font_id="base", font_size=12), pdf_character_id=i + 1)
              for i, c in enumerate("E = m c")]
    power = PdfCharacter(char_unicode="2", box=Box(45, 26, 49, 34),
                         pdf_style=PdfStyle(font_id="base", font_size=8), pdf_character_id=20)
    parts = [Part(pdf_line=PdfLine(pdf_character=glyphs)),
             Part(pdf_formula=PdfFormula(box=power.box, pdf_character=[power], x_offset=0, y_offset=6, x_advance=0))]
    merged = merge_formula_runs(parts, 12)
    assert len(merged) == 1 and merged[0].pdf_formula
    assert composition_chars(merged[0]) == glyphs + [power]
    assert power.box == Box(45, 26, 49, 34)
