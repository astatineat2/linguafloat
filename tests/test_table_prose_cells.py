"""Native prose cells reduced from the November 18 Table 2 evidence.

Translations below are fixed geometry fixtures, not model responses.
The optional source checks use the recorded local task, never a service.
"""
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace as NS

import pymupdf as f
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_graphics import GraphicAnalyzer, apply_graphics
from linguafloat.document_graphic_layout import plan_area


@pytest.mark.parametrize('text', ['Fiber/Prebiotic', 'Male/Female', 'Treatment/Control'])
def test_slash_separated_natural_labels_are_translation_targets(text):
    from linguafloat.document_graphics import natural_word
    assert natural_word(text)


@pytest.mark.parametrize('text', ['mg/kg', 'mol/mol', 'mL/min', 'W/mK', 'M/Z', 'CD4/CD8'])
def test_slash_scientific_units_and_identifiers_are_not_natural_labels(text):
    from linguafloat.document_graphics import natural_word
    assert not natural_word(text)


def analyzer(bounds):
    instance = GraphicAnalyzer(None, JobControl(), lambda _: None)
    instance.discover_areas = lambda *a: [dict(id='table', kind='table', box=bounds)]
    instance.ocr = NS(recognize=lambda *a, **kw: [])
    return instance


def prose_table(page, ruled=False):
    for y in (30, 48, 240):
        page.draw_line((20, y), (350, y), width=.5)
    if ruled:
        for x in (20, 100, 245, 350):
            page.draw_line((x, 30), (x, 240), width=.5)
        for y in (110, 175):
            page.draw_line((20, y), (350, y), width=.5)
    rows = [('Factor', 25, 42), ('Action', 105, 42), ('References', 250, 42),
            ('Substrate supply', 25, 60), ('(1) Tryptophan is the', 105, 60),
            ('essential precursor for', 117, 70), ('indole derivative synthesis.', 117, 80),
            ('(2) Products like IAA and', 105, 90), ('IPA support growth.', 117, 100),
            ('Smith et al. (2024);', 250, 60), ('Jones et al. (2023)', 250, 70),
            ('Dietary patterns', 25, 122), ('(1) Balanced diets promote de\u00ad', 105, 122),
            ('rivatives by supporting a', 117, 132), ('healthy microbiome.', 117, 142),
            ('Smith et al. (2024)', 250, 122), ('Conditions', 25, 187),
            ('(1) Optimal IAA synthesis in', 105, 187),
            ('occurs at 37 C and pH 7.0.', 117, 207),
            ('Smith et al. (2024)', 250, 187)]
    # Base-14 Helvetica extracts the discretionary hyphen as a hard dash.
    # The embedded face has a Unicode map, as in the recorded source.
    page.insert_font(fontname='Fixture', fontbuffer=f.Font('helv').buffer)
    for text, x, y in rows:
        page.insert_text((x, y), text, fontsize=7, fontname='Fixture' if '\u00ad' in text else 'helv')
    page.insert_text((117, 197), 'B. pyrrocinia', fontsize=7, fontname='heit')


@pytest.mark.parametrize('ruled', [False, True])
def test_prose_cell_groups_continuations_and_short_words(ruled):
    with f.open() as pdf:
        definitions = pdf.new_page()
        definitions.insert_text((20, 30), 'Indole acetic acid (IAA) and indole propionic acid (IPA).')
        page = pdf.new_page(width=380, height=270)
        prose_table(page, ruled)
        area = analyzer([20, 30, 350, 240]).discover(page, 'prose', kinds={'table'})[0]
        texts = [r['text'] for r in area['regions']]
        assert 'Tryptophan is the essential precursor for indole derivative synthesis.' in texts
        assert any('promote derivatives by supporting a healthy microbiome.' in t for t in texts)
        assert not any('pyrrocinia' in t or 'IAA' in t or 'IPA' in t for t in texts)
        assert not any(t.strip() in {'is', 'de-', 'de\u00ad', 'rivatives'} for t in texts)
        candidates = [r for r in plan_area(page, area) if 'Products like' in r['text']]
        assert len(candidates) == 1
        assert candidates[0]['cell_box'][2] <= 250
        assert candidates[0]['row'] == 2 and candidates[0]['column'] == 2
        assert len(area['cells']) == 12


def run_fixture(page, target, fail=None, *, value='\u8bd1\u6587', bounds=(20, 30, 350, 240)):
    instance = analyzer(list(bounds))
    def translate(text, *a, **kw):
        if fail and fail in text:
            raise ValueError('fixed fixture failure')
        return value if not callable(value) else value(text)
    translator = NS(control=instance.control, translate_fragment=translate,
        job=NS(translate_tables=True, translate_figures=False, target='zh-Hans',
               skip_references=False, chinese_font='', latin_font=''))
    return apply_graphics(page, target, translator, instance, 'prose')


