import pymupdf as f
from test_graphic_density import analyzer, translator
from linguafloat.document_graphics import apply_graphics


def test_child_does_not_commit_first_label_when_second_does_not_fit():
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=260, height=200)
        p.insert_text((70,60), 'Alpha', fontsize=8)
        p.insert_text((170,120), 'Beta', fontsize=8)
        dst.insert_pdf(src)
        t = translator({'Alpha':'甲乙', 'Beta':'完整译文'*100})
        t.job.workflow = 'graphics'
        records = apply_graphics(p, dst[0], t, analyzer([10,10,250,190]), 'groups')
        assert [r['status'] for r in records] == ['figure_skipped']
        assert records[0]['area_id'] == 'panel' and records[0]['reason']
        assert p.get_text() == dst[0].get_text()
        assert p.get_pixmap().samples == dst[0].get_pixmap().samples


def test_explicit_figure_exclusion_precedes_ocr_and_detection_cache():
    with f.open() as src:
        p = src.new_page(); a = analyzer([10,10,250,190]); t=translator({})
        a.ocr.recognize = lambda *args, **kw: (_ for _ in ()).throw(AssertionError('OCR'))
        t.job.workflow = 'graphics'
        records = apply_graphics(p,p,t,a,'groups',{'panel':{'skip_figure':True}})
        assert [r['status'] for r in records] == ['figure_skipped']
