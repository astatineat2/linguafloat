"""Scientific quantities must not straddle independently translated fragments."""
import pytest
from babeldoc.format.pdf.document_il import (Box, PdfCharacter, PdfFormula,
    PdfParagraph, PdfParagraphComposition, PdfStyle)
from linguafloat.document_scientific import composition_chars, protect_math


def paragraph(text, partial):
    chars = [PdfCharacter(box=Box(10+i*4, 30, 14+i*4, 40),
        pdf_style=PdfStyle(font_id='body', font_size=9), char_unicode=c,
        pdf_character_id=i+10, render_order=i) for i,c in enumerate(text)]
    start = text.index(partial)
    end = start+len(partial)
    formula = PdfFormula(box=Box(10+start*4,30,10+end*4,40),pdf_character=chars[start:end],
        x_offset=0,y_offset=0,x_advance=0)
    parts = [PdfParagraphComposition(pdf_character=c) for c in chars[:start]]
    parts.append(PdfParagraphComposition(pdf_formula=formula))
    parts.extend(PdfParagraphComposition(pdf_character=c) for c in chars[end:])
    return PdfParagraph(box=Box(10,30,10+len(text)*4,40),unicode=text,
        pdf_style=PdfStyle(font_id='body',font_size=9),pdf_paragraph_composition=parts), chars


@pytest.mark.parametrize('quantity,partial', [
    ('0.5\u22120.6','5\u22120'), ('25.19%','19%'), ('-3.25e-7','25e-7'),
    ('6.83','83'), ('AraT5','5'), ('DX7','7')])
def test_quantity_or_identifier_is_one_original_glyph_anchor(quantity, partial):
    p, chars=paragraph('Dose '+quantity+' in medium.',partial)
    protect_math(p,{'body':'TimesRoman'})
    formulas=[''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
              for part in p.pdf_paragraph_composition if part.pdf_formula]
    assert quantity in formulas
    assert [c.pdf_character_id for part in p.pdf_paragraph_composition for c in composition_chars(part)] == [c.pdf_character_id for c in chars]
    assert 'Dose' not in ''.join(formulas) and 'medium' not in ''.join(formulas)


def test_sentence_decimal_punctuation_never_acquires_next_prose():
    p, chars=paragraph('At 0.25. Compare results.','25')
    protect_math(p,{'body':'TimesRoman'})
    formulas=[''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
              for part in p.pdf_paragraph_composition if part.pdf_formula]
    assert '0.25' in formulas
    assert all(not s.endswith('.') and 'Compare' not in s for s in formulas)


@pytest.mark.parametrize('lexeme,partial', [('=42.06','=42'),('=1.234','=1'),('=-3.25','=-3')])
def test_assignment_anchor_keeps_its_decimal_tail(lexeme, partial):
    p, chars=paragraph('Reading '+lexeme+' ppm in sample.',partial)
    protect_math(p,{'body':'TimesRoman'})
    formulas=[''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
              for part in p.pdf_paragraph_composition if part.pdf_formula]
    assert lexeme in formulas
    assert [c.pdf_character_id for part in p.pdf_paragraph_composition for c in composition_chars(part)] == [c.pdf_character_id for c in chars]


def test_uncertainty_slot_owning_both_partial_values_is_completed_as_one_quantity():
    p, chars=paragraph('Mean 37.2 ± 6.9 years.', '2 ± 6')
    protect_math(p,{'body':'TimesRoman'})
    assert [''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
            for part in p.pdf_paragraph_composition if part.pdf_formula] == ['37.2 ± 6.9']
    assert [c.pdf_character_id for part in p.pdf_paragraph_composition for c in composition_chars(part)] == [c.pdf_character_id for c in chars]


def test_paint_order_does_not_join_different_lines_as_one_number():
    p, chars=paragraph('Value 0.25 in medium.','25')
    for c in chars[8:10]:
        c.box.y += 20
        c.box.y2 += 20
    from linguafloat.document_scientific import join_partial_scientific_tokens
    join_partial_scientific_tokens(p)
    assert [''.join(c.char_unicode for c in part.pdf_formula.pdf_character)
            for part in p.pdf_paragraph_composition if part.pdf_formula] == ['25']


def test_paint_order_does_not_join_distant_columns_as_one_number():
    p, chars=paragraph('Value 0.25 in medium.','25')
    for c in chars[8:10]:
        c.box.x += 100
        c.box.x2 += 100
    from linguafloat.document_scientific import join_partial_scientific_tokens
    join_partial_scientific_tokens(p)
    assert [''.join(c.char_unicode for c in part.pdf_formula.pdf_character)
            for part in p.pdf_paragraph_composition if part.pdf_formula] == ['25']


@pytest.mark.parametrize('quantity,partial,unit', [
    ('0.5\u22120.6','5\u22120','mM'), ('25.19','19','mg'), ('-3.25e-7','25e-7','mol')])
