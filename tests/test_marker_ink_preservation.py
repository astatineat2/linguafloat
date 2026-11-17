import numpy as np
import pymupdf as f

from linguafloat.document_failures import mark_pdf


def test_marker_measures_actual_cjk_encoded_width_and_does_not_touch_neighbor():
    import pymupdf as f
    with f.open() as doc:
        page=doc.new_page(width=220,height=220)
        # The old proportional Font.text_length reserves <40pt, while the
        # actual china-s PDF encoding draws all label characters at 6pt.
        region=dict(box=[10,80,60,100],coordinates='top-left',id='P3-G33')
        neighbor=f.Rect(52,69,67,78)
        page.draw_rect(neighbor,fill=(0,0,0),color=None)
        before=page.get_pixmap(clip=neighbor,matrix=f.Matrix(4,4)).samples
        mark_pdf(page,[region],0)
        assert page.get_pixmap(clip=neighbor,matrix=f.Matrix(4,4)).samples==before
        label=f.Rect(region['label_box'])
        assert label.width>=f.get_text_length('未翻译 P3-G33',fontname='china-s',fontsize=6)
        spans=[s for b in page.get_text('dict')['blocks'] for line in b.get('lines',[]) for s in line['spans']]
        assert spans and all(label.contains(f.Rect(s['bbox'])) for s in spans)


def test_untranslated_outline_does_not_paint_over_neighbouring_symbols():
    with f.open() as doc:
        page=doc.new_page(width=220,height=180)
        page.insert_text((40,58),'Untranslated label',fontsize=10)
        page.insert_text((55,76),'L. plantarum DX7',fontsize=9)
        # A nearby retained overbar/plot rule intersects the old expanded frame.
        page.draw_line((50,69),(150,69),color=(0,0,0),width=.7)
        before=page.get_pixmap(matrix=f.Matrix(2,2))
        region=dict(box=[39,46,155,68],coordinates='top-left',status='needs_review')
        mark_pdf(page,[region],0)
        after=page.get_pixmap(matrix=f.Matrix(2,2))
        a=np.frombuffer(before.samples,dtype=np.uint8).reshape(-1,3)
        b=np.frombuffer(after.samples,dtype=np.uint8).reshape(-1,3)
        ink=np.min(a,axis=1)<220
        assert np.array_equal(a[ink],b[ink]),'Failure marker overwrote original ink'
        assert region['marked']
        assert any(d.get('color',(0,))[0]>.8 for d in page.get_drawings())
        assert '未翻译' in page.get_text().replace(' ','')
