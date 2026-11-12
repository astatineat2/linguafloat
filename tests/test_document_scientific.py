from types import SimpleNamespace as NS
import threading

import pymupdf as fitz

from linguafloat.document_scientific import (ScientificLayout, check_formula_tokens,
    composition_chars, protect_math, reference_entries, reference_title)


def test_reference_title_preserves_author_journal_and_year():
    text = "Fert, A., Cros, V. & Sampaio, J. Skyrmions on the Track. Nat. Nanotechnol. 8, 152 (2013)."
    before, title, after = reference_title(text)
    assert title == "Skyrmions on the Track."
    assert before + title + after == text
    assert before.endswith("Sampaio, J. ") and after.startswith(" Nat.")
    assert reference_title("An uncertain reference without title boundaries") is None


def test_reference_columns_and_number_boundaries(tmp_path):
    with fitz.open() as doc:
        p = doc.new_page(width=500, height=300)
        p.insert_text((20, 30), "References", fontsize=12)
        for x, first in ((20, 1), (270, 3)):
            for offset in range(2):
                y = 60 + offset * 70
                p.insert_text((x, y), f"{first+offset}.", fontsize=8)
                p.insert_text((x+20, y), "Fert, A. A scientific title.", fontsize=8)
                p.insert_text((x+20, y+12), "Nature 442, 797 (2006).", fontsize=8)
        entries = reference_entries(p)
    assert [e["number"] for e in entries] == ["1.", "2.", "3.", "4."]
    assert all(e["text"] == "Fert, A. A scientific title. Nature 442, 797 (2006)." for e in entries)
    assert all(e["box"][0] > e["marker"][2] for e in entries)


def paragraph_fixture():
    from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfParagraph, PdfParagraphComposition, PdfStyle, PdfLine
    chars = []
    for i, token in enumerate(("T", "e", "x", "t", " ", "ﬃ", "ﬃ", "ﬃ", " ", "C", "o", "2")):
        style = PdfStyle(font_id="math" if token == "ﬃ" else "body", font_size=6 if token == "2" else 9)
        chars.append(PdfCharacter(box=Box(i*5, 30, i*5+5, 40), pdf_style=style, char_unicode=token, pdf_character_id=40+i))
    paragraph = PdfParagraph(box=Box(0, 30, 70, 40), pdf_style=PdfStyle(font_id="body", font_size=9),
        unicode="".join(c.char_unicode for c in chars), pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars))])
    return paragraph, chars


def test_mapped_math_font_and_subscripts_keep_original_glyph_ids():
    paragraph, chars = paragraph_fixture()
    assert protect_math(paragraph, {"math": "AdvP4C4E46", "body": "TimesRoman"})
    formulas = [p.pdf_formula for p in paragraph.pdf_paragraph_composition if p.pdf_formula]
    protected = [c for f in formulas for c in f.pdf_character]
    assert all(chars[i] in protected for i in (5, 6, 7, 11))
    assert [c.pdf_character_id for p in paragraph.pdf_paragraph_composition for c in composition_chars(p)] == [c.pdf_character_id for c in chars]
    assert chars[0] not in protected


def test_formula_placeholders_must_keep_order_count_and_spelling():
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    ti = NS(unicode="Since {v1} within {v2}", placeholders=[FormulaPlaceholder(1, None, "{v1}", ""), FormulaPlaceholder(2, None, "{v2}", "")])
    assert check_formula_tokens(ti, "由于 {v1} 在 {v2} 中")
    for text in ("由于 {v1}", "{v2}{v1}", "{v1}{v1}{v2}", "{V1}{v2}", "{v1}{v2}{v99}"):
        assert not check_formula_tokens(ti, text)


def test_journal_prefixes_never_enter_translated_title():
    for journal in ("N. J. Phys.", "SciPost Phys.", "ACS Nano", "Nano Lett.", "Adv. Mater."):
        text = f"Fert, A. Magnetic textures and their motion. {journal} 12, 233 (2024)."
        prefix, title, suffix = reference_title(text)
        assert title == "Magnetic textures and their motion."
        assert suffix == f" {journal} 12, 233 (2024)."


