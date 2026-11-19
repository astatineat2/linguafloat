import pytest
from linguafloat.document_graphics import cell_graphic_lines,coded_legend_labels
from linguafloat.document_glyph_patch import glyph_slice


def test_category_is_not_its_prefix_or_an_ocr_bar_count():
    from linguafloat.document_graphics import category_description
    line=dict(text='Primary categories: nested label-2',confidence=.99)
    assert category_description(line,[[dict(text='Primary categories: nested')]])=='Primary categories: nested label'
    assert category_description(dict(line,text='Mass: IL-2'),[]) is None
    assert category_description(line,[[dict(text='Primary categories: nested label')]]) is None


def label(text,left,right):
    box=[left,10,right,18]
    return dict(text=text,box=box,baseline=16,source='native',horizontal=True,rotation=0,
                font_size=8,confidence=1,words=[dict(text=text,box=box,source='native',size=8,native_table_word=True)])


def test_locant_and_head_cross_pdf_runs_but_not_cells():
    a=label('3-hydroxybutyrate',1,68);b=label('dehydrogenase',71,125)
    grid=lambda l,r:dict(box=[l,5,r,25],row=3,native_reliable=True)
    assert [m['text'] for m in cell_graphic_lines([b,a],[grid(0,135)])]==[a['text']+' '+b['text']]
    assert len(cell_graphic_lines([a,b],[grid(0,70),grid(70,135)]))==2
    assert len(cell_graphic_lines([a,b],[grid(0,135)],protected={'dehydrogenase'}))==2


def test_caption_byte_slice_preserves_neighbor_and_refuses_interleaved_art():
    def glyph(x,val):return f'q 0 g BT/F1 7 Tf 1 0 0 1 {x} 12 Tm{val}Tj ET Q'.encode()
    neighbor=glyph(55,'<01>'); a=glyph(1,'(;;)'); b=glyph(9,'<02>'); bounds=(0,8,20,15)
    stream=b' '.join([neighbor,a,b]);start,end=glyph_slice(stream,bounds)
    assert stream[start:end]==a+b' '+b
    with pytest.raises(ValueError):glyph_slice(a+b' q /Art Do Q '+b,bounds)


def test_coded_label_keeps_first_word_and_rejects_duplicates_or_numeric_axes():
    lines=[dict(text=t,confidence=.99) for t in ('A:First category','B:Second label','C:Third category','EC: 1.2.3','A:Another label')]
    assert coded_legend_labels(lines)=={'B:Second label':'Second label','C:Third category':'Third category'}


def test_outline_heading_rule_does_not_authorize_erasing_the_rule():
    import pymupdf as f
    from linguafloat.document_scan_layout import ink_patch
    with f.open() as d:
        p=d.new_page();row=f.Rect(20,20,54,31)
        for x in (21,29,37,45):p.draw_circle((x+3,25),3,color=None,fill=(.9,.1,.1))
        p.draw_line((12,32),(70,32)); region=dict(label='title',outline_prose=True,
            lines=[dict(box=list(row),text='Test',confidence=.99,horizontal=True)],box=list(row+(-2,-2,2,2)))
        patch,reason=ink_patch(p,region);assert patch and not reason and region['erase_box']==list(row)


def test_independent_legend_read_cannot_promote_an_incomplete_label():
    from types import SimpleNamespace
    import pymupdf as f
    from linguafloat.document_graphics import corroborated_legend
    region=dict(kind='figure',source='ocr',text='Complete label',source_line='Complete label',box=[2,2,60,15]);p=SimpleNamespace(rect=f.Rect(0,0,100,100))
    def analyzer(text):return SimpleNamespace(ocr=SimpleNamespace(recognize=lambda *a,**k:[dict(text=text,confidence=.99,horizontal=True)]))
    assert corroborated_legend(p,region,analyzer('Complete label'))
    assert not corroborated_legend(p,region,analyzer('label'))
    assert not corroborated_legend(p,dict(region,text='label'),analyzer('Complete label'))
