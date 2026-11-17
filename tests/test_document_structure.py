"""Offline native-structure and lossless segmentation regressions."""
from types import SimpleNamespace
import copy
import json
import os
from pathlib import Path
import socket

import pytest

from babeldoc.format.pdf.document_il import (
    Box, Cropbox, Page, PdfCharacter, PdfFont, PdfFormula, PdfParagraph,
    PdfParagraphComposition as Part, PdfSameStyleCharacters, PdfSameStyleUnicodeCharacters,
    PdfStyle,
)
from linguafloat.document_structure import source_structure, split_structures, normalize_cjk_runs
from linguafloat.document_scientific import composition_chars


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Structure tests must not access the network")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def glyph(text, x, y=60, size=10):
    return PdfCharacter(char_unicode=text, pdf_character_id=ord(text), box=Box(x, y, x+5, y+size),
                        pdf_style=PdfStyle(font_id="Original", font_size=size), xobj_id=-1)


def run(chars):
    return Part(pdf_same_style_characters=PdfSameStyleCharacters(pdf_character=chars,
        pdf_style=chars[0].pdf_style))


def page_with(parts):
    paragraph = PdfParagraph(box=Box(0, 0, 100, 100), pdf_style=PdfStyle(font_id="Original", font_size=10),
        unicode="".join(c.char_unicode for p in parts for c in composition_chars(p)),
        debug_id="paragraph", pdf_paragraph_composition=parts)
    return Page(page_number=0, cropbox=Cropbox(Box(0, 0, 100, 100)), pdf_paragraph=[paragraph])


def structure(*boxes):
    return dict(headings=[dict(box=list(b), text="HEADING") for b in boxes], authors=[], preserve=[])


def test_unanchored_chunks_stay_in_contiguous_reading_order():
    chars = [glyph("A", 0), glyph("H", 20), glyph("B", 40)]
    page = page_with([run(chars)])
    changes = split_structures(page, structure((19, 29, 26, 41)))
    assert changes
    assert [p.unicode for p in page.pdf_paragraph] == ["A", "H", "B"]
    assert [c for p in page.pdf_paragraph for part in p.pdf_paragraph_composition
            for c in composition_chars(part)] == chars
    assert len({p.debug_id for p in page.pdf_paragraph}) == 3


def test_revisited_anchor_also_keeps_distinct_contiguous_segments():
    chars = [glyph("A", 20), glyph("B", 40), glyph("C", 21)]
    page = page_with([run(chars)])
    split_structures(page, structure((19, 29, 27, 41)))
    assert [p.unicode for p in page.pdf_paragraph] == ["A", "B", "C"]


def test_pure_latin_formula_crossing_heading_is_never_split():
    chars = [glyph("x", 20), glyph("y", 40)]
    formula = PdfFormula(box=Box(20, 60, 45, 70), pdf_character=chars, x_offset=0, y_offset=0)
    page = page_with([Part(pdf_formula=formula)])
    original = page.pdf_paragraph[0]
    assert split_structures(page, structure((19, 29, 27, 41))) == []
    assert page.pdf_paragraph == [original]
    assert page.pdf_paragraph[0] is original
    assert original.pdf_paragraph_composition[0].pdf_formula is formula


def test_unmatched_paragraph_keeps_original_composition_and_glyph_identity():
    part = run([glyph("A", 0), glyph("B", 5)])
    page = page_with([part])
    paragraph = page.pdf_paragraph[0]
    assert split_structures(page, structure()) == []
    assert page.pdf_paragraph[0] is paragraph
    assert paragraph.pdf_paragraph_composition[0] is part


def test_spaces_inside_latin_identifiers_are_preserved():
    text = "中文 空格，型号 A B DX7，菌株 L. plantarum，URL https://x.test/a_b，编号（ A B ）"
    translated = PdfSameStyleUnicodeCharacters(unicode=text)
    page = page_with([])
    paragraph = page.pdf_paragraph[0]
    paragraph.pdf_paragraph_composition = [Part(pdf_same_style_unicode_characters=translated)]
    normalize_cjk_runs(paragraph)
    assert translated.unicode == text.replace("中文 空格", "中文空格")


