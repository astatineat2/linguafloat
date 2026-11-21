import pymupdf as f
from linguafloat.document_clip import show_source_region


def test_embedded_restoration_does_not_copy_invisible_body():
    with f.open() as src, f.open() as dual:
        p=src.new_page(width=200,height=170); p.insert_text((15,30),'Outside verified area')
        p.insert_text((50,80),'Label');q=dual.new_page(width=200,height=170)
        box=f.Rect(40,60,125,90);show_source_region(q,box,src,0)
        assert 'Outside' not in q.get_text('text',flags=0) and 'Label' in q.get_text()
        assert q.get_pixmap(clip=box,dpi=144).samples==p.get_pixmap(clip=box,dpi=144).samples


def test_retained_text_object_starts_at_its_own_origin():
    with f.open() as src, f.open() as out:
        p = src.new_page(width=200, height=170)
        p.insert_font(fontname='helv')
        stream = src.get_new_xref()
        src.update_object(stream, '<<>>')
        src.update_stream(stream, b'q 1 0 0 1 15 135 cm BT /helv 11 Tf (Outside) Tj ET Q '
                                  b'q 1 0 0 1 50 90 cm BT /helv 11 Tf (BT Label) Tj ET Q')
        p.set_contents(stream)
        box = f.Rect(49, 60, 105, 90)
        q = out.new_page(width=200, height=170)
        show_source_region(q, box, src, 0)
        assert q.get_text().strip() == 'BT Label'
        assert q.search_for('BT Label') == p.search_for('BT Label')
        assert q.get_pixmap(clip=box, dpi=144).samples == p.get_pixmap(clip=box, dpi=144).samples
