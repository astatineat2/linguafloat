"""Outlined text pages have no PDF fonts but are not blank pages."""
import pymupdf as f
from linguafloat.document_scan_layout import classify_page


def test_vector_only_page_routes_to_ocr_not_empty():
    with f.open() as doc:
        p=doc.new_page()
        for x in range(20,80,10):
            p.draw_rect(f.Rect(x,30,x+4,38),color=None,fill=(0,0,0))
        assert not p.get_text() and not p.get_image_info()
        assert classify_page(p) == 'scan_vector'


def test_genuinely_blank_page_remains_empty():
    with f.open() as doc:
        assert classify_page(doc.new_page()) == 'empty'


def test_vector_page_reaches_real_scan_routing(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from linguafloat.document_core import DocumentJob
    from linguafloat.document_pdf import translate_layout_pdf
    source=tmp_path/'outlined.pdf'
    with f.open() as doc:
        doc.new_page().draw_rect(f.Rect(20,20,40,40),fill=(0,0,0))
        doc.save(source)
    called=[]
    def scan(job, translator, output, emit, pages, kinds):
        called.append((pages,kinds))
        return [],[]
    monkeypatch.setattr('linguafloat.document_scan_layout.translate_mixed_layout_pdf',scan)
    job=DocumentJob(str(source),str(tmp_path),{},pdf_mode='layout')
    translate_layout_pdf(job,NS(),tmp_path,lambda _:None)
    assert called == [([0],{0:'scan_vector'})]
def test_native_figure_caption_next_to_large_bitmap_remains_digital():
    import pymupdf as f
    from linguafloat.document_scan_layout import classify_page
    with f.open() as doc:
        page = doc.new_page(width=300,height=400)
        bitmap = f.Pixmap(f.csRGB,f.IRect(0,0,30,30),False)
        bitmap.clear_with(248)
        page.insert_image(f.Rect(12,20,288,300),pixmap=bitmap,keep_proportion=False)
        page.insert_text((15,323),'Figure 2. Measurements across groups.',fontsize=10)
        assert classify_page(page) == 'digital'
        other = doc.new_page(width=300,height=400)
        other.insert_image(f.Rect(0,0,300,400),pixmap=bitmap,keep_proportion=False)
        other.insert_text((15,323),'Figure 2. Measurements across groups.',fontsize=10)
        assert classify_page(other) == 'mixed'
