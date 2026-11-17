from dataclasses import replace
import pymupdf as f
import pytest

from linguafloat.document_core import DocumentJob
from linguafloat.document_artifacts import LayoutSnapshot
from linguafloat.document_pdf import append_bilingual


def test_font_change_reuses_translated_snapshot_but_not_render_identity(tmp_path):
    source = tmp_path/'paper.pdf'; source.write_bytes(b'identity')
    job = DocumentJob(str(source), str(tmp_path), dict(id='local', base_url='https://offline.invalid',
                      model='mock', api_mode='responses'), chinese_font='simsun', latin_font='times')
    first = LayoutSnapshot(tmp_path, job, [0], 'structure-v1')
    second = LayoutSnapshot(tmp_path, replace(job, chinese_font='msyh'), [0], 'structure-v1')
    assert first.path == second.path and first.key == second.key
    assert first.render_key != second.render_key
    assert LayoutSnapshot(tmp_path, job, [0], 'structure-v2').path != first.path


@pytest.mark.parametrize('rotation', [0, 90])
def test_side_by_side_preserves_external_link_rectangles(tmp_path, rotation):
    with f.open() as source, f.open() as translated, f.open() as dual:
        for doc in (source, translated):
            page = doc.new_page(width=300, height=400)
            page.insert_text((30, 40), 'https://example.org/')
            page.insert_link({'kind':f.LINK_URI, 'from':f.Rect(30, 28, 145, 43), 'uri':'https://example.org/'})
            page.set_rotation(rotation)
        source = f.open(stream=source.tobytes(), filetype='pdf')
        translated = f.open(stream=translated.tobytes(), filetype='pdf')
        append_bilingual(dual, source, 0, translated, 'side_by_side')
        with f.open(stream=dual.tobytes(), filetype='pdf') as result:
            links = result[0].get_links()
            assert len(links) == 2
            for link in links:
                assert link['uri'] == 'https://example.org/'
                assert result[0].rect.contains(link['from'])
            assert links[1]['from'].x0 - links[0]['from'].x0 == pytest.approx(source[0].rect.width+20)
        source.close(); translated.close()


def test_reference_boundary_ignores_only_empty_spacing_not_real_formula():
    from babeldoc.format.pdf.document_il import (Page, Box, PdfParagraph, PdfParagraphComposition,
        PdfFormula, PdfCharacter, Cropbox)
    from linguafloat.document_references import filter_layout_paragraphs
    boundary = dict(page=0, boxes=[[300, 200, 600, 800]])
    blank = PdfParagraph(unicode=' ', box=Box(295, 30, 310, 40))
    formula = PdfParagraph(unicode='', box=Box(295, 70, 310, 85),
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_formula=PdfFormula(
            box=Box(295,70,310,85), pdf_character=[PdfCharacter(char_unicode='x')]))])
    page = Page(page_number=0, cropbox=Cropbox(box=Box(0,0,600,800)), pdf_paragraph=[blank, formula])
    errors=[]
    filter_layout_paragraphs(page, boundary, lambda p,*_: errors.append(p))
    assert errors == [formula]


def test_native_author_identity_is_immutable_and_survives_snapshot_decode():
    from babeldoc.format.pdf.document_il import (Page, Box, PdfParagraph, PdfParagraphComposition,
        PdfSameStyleCharacters, PdfCharacter, PdfStyle, Cropbox)
    from linguafloat.document_richtext import protect_author_identities
    from linguafloat.document_artifacts import _decoder
    from dataclasses import asdict
    style=PdfStyle(font_id='NativeBold',font_size=10)
    chars=[PdfCharacter(char_unicode=c,box=Box(30+i*5,700,35+i*5,710),pdf_style=style)
           for i,c in enumerate('Jane Doe - University')]
    p=PdfParagraph(unicode='Jane Doe - University',box=Box(30,700,140,710),pdf_style=style,
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_same_style_characters=
            PdfSameStyleCharacters(pdf_character=chars,pdf_style=style,box=Box(30,700,140,710)))])
    page=Page(page_number=0,cropbox=Cropbox(box=Box(0,0,600,800)),pdf_paragraph=[p])
    structure=dict(authors=[dict(name='Jane Doe',name_box=[30,90,70,100])])
    assert protect_author_identities(page, structure)==1
    formula=p.pdf_paragraph_composition[0].pdf_formula
    assert ''.join(c.char_unicode for c in formula.pdf_character)=='Jane Doe'
    assert formula._lf_source_identity and formula._lf_caption_marker
    decoded=_decoder(Page)(asdict(page))
    protect_author_identities(decoded, structure, cached=True)
    assert decoded.pdf_paragraph[0].pdf_paragraph_composition[0].pdf_formula._lf_source_identity
