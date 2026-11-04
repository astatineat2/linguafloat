import copy

import pytest
from babeldoc.format.pdf.document_il import (Box, Page, PdfCharacter, PdfCurve, PdfFormula,
    PdfLine, PdfParagraph, PdfParagraphComposition, PdfSameStyleUnicodeCharacters, PdfStyle, VisualBbox)
from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
from linguafloat.document_geometry import (attach_formula_rules, join_fragmented_paragraphs,
    preserve_symbol_labels, restore_reference_styles, shift_unit, space_formula_lines)
from linguafloat.document_layout import LayoutPolicy, bounds
from linguafloat.document_core import JobControl
from test_document_layout import engine_fixture


def char(text, box, size=9):
    return PdfCharacter(char_unicode=text, pdf_character_id=ord(text), box=box,
                        visual_bbox=VisualBbox(copy.copy(box)), pdf_style=PdfStyle(font_id="body", font_size=size))


def fraction():
    numerator = char("a", Box(22, 42, 27, 47))
    denominator = char("b", Box(21, 32, 28, 37))
    formula = PdfFormula(box=Box(20, 31, 30, 48), pdf_character=[numerator, denominator],
                         x_offset=0, y_offset=-3, x_advance=0)
    rule = PdfCurve(box=Box(20, 39, 30, 39), ctm=[1, 0, 0, 1, 0, 0])
    p = PdfParagraph(pdf_style=PdfStyle(font_size=9), pdf_paragraph_composition=[PdfParagraphComposition(pdf_formula=formula)])
    page = Page(pdf_paragraph=[p], pdf_curve=[rule])
    return page, formula, rule


def test_fraction_rule_has_one_owner_and_moves_with_glyphs():
    page, formula, rule = fraction()
    before = copy.deepcopy(formula)
    assert attach_formula_rules(page) == 1
    assert not page.pdf_curve and formula.pdf_curve == [rule]
    assert attach_formula_rules(page) == 0
    with LayoutPolicy(JobControl()):
        placed = TypesettingUnit(formular=formula).relocate(100, 200, .8)
    assert placed._lf_line_y == 200
    shifted = shift_unit(placed, -7)
    assert shifted.formular.pdf_curve[0].box.y == pytest.approx(placed.formular.pdf_curve[0].box.y-7)
    assert shifted.formular.pdf_curve[0].relocation_transform[5] == pytest.approx(placed.formular.pdf_curve[0].relocation_transform[5]-7)
    assert shifted.formular.pdf_character[0].box.y == pytest.approx(placed.formular.pdf_character[0].box.y-7)
    assert shifted.formular.pdf_character[0].box.y-shifted.formular.pdf_character[1].box.y == pytest.approx(8)
    assert before.pdf_character == formula.pdf_character


@pytest.mark.parametrize("ambiguous", [False, True])
def test_table_rule_or_ambiguous_formula_is_not_claimed(ambiguous):
    page, formula, rule = fraction()
    if ambiguous:
        page.pdf_paragraph.append(copy.deepcopy(page.pdf_paragraph[0]))
    else:
        rule.box.x2 = 150
    assert attach_formula_rules(page) == 0 and page.pdf_curve == [rule]


def test_rule_in_different_xobject_is_not_moved():
    page, formula, rule = fraction()
    page.pdf_paragraph[0].xobj_id = 3
    rule.xobj_id = 17
    assert attach_formula_rules(page) == 0 and not formula.pdf_curve


def test_formula_cannot_overlap_previous_line_and_rule_moves_atomically():
    engine, _, _, units = engine_fixture(text="中文字", size=9)
    first = units[0].relocate(20, 70, 1)
    first._lf_line_y = 70
    page, formula, rule = fraction()
    attach_formula_rules(page)
    tall = TypesettingUnit(formular=formula).relocate(20, 59, 1)
    tall._lf_line_y = 59
    placed, fits = space_formula_lines([first, tall], Box(10, 20, 100, 79), 9)
    assert fits and placed[1].box.y2 < placed[0].box.y
    assert LayoutPolicy.safe_placement(placed, (10, 20, 100, 79), [], (0, 0, 300, 400))


def fragments(translated):
    def para(text, box, label, order):
        style = PdfStyle(font_size=9, font_id="body")
        return PdfParagraph(box=box, pdf_style=style, layout_label=label, layout_id=4,
            xobj_id=0, render_order=order, unicode=text,
            pdf_paragraph_composition=[PdfParagraphComposition(pdf_same_style_unicode_characters=
                PdfSameStyleUnicodeCharacters(unicode=text, pdf_style=style))])
    a=para("研究 t" if translated else "Study t",Box(20, 80, 48, 89),"plain text",1)
    b=para("h",Box(48, 80, 51, 87),"title",2)
    c=para("e 性质" if translated else "e properties",Box(20, 40, 180, 89),"plain text",3)
    glyphs=[char("t",Box(45,80,48,87)),char("h",Box(48,80,51,87)),char("e",Box(51,80,54,87))]
    for glyph in glyphs:
        glyph.xobj_id=0
    return Page(pdf_paragraph=[a,b,c]),[("the",(45,78,54,91))],glyphs,c