def test_formula_merge_preserves_fraction_geometry_and_curve():
    from linguafloat.document_scientific import merge_formula_runs
    from babeldoc.format.pdf.document_il import Box, PdfFormula, PdfParagraphComposition, PdfCharacter, PdfStyle
    style = PdfStyle(font_id="math", font_size=9)
    numerator = PdfCharacter(box=Box(20, 40, 25, 46), pdf_style=style, char_unicode="A", pdf_character_id=10)
    denominator = PdfCharacter(box=Box(20, 32, 25, 38), pdf_style=style, char_unicode="K", pdf_character_id=11)
    curve = NS(box=Box(19, 39, 26, 39.3))
    a = PdfFormula(box=Box(19, 38, 26, 46), pdf_character=[numerator], pdf_curve=[curve], x_offset=0, y_offset=2, x_advance=0)
    b = PdfFormula(box=denominator.box, pdf_character=[denominator], x_offset=0, y_offset=-4, x_advance=0)
    merged = merge_formula_runs([PdfParagraphComposition(pdf_formula=a), PdfParagraphComposition(pdf_formula=b)], 9)
    assert len(merged) == 1
    formula = merged[0].pdf_formula
    assert formula.pdf_character == [numerator, denominator]
    assert formula.pdf_curve == [curve]
    assert numerator.box.y - denominator.box.y == 8
    assert formula.box.y == 32 and formula.box.y2 == 46


def test_reference_prepare_keeps_numbers_and_metadata_separate(tmp_path):
    from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfParagraph, PdfParagraphComposition, PdfStyle
    source = tmp_path / "refs.pdf"
    with fitz.open() as pdf:
        p = pdf.new_page(width=320, height=240)
        p.insert_text((20, 30), "References", fontsize=10)
        for n, y in ((1, 70), (2, 120)):
            p.insert_text((20, y), f"{n}.", fontsize=9)
            p.insert_text((40, y), "Fert, A. A magnetic title. Nature 442 (2006).", fontsize=9)
        pdf.save(source)
        chars = []
        style = PdfStyle(font_id="F1", font_size=9)
        for block in p.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    for c in span["chars"]:
                        x0, y0, x1, y1 = c["bbox"]
                        chars.append(PdfCharacter(box=Box(x0, 240-y1, x1, 240-y0), char_unicode=c["c"],
                                                  pdf_style=style, pdf_character_id=len(chars)))
    paragraph = PdfParagraph(box=Box(20, 100, 290, 220), pdf_style=style, unicode="",
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=c) for c in chars])
    page = NS(page_number=0, pdf_font=[], pdf_xobject=[], pdf_character=[], pdf_paragraph=[paragraph])
    calls = []
    barrier = threading.Barrier(2)
    def translate(text, context, **kwargs):
        assert kwargs["part"] == "参考文献题名"
        calls.append(text)
        barrier.wait(timeout=3)
        return "磁性题名。"
    policy = ScientificLayout(source, NS(translate_fragment=translate, gate=NS(maximum=2)), [])
    docs = NS(page=[page])
    policy.prepare(docs)
    refs = [p for p in page.pdf_paragraph if p.layout_label == "reference"]
    assert len(refs) == 2 and calls == ["A magnetic title.", "A magnetic title."]
    assert all(p.unicode == "Fert, A. 磁性题名。 Nature 442 (2006)." for p in refs)
    assert "".join(c.char_unicode for c in page.pdf_character) == "1.2."
    removed = policy.detach_references(docs)
    assert all(p.layout_label != "reference" for p in page.pdf_paragraph)
    policy.restore_references(removed)
    policy.prepare(docs)
    assert len(calls) == 2


def test_fragment_repair_reassembles_by_id_without_reordering_formulas():
    import json
    from linguafloat.document_scientific import translate_formula_segments
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    ti = NS(unicode="Since {v1} within {v2}", placeholders=[FormulaPlaceholder(1, None, "{v1}", ""), FormulaPlaceholder(2, None, "{v2}", "")])
    def request(messages, **kwargs):
        data = json.loads(messages[1]["content"])
        assert [item["input"] for item in data["fragments"]] == ["Since ", " within "]
        return json.dumps([dict(id=2, output="位于"), dict(id=0, output="由于")])
    translator = NS(messages=lambda _: [dict(content="Translate"), dict(content="")], request=request)
    assert translate_formula_segments(ti, translator) == "由于{v1}位于{v2}"