def test_complete_cell_render_preserves_science_and_compacts_prose():
    with f.open() as source, f.open() as target:
        source.new_page().insert_text((20, 30), 'Indole acetic acid (IAA) and indole propionic acid (IPA).')
        page = source.new_page(width=380, height=270)
        prose_table(page)
        target.insert_pdf(source, from_page=1, to_page=1)
        records = run_fixture(page, target[0])
        assert records and all(r['status'] == 'translated' for r in records), records
        cells = [r for r in records if r.get('prose_cell') and r['column'] == 2]
        assert {r['row'] for r in cells} == {2, 3, 4}
        assert all(r['size'] >= 6 and r['native_vectors'] for r in cells)
        assert all(not r['header_cell'] and 'header_content' not in r for r in cells)
        assert all(r['rows'] <= 4 for r in cells)
        text = target[0].get_text()
        for token in ('is', 'Tryptophan', 'derivatives', 'rivatives', 'occurs'):
            assert token not in text
        for token, count in [('IAA', 2), ('IPA', 1), ('pyrrocinia', 1), ('7.0', 1)]:
            assert text.count(token) == count
        assert text.count('(1)') == 3 and text.count('(2)') == 1
        clip = f.Rect(248, 50, 345, 235)
        assert page.get_pixmap(clip=clip, matrix=f.Matrix(3, 3)).samples == target[0].get_pixmap(clip=clip, matrix=f.Matrix(3, 3)).samples


@pytest.mark.parametrize('failure,bad_value', [('translation', None), ('empty', ' '),
                                               ('glyph', '\U0001f9ec'), ('width', 'A'*200)])
def test_failed_fragment_preserves_entire_prose_cell(failure, bad_value):
    with f.open() as source, f.open() as target:
        page = source.new_page(width=380, height=270)
        prose_table(page, ruled=True)
        target.insert_pdf(source)
        area = analyzer([20, 30, 350, 240]).discover(page, 'prose', kinds={'table'})[0]
        expected = {r['id'] for r in plan_area(page, area) if r['row'] == 2 and r['column'] == 2}
        records = run_fixture(page, target[0], fail='Products' if failure=='translation' else None,
            value=lambda t: bad_value if 'Products' in t else '\u8bd1\u6587')
        clip = f.Rect(101, 49, 244, 109)
        assert page.get_pixmap(clip=clip).samples == target[0].get_pixmap(clip=clip).samples
        failed = [r for r in records if r['status'] == 'needs_review']
        assert Counter(r['id'] for r in failed) == Counter(expected)
        assert page.get_text(clip=clip) == target[0].get_text(clip=clip)
        assert any(r.get('prose_cell') and r['status']=='translated' for r in records)


def test_prose_layout_keeps_translated_word_spaces():
    with f.open() as source, f.open() as target:
        page = source.new_page(width=380, height=270)
        prose_table(page)
        target.insert_pdf(source)
        records = run_fixture(page, target[0], value='New text')
        cell = next(r for r in records if r.get('row')==3 and r.get('column')==2)
        assert cell['status']=='translated'
        assert 'New text' in cell['cell_lines'][0]['text']
        assert 'New text' in target[0].get_text(clip=f.Rect(cell['cell_box']))


def test_actual_review_table2_has_cells_and_complete_prose():
    task = Path(__file__).resolve().parents[1] / '.scratch/six-quality-20261118/e99ed9afc949/task.json'
    if not task.exists():
        pytest.skip('Recorded review task is local optional evidence')
    source = Path(json.loads(task.read_text(encoding='utf-8-sig'))['source'])
    if not source.exists():
        pytest.skip('Original review source is unavailable')
    with f.open(source) as pdf:
        page = pdf[16]
        area = analyzer([37.61, 74.67, 288.67, 649.93]).discover(page, 'actual-review', kinds={'table'})[0]
        assert len(area['cells']) == 21
        texts = [r['text'] for r in area['regions']]
        assert 'Tryptophan is the essential precursor for indole derivative synthesis.' in texts
        assert any('Its availability directly determines the yield of products like' in t for t in texts)
        assert not any('pyrrocinia' in t for t in texts)
        assert not any('Zhang' in t or 'Zhuang' in t for t in texts)
        planned = plan_area(page, area)
        assert all(r['cell_box'][2] < 211 for r in planned if 'products like' in r['text'])
        assert any('high-fiber' in t and 'indole derivatives' in t for t in texts)
        with f.open() as target:
            target.insert_pdf(pdf, from_page=16, to_page=16)
            records = run_fixture(page, target[0], bounds=area['box'])
            actions = [r for r in records if r.get('prose_cell')]
            assert {(r['row'],r['column']) for r in actions} == {(row,2) for row in range(2,8)}
            assert all(r['status']=='translated' and not r['header_cell'] for r in actions)
            chars = [c for b in target[0].get_text('rawdict', flags=f.TEXTFLAGS_WORDS)['blocks']
                     for l in b.get('lines', []) for sp in l['spans'] for c in sp['chars'] if not c['c'].isspace()]
            for row in range(2,8):
                record = next(r for r in actions if r['row']==row)
                cell, layout = f.Rect(record['cell_box']), f.Rect(record['layout_box'])
                glyphs = [c for c in chars if cell.contains((f.Rect(c['bbox']).tl+f.Rect(c['bbox']).br)/2)]
                assert all((layout+(-.03,-.03,.03,.03)).contains(c['bbox']) for c in glyphs)
                content = ''.join(line['text'] for line in record['cell_lines'])
                assert Counter(''.join(content.split())) == Counter(c['c'] for c in glyphs)
                assert sum(line['text'].startswith('(') and line['text'][1].isdigit()
                           for line in record['cell_lines']) == (2 if row in (2,6) else 3)
            refs = f.Rect(205,89,288,649)
            assert target[0].get_text(clip=refs) == page.get_text(clip=refs)