def test_formula_within_one_segment_keeps_object_geometry_and_glyph_ids():
    chars = [glyph("x", 40), glyph("y", 46)]
    formula = PdfFormula(box=Box(38, 58, 54, 73), pdf_character=chars, x_offset=2, y_offset=-1)
    formula_part = Part(pdf_formula=formula)
    page = page_with([run([glyph("H", 20)]), formula_part])
    snapshot = copy.deepcopy(formula)
    split_structures(page, structure((19, 29, 27, 41)))
    assert page.pdf_paragraph[1].pdf_paragraph_composition[0] is formula_part
    assert formula == snapshot
    assert page.pdf_paragraph[1].box == formula.box


def test_unknown_unlocated_composition_vetoes_whole_change_instead_of_disappearing():
    unknown = Part(pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(unicode="KEEP A B"))
    page = page_with([run([glyph("H", 20)]), unknown])
    paragraph = page.pdf_paragraph[0]
    assert split_structures(page, structure((19, 29, 27, 41))) == []
    assert page.pdf_paragraph[0] is paragraph
    assert paragraph.pdf_paragraph_composition[-1] is unknown


def test_overlapping_anchors_and_shifted_crop_are_not_guessed():
    page = page_with([run([glyph("H", 20), glyph("B", 40)])])
    original = page.pdf_paragraph[0]
    assert not split_structures(page, structure((19, 29, 27, 41), (18, 28, 28, 42)))
    page.cropbox.box.x = 5
    assert not split_structures(page, structure((19, 29, 27, 41)))
    assert page.pdf_paragraph[0] is original


def test_only_verified_decoration_is_removed_not_whitespace_or_title_style():
    bullet, space, title = glyph("■", 0, size=32), glyph(" ", 0), glyph("H", 20, size=10.5)
    page = page_with([run([bullet, space, title])])
    evidence = structure((19, 28, 27, 41))
    evidence['preserve'] = [dict(kind='decoration', box=[0, 29, 5, 36], glyph_box=[0, 8, 5, 40])]
    split_structures(page, evidence)
    assert [p.unicode for p in page.pdf_paragraph] == [" ", "H"]
    assert page.pdf_paragraph[-1].layout_label == 'title'
    assert page.pdf_paragraph[-1].pdf_style.font_size == 10.5
    assert page.pdf_paragraph[-1].pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character[0] is title
    before = list(page.pdf_paragraph)
    assert split_structures(page, evidence) == []
    assert all(a is b for a, b in zip(before, page.pdf_paragraph))


def test_normalization_preserves_native_math_whitespace_types_and_mixed_runs():
    native = run([glyph("中", 0), glyph(" ", 5), glyph("文", 10)])
    formula = Part(pdf_formula=PdfFormula(pdf_character=[glyph("x", 20), glyph(" ", 25), glyph("y", 30)]))
    translated = Part(pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(
        unicode="【 中 文 】 中\u00a0文；中\t文；中\n文 A B DOI:10.1/a_b x = y 中 A B 文"))
    page = page_with([native, formula, translated])
    original = copy.deepcopy([native, formula])
    assert normalize_cjk_runs(page.pdf_paragraph[0]) == 1
    assert translated.pdf_same_style_unicode_characters.unicode == "【中文】 中\u00a0文；中\t文；中\n文 A B DOI:10.1/a_b x = y 中 A B 文"
    assert [native, formula] == original
    assert normalize_cjk_runs(page.pdf_paragraph[0]) == 0


@pytest.fixture
def walnut():
    import pymupdf as fitz
    path = Path(os.environ.get('WALNUT_SOURCE_PDF', str(Path.home() / 'Downloads' /
        'Walnut Pellicle Polyphenols Trigger Lactiplantibacillus plantarum DX7 to Produce Indole-3-Lactic Acid.pdf')))
    if not path.exists():
        pytest.skip('Set WALNUT_SOURCE_PDF to run actual local native-PDF evidence checks')
    with fitz.open(path) as pdf:
        yield pdf


