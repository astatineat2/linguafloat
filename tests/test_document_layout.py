from types import SimpleNamespace

import pytest
import pymupdf as fitz

from linguafloat.document_core import DocumentJob, JobControl, Cancelled
from linguafloat.document_layout import LayoutPolicy, bounds, expanded_boxes, font_scales, intersection
from linguafloat.document_pdf import assemble_layout_pdf, append_reference_page


def engine_fixture(text="中文译文" * 10, size=9, box=None):
    from babeldoc.format.pdf.document_il import (Box, Cropbox, Mediabox, Page, PdfParagraph,
        PdfStyle, PdfFont, PdfParagraphComposition, PdfSameStyleUnicodeCharacters)
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
    font = fitz.Font("china-s")
    style = PdfStyle(font_id="base", font_size=size)
    paragraph = PdfParagraph(box=box or Box(40, 100, 240, 122), pdf_style=style,
        unicode=text, xobj_id=-1, debug_id="paragraph",
        pdf_paragraph_composition=[PdfParagraphComposition(
            pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(unicode=text, pdf_style=style))])
    page = Page(page_number=0, cropbox=Cropbox(Box(0, 0, 300, 400)),
                mediabox=Mediabox(Box(0, 0, 300, 400)), pdf_paragraph=[paragraph],
                pdf_font=[PdfFont(font_id="base", encoding_length=2)])
    engine = Typesetting.__new__(Typesetting)
    engine.is_cjk = True
    engine.font_mapper = SimpleNamespace(base_font=font, fontid2font={"base": font}, map=lambda *_: font)
    units = engine.create_typesetting_units(paragraph, {"base": font})
    return engine, page, paragraph, units


def test_retained_individual_glyphs_typeset_without_paragraph_debug_info():
    from babeldoc.format.pdf.document_il import (Box, PdfCharacter, PdfFormula,
        PdfParagraphComposition, PdfStyle)
    engine, page, paragraph, _ = engine_fixture(text="译文")
    glyph = PdfCharacter(box=Box(20, 30, 25, 39), char_unicode="A",
        pdf_character_id=61, pdf_style=PdfStyle(font_id="base", font_size=9))
    formula_glyph = PdfCharacter(box=Box(30, 30, 35, 39), char_unicode="α",
        pdf_character_id=89, pdf_style=glyph.pdf_style)
    formula = PdfFormula(box=formula_glyph.box, pdf_character=[formula_glyph],
        x_offset=0, y_offset=0, x_advance=0)
    bare = PdfParagraphComposition(pdf_character=glyph)
    paragraph.pdf_paragraph_composition = [bare, PdfParagraphComposition(pdf_formula=formula),
        *paragraph.pdf_paragraph_composition]
    original_parts = list(paragraph.pdf_paragraph_composition)
    assert not hasattr(paragraph, "debug_info")
    with LayoutPolicy(JobControl()):
        units = engine.create_typesetting_units(paragraph, {"base": page.pdf_font[0]})
    assert units[0].char is glyph and units[1].formular is formula
    assert units[0].char.pdf_character_id == 61
    assert units[0].char.box == Box(20, 30, 25, 39)
    assert "".join(u.unicode or "" for u in units) == "译文"
    assert paragraph.pdf_paragraph_composition == original_parts
    assert paragraph.pdf_paragraph_composition[0] is bare


@pytest.mark.parametrize("obstacle", [(40, 98, 240, 99.5), (40, 90, 240, 100), (240, 100, 250, 122)])
def test_expansion_never_crosses_body_figure_formula_or_footer(obstacle):
    from babeldoc.format.pdf.document_il import Box
    box = Box(40, 100, 240, 122)
    for candidate in expanded_boxes(box, [obstacle], (0, 0, 300, 400)):
        assert intersection(bounds(candidate), obstacle) <= intersection(bounds(box), obstacle) + .01


def test_line_spacing_and_expansion_preserve_original_font():
    engine, page, paragraph, units = engine_fixture(text="译" * 40)
    policy = LayoutPolicy(JobControl())
    policy.prepare_page(page)
    scale, placed = policy.find(engine, paragraph, page, units, apply_layout=True)
    assert scale == 1 and placed
    assert len(paragraph.pdf_paragraph_composition) == 40
    assert policy.records[-1]["spacing"] in (1.3, 1.2)
    assert not policy.continuations


