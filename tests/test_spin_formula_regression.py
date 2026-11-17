"""Original glyph regression: Nature s41586-025-09883-2, page 3 box.

The source prints Z with subscript 2 and superscript C(subscript 2)T,
followed by LEFT SEMIDIRECT PRODUCT (U+22C9), then SO(2). BabelDOC sees
the operator as (cid:31); it must keep that original font/code, not replace it.
Coordinates below are from the native parser and the recorded translated IL.
"""
import copy
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from babeldoc.format.pdf.document_il import (
    Box, PdfCharacter, PdfFormula, PdfParagraph, PdfParagraphComposition as Part,
    PdfStyle,
)

from linguafloat.document_scientific import composition_chars, protect_math


def glyph(token, font, size, code, box, scope=0):
    return PdfCharacter(char_unicode=token, pdf_character_id=code, box=Box(*box),
                        pdf_style=PdfStyle(font_id=font, font_size=size), xobj_id=scope)


def formula(chars):
    boxes = [c.box for c in chars]
    return Part(pdf_formula=PdfFormula(
        box=Box(min(b.x for b in boxes), min(b.y for b in boxes),
                max(b.x2 for b in boxes), max(b.y2 for b in boxes)),
        pdf_character=chars, x_offset=0, y_offset=0, x_advance=0))


def spin_glyphs():
    return [glyph(*row) for row in [
        ('(cid:31)', 'T1_8', 8.064, 31, (242.204,595.480,248.478,603.544)),
        ('Z', 'T1_9', 8.064, 90, (225.788,595.480,230.586,603.544)),
        ('S', 'T1_7', 8.064, 83, (248.537,595.480,253.593,603.544)),
        ('O', 'T1_7', 8.064, 79, (253.722,595.480,260.262,603.544)),
        ('(', 'T1_7', 8.064, 40, (260.342,595.480,262.939,603.544)),
        ('2', 'T1_7', 8.064, 50, (262.931,595.480,267.398,603.544)),
        (')', 'T1_7', 8.064, 41, (267.544,595.480,270.140,603.544)),
        ('C', 'T1_9', 5.760, 67, (231.548,599.800,235.741,605.560)),
        ('T', 'T1_9', 5.760, 84, (238.748,599.800,241.980,605.560)),
        ('2', 'T1_7', 5.760, 50, (230.972,593.464,234.163,599.224)),
        ('2', 'T1_7', 4.896, 50, (236.156,598.360,238.869,603.256)),
    ]]


FONTS = {'T1_7': 'GraphikNaturel-Regular', 'T1_8': 'EuclidMathOne',
         'T1_9': 'GraphikNaturel-RegularItalic'}


def assert_one_original_formula(paragraph, originals):
    expected = {id(c) for c in originals}
    owners = [p.pdf_formula for p in paragraph.pdf_paragraph_composition
              if p.pdf_formula and any(id(c) in expected for c in p.pdf_formula.pdf_character)]
    assert len(owners) == 1, 'The original expression must occupy exactly one formula slot'
    assert expected <= {id(c) for c in owners[0].pdf_character}
    return owners[0]


def test_spin_group_keeps_SO_argument_and_nested_scripts_in_one_slot():
    chars = spin_glyphs()
    before = copy.deepcopy(chars)
    # Recorded failure: 2 from SO(2) is already in the first formula, while
    # SO( ) and the superscript T are separate compositions. No text repair.
    parts = [formula([chars[i] for i in (0,9,10,5,7,1)]),
             *[Part(pdf_character=chars[i]) for i in (2,3,4,6)], formula([chars[8]])]
    paragraph = PdfParagraph(pdf_style=PdfStyle(font_size=8), pdf_paragraph_composition=parts)
    protect_math(paragraph, FONTS)
    merged = assert_one_original_formula(paragraph, chars)
    assert chars == before  # glyph codes, scope, styles and all coordinates
    assert merged.box.y2 >= chars[7].box.y2
    protect_math(paragraph, FONTS)
    assert_one_original_formula(paragraph, chars)


@pytest.mark.parametrize('text', ['Mn3Sn', 'NiF2', 'Fe2O3', 'RuO2'])
def test_chemical_word_survives_existing_script_formula(text):
    chars = [glyph(c, 'body', 5 if c.isdigit() else 8, ord(c),
                   (i*5, 98 if c.isdigit() else 100, i*5+5, 103 if c.isdigit() else 108))
             for i,c in enumerate(text)]
    suffix = [glyph(c, 'body', 8, ord(c), (len(chars)*5+i*4,100,len(chars)*5+i*4+4,108))
              for i,c in enumerate(' spin orders')]
    paragraph = PdfParagraph(pdf_style=PdfStyle(font_size=8), pdf_paragraph_composition=[
        *[formula([c]) if c.char_unicode.isdigit() else Part(pdf_character=c) for c in chars],
        *[Part(pdf_character=c) for c in suffix]])
    protect_math(paragraph, {'body':'GraphikNaturel-Regular'})
    assert_one_original_formula(paragraph, chars)
    prose = ''.join(c.char_unicode for p in paragraph.pdf_paragraph_composition
                    if not p.pdf_formula for c in composition_chars(p))
    assert 'spin orders' in prose


