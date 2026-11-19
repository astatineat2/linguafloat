import pymupdf as fitz
from babeldoc.format.pdf.document_il import Box, Page, PdfParagraph

from linguafloat.document_core import JobControl
from linguafloat.document_layout import LayoutPolicy


def test_title_tail_is_removed_but_standalone_variable_is_not():
    title = PdfParagraph(layout_label='title', box=Box(20, 90, 108, 100),
                         unicode='2.1. \u4ee3\u8c22\u673a\u5236')
    tail = PdfParagraph(layout_label='fallback_line', box=Box(108.4, 92, 112, 98), unicode='n')
    variable = PdfParagraph(layout_label='fallback_line', box=Box(121, 92, 124, 98), unicode='n')
    page = Page(page_number=0, pdf_paragraph=[title, tail, variable])
    policy = LayoutPolicy(JobControl())
    policy.source_words[0] = [('elucidation', (72, 89, 112, 101)), ('n', (121, 89, 124, 101))]
    policy.remove_title_remnants(page)
    assert page.pdf_paragraph == [title, variable]


def test_masthead_reflows_existing_translation_and_preserves_both_targets():
    from linguafloat.document_masthead import external_web_uri, repair_masthead, restore_masthead_actions
    from linguafloat.document_pdf import append_bilingual

    with fitz.open() as source, fitz.open() as target:
        page = source.new_page(width=600, height=800)
        page.draw_rect((110, 55, 490, 130), color=None, fill=(.9, .9, .9))
        for y, text, size in [(69, 'Contents available at Portal', 8),
                               (98, 'Journal of Sample Studies', 14),
                               (125, 'journal homepage: www.example.org/journal', 8)]:
            width = fitz.get_text_length(text, fontsize=size)
            page.insert_text((300-width/2, y), text, fontsize=size)
        for text, url in [('Portal', 'www.example.org/index'),
                          ('www.example.org/journal', 'https://www.example.org/journal')]:
            page.insert_link(dict(kind=fitz.LINK_URI, uri=url, **{'from': page.search_for(text)[0]}))
        page = source.reload_page(page)
        out = target.new_page(width=600, height=800)
        out.draw_rect((110, 55, 490, 130), color=None, fill=(.9, .9, .9))
        out.insert_text((185, 76), '\u76ee\u5f55\uff1aPortal', fontname='china-s', fontsize=8)
        out.insert_text((250, 76), '\u793a\u4f8b', fontname='china-s', fontsize=14)
        out.insert_text((165, 94), '\u5b66\u62a5', fontname='china-s', fontsize=14)
        out.insert_text((193, 94), '\u671f\u520a\u4e3b\u9875\uff1awww.example.org/journal', fontname='china-s', fontsize=8)
        rec = repair_masthead(page, out, JobControl(), ('', ''))
        out = target.reload_page(out)
        assert rec['status'] == 'translated'
        lines = [l for b in out.get_text('dict')['blocks'] for l in b.get('lines', [])]
        assert len(lines) == 3
        assert '\u793a\u4f8b\u5b66\u62a5' in out.get_text()
        assert all(abs((line['bbox'][0]+line['bbox'][2])/2-300) < 2 for line in lines)
        expected = {'www.example.org/index', 'https://www.example.org/journal'}
        assert {external_web_uri(out, link) for link in out.get_links()} == expected
        with fitz.open() as mono, fitz.open() as dual:
            mono.insert_pdf(target)
            restore_masthead_actions(mono[0], rec['links'])
            assert {external_web_uri(mono[0], link) for link in mono[0].get_links()} == expected
            append_bilingual(dual, source, 0, mono, 'side_by_side')
            for translated in (False, True):
                assert {external_web_uri(dual[0], link) for link in dual[0].get_links()
                        if (link['from'].x0 > 600) == translated} == expected


def test_common_table_size_is_planned_before_any_cell_is_drawn():
    from linguafloat.document_graphics import plan_table_fits

    def group(name, ceiling):
        return [(dict(id=name, preferred_size=8, ceiling=ceiling), None, {}, name)]

    def fit(group, size, previous):
        return [size] if size <= group[0][0]['ceiling'] else None

    easy, narrow, impossible = group('easy', 8), group('narrow', 7.25), group('impossible', 3.75)
    assert plan_table_fits([easy], fit) == [[8]]
    assert plan_table_fits([easy, narrow], fit) == [[7.25], [7.25]]
    assert plan_table_fits([easy, narrow, impossible], fit) == [[7.25], [7.25], None]
    assert plan_table_fits([easy, group('fits_at_six', 6)], fit) == [[6], [6]]


def test_compact_table_cells_render_at_four_points_without_crossing_rules():
    from linguafloat.document_graphic_layout import clear_of_original, fit_label
    from linguafloat.document_graphics import plan_table_fits

    with fitz.open() as source:
        page = source.new_page(width=120, height=60)
        for x in (10, 60, 110):
            page.draw_line((x, 10), (x, 33))
        for y in (10, 19, 33):
            page.draw_line((10, y), (110, y))
        page.insert_text((66, 29), '1.25', fontsize=7)
        groups = []
        for x, height in ((10, 7), (60, 3.8)):
            box = [x+1, 11, x+49, 11+height]
            region = dict(box=box, layout_box=box, preferred_size=8, source='native',
                          header_cell=True, cell_box=[x, 10, x+50, 19])
            groups.append([(region, None, {}, '\u7ec4\u5206')])

        def fit(group, size, previous):
            region, _, _, text = group[0]
            result = fit_label(page, region, text, 'zh-Hans', JobControl(), size)
            if result and clear_of_original(page, group, [result]):
                return [result]
            return None

        fitted = plan_table_fits(groups, fit)
        assert all(result is not None for result in fitted)
        for [overlay] in fitted:
            assert overlay[1]['size'] == 4
            with fitz.open(stream=overlay[0], filetype='pdf') as pdf:
                assert pdf[0].get_text().strip() == '\u7ec4\u5206'