def test_translated_fragment_join_requires_source_word_and_keeps_real_translation():
    page,words,glyphs,carrier=fragments(True)
    assert not join_fragmented_paragraphs(page,[],glyphs)
    changes=join_fragmented_paragraphs(page,words,glyphs)
    assert changes and changes[0]['removed_fragments']==['t','h','e']
    assert page.pdf_paragraph == [carrier]
    assert carrier.unicode.replace(' ','') == "研究性质"
    assert not join_fragmented_paragraphs(page,words,glyphs)


def test_source_fragments_join_before_api_without_losing_letters():
    page,words,glyphs,carrier=fragments(False)
    assert join_fragmented_paragraphs(page,words,glyphs)
    assert carrier.unicode == "Study the properties"


def test_fragment_join_does_not_absorb_an_unrelated_heading():
    page,words,glyphs,carrier=fragments(False)
    heading=copy.deepcopy(page.pdf_paragraph[1])
    heading.unicode="Methods"
    heading.box=Box(75,90,110,97)
    heading.render_order=0
    page.pdf_paragraph.insert(0,heading)
    assert join_fragmented_paragraphs(page,words,glyphs)
    assert heading in page.pdf_paragraph and carrier.unicode == "Study the properties"


def test_original_symbol_labels_keep_glyphs_and_subscript_positions():
    p=PdfParagraph(layout_label="fallback_line",box=Box(10,20,25,30),unicode="E{v1}",
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_same_style_unicode_characters=
            PdfSameStyleUnicodeCharacters(unicode="E"))])
    glyphs=[char("E",Box(10,21,18,30)),char("2",Box(18,19,22,24),5)]
    page=Page(pdf_paragraph=[p])
    assert preserve_symbol_labels(page,glyphs)==1
    assert p.pdf_paragraph_composition[0].pdf_formula.pdf_character == glyphs
    assert p.pdf_paragraph_composition[0].pdf_formula.box.y == 19


def test_natural_language_figure_caption_is_not_marked_as_symbol():
    p=PdfParagraph(layout_label="fallback_line",box=Box(10,20,40,30),unicode="Voltage")
    assert preserve_symbol_labels(Page(pdf_paragraph=[p]),[char('V',Box(10,20,18,29))])==0


def test_symbol_label_does_not_lose_a_subscript_from_an_ordinary_line():
    glyphs=[char('E',Box(10,21,18,30)),char('2',Box(18,16,22,20),5)]
    part=PdfParagraphComposition(pdf_line=PdfLine(pdf_character=glyphs))
    p=PdfParagraph(layout_label='fallback_line',box=Box(10,21,25,30),unicode='E2',
                   pdf_paragraph_composition=[part])
    assert preserve_symbol_labels(Page(pdf_paragraph=[p]),glyphs)==0
    assert p.pdf_paragraph_composition==[part]


def test_short_heading_can_use_its_column_without_crossing_neighbor():
    engine,page,p,units=engine_fixture(text="化合物中的自旋霍尔效应",size=10,box=Box(40,100,80,111))
    p.layout_label="title"
    page.pdf_paragraph.append(PdfParagraph(box=Box(40,65,240,95),layout_label="plain text"))
    with LayoutPolicy(JobControl()) as policy:
        policy.prepare_page(page)
        scale,placed=policy.find(engine,p,page,units,apply_layout=True)
    assert placed and scale==1 and p.box.x2==240


def test_retained_region_includes_complete_source_subscript():
    engine,page,p,units=engine_fixture(text="译文"*100,size=9,box=Box(40,100,50,105))
    with LayoutPolicy(JobControl()) as policy:
        glyph=char('2',Box(45,98,49,104),5)
        glyph.xobj_id=p.xobj_id
        policy.source_characters[0]=[glyph]
        policy.prepare_page(page)
        policy.defer(engine,p,page,units)
        assert policy.retained_regions[0][0]['box'][1]==98


def test_reference_uses_original_body_font_without_changing_cached_text():
    engine,page,p,units=engine_fixture(text="已有参考文献译文",size=10,box=Box(20,30,180,50))
    p.layout_label='reference'
    glyphs=[char('a',Box(21+i*5,32,25+i*5,39),7.5) for i in range(20)]
    for glyph in glyphs:
        glyph.xobj_id=p.xobj_id
    assert restore_reference_styles(page,glyphs)==1
    assert p.pdf_style.font_size==7.5
    assert p.pdf_paragraph_composition[0].pdf_same_style_unicode_characters.unicode=='已有参考文献译文'
    assert restore_reference_styles(page,glyphs)==0


def test_dropcap_normalizes_entire_translated_prefix_but_leaves_header_style():
    engine,page,p,units=engine_fixture(text="对于"+"正文"*30,size=9)
    units[0].font_size=units[1].font_size=35
    policy=LayoutPolicy(JobControl())
    normalized=policy.normalize_dropcap(units,p,page)
    assert normalized[0].font_size==normalized[1].font_size==9
    p.layout_label='abandon'
    assert policy.normalize_dropcap(units,p,page)[0].font_size==35
