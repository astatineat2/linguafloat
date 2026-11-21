import pymupdf as f
from linguafloat.document_panel_text import promote_panel_text


def test_clipped_text_with_sibling_keeps_ink_and_text():
    with f.open() as source, f.open() as inner, f.open() as doc:
        p = source.new_page(); p.insert_text((36, 72), 'One short sentence')
        inner.new_page().show_pdf_page(p.rect, source, 0)
        out = doc.new_page(); out.show_pdf_page(p.rect, inner, 0)
        out.show_pdf_page(f.Rect(1,1,61,75), source, 0)
        before = out.get_pixmap().samples, out.get_text('words')
        assert promote_panel_text(out) > 1
        assert b'BT' in out.read_contents()
        assert before == (out.get_pixmap().samples, out.get_text('words'))


def test_alternating_export_exposes_text_only_on_translation():
    from linguafloat.document_pdf import append_bilingual
    with f.open() as source, f.open() as fragment, f.open() as dual:
        source.new_page().insert_text((36,72),'Select one phrase')
        fragment.new_page().show_pdf_page(source[0].rect,source,0)
        append_bilingual(dual,source,0,fragment,'alternating')
        assert dual[0].read_contents() == source[0].read_contents()
        assert b'BT' in dual[1].read_contents()
        assert dual[1].get_text('words') == fragment[0].get_text('words')


def test_candidate_mismatch_restores_input(monkeypatch):
    from linguafloat import document_panel_text as panel
    with f.open() as source, f.open() as target:
        source.new_page().insert_text((36,72),'Original')
        page=target.new_page();page.show_pdf_page(page.rect,source,0)
        original=page.read_contents()
        def unsafe(p):
            p.insert_text((36,90),'Changed');return 1
        monkeypatch.setattr(panel,'promote_panel_text',unsafe)
        assert not panel.promote_verified_panel(page)
        assert page.read_contents()==original
        assert page.get_text().strip()=='Original'