def native_il(page, predicate=lambda line: True):
    """Native positions/styles projected into IL for segmentation, not engine replay."""
    import pymupdf as fitz
    from babeldoc.format.pdf.document_il import PdfLine
    parts = []
    height = page.rect.height
    for block in page.get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']:
        for line in block.get('lines', []):
            if tuple(line['dir']) != (1, 0) or not predicate(line):
                continue
            chars = []
            for span in line['spans']:
                for char in span['chars']:
                    x0, y0, x1, y1 = char['bbox']
                    chars.append(PdfCharacter(char_unicode=char['c'], pdf_character_id=ord(char['c']),
                        box=Box(x0, height-y1, x1, height-y0),
                        pdf_style=PdfStyle(font_id=span['font'], font_size=span['size'])))
            parts.append(Part(pdf_line=PdfLine(pdf_character=chars, render_order=len(parts))))
    result = page_with(parts)
    result.cropbox = Cropbox(Box(0, 0, page.rect.width, height))
    return result


def test_actual_walnut_p10_authors_heading_sizes_and_marker_ink(walnut, tmp_path):
    import pymupdf as fitz
    page = walnut[9]
    evidence = source_structure(page)
    names = ['Yujie Zhong', 'Qing Wang', 'Xinyu Yuan', 'Qiqi Feng', 'Zhu Qiao', 'Tao Wang', 'Hongyu Pan', 'Yongliang Zhuang']
    assert [a['name'] for a in evidence['authors']] == names
    assert all(a['separator'] == '\u2212' for a in evidence['authors'])
    rows = [line for block in page.get_text('dict')['blocks'] for line in block.get('lines', [])]
    for author in evidence['authors']:
        spans = [s for row in rows for s in row['spans'] if s['text'] == author['name']]
        assert len(spans) == 1 and spans[0]['font'] == 'ArnoPro-SmbdSubhead'
        assert spans[0]['size'] == pytest.approx(10)
    markers = [r for r in evidence['preserve'] if r['kind'] == 'decoration']
    assert len(markers) == 4
    assert len(evidence['headings']) == 4
    assert all(r['glyph_box'][3]-r['glyph_box'][1] == pytest.approx(32, abs=.001) for r in markers)
    assert all(6 < r['box'][3]-r['box'][1] < 9 for r in markers)
    assert all(h['box'][3]-h['box'][1] == pytest.approx(10.5) for h in evidence['headings'])
    # Detached right-hand ORCID on Yujie's continuation must be in his band.
    assert evidence['authors'][0]['box'][2] >= 287
    for marker in markers:
        ink = fitz.Rect(marker['box'])
        for row in rows:
            if row['bbox'][1] >= ink.y1 + 1:
                assert not ink.intersects(fitz.Rect(row['bbox']))
    projected = native_il(page, lambda line: line['bbox'][0] < 300 and 276 <= line['bbox'][1] <= 639)
    glyphs = [c for p in projected.pdf_paragraph[0].pdf_paragraph_composition for c in composition_chars(p)]
    split_structures(projected, evidence)
    author_chunks = [p for p in projected.pdf_paragraph if any(p.unicode.startswith(name) for name in names)]
    assert len(author_chunks) == 8
    assert all(p.unicode.startswith(name) for p, name in zip(author_chunks, names))
    assert [c for p in projected.pdf_paragraph for part in p.pdf_paragraph_composition for c in composition_chars(part)] == glyphs
    (tmp_path / 'walnut-p10-structure.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')


def test_actual_walnut_p1_navigation_preserves_items_and_source_baselines(walnut, tmp_path):
    page = walnut[0]
    evidence = source_structure(page)
    nav = [r for r in evidence['preserve'] if r['kind'] == 'navigation']
    by_text = {r['text']: r for r in nav}
    labels = ['Read Online', 'ACCESS', 'Metrics & More', 'Article Recommendations', 'Supporting Information']
    assert all(text in by_text for text in labels)
    assert len(nav) == 6  # five labels plus one merged two-layer icon
    assert len({round(by_text[text]['baseline'], 3) for text in labels[2:]}) == 1
    assert by_text['Read Online']['baseline'] == pytest.approx(200.409973)
    assert by_text['ACCESS']['baseline'] == pytest.approx(236.353027)
    projected = native_il(page, lambda line: 193 <= line['bbox'][1] < 280)
    split_structures(projected, evidence)
    text = ''.join(p.unicode for p in projected.pdf_paragraph)
    assert all(label not in text for label in labels)
    assert 'ABSTRACT:' in text and 'Indole-3-lactic acid' in text
    assert 'Cite This:' in text
    (tmp_path / 'walnut-p1-navigation.json').write_text(json.dumps(nav, ensure_ascii=False, indent=2), encoding='utf-8')


