import pymupdf as pdf

from linguafloat.document_clip import show_source_region
from linguafloat.document_pdf import save_composed_pdf


def test_relocated_source_clip_does_not_import_off_clip_text():
    with pdf.open() as source, pdf.open() as final:
        p = source.new_page()
        p.insert_text((50, 60), 'KEEP')
        p.insert_text((50, 140), 'OFFCLIP')
        out = final.new_page()
        show_source_region(out, pdf.Rect(200, 200, 250, 230), source, 0,
                           clip=pdf.Rect(45, 40, 95, 70))
        raw = ''.join(chr(c[0]) for s in out.get_texttrace() for c in s['chars'])
        assert 'KEEP' in raw and 'OFFCLIP' not in raw


def test_final_save_keeps_invisible_search_text_link_and_widgets(tmp_path):
    with pdf.open() as doc:
        p = doc.new_page()
        p.insert_text((50, 90), 'hidden OCR', render_mode=3)
        p.insert_link({'kind': pdf.LINK_URI, 'from': pdf.Rect(50,80,120,95), 'uri': 'https://example.org/'})
        w = pdf.Widget(); w.field_type = pdf.PDF_WIDGET_TYPE_TEXT
        w.field_name = 'example'; w.field_value = 'unchanged'; w.rect = pdf.Rect(40,120,160,140)
        p.add_widget(w)
        save_composed_pdf(doc, tmp_path/'final.pdf')
    with pdf.open(tmp_path/'final.pdf') as saved:
        assert 'hidden OCR' in saved[0].get_text()
        assert saved[0].get_links()[0]['uri'] == 'https://example.org/'
        assert next(saved[0].widgets()).field_value == 'unchanged'


def test_equal_pixels_with_different_alpha_masks_are_not_coalesced(tmp_path):
    import io
    from PIL import Image
    with pdf.open() as doc:
        p = doc.new_page(width=100, height=50)
        for x, alpha in ((0, 64), (50, 192)):
            img = Image.new('RGBA', (12, 12), (20, 90, 150, alpha))
            buf = io.BytesIO(); img.save(buf, format='PNG')
            p.insert_image(pdf.Rect(x, 0, x + 50, 50), stream=buf.getvalue())
        # Compare PDF serialization with PDF serialization; the in-memory PNG
        # pixmap has not yet been separated into PDF colour and alpha planes.
        with pdf.open(stream=doc.tobytes(garbage=0),filetype='pdf') as baseline:
            original = baseline[0].get_pixmap()
        assert original.pixel(25, 25) != original.pixel(75, 25)
        save_composed_pdf(doc, tmp_path/'alpha.pdf')
    with pdf.open(tmp_path/'alpha.pdf') as saved:
        assert saved[0].get_pixmap().samples == original.samples


def test_large_late_embedded_font_is_subset_without_losing_search(tmp_path):
    from pathlib import Path
    import pytest
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if not font.exists(): pytest.skip('Local CJK face unavailable')
    with pdf.open() as doc:
        p=doc.new_page(); p.insert_text((40, 80), '中文科研样本',fontname='LateCJK',fontfile=str(font))
        full = tmp_path/'full.pdf'; doc.save(full, garbage=4, deflate=True)
        save_composed_pdf(doc,tmp_path/'subset.pdf')
    with pdf.open(tmp_path/'subset.pdf') as doc:
        assert doc[0].search_for('中文科研样本')
        assert (tmp_path/'subset.pdf').stat().st_size < full.stat().st_size
