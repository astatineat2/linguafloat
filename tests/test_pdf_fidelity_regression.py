import copy

import pymupdf as fitz
import pytest
from babeldoc.format.pdf.document_il import Box, Cropbox, Page, PdfFormula, PdfLine, PdfParagraph, PdfParagraphComposition
from linguafloat.document_core import JobControl, Cancelled
from linguafloat.document_failures import mark_pdf
from linguafloat.document_geometry import split_source_columns, exclude_graphic_glyphs
from linguafloat.document_scientific import composition_chars
from test_document_geometry import char


def columns():
    parts, lines = [], []
    for x in (40, 310):
        for y in (100, 112, 124):
            glyphs = [char(c, Box(x+i*6, y, x+i*6+5, y+9)) for i,c in enumerate('Native caption words')]
            box = Box(x,y,x+240,y+9)
            lines.append((box.x,box.y,box.x2,box.y2))
            parts.append(PdfParagraphComposition(pdf_line=PdfLine(box=box,pdf_character=glyphs)))
    p = PdfParagraph(box=Box(40,100,550,133),unicode='Native caption words',pdf_paragraph_composition=parts)
    return Page(page_number=0,cropbox=Cropbox(Box(0,0,600,800)),pdf_paragraph=[p]), lines


def test_two_column_caption_has_exact_glyph_ownership_and_is_idempotent():
    page, lines = columns()
    original = [c for p in page.pdf_paragraph[0].pdf_paragraph_composition for c in composition_chars(p)]
    assert split_source_columns(page, lines)
    assert len(page.pdf_paragraph) == 2
    assert page.pdf_paragraph[0].box.x2 < page.pdf_paragraph[1].box.x
    actual = [c for p in page.pdf_paragraph for part in p.pdf_paragraph_composition for c in composition_chars(part)]
    assert sorted(map(id,actual)) == sorted(map(id,original))
    assert not split_source_columns(page, lines)


def test_true_full_width_line_vetoes_column_split():
    page, lines = columns()
    lines.append((40,110,550,119))
    assert not split_source_columns(page,lines)
    assert len(page.pdf_paragraph) == 1


def test_formula_advance_into_gutter_does_not_merge_native_columns():
    page, lines = columns()
    glyphs = [char(c, Box(270 + i * 5, 110, 274 + i * 5, 118)) for i, c in enumerate('z=0')]
    formula = PdfFormula(box=Box(270, 110, 306, 119), pdf_character=glyphs)
    page.pdf_paragraph[0].pdf_paragraph_composition.append(PdfParagraphComposition(pdf_formula=formula))
    assert split_source_columns(page, lines)
    assert len(page.pdf_paragraph) == 2
    assert page.pdf_paragraph[0].box.x2 < page.pdf_paragraph[1].box.x
    assert any(part.pdf_formula is formula for part in page.pdf_paragraph[0].pdf_paragraph_composition)
    assert all(part.pdf_formula is not formula for part in page.pdf_paragraph[1].pdf_paragraph_composition)


def test_formula_entering_opposite_column_vetoes_split():
    page, lines = columns()
    glyph = char('z', Box(270, 110, 274, 118))
    page.pdf_paragraph[0].pdf_paragraph_composition.append(
        PdfParagraphComposition(pdf_formula=PdfFormula(box=Box(270, 110, 318, 119), pdf_character=[glyph])))
    assert not split_source_columns(page, lines)


def test_mixed_caption_and_figure_excludes_only_source_figure_glyphs():
    page, _ = columns()
    # Protect the left group; right-hand caption remains translatable.
    exclude_graphic_glyphs(page,[dict(box=[30,650,290,710])])
    glyphs=[c for p in page.pdf_paragraph for part in p.pdf_paragraph_composition for c in composition_chars(part)]
    assert len(glyphs)==3*len('Native caption words')
    assert all(c.box.x>=310 for c in glyphs)


