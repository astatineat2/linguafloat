import json
import os
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_pdf import assemble_layout_pdf
from linguafloat.document_publisher_footer import publisher_footer_box, restore_publisher_footer


DOI = 'https://doi.org/10.5678/example.2024.501'
CITE = 'Journal of Example Studies 2024, 19, 321-338'


def source_pdf(*, repeat=True, top=760, citation=CITE):
    doc = fitz.open()
    for n in range(2):
        p = doc.new_page(width=600, height=800)
        p.insert_text((40, 735), 'Source caption, not running metadata')
        p.insert_text((45, top), 'Independent marginal note', fontsize=7)
        if repeat or n == 0:
            p.insert_text((390, top), DOI, fontsize=7)
            p.insert_text((390, top + 9), citation, fontsize=7)
            p.insert_link({'kind': fitz.LINK_URI, 'uri': DOI, 'from': p.search_for(DOI)[0]})
    return doc


def pixels(page, rect):
    return page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect).samples


def test_digital_assembly_restores_only_repeated_footer_without_reference_mode(tmp_path):
    source = tmp_path / 'article.pdf'
    rendered = tmp_path / 'engine.pdf'
    with source_pdf() as doc, fitz.open() as engine:
        doc.save(source)
        for _ in doc:
            p = engine.new_page(width=600, height=800)
            p.insert_text((40, 735), 'TRANSLATED CAPTION')
            p.insert_text((45, 760), 'TRANSLATED NOTE', fontsize=7)
            p.insert_text((404, 760), DOI[:-7], fontsize=7)
            p.insert_text((397, 772), '321-338', fontsize=7)
        engine.save(rendered)
    job = SimpleNamespace(source=str(source), target='en', mono=True, bilingual=True,
                          bilingual_layout='side_by_side', skip_references=False)
    paths = assemble_layout_pdf(job, [0], rendered, tmp_path, {}, JobControl())
    with fitz.open(source) as src, fitz.open(rendered) as eng:
        area = fitz.Rect(385, 747, 565, 779)
        assert pixels(eng[0],area) != pixels(src[0],area)
        for path, dx, dy in zip(paths, (0,620), (0,26)):
            with fitz.open(path) as out:
                offset = (dx,dy,dx,dy)
                assert pixels(out[0],area+offset) == pixels(src[0],area)
                assert out[0].get_textbox(area + offset).splitlines() == [DOI, CITE]
                assert len(out[0].search_for(DOI, clip=area + offset)) == 1
                for body in [fitz.Rect(20,710,250,747), fitz.Rect(20,750,240,780)]:
                    assert pixels(out[0],body+offset) == pixels(eng[0],body)
                links = [l for l in out[0].get_links() if l.get('uri') == DOI and l['from'].x0 >= dx]
                assert len(links) == 1


@pytest.mark.parametrize('kwargs', [dict(repeat=False), dict(top=600), dict(citation='A nearby sentence in prose.')])
def test_requires_repeated_footer_pair(kwargs):
    with source_pdf(**kwargs) as doc:
        assert publisher_footer_box(doc[0]) is None


