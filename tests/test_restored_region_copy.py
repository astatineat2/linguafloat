import pymupdf as f
from linguafloat.document_clip import show_source_region


def test_orphan_cleanup_preserves_live_text_accessibility_and_inline_images():
    from linguafloat.document_clip import _strip_orphan_spacing
    marker = b'/Span<</ActualText<FEFF200B>>>BDC '
    for data in [marker + b'(X) Tj EMC', marker + b'/Figure Do EMC',
                 b'/Span<</ActualText<FEFF0041>>>BDC T* EMC',
                 b'BI /W 1 /H 1 ID '+marker+b'T* EMC EI']:
        assert _strip_orphan_spacing(data) == data


def test_empty_mixed_spacing_span_retains_text_state_for_next_operator():
    from linguafloat.document_clip import _strip_orphan_spacing
    ops = b' BT /F1 9 Tf 20 10 Td T* EMC (Next) Tj'
    marked = b'/Span<</ActualText<FEFF200B2007>>>BDC'
    assert _strip_orphan_spacing(marked + ops) == b'/Span BMC ' + ops


def test_removed_text_does_not_append_its_actualtext_to_last_figure_label():
    with f.open() as src, f.open() as out:
        p = src.new_page(width=300, height=300)
        p.insert_font(fontname='helv')
        stream = src.get_new_xref(); src.update_object(stream, '<<>>')
        src.update_stream(stream, b'BT /helv 12 Tf 1 0 0 1 80 200 Tm (D) Tj '
            b'/Span << /ActualText <FEFF200B200B> >> BDC /helv 12 Tf '
            b'1 0 0 1 100 60 Tm (X) Tj T* EMC ET')
        p.set_contents(stream)
        rect = f.Rect(70, 80, 115, 110)
        q = out.new_page(width=300,height=300)
        show_source_region(q, rect, src, 0)
        assert q.get_text().strip() == 'D'
        assert q.get_pixmap(clip=rect,dpi=144).samples == p.get_pixmap(clip=rect,dpi=144).samples


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