def test_navigation_requires_link_geometry_not_label_text():
    import pymupdf as fitz
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for x, text in [(40, 'Metrics & More'), (230, 'Article Recommendations'), (440, 'Supporting Information')]:
            page.insert_text((x, 150), text, fontsize=8)
        assert not [r for r in source_structure(page)['preserve'] if r['kind'] == 'navigation']


def metadata_document(labels=('Report', 'Report', 'Report'), *, y=45, first_offset=0, badge=False):
    import pymupdf as fitz
    pdf = fitz.open()
    for n, label in enumerate(labels):
        page = pdf.new_page(width=600, height=800)
        baseline = y + (first_offset if n == 0 else 0)
        if badge:
            page.draw_rect(fitz.Rect(515, baseline-9, 560, baseline+3), color=None, fill=(.2, .4, .2))
        page.insert_text((525, baseline), label, fontsize=8, color=(1, 1, 1) if badge else (0, 0, 0))
        page.insert_text((50, 150), 'Body text is not page metadata', fontsize=10)
    return pdf


def test_running_metadata_requires_repeated_text_style_position_and_margin():
    for kwargs, expected in [({}, True), ({'labels': ('Report',)}, False),
                             ({'labels': ('Report', 'Other', 'Report')}, False),
                             ({'y': 150}, False), ({'first_offset': 44}, False),
                             ({'first_offset': 44, 'badge': True}, True)]:
        with metadata_document(**kwargs) as pdf:
            evidence = source_structure(pdf[0])
            found = [r for r in evidence['preserve'] if r['kind'] == 'running_metadata']
            assert bool(found) == expected
            if found:
                assert found[0]['text'] == 'Report'
                assert len(found[0]['evidence']) >= 3
                projected = native_il(pdf[0])
                split_structures(projected, evidence)
                text = ''.join(p.unicode for p in projected.pdf_paragraph)
                assert 'Report' not in text and 'Body text' in text


def test_running_metadata_rejects_changed_font_or_position():
    import pymupdf as fitz
    for font, x in [('cour', 525), ('helv', 500)]:
        with metadata_document(labels=('Report', 'Report')) as pdf:
            page = pdf.new_page(width=600, height=800)
            page.insert_text((x, 45), 'Report', fontsize=8, fontname=font)
            assert not [r for r in source_structure(pdf[0])['preserve'] if r['kind'] == 'running_metadata']


def test_actual_walnut_repeated_badge_including_shifted_first_page(walnut):
    for number in range(len(walnut)):
        evidence = source_structure(walnut[number])
        records = [r for r in evidence['preserve'] if r['kind'] == 'running_metadata' and r['text'] == 'Article']
        assert len(records) == 1
        assert records[0]['font'] == 'MyriadPro-Regular'
        assert records[0]['size'] == pytest.approx(8, abs=.001)
        projected = native_il(walnut[number], lambda line: line['bbox'][1] < 100)
        split_structures(projected, evidence)
        assert 'Article' not in ''.join(p.unicode for p in projected.pdf_paragraph)


def test_actual_keywords_three_midword_fragments_rejoin_losslessly(walnut):
    """025763 boundary log: 'KEYWORDS: L' / 'act...genomi' / 'cs'."""
    original = native_il(walnut[0], lambda line: any('KEYWORDS:' in ''.join(c['c'] for c in s['chars'])
                                                   for s in line['spans']))
    chars = [c for part in original.pdf_paragraph[0].pdf_paragraph_composition for c in composition_chars(part)]
    text = ''.join(c.char_unicode for c in chars)
    assert text.startswith('KEYWORDS: Lactiplantibacillus') and text.endswith('genomics')
    cut = text.index('Lactiplantibacillus')+1
    fragments = [chars[:cut], chars[cut:-2], chars[-2:]]
    original.pdf_paragraph = [page_with([run(chunk)]).pdf_paragraph[0] for chunk in fragments]
    before = [c for p in original.pdf_paragraph for part in p.pdf_paragraph_composition for c in composition_chars(part)]
    changes = split_structures(original, source_structure(walnut[0]))
    assert len(original.pdf_paragraph) == 1
    assert original.pdf_paragraph[0].unicode == text
    assert [c for part in original.pdf_paragraph[0].pdf_paragraph_composition for c in composition_chars(part)] == before
    assert any(c['kind'] == 'native_metadata_line' for c in changes)