@pytest.mark.parametrize('nested', [False, True])
def test_restore_removes_broken_copy_text_and_stale_uri_without_touching_surroundings(nested):
    with source_pdf() as source, fitz.open() as engine, fitz.open() as output:
        page = engine.new_page(width=600, height=800)
        page.insert_text((40, 735), 'TRANSLATED CAPTION')
        page.insert_text((45, 760), 'TRANSLATED NOTE', fontsize=7)
        page.insert_text((404, 760), DOI[:-7], fontsize=7)
        page.insert_text((397, 772), '321-338', fontsize=7)
        page.draw_line((25, 740), (575, 740), color=(1, 0, 0), width=.7)
        page.draw_line((25, 785), (575, 785), color=(0, 0, 1), width=.7)
        if nested:
            for _ in range(2):
                target = output.new_page(width=600, height=800)
                target.show_pdf_page(target.rect, engine, 0)
            output[1].set_contents(output[0].get_contents()[0])
        else:
            output.insert_pdf(engine)
        target = output[0]
        rect = publisher_footer_box(source[0])
        target.insert_link({'kind': fitz.LINK_URI, 'from': fitz.Rect(404, 752, 520, 762),
                            'uri': DOI[:-7]})
        note_link = {'kind': fitz.LINK_URI, 'from': fitz.Rect(45, 750, 150, 765),
                     'uri': 'https://example.org/note'}
        target.insert_link(note_link)
        before = pixels(target, target.rect)
        restore_publisher_footer(source[0], target)
        restore_publisher_footer(source[0], target)
        with fitz.open(stream=output.tobytes(garbage=4, deflate=True), filetype='pdf') as saved:
            target = saved[0]
            assert target.get_text().splitlines() == ['TRANSLATED CAPTION', 'TRANSLATED NOTE', DOI, CITE]
            assert target.get_textbox(rect).splitlines() == [DOI, CITE]
            assert len(target.search_for(DOI)) == 1
            assert target.get_text().count('https://doi.org/') == 1
            traced = ''.join(chr(c[0]) for span in target.get_texttrace() for c in span['chars'])
            assert traced == 'TRANSLATED CAPTIONTRANSLATED NOTE' + DOI + CITE
            assert pixels(target, rect) == pixels(source[0], rect)
            for box in (fitz.Rect(0, 0, 600, rect.y0), fitz.Rect(0, rect.y1, 600, 800),
                        fitz.Rect(0, rect.y0, rect.x0, rect.y1),
                        fitz.Rect(rect.x1, rect.y0, 600, rect.y1)):
                assert pixels(target, box) == pixels(engine[0], box)
            assert sorted(link['uri'] for link in target.get_links()) == sorted([DOI, note_link['uri']])
            if nested:
                assert pixels(saved[1], saved[1].rect) == before
                assert saved[1].get_text() == engine[0].get_text()
        assert 'Source caption, not running metadata' in source[0].get_text()


def test_restore_preserves_implicit_text_matrix_and_lexes_literal_operator_names():
    doi = DOI + '/BT/ET/BI'
    with fitz.open() as source, fitz.open() as output:
        for _ in range(2):
            page = source.new_page(width=600, height=800)
            page.insert_font(fontname='helv')
            xref = source.get_new_xref()
            source.update_object(xref, '<<>>')
            source.update_stream(xref, (
                b'BT /helv 10 Tf 1 0 0 1 40 65 Tm (Source caption) Tj ET\n'
                b'q 7 0 0 7 360 40 cm BT /helv 1 Tf (' + doi.encode('ascii') + b') Tj\n'
                b'0 -1.285714 Td (' + CITE.encode('ascii') + b') Tj ET Q'))
            page.set_contents(xref)
        target = output.new_page(width=600, height=800)
        target.insert_text((40, 735), 'TRANSLATED CAPTION')
        target.insert_text((390, 760), 'https://doi.org/BROKEN', fontsize=7)
        rect = publisher_footer_box(source[0])
        restore_publisher_footer(source[0], target)
        assert pixels(target, rect) == pixels(source[0], rect)
        assert target.get_text().splitlines() == ['TRANSLATED CAPTION', doi, CITE]


def test_identical_footer_pixels_still_remove_hidden_broken_copy_text():
    with source_pdf() as source, fitz.open() as output:
        output.insert_pdf(source, from_page=0, to_page=0)
        target = output[0]
        rect = publisher_footer_box(source[0])
        target.insert_text((404, 760), DOI[:-7], fontsize=7, render_mode=3)
        before = pixels(target, rect)
        assert before == pixels(source[0], rect)
        restore_publisher_footer(source[0], target)
        assert pixels(target, rect) == before
        assert target.get_textbox(rect).splitlines() == [DOI, CITE]
        assert target.get_text().count('https://doi.org/') == 1