def test_quantity_before_adjacent_unit_is_never_a_truncated_prefix(quantity, partial, unit):
    # PDF extractors can omit the space before a unit, as in the actual p1 range.
    p, chars=paragraph('Dose '+quantity+unit+' in medium.',partial)
    protect_math(p,{'body':'TimesRoman'})
    formulas=[''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
              for part in p.pdf_paragraph_composition if part.pdf_formula]
    assert quantity in formulas
    assert all(unit not in value for value in formulas)
    assert [c.pdf_character_id for part in p.pdf_paragraph_composition for c in composition_chars(part)] == [c.pdf_character_id for c in chars]


def test_formula_inferred_space_does_not_create_a_different_resource_owner():
    p, chars=paragraph('AraT5 in medium.','5 ')
    for c in chars:
        c.xobj_id = None if c.char_unicode.isspace() else 0
    protect_math(p,{'body':'TimesRoman'})
    formulas=[''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
              for part in p.pdf_paragraph_composition if part.pdf_formula]
    assert 'AraT5' in formulas


def test_visual_word_gap_keeps_prose_outside_identifier_anchor():
    p, chars=paragraph('AraT5was active.','5')
    for c in chars[5:]:
        c.box.x += 3
        c.box.x2 += 3
    protect_math(p,{'body':'TimesRoman'})
    formulas=[''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
              for part in p.pdf_paragraph_composition if part.pdf_formula]
    assert 'AraT5' in formulas
    assert all('was' not in value for value in formulas)
    assert [c.pdf_character_id for part in p.pdf_paragraph_composition for c in composition_chars(part)] == [c.pdf_character_id for c in chars]


def test_real_resource_boundary_is_not_treated_as_inferred_spacing():
    p, chars=paragraph('AraT5 in medium.','5')
    for c in chars:
        c.xobj_id = 1 if c.char_unicode == '5' else 0
    from linguafloat.document_scientific import join_partial_scientific_tokens
    join_partial_scientific_tokens(p)
    assert [''.join(c.char_unicode for c in part.pdf_formula.pdf_character)
            for part in p.pdf_paragraph_composition if part.pdf_formula] == ['5']


def test_contiguous_identifier_suffix_stays_part_of_identifier():
    p, chars=paragraph('AraT5b in medium.','5')
    protect_math(p,{'body':'TimesRoman'})
    assert 'AraT5b' in [''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
                        for part in p.pdf_paragraph_composition if part.pdf_formula]


@pytest.mark.parametrize('word', ['measurements', 'contributes', 'by'])
def test_raised_citation_does_not_make_the_preceding_word_immutable(word):
    text = f'Using {word}43 in this study.'
    p, chars = paragraph(text, '43')
    for c in chars[text.index('43'):text.index('43')+2]:
        c.pdf_style.font_size = 6
        c.box.y += 2
        c.box.y2 += 2
    protect_math(p, {'body': 'TimesRoman'})
    parts = p.pdf_paragraph_composition
    assert [''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
            for part in parts if part.pdf_formula] == ['43']
    assert word in ''.join(c.char_unicode for part in parts if not part.pdf_formula for c in composition_chars(part))
    assert [id(c) for part in parts for c in composition_chars(part)] == [id(c) for c in chars]


@pytest.mark.parametrize('shift', [-2, 2])
def test_script_before_visual_word_gap_does_not_absorb_prose(shift):
    p, chars=paragraph('AraT5was active.','5')
    chars[4].pdf_style.font_size = 6
    chars[4].box.y += shift
    chars[4].box.y2 += shift
    for c in chars[5:]:
        c.box.x += 3
        c.box.x2 += 3
    protect_math(p,{'body':'TimesRoman'})
    formulas=[''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
              for part in p.pdf_paragraph_composition if part.pdf_formula]
    assert 'AraT5' in formulas
    assert all('was' not in value for value in formulas)
    assert [c.pdf_character_id for part in p.pdf_paragraph_composition for c in composition_chars(part)] == [c.pdf_character_id for c in chars]


def test_contiguous_script_identifier_keeps_relative_glyph_positions():
    p, chars=paragraph('C2T in medium.','2')
    chars[1].pdf_style.font_size = 6
    chars[1].box.y -= 2
    chars[1].box.y2 -= 2
    expected = [(id(c), c.box.y) for c in chars]
    protect_math(p,{'body':'TimesRoman'})
    assert 'C2T' in [''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
                    for part in p.pdf_paragraph_composition if part.pdf_formula]
    assert [(id(c), c.box.y) for part in p.pdf_paragraph_composition
            for c in composition_chars(part)] == expected


def test_decimal_with_existing_micro_unit_keeps_original_glyphs():
    p, chars = paragraph('Scale (7.5 μg measured).', '5 μ')
    protect_math(p, {'body': 'TimesRoman'})
    anchors = [''.join(c.char_unicode for c in part.pdf_formula.pdf_character).strip()
               for part in p.pdf_paragraph_composition if part.pdf_formula]
    assert any('7.5 μ' in a for a in anchors)
    assert [id(c) for part in p.pdf_paragraph_composition for c in composition_chars(part)] == list(map(id, chars))