def test_keywords_merge_requires_complete_native_content_and_never_joins_neighbor():
    import pymupdf as fitz
    with fitz.open() as pdf:
        native = pdf.new_page(width=600, height=800)
        native.insert_text((50, 100), 'Keywords: alpha, beta, genomics', fontsize=10)
        projected = native_il(native)
        chars = [c for part in projected.pdf_paragraph[0].pdf_paragraph_composition for c in composition_chars(part)]
        for bad in ('missing', 'duplicate', 'neighbor'):
            fragments = [chars[:12], chars[12:]]
            if bad == 'missing':
                fragments[1] = fragments[1][:-1]
            elif bad == 'duplicate':
                fragments[1] = [chars[11], *fragments[1]]
            else:
                fragments[1] = [*fragments[1], glyph('X', 50, y=650)]
            projected.pdf_paragraph = [page_with([run(chunk)]).pdf_paragraph[0] for chunk in fragments]
            before = list(projected.pdf_paragraph)
            split_structures(projected, source_structure(native))
            assert projected.pdf_paragraph == before


def numbered_document(numbers=('101', '102', '103'), *, changed_font=None, shifted=None):
    import pymupdf as fitz
    pdf = fitz.open()
    for index, number in enumerate(numbers):
        page = pdf.new_page(width=600, height=800)
        if number is not None:
            page.insert_text((290, 780 if index != shifted else 770), number, fontsize=8,
                             fontname='hebo' if index == changed_font else 'helv')
        page.insert_text((100, 400), '102', fontsize=8)  # identical body digits
        page.insert_text((100, 780), '2026', fontsize=8)  # repeated footer year
    return pdf


def test_page_numbers_require_adjacent_source_sequence_and_keep_body_digits():
    with numbered_document() as pdf:
        for index, expected in enumerate(('101', '102', '103')):
            records = [r for r in source_structure(pdf[index])['preserve'] if r['kind'] == 'page_number']
            assert [r['text'] for r in records] == [expected]
            assert len(records[0]['sequence']) >= 2
        evidence = source_structure(pdf[1])
        projected = native_il(pdf[1])
        split_structures(projected, evidence)
        assert ''.join(p.unicode for p in projected.pdf_paragraph) == '1022026'


@pytest.mark.parametrize('kwargs', [dict(numbers=('101', '102', '104')),
    dict(numbers=('102',)), dict(numbers=('101', '102', None)),
    dict(changed_font=2), dict(shifted=2)])
def test_page_number_sequence_font_position_and_missing_neighbor_counterexamples(kwargs):
    with numbered_document(**kwargs) as pdf:
        page = pdf[min(1, len(pdf)-1)]
        assert not [r for r in source_structure(page)['preserve'] if r['kind'] == 'page_number']


def test_actual_walnut_page_7588_is_verified_by_7587_and_7589(walnut, tmp_path):
    evidence = source_structure(walnut[9])
    records = [r for r in evidence['preserve'] if r['kind'] == 'page_number']
    assert len(records) == 1 and records[0]['text'] == '7588'
    assert [(r['page'], r['text']) for r in records[0]['sequence']] == [(9, '7587'), (10, '7588'), (11, '7589')]
    assert records[0]['font'] == 'MyriadPro-Semibold'
    projected = native_il(walnut[9], lambda line: line['bbox'][1] > 760)
    split_structures(projected, evidence)
    assert '7588' not in ''.join(p.unicode for p in projected.pdf_paragraph)
    # Exact narrower IL box reported by the real integration-2 failure.
    x0, y0, x1, y1 = 295.8244947, 30.4749011, 311.251789, 35.5148339
    chars = [PdfCharacter(char_unicode=char, pdf_character_id=ord(char),
        box=Box(x0+i*(x1-x0)/4, y0, x0+(i+1)*(x1-x0)/4, y1),
        pdf_style=PdfStyle(font_id='MyriadPro-Semibold', font_size=7.4999)) for i, char in enumerate('7588')]
    failed = page_with([run(chars)])
    failed.cropbox = Cropbox(Box(0, 0, walnut[9].rect.width, walnut[9].rect.height))
    assert split_structures(failed, evidence)[0]['restored_glyphs'] == 4
    assert failed.pdf_paragraph == []
    (tmp_path / 'walnut-page-number.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