def test_restore_leaves_target_untouched_when_footer_boundary_cuts_adjacent_text():
    with source_pdf() as source, fitz.open() as output:
        target = output.new_page(width=600, height=800)
        target.insert_text((300, 755), 'ADJACENT TRANSLATED CAPTION', fontsize=10)
        target.insert_text((404, 760), DOI[:-7], fontsize=7)
        before = target.read_contents(), target.get_text(), pixels(target, target.rect)
        restore_publisher_footer(source[0], target)
        assert (target.read_contents(), target.get_text(), pixels(target, target.rect)) == before


def test_restore_leaves_binary_inline_image_page_untouched():
    with source_pdf() as source, fitz.open() as output:
        target = output.new_page(width=600, height=800)
        target.insert_text((404, 760), DOI[:-7], fontsize=7)
        xref = target.get_contents()[0]
        output.update_stream(xref, output.xref_stream(xref) + (
            b'\nq 2 0 0 2 30 60 cm BI /W 1 /H 1 /CS /RGB /BPC 8 ID \xff\x00\x00 EI Q'))
        before = target.read_contents(), target.get_text(), pixels(target, target.rect)
        restore_publisher_footer(source[0], target)
        assert (target.read_contents(), target.get_text(), pixels(target, target.rect)) == before


def test_actual_ahr_page4_footer_copy_search_links_and_caption_pixels():
    evidence = os.environ.get('LINGUAFLOAT_TEST_FOOTER_EVIDENCE')
    if not evidence:
        pytest.skip('Set LINGUAFLOAT_TEST_FOOTER_EVIDENCE to the six-quality evidence directory')
    root = Path(evidence)
    baseline = json.loads((root / 'baseline.json').read_text(encoding='utf-8'))[5]
    replay = json.loads((root / 'ahr-replay/result.json').read_text(encoding='utf-8'))
    doi = 'https://doi.org/10.1021/acs.jafc.4c01622'
    citation = 'J. Agric. Food Chem. 2024, 72, 9236\u22129246'
    with fitz.open(baseline['source']) as source, fitz.open(replay['details']['outputs'][0]) as output:
        page, target = source[3], output[3]
        rect = publisher_footer_box(page)
        assert rect is not None
        assert doi not in target.get_text()
        assert doi[:-5] in target.get_text()
        caption = fitz.Rect(45, 665, 565, 755)
        figure = fitz.Rect(45, 50, 565, 665)
        before = [pixels(target, box) for box in (caption, figure)]
        expected_links = [link['uri'] for link in page.get_links()
                          if link['kind'] == fitz.LINK_URI and rect.contains(link['from'])]
        assert expected_links == [doi + '?urlappend=%3Fref%3DPDF&jav=VoR&rel=cite-as']
        restore_publisher_footer(page, target)
        restore_publisher_footer(page, target)
        with fitz.open(stream=output.tobytes(garbage=4, deflate=True), filetype='pdf') as saved:
            target = saved[3]
            assert target.get_textbox(rect).splitlines() == [doi, citation]
            assert target.get_text().count('https://doi.org/') == 1
            assert target.get_text().count(doi) == 1
            assert len(target.search_for(doi)) == 1
            assert 'Representative images' not in target.get_text()
            traced = ''.join(chr(c[0]) for span in target.get_texttrace() for c in span['chars'])
            assert traced.count('https://doi.org/') == 1
            assert traced.count(doi) == 1
            assert 'Representative images' not in traced
            assert pixels(target, rect) == pixels(page, rect)
            for scale in (1, 4):
                opts = dict(clip=rect, matrix=fitz.Matrix(scale, scale), annots=False)
                assert target.get_pixmap(**opts).samples == page.get_pixmap(**opts).samples
            assert [pixels(target, box) for box in (caption, figure)] == before
            assert [link['uri'] for link in target.get_links() if rect.contains(link['from'])] == expected_links