def test_unit_formula_cannot_acquire_tail_of_grouped_prose_integer():
    p, chars = paragraph('Value 26,470 X−1.', '−1')
    protect_math(p, {'body': 'TimesRoman'})
    assert not any('470' in ''.join(c.char_unicode for c in part.pdf_formula.pdf_character)
                   for part in p.pdf_paragraph_composition if part.pdf_formula)
    assert [id(c) for part in p.pdf_paragraph_composition for c in composition_chars(part)] == list(map(id, chars))


def test_sentence_stop_before_raised_reference_list_is_not_a_decimal_point():
    from types import SimpleNamespace
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    from linguafloat.document_scientific import check_formula_tokens
    p, _ = paragraph('100-23.10,49 ', '10,49 ')
    f = p.pdf_paragraph_composition[-1].pdf_formula
    f.y_offset = 3
    for c in f.pdf_character:
        c.pdf_style.font_size = 6
    source = "<style id='5'>100-23.</style>{v7}Next"
    ti = SimpleNamespace(unicode=source, placeholders=[FormulaPlaceholder(7, f, '{v7}', '')])
    assert check_formula_tokens(ti, source.replace('.', '。'))
    assert check_formula_tokens(ti, source.replace('100-23.', '在100-23中观察到差异。'))
    assert not check_formula_tokens(ti, source.replace('100-23.', '在100-24中观察到差异。'))
    assert not check_formula_tokens(ti, source.replace('23.', '24。'))
    assert not check_formula_tokens(ti, source.replace('100-', '101-'))
    assert not check_formula_tokens(ti, source.replace('{v7}', '{v8}'))
    f.y_offset = 0
    assert not check_formula_tokens(ti, source.replace('.', '。'))


def test_label_spacing_does_not_change_precision_sign_or_percent_validation():
    from linguafloat.document_scientific import _formula_fragment_problem as problem
    source = 'Level pH 7.0 and 0.02% for 100-23.'
    assert not problem(source, source.replace('pH 7.0', 'pH7.0'), [])
    for change in ('pH7.1', 'pH-7.0', 'pH7.0%', 'pH7.0 7.0'):
        assert problem(source, source.replace('pH 7.0', change), [])
    for change in ('0.02', '0.2%', '-0.02%', '0.02% 0.02%'):
        assert problem(source, source.replace('0.02%', change), [])
    assert problem(source, source.replace('100-23', '100-24'), [])


def test_chemical_locant_is_not_a_negative_reading():
    from linguafloat.document_scientific import prose_numbers
    assert prose_numbers('indole 3-acid') == prose_numbers('吲哚-3-酸')
    assert prose_numbers('value -3 mg') != prose_numbers('值3 mg')
    assert prose_numbers('value -3.5%') != prose_numbers('值3.5%')
    assert prose_numbers('indole-3- acetic acid') == prose_numbers('\u5422\u54da-3-\u4e59\u9178')
    assert prose_numbers('value -3- acid') != prose_numbers('value 3- acid')


@pytest.mark.parametrize('label', ['Figure', 'Fig', 'Table', 'Eq', 'Equation'])
def test_dotted_cross_reference_is_not_a_leading_decimal(label):
    from linguafloat.document_scientific import _formula_fragment_problem as problem
    source = f'As shown in {label}.2a, the thickness is .25 nm.'
    target = '\u89c1\u56fe 2a\uff0c\u539a\u5ea6\u4e3a 0.25 nm\u3002'
    assert not problem(source, target, [])
    assert problem(source, target.replace('2a', '3a'), [])
    assert problem(source, target.replace('0.25', '0.2'), [])


@pytest.mark.parametrize('source,translation', [
    ('53-55 kDa', '53\u201355 kDa'),
    ('1.2-3.4 mg', '1.2\u20133.4 mg'),
    ('1e-3-2e-3 mol', '1e-3\u20132e-3 mol'),
    ('-53--55 kDa', '-53\u2013-55 kDa'),
])
def test_prose_range_dash_does_not_create_a_negative_endpoint(source, translation):
    from linguafloat.document_scan_layout import validate_scan_values
    validate_scan_values(source, translation)


@pytest.mark.parametrize('source,translation', [
    ('53-55 kDa', '53\u201356 kDa'),
    ('-53--55 kDa', '-53\u201355 kDa'),
    ('53 -55 kDa', '53 55 kDa'),
    ('1e-3-2e-3 mol', '1e-3\u20132e3 mol'),
    ('53-55 kDa', '53\u201355% kDa'),
])
def test_range_normalization_keeps_endpoint_sign_exponent_and_percent(source, translation):
    from linguafloat.document_core import DocumentError
    from linguafloat.document_scan_layout import validate_scan_values
    with pytest.raises(DocumentError):
        validate_scan_values(source, translation)


def test_range_normalization_does_not_rewrite_identifiers_or_url_paths():
    from linguafloat.document_context import normalize_numeric_ranges
    for text in ('CD14-53', 'https://example.org/25-0149-42', 'word-3-acid'):
        assert normalize_numeric_ranges(text) == text
