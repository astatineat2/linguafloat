import unittest

import pymupdf as f

from linguafloat.document_pdf import append_bilingual
from linguafloat.document_publisher_footer import restore_printed_urls


class PublicationButtons(unittest.TestCase):
    def test_url_touching_a_chinese_label_binds_only_the_exact_address(self):
        uri='https://www.example.org/journal'
        for suffix, ok in [('',True),('/more',False)]:
            with self.subTest(suffix=suffix), f.open() as a, f.open() as b:
                s=a.new_page();t=b.new_page();label=uri.split('://')[1];s.insert_text((40,80),label)
                s.insert_link(dict(kind=f.LINK_URI,uri=uri,**{'from':s.search_for(label)[0]}));s=a.reload_page(s)
                t.insert_text((60,140),'主页：'+label+suffix,fontname='china-s')
                restore_printed_urls(s,t); links=b[0].get_links();self.assertEqual(bool(links),ok)
                if ok:self.assertEqual(t.get_textbox(links[0]['from']).strip(),label)

    def test_doi_identifier_rebinds_after_reflow_not_to_longer_identifier(self):
        uri='https://doi.org/10.1234/abc.19'
        for word,ok in [('10.1234/abc.19.',True),('10.1234/abc.191',False)]:
            with self.subTest(word=word), f.open() as a, f.open() as b:
                s=a.new_page();t=b.new_page();s.insert_text((55,80),'doi:10.1234/abc.19.');t.insert_text((92,140),word)
                s.insert_link({'kind':f.LINK_URI,'from':s.search_for('doi:10.1234/abc.19.')[0],'uri':uri});s=a.reload_page(s)
                restore_printed_urls(s,t);self.assertEqual(bool(b[0].get_links()),ok)
    def test_printed_http_button_survives_in_both_panels_without_active_form(self):
        uri = 'https://example.org/paper/status'
        with f.open() as source, f.open() as mono, f.open() as dual:
            page = source.new_page(); box = f.Rect(25, 45, 85, 65)
            for target in (page, mono.new_page()):
                target.draw_rect(box, fill=(0.2, 0.4, 0.7))
            widget = f.Widget(); widget.field_name = 'publisher'; widget.field_type = f.PDF_WIDGET_TYPE_BUTTON
            widget.field_flags = 65536; widget.rect = box; page.add_widget(widget)
            page = source.reload_page(page); native = next(page.widgets())
            source.xref_set_key(native.xref, 'A', f'<</S/URI/URI({uri})>>')
            restore_printed_urls(page, mono[0]); restore_printed_urls(page, mono[0])
            append_bilingual(dual, source, 0, mono, 'side_by_side')
            self.assertEqual([l['uri'] for l in mono[0].get_links()], [uri])
            self.assertEqual([l['uri'] for l in dual[0].get_links()], [uri, uri])
            self.assertFalse(list(dual[0].widgets() or []))
            # A different visible field must not become a publisher link.
            with f.open() as changed:
                t = changed.new_page(); t.draw_rect(box, fill=(1, 0, 0))
                restore_printed_urls(page, t); self.assertEqual(changed[0].get_links(), [])
            source.xref_set_key(native.xref, 'A', '<</S/JavaScript/JS(null)>>')
            with f.open() as other:
                append_bilingual(other, source, 0, mono, 'side_by_side')
                self.assertEqual(len(other[0].get_links()), 1)