def test_filtered_caption_not_removed_by_later_paragraph_filter():
    from linguafloat.document_layout import LayoutPolicy
    page, _ = columns()
    policy = LayoutPolicy(JobControl())
    policy.graphic_regions[0] = [dict(box=[30,670,560,680])]
    exclude_graphic_glyphs(page, policy.graphic_regions[0])
    before = list(page.pdf_paragraph)
    policy.exclude_frontmatter(page)
    assert page.pdf_paragraph == before


def test_column_styles_follow_remaining_glyphs_without_shared_mutability():
    from babeldoc.format.pdf.document_il import PdfStyle
    page, lines = columns()
    page.pdf_paragraph[0].pdf_style = PdfStyle(font_id='wrong', font_size=5)
    for part in page.pdf_paragraph[0].pdf_paragraph_composition[3:]:
        for c in composition_chars(part):
            c.pdf_style.font_size = 10
    split_source_columns(page, lines)
    assert [p.pdf_style.font_size for p in page.pdf_paragraph] == [9,10]
    assert page.pdf_paragraph[0].pdf_style is not page.pdf_paragraph[1].pdf_style


@pytest.mark.parametrize('rotation',[90,180,270])
def test_marking_rotation_keeps_source_geometry(rotation):
    with fitz.open() as doc:
        page=doc.new_page(width=120,height=200)
        page.set_rotation(rotation)
        r=dict(box=[20,30,50,60],coordinates='top-left')
        mark_pdf(page,[r],0,JobControl())
        assert page.rotation == rotation
        # Ink-safe markers use separate visible edge segments rather than one
        # rectangle object. Check all four edges in unrotated source coordinates.
        actual=sorted(tuple(d['rect']) for d in page.get_drawings())
        assert actual == sorted([(19,29,51,29),(19,61,51,61),
                                 (19,29,19,61),(51,29,51,61)])


def test_marker_snapshot_does_not_allow_labels_to_overlap(monkeypatch,tmp_path):
    with fitz.open() as doc:
        page=doc.new_page(width=300,height=250)
        page.insert_text((60,80),'ORIGINAL')
        regions=[dict(box=[60,60,150,90],coordinates='top-left'),dict(box=[60,60,150,90],coordinates='top-left')]
        calls=[]
        original=fitz.Page.get_displaylist
        def capture(self,*args,**kwargs):
            calls.append(self.number)
            return original(self,*args,**kwargs)
        monkeypatch.setattr(fitz.Page,'get_displaylist',capture)
        mark_pdf(page,regions,0,JobControl())
        assert len(calls)==1
        assert all(r['marked'] for r in regions)
        assert not fitz.Rect(regions[0]['label_box']).intersects(fitz.Rect(regions[1]['label_box']))
        doc.save(tmp_path/'marked.pdf')
    with fitz.open(tmp_path/'marked.pdf') as doc:
        text=doc[0].get_text()
        assert 'ORIGINAL' in text and 'P1-U1' in text and 'P1-U2' in text


def test_marking_obeys_cancel(monkeypatch):
    control=JobControl()
    control.cancelled.set()
    with fitz.open() as doc:
        page=doc.new_page()
        def forbidden(*args, **kwargs):
            raise AssertionError('Cancelled marker must not parse the page')
        monkeypatch.setattr(fitz.Page,'get_displaylist',forbidden)
        with pytest.raises(Cancelled):
            mark_pdf(page,[dict(box=[30,30,60,60],coordinates='top-left')],0,control)


def test_page_timings_survive_performance_summary():
    from linguafloat.document_metrics import PerformanceLog
    log=PerformanceLog(JobControl(),lambda _:None)
    for page,seconds in [(1,2.5),(2,3.0),(1,.5)]:
        log(dict(type='measurement',name='未翻译区域标记',page=page,seconds=seconds))
    summary=log.summary()
    assert summary['operations']['未翻译区域标记']==6
    assert summary['page_operations']=={'1':{'未翻译区域标记':3},'2':{'未翻译区域标记':3}}