def test_formula_only_paragraph_still_merges_original_runs():
    chars = spin_glyphs()
    paragraph = PdfParagraph(pdf_style=PdfStyle(font_size=8),
        pdf_paragraph_composition=[formula(chars[:7]), formula(chars[7:])])
    protect_math(paragraph, FONTS)
    assert_one_original_formula(paragraph, chars)


def test_math_font_resolution_respects_xobject_scope():
    chars = [glyph('(cid:31)', 'F1', 8, 31, (10,100,16,108), 4),
             glyph('normal', 'F1', 8, 42, (40,100,70,108), 5)]
    paragraph = PdfParagraph(pdf_style=PdfStyle(font_size=8),
        pdf_paragraph_composition=[Part(pdf_character=c) for c in chars])
    protect_math(paragraph, {'F1':'TimesRoman', (4,'F1'):'EuclidMathOne', (5,'F1'):'TimesRoman'})
    assert_one_original_formula(paragraph, chars[:1])
    assert any(p.pdf_character is chars[1] for p in paragraph.pdf_paragraph_composition)


def test_original_pdf_box_with_native_parser(tmp_path, monkeypatch):
    """Opt-in real-file check; no model, transport, translated-cache or fixture PDF."""
    source = os.environ.get('LINGUAFLOAT_SPIN_PDF')
    if not source:
        pytest.skip('Set LINGUAFLOAT_SPIN_PDF to the original s41586-025-09883-2.pdf')
    assert Path(source).is_file()
    import socket
    import pymupdf
    from babeldoc.format.pdf.parse_shared import build_parse_only_config
    from babeldoc.format.pdf.new_parser.native_parse import parse_prepared_pdf_with_new_parser_to_legacy_ir
    from babeldoc.format.pdf.document_il import PageLayout
    from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
    from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
    from linguafloat.document_scientific import ScientificLayout

    connect = socket.socket.connect
    def offline(sock, address):
        # Windows asyncio's self-pipe uses a loopback socket pair.
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1'):
            raise AssertionError('External networking is forbidden in the source regression')
        return connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', offline)
    with pymupdf.open(source) as pdf:
        config = build_parse_only_config(source, pages='3', working_dir=tmp_path)
        docs = parse_prepared_pdf_with_new_parser_to_legacy_ir(source, config=config, doc_pdf=pdf)
        page = docs.page[0]
        # Exact original box body, excluding its heading and right-hand figure.
        page.page_layout = [PageLayout(id=1, box=Box(44,539,290,692), class_name='plain text', conf=1)]
        page.pdf_character = [c for c in page.pdf_character if c.box and
                              44<c.box.x<290 and 539<c.box.y<692]
        expected = [c for c in page.pdf_character if 225<c.box.x<271 and 592<c.box.y<606
                    and (c.char_unicode or '').strip()]
        chemistry = [c for c in page.pdf_character if 155<c.box.x<181 and 647<c.box.y<650
                     and (c.char_unicode or '').isalnum()]
        assert len(expected) == 11
        before = copy.deepcopy(expected)
        ParagraphFinder(config).process_page(page)
        StylesAndFormulas(config).process_page(page)
        policy = ScientificLayout(source, NS(), [])
        policy.prepare(docs)
        paragraphs = [p for p in page.pdf_paragraph if any(
            c is expected[0] for part in p.pdf_paragraph_composition for c in composition_chars(part))]
        assert len(paragraphs) == 1
        merged = assert_one_original_formula(paragraphs[0], expected)
        assert expected == before
        assert ''.join(c.char_unicode for c in chemistry) == 'Mn3Sn'
        assert_one_original_formula(paragraphs[0], chemistry)
        # Exercise the actual layout primitive: all original glyphs receive
        # one rigid transform, including the numeral formerly lost from SO().
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        moved = TypesettingUnit(formular=merged).relocate(100, 200, 0.9).formular
        offsets = [(b.box.x - a.box.x*.9, b.box.y - a.box.y*.9)
                   for a,b in zip(merged.pdf_character, moved.pdf_character)]
        assert all(x == pytest.approx(offsets[0][0]) and y == pytest.approx(offsets[0][1])
                   for x,y in offsets)
        assert [(c.pdf_character_id,c.pdf_style.font_id,c.xobj_id) for c in moved.pdf_character] == [
            (c.pdf_character_id,c.pdf_style.font_id,c.xobj_id) for c in merged.pdf_character]
