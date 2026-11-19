import pymupdf as f
from linguafloat.document_clip import show_source_region


def test_embedded_restoration_does_not_copy_invisible_body():
    with f.open() as src, f.open() as dual:
        p=src.new_page(width=200,height=170); p.insert_text((15,30),'Outside verified area')
        p.insert_text((50,80),'Label');q=dual.new_page(width=200,height=170)
        box=f.Rect(40,60,125,90);show_source_region(q,box,src,0)
        assert 'Outside' not in q.get_text('text',flags=0) and 'Label' in q.get_text()
        assert q.get_pixmap(clip=box,dpi=144).samples==p.get_pixmap(clip=box,dpi=144).samples
