import pymupdf as fitz
import pytest
from babeldoc.format.pdf.document_il import Box, Page, PdfCharacter, PdfFormula, PdfParagraph, PdfParagraphComposition, PdfStyle


def test_native_table_names_accept_locants_but_not_scientific_identifiers():
    from linguafloat.document_graphics import translation_word, validate_graphic_fragment
    from linguafloat.document_core import DocumentError

    for name in ('2-aminoadipate', 'D-2-hydroxyacid', 'd-2-hydroxyacid', 'N-acetyl-L-2-aminoadipate'):
        assert translation_word(dict(text=name, native_table_word=True), set())
        assert not translation_word(dict(text=name), set())
        assert not translation_word(dict(text=name, native_table_word=True), {name})
    for value in ('H2O', 'DX7', 'pH-7', '10-mg', '1,2-DX7', '1.25'):
        assert not translation_word(dict(text=value, native_table_word=True), set())
    validate_graphic_fragment('D-2-hydroxyacid', 'D-2-\u7f9f\u57fa\u9178')
    with pytest.raises(DocumentError):
        validate_graphic_fragment('D-2-hydroxyacid', 'L-2-\u7f9f\u57fa\u9178')
    with pytest.raises(DocumentError):
        validate_graphic_fragment('D-2-hydroxyacid', 'D-3-\u7f9f\u57fa\u9178')


def test_caption_rejoins_only_source_proven_fragments_before_translation():
    from linguafloat.document_geometry import repair_split_captions

    def part(text, x, y, label='fallback_line', layout_id=2):
        style = PdfStyle(font_size=8)
        chars = [PdfCharacter(char_unicode=c, box=Box(x+3*i, y, x+3*(i+1), y+6), pdf_style=style)
                 for i, c in enumerate(text)]
        return PdfParagraph(unicode=text, box=Box(x, y, x+3*len(text), y+6), pdf_style=style,
            layout_label=label, layout_id=layout_id,
            pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=c) for c in chars])

    prefix = part('Fig. 1. afte', 10, 60)
    caption = part('r OH', 46, 60, 'figure_caption', 1)
    symbol = caption.pdf_paragraph_composition[-1].pdf_character
    anchor = PdfFormula(box=symbol.box, pdf_character=[symbol])
    caption.pdf_paragraph_composition[-1] = PdfParagraphComposition(pdf_formula=anchor)
    independent = part('H', 64, 60)
    page = Page(page_number=0, pdf_paragraph=[prefix, caption, independent])
    words = [('after', (34, 59, 49, 67)), ('H', (64, 59, 67, 67))]
    assert repair_split_captions(page, words)
    assert page.pdf_paragraph == [caption, independent]
    assert caption.unicode == 'Fig. 1. after OH'
    assert caption.pdf_paragraph_composition[-1].pdf_formula is anchor
    assert independent.unicode == 'H'

    # Detector labels alone cannot establish that these rows are a caption.
    prefix = part('Body text. afte', 10, 60)
    suspected = part('r OH', 55, 60, 'figure_caption', 1)
    page.pdf_paragraph = [prefix, suspected]
    assert not repair_split_captions(page, [('after', (43, 59, 58, 67))])
    assert page.pdf_paragraph == [prefix, suspected]


def test_outlined_color_prose_requires_glyph_evidence_and_preserves_rules():
    from linguafloat.document_scan_layout import ink_patch

    with fitz.open() as doc:
        page = doc.new_page(width=180, height=90)
        # Curved outlined glyph fixtures, no underlying text operators.
        for x in range(22, 76, 8):
            shape = page.new_shape()
            shape.draw_oval(fitz.Rect(x, 22, x+5, 29))
            shape.draw_oval(fitz.Rect(x+1, 23, x+4, 28))
            shape.finish(color=None, fill=(.9, .1, .15), even_odd=True)
            shape.commit()
        page.draw_line((15, 38), (160, 38), width=.5)
        line = dict(text='Methods', box=[21, 21, 77, 30], confidence=.999, horizontal=True)
        region = dict(text=line['text'], box=[19, 19, 79, 33], lines=[line], outline_prose=True)
        assert ink_patch(page, region)[0] is not None
        line['confidence'] = .7
        assert ink_patch(page, region)[0] is None
        line['confidence'] = .999
        page.draw_line((18, 24), (81, 24), color=(.9, .1, .15), width=.5)
        assert ink_patch(page, region)[0] is None


def test_outline_line_cannot_erase_only_the_first_word_of_a_field():
    from linguafloat.document_scan_layout import verified_outline_text

    with fitz.open() as doc:
        page = doc.new_page(width=180, height=90)
        for x in (20, 26, 32, 38, 49, 55, 61, 67):
            page.draw_oval(fitz.Rect(x, 21, x + 4, 29), color=None, fill=(.8, .1, .2))
        line = dict(text='Life', box=[19, 20, 43, 30], confidence=.999, horizontal=True)
        region = dict(text='Life', box=[18, 18, 45, 32], lines=[line], outline_prose=True)
        assert not verified_outline_text(page, region, fitz.Rect(region['box']))
        line.update(text='Life club', box=[19, 20, 72, 30])
        region.update(text=line['text'], box=[18, 18, 74, 32])
        assert verified_outline_text(page, region, fitz.Rect(region['box']))
        region['recovered'] = True
        assert not verified_outline_text(page, region, fitz.Rect(region['box']))
        line['outline_whole_row'] = True
        assert verified_outline_text(page, region, fitz.Rect(region['box']))


def test_outline_row_requires_matching_uncropped_text_and_baseline():
    from linguafloat.document_scan_recovery import outline_row_matches_source

    full = dict(text='Life sciences study design', box=[24, 61, 160, 74],
                horizontal=True, confidence=.999)
    cropped = dict(full, text='Life', box=[25, 62, 51, 72])
    assert not outline_row_matches_source(cropped, [full])
    assert not outline_row_matches_source(dict(full, box=[24, 75, 160, 88]), [full])
    assert outline_row_matches_source(dict(full, box=[25, 62, 158, 73]), [full])