def test_invalid_fragment_reply_evicts_only_its_cache_entry(tmp_path):
    import pytest
    from linguafloat.document_core import DocumentError, JobStore
    from linguafloat.document_scientific import translate_formula_segments
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    ti = NS(unicode="Since {v1}", placeholders=[FormulaPlaceholder(1, None, "{v1}", "")])
    store = JobStore(tmp_path / "cache.sqlite")
    try:
        store.put("invalid", "[]")
        store.put("valid", "保持有效译文")
        translator = NS(store=store, cache_key=lambda _: "invalid", messages=lambda _: [dict(content="Translate"), dict(content="")],
                        request=lambda *a, **kw: "[]")
        with pytest.raises(DocumentError, match="片段数量"):
            translate_formula_segments(ti, translator)
        assert store.get("invalid") is None
        assert store.get("valid") == "保持有效译文"
    finally:
        store.close()


def test_formula_baseline_uses_own_body_glyph_and_preserves_scripts():
    from linguafloat.document_scientific import normalize_formula_baseline
    from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfFormula, PdfStyle
    base = PdfCharacter(box=Box(10, 100, 16, 109), char_unicode="O", pdf_style=PdfStyle(font_size=9))
    sub = PdfCharacter(box=Box(16, 98, 19, 103), char_unicode="2", pdf_style=PdfStyle(font_size=5))
    f = PdfFormula(box=Box(10, 98, 19, 109), pdf_character=[base, sub], y_offset=8.6)
    normalize_formula_baseline(f, 9)
    assert f.y_offset == -2 and sub.box.y - base.box.y == -2
    single = PdfFormula(box=sub.box, pdf_character=[sub], y_offset=-2)
    normalize_formula_baseline(single, 9)
    assert single.y_offset == -2


def test_cache_chemical_repair_requires_original_word_and_same_font():
    from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfFormula, PdfStyle, PdfParagraph, PdfParagraphComposition as Part, PdfSameStyleUnicodeCharacters as Text
    from linguafloat.document_scientific import repair_cached_chemical_scripts
    style = PdfStyle(font_id="regular", font_size=9)
    glyphs = [PdfCharacter(box=Box(10+i*5, 100 if i<3 else 98, 15+i*5, 109 if i<3 else 103),
        char_unicode=t, pdf_character_id=ord(t), pdf_style=PdfStyle(font_id="regular",font_size=9 if i<3 else 5))
        for i,t in enumerate("RuO2")]
    text = Text(unicode="RuO 的晶体结构", pdf_style=style)
    f = PdfFormula(box=glyphs[3].box, pdf_character=[glyphs[3]], y_offset=-2)
    p = PdfParagraph(pdf_style=style, pdf_paragraph_composition=[Part(pdf_same_style_unicode_characters=text),Part(pdf_formula=f)])
    page = NS(pdf_paragraph=[p])
    assert repair_cached_chemical_scripts(page, [], glyphs) == 0
    assert repair_cached_chemical_scripts(page, [("RuO2",(9,97,31,110))], glyphs) == 1
    assert "".join(c.char_unicode for c in p.pdf_paragraph_composition[0].pdf_formula.pdf_character) == "RuO2"
    assert p.pdf_paragraph_composition[1].pdf_same_style_unicode_characters.unicode == " 的晶体结构"
    assert repair_cached_chemical_scripts(page, [("RuO2",(9,97,31,110))], glyphs) == 0


def test_existing_subscript_formula_does_not_hide_chemical_word_boundary():
    from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfFormula, PdfParagraph, PdfParagraphComposition as Part, PdfStyle
    style = PdfStyle(font_id="body", font_size=9)
    chars = [PdfCharacter(box=Box(i*5, 100, i*5+5,109),char_unicode=t,pdf_style=style) for i,t in enumerate("RuOfilm")]
    sub = PdfCharacter(box=Box(15,98,18,103),char_unicode="2",pdf_style=PdfStyle(font_id="body",font_size=5))
    space = PdfCharacter(box=Box(18,100,20,109),char_unicode=" ",pdf_style=style)
    p = PdfParagraph(pdf_style=style,pdf_paragraph_composition=[*[Part(pdf_character=c) for c in chars[:3]],
        Part(pdf_formula=PdfFormula(box=Box(15,98,20,109),pdf_character=[sub,space],x_offset=0,y_offset=-2,x_advance=0)),
        *[Part(pdf_character=c) for c in chars[3:]]])
    protect_math(p,{"body":"Times"})
    assert all(any(c is original for part in p.pdf_paragraph_composition if part.pdf_formula for c in part.pdf_formula.pdf_character) for original in chars[:3])