def test_preliminary_scale_search_does_not_repeat_final_layout(monkeypatch):
    engine, page, paragraph, units = engine_fixture(text="译" * 40)
    policy = LayoutPolicy(JobControl())
    policy.prepare_page(page)
    original = engine._layout_typesetting_units
    calls = []
    def layout(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(engine, "_layout_typesetting_units", layout)
    assert policy.find(engine, paragraph, page, units) == (1, None)
    assert not calls
    scale, placed = policy.find(engine, paragraph, page, units, apply_layout=True)
    assert calls and scale == 1 and len(placed) == 40


def test_font_floor_distinguishes_original_small_print():
    assert min(font_scales(12)) == .5
    assert min(font_scales(8)) == .75
    assert list(font_scales(5)) == [1]
    assert list(font_scales(6)) == [1]


def test_continuations_preserve_every_character_and_source_reference():
    engine, page, paragraph, units = engine_fixture(text="长译文测试" * 1200)
    policy = LayoutPolicy(JobControl())
    policy.defer_to_supplement(engine, paragraph, page, units)
    fragments = policy.continuations[0]
    assert len(fragments) > 1
    assert "".join(c.char_unicode for p in fragments for c in p.pdf_character) == "长译文测试" * 1200
    assert paragraph.unicode == "[P1-1]"
    assert all(c.pdf_style.font_size == pytest.approx(11) for p in fragments for c in p.pdf_character)


def test_small_diagram_label_uses_compact_reference():
    from babeldoc.format.pdf.document_il import Box, PdfParagraph
    engine, page, paragraph, units = engine_fixture(text="母排 A", size=5.5899,
                                                   box=Box(100, 100, 112.807, 105.666))
    page.pdf_paragraph.append(PdfParagraph(box=Box(112.807, 90, 300, 110)))
    policy = LayoutPolicy(JobControl())
    policy.defer_to_supplement(engine, paragraph, page, units)
    assert paragraph.unicode == "[1]"
    assert policy.margin_references[0][0]["content_index"] == 0
    assert policy.continuation_layout[0][0]["inline"]
    assert "".join(c.char_unicode for p in policy.continuations[0] for c in p.pdf_character) == "母排 A"
    assert all(c.pdf_character.pdf_style.font_size >= 5.5898 for c in paragraph.pdf_paragraph_composition)


def test_reference_uses_gutter_when_surrounded_by_obstacles():
    from babeldoc.format.pdf.document_il import Box, PdfParagraph
    engine, page, paragraph, units = engine_fixture(text="完整译文", size=8,
                                                   box=Box(100, 100, 101, 101))
    page.pdf_paragraph.extend([PdfParagraph(box=Box(0, 0, 300, 100)),
                               PdfParagraph(box=Box(101, 100, 300, 400))])
    policy = LayoutPolicy(JobControl())
    policy.defer_to_supplement(engine, paragraph, page, units)
    assert paragraph.unicode == "" and not paragraph.pdf_paragraph_composition
    assert policy.margin_references[0] == [dict(marker="[P1-1]", box=(100, 100, 101, 101), content_index=0)]
    assert "".join(c.char_unicode for p in policy.continuations[0] for c in p.pdf_character) == "完整译文"
    assert "右侧引用栏" in policy.warnings[-1]


@pytest.mark.parametrize("arrangement", ["side_by_side", "alternating"])
def test_gutter_reference_survives_bilingual_assembly(tmp_path, arrangement):
    source, rendered = tmp_path / "source.pdf", tmp_path / "rendered.pdf"
    with fitz.open() as doc:
        doc.new_page(width=300, height=400).insert_text((40, 40), "SOURCE")
        doc.save(source)
    with fitz.open() as doc:
        doc.new_page(width=300, height=400).insert_text((40, 40), "TRANSLATION")
        doc.new_page(width=300, height=400).insert_text((40, 40), "Continuation P1-1: ALL CONTENT")
        doc.save(rendered)
    job = DocumentJob(str(source), str(tmp_path), {}, bilingual_layout=arrangement)
    refs = {0: [dict(marker="[P1-1]", box=(100, 100, 101, 101))]}
    paths = assemble_layout_pdf(job, [0], rendered, tmp_path, {0: [1]}, JobControl(), refs)
    with fitz.open(paths[0]) as mono, fitz.open(paths[1]) as dual:
        assert mono[0].rect.width == 456
        assert "[P1-1]" in mono[0].get_text()
        assert "[P1-1]" in "".join(p.get_text() for p in dual)
        assert "ALL CONTENT" in mono[1].get_text()
        assert mono[0].search_for("TRANSLATION")[0].x0 == pytest.approx(40)


def test_gutter_handles_empty_small_page_and_multiple_columns():
    with fitz.open() as source, fitz.open() as output:
        source.new_page(width=100, height=50)
        refs = [dict(marker=f"[P1-{n}]", box=(10, 10, 12, 12)) for n in range(1, 4)]
        append_reference_page(output, source, 0, refs, JobControl())
        assert output[0].rect.height == 110
        assert all(item["marker"] in output[0].get_text() for item in refs)
        assert all(output[0].rect.contains(fitz.Rect(word[:4])) for word in output[0].get_text("words"))


@pytest.mark.parametrize("arrangement", ["side_by_side", "alternating"])
def test_short_labels_stay_on_source_page_and_long_fragments_share_pages(tmp_path, arrangement):
    source, rendered = tmp_path / "source.pdf", tmp_path / "rendered.pdf"
    with fitz.open() as doc:
        doc.new_page(width=300, height=400).insert_text((40, 40), "SOURCE")
        doc.save(source)
    metadata = []
    with fitz.open() as doc:
        doc.new_page(width=300, height=400).insert_text((40, 40), "TRANSLATION")
        for text, inline in (("150 mm", True), ("BUS A", True), ("Long passage one", False),
                             ("Long passage two", False)):
            page = doc.new_page(width=300, height=400)
            page.insert_text((40, 80), text, fontsize=11)
            box = page.search_for(text)[0] * ~page.transformation_matrix
            metadata.append(dict(box=list(box), inline=inline))
        doc.save(rendered)
    refs = {0: [dict(marker=f"[P1-{n + 1}]", box=(40, 100, 60, 110), content_index=n) for n in range(2)]}
    job = DocumentJob(str(source), str(tmp_path), {}, bilingual_layout=arrangement)
    paths = assemble_layout_pdf(job, [0], rendered, tmp_path, {0: [1, 2, 3, 4]}, JobControl(), refs, {0: metadata})
    with fitz.open(paths[0]) as mono, fitz.open(paths[1]) as dual:
        assert len(mono) == 2
        assert "150 mm" in mono[0].get_text() and "BUS A" in mono[0].get_text()
        assert all(text in mono[1].get_text() for text in ("Long passage one", "Long passage two", "[P1-3]", "[P1-4]"))
        assert all(word in "".join(p.get_text() for p in dual) for word in ("150 mm", "BUS A", "Long passage two"))
        spans = [s for p in mono for block in p.get_text("dict")["blocks"] if "lines" in block
                 for line in block["lines"] for s in line["spans"] if s["text"] in ("150 mm", "BUS A", "Long passage one")]
        assert len(spans) == 3 and all(s["size"] == pytest.approx(11) for s in spans)


def test_title_overlap_joins_runs_without_discarding_text():
    import copy
    engine, page, first, _ = engine_fixture(text="设计与", size=24)
    second = copy.deepcopy(first)
    second.unicode = "优化"
    second.pdf_paragraph_composition[0].pdf_same_style_unicode_characters.unicode = "优化"
    second.box.x -= 1
    second.box.y -= 24
    page.pdf_paragraph.append(second)
    policy = LayoutPolicy(JobControl())
    policy.prepare_page(page)
    assert len(page.pdf_paragraph) == 1
    assert first.unicode == "设计与 优化"
    assert len(first.pdf_paragraph_composition) == 2


def test_overlap_detection_rejects_new_intrusions():
    unit = SimpleNamespace(box=SimpleNamespace(x=40, y=92, x2=50, y2=102), unicode="中")
    assert not LayoutPolicy.safe_placement([unit], (40, 100, 240, 120), [(40, 90, 240, 95)], (0, 0, 300, 400))


@pytest.mark.parametrize("arrangement", ["side_by_side", "alternating"])
def test_assembly_maps_selected_pages_and_continuations(tmp_path, arrangement):
    source = tmp_path / "source.pdf"
    with fitz.open() as doc:
        for i in range(3):
            doc.new_page().insert_text((40, 40), f"SOURCE-{i + 1}")
        doc.save(source)
    rendered = tmp_path / "rendered.pdf"
    with fitz.open() as doc:
        for text in ("TRANS-1", "TRANS-2", "TRANS-3", "CONT-2-A", "CONT-2-B"):
            doc.new_page().insert_text((40, 40), text)
        doc.save(rendered)
    job = DocumentJob(str(source), str(tmp_path), {}, bilingual_layout=arrangement)
    paths = assemble_layout_pdf(job, [1, 2], rendered, tmp_path, {1: [3, 4]}, JobControl())
    with fitz.open(paths[0]) as mono, fitz.open(paths[1]) as dual:
        assert [p.get_text().strip() for p in mono] == ["TRANS-2", "CONT-2-A", "CONT-2-B", "TRANS-3"]
        assert "SOURCE-1" not in "".join(p.get_text() for p in dual)
        if arrangement == "side_by_side":
            assert all("SOURCE-2" in dual[i].get_text() for i in range(3))
            assert "SOURCE-3" in dual[3].get_text()
        else:
            assert len(dual) == 6
            assert "SOURCE-2" in dual[0].get_text()
            assert "SOURCE-3" in dual[4].get_text()


def test_cancel_interrupts_layout():
    engine, page, paragraph, units = engine_fixture()
    control = JobControl()
    control.cancelled.set()
    with pytest.raises(Cancelled):
        LayoutPolicy(control).find(engine, paragraph, page, units, apply_layout=True)


def test_only_verified_subscripts_are_preserved():
    engine, page, paragraph, _ = engine_fixture(text="available", size=5.3)
    paragraph.layout_label = "fallback_line"
    policy = LayoutPolicy(JobControl())
    policy.source_subscripts[0] = [("available", bounds(paragraph))]
    docs = SimpleNamespace(page=[page])
    assert len(policy.formula_subscripts(docs)) == 1
    paragraph.layout_label = "caption"
    assert not policy.formula_subscripts(docs)
    paragraph.layout_label = "fallback_line"
    paragraph.unicode = "unrelated"
    assert not policy.formula_subscripts(docs)


def test_title_initial_removal_requires_source_word_evidence():
    import copy
    engine, page, title, _ = engine_fixture(text="中文标题", size=24)
    title.layout_label = "title"
    orphan = copy.deepcopy(title)
    orphan.unicode, orphan.layout_label = "O", "abandon"
    page.pdf_paragraph.append(orphan)
    policy = LayoutPolicy(JobControl())
    policy.remove_title_remnants(page)
    assert orphan in page.pdf_paragraph
    policy.source_words[0] = [("Optimization", bounds(orphan))]
    policy.remove_title_remnants(page)
    assert page.pdf_paragraph == [title]


def test_formula_superscripts_survive_dropcap_normalization_without_source_evidence():
    from babeldoc.format.pdf.document_il import Box, PdfFormula, PdfCharacter, PdfStyle
    from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
    engine, page, paragraph, units = engine_fixture(text="随着科学的发展" * 8)
    units[0].font_size = 30
    formula = PdfFormula(box=Box(65, 110, 80, 119), is_corner_mark=True,
                         pdf_character=[PdfCharacter(char_unicode="HE", pdf_style=PdfStyle(font_size=9))])
    formula_unit = TypesettingUnit(formular=formula)
    units.insert(1, formula_unit)
    result = LayoutPolicy(JobControl()).normalize_dropcap(units, paragraph, page)
    assert result[0].font_size == 9
    assert formula_unit in result
