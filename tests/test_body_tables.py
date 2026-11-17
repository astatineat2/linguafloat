"""Independent tables belong to body jobs, never to the image-only UI action."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pymupdf as f

from linguafloat.document_core import DocumentJob, JobControl
from linguafloat.document_graphics import GraphicAnalyzer
from linguafloat.document_graphic_layout import native_grid_cells, plan_area


def three_line_table(page, wrapped=False):
    edges = (20, 135, 200, 280)
    for left, right in zip(edges, edges[1:]):
        page.draw_rect(f.Rect(left, 20, right, 48), color=None, fill=(.9, .9, .9))
    page.draw_line((20, 120), (280, 120))
    for text, x, y in [('compounds', 24, 40), ('molecular', 140, 32), ('formula', 140, 40),
                       ('retention', 205, 32), ('time', 205, 40),
                       ('Ferulic acid', 24, 62), ('C10H10O4', 140, 62), ('9.24', 210, 62),
                       ('Gallic acid', 24, 80), ('C7H6O5', 140, 80), ('2.17', 210, 80),
                       ('Caffeic' if wrapped else 'Caffeic acid', 24, 98),
                       ('C9H8O4', 140, 98), ('3.54', 210, 98)]:
        page.insert_text((x, y), text, fontsize=7)
    if wrapped:
        page.insert_text((30, 106), 'acid', fontsize=7)


def test_wrapped_first_column_keeps_one_data_row():
    with f.open() as pdf:
        page = pdf.new_page(width=320, height=160)
        three_line_table(page, wrapped=True)
        area = dict(id='table', kind='table', box=[20, 20, 280, 120])
        cells = native_grid_cells(page, area)
        assert len(cells) == 12
        final = next(c for c in cells if c['row'] == 4 and c['column'] == 1)
        words = page.get_text('words')
        assert {'Caffeic', 'acid'} <= {w[4] for w in words if f.Rect(final['box']).contains(
            (f.Rect(w[:4]).tl+f.Rect(w[:4]).br)/2)}
        analyzer = GraphicAnalyzer(None, JobControl(), lambda _: None)
        analyzer.discover_areas = lambda *a: [dict(area)]
        analyzer.ocr = NS(recognize=lambda *a, **kw: [])
        assert 'Caffeic acid' in {r['text'] for r in analyzer.discover(page, 'wrapped', kinds={'table'})[0]['regions']}


def test_mixed_header_joins_natural_words_without_sending_units():
    from linguafloat.document_graphics import cell_graphic_lines
    def line(text, y, words):
        return dict(text=text, box=[20, y-7, 95, y+1], horizontal=True,
            source='native', confidence=1, baseline=y, font_size=7,
            words=[dict(text=t, box=[x, y-7, x+w, y+1]) for t,x,w in words])
    lines = [line('(peak', 30, [('(peak',20,18)]),
             line('area 10', 38, [('area',22,14), ('10',39,9)])]
    joined = cell_graphic_lines(lines, [dict(box=[18,20,100,42])])
    assert '(peak area' in {l['text'] for l in joined}
    assert any(w['text']=='10' for l in joined for w in l['words'])
    outside = cell_graphic_lines(lines, [])
    assert {l['text'] for l in outside} == {'(peak', 'area 10'}


def test_body_allows_independent_tables_but_not_automatic_images(tmp_path):
    source = tmp_path / 'table.pdf'
    with f.open() as pdf:
        pdf.new_page()
        pdf.save(source)
    job = DocumentJob(str(source), str(tmp_path), dict(base_url='https://test.invalid',
        model='mock', api_mode='responses'), workflow='body', pdf_mode='layout', translate_tables=True)
    job.validate()
    from linguafloat.document_core import DocumentError
    import pytest
    with pytest.raises(DocumentError):
        replace(job, translate_figures=True).validate()


def test_three_line_table_uses_native_cells_without_ocr():
    with f.open() as pdf:
        page = pdf.new_page(width=320, height=160)
        three_line_table(page)
        area = dict(id='table', kind='table', box=[20, 20, 280, 120])
        cells = native_grid_cells(page, area)
        assert len(cells) == 12
        assert {c['column'] for c in cells} == {1, 2, 3}
        analyzer = GraphicAnalyzer(None, JobControl(), lambda _: None)
        analyzer.discover_areas = lambda *a: [dict(area)]
        def no_ocr(*a, **kw):
            raise AssertionError('A complete native three-line table must not invoke OCR')
        analyzer.ocr = NS(recognize=no_ocr)
        discovered = analyzer.discover(page, 'native-table', kinds={'table'})[0]
        assert {r['text'] for r in discovered['regions']} == {
            'compounds', 'molecular formula', 'retention time',
            'Ferulic acid', 'Gallic acid', 'Caffeic acid'}
        planned = plan_area(page, discovered)
        assert all(f.Rect(r['cell_box']).contains(f.Rect(r['layout_box'])) for r in planned)
        assert not any('9.24' in r['text'] or 'C10H10O4' in r['text'] for r in planned)


def test_table_snapshot_does_not_reuse_preserve_table_configuration(tmp_path):
    from linguafloat.document_artifacts import LayoutSnapshot
    source = tmp_path / 'table.pdf'
    with f.open() as pdf:
        pdf.new_page()
        pdf.save(source)
    old = DocumentJob(str(source), str(tmp_path), {}, workflow='body')
    new = replace(old, translate_tables=True)
    assert LayoutSnapshot(tmp_path, old, [0]).key != LayoutSnapshot(tmp_path, new, [0]).key


def test_compound_names_translate_but_acronym_infix_is_not_rewritten():
    import pytest
    from linguafloat.document_core import DocumentError
    from linguafloat.document_graphics import natural_word, validate_graphic_fragment
    assert natural_word('Galloyl-HHDP-glucose')
    validate_graphic_fragment('Galloyl-HHDP-glucose', '没食子酰基-HHDP-葡萄糖')
    with pytest.raises(DocumentError):
        validate_graphic_fragment('Galloyl-HHDP-glucose', '没食子酰基-葡萄糖')


def test_embedded_table_does_not_replace_owning_figure():
    with f.open() as pdf:
        page = pdf.new_page(width=320, height=220)
        for reverse in (False, True):
            analyzer = GraphicAnalyzer(None, JobControl(), lambda _: None)
            detections = [NS(cls=0, conf=.96, xyxy=[40,40,560,360]),
                          NS(cls=1, conf=.99, xyxy=[60,120,540,300])]
            if reverse:
                detections.reverse()
            analyzer.model = NS(predict=lambda *a, **kw: [NS(names={0:'figure',1:'table'}, boxes=detections)])
            areas = analyzer.discover_areas(page, 'embedded')
            assert len(areas) == 1 and areas[0]['kind'] == 'figure'


def test_native_header_keeps_its_original_tier():
    import numpy as np
    from linguafloat.document_graphic_layout import fit_label, native_cell_layout
    with f.open() as pdf:
        page = pdf.new_page(width=160, height=100)
        region = dict(id='label', text='retention', box=[22,20,50,28.005],
            source_boxes=[[22,20,50,28.005]], native_cell=True, layout_box=[20,18,70,75])
        area = dict(regions=[region], cells=[dict(box=[20,18,70,75],row=1,column=1)],
            lines=[dict(words=[dict(text='retention',box=region['box']),
                               dict(text='(RT)',box=[22,28,45,36])])])
        lane = native_cell_layout(page, area)['label']['layout_box']
        assert lane[3] < 28.1
        fitted = fit_label(page, region, '保留', 'zh-Hans', JobControl(), 7)
        assert fitted
        with f.open(stream=fitted[0], filetype='pdf') as overlay:
            pix=overlay[0].get_pixmap(colorspace=f.csGRAY, matrix=f.Matrix(4,4))
            ys,_=np.where(np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width)<180)
            assert abs(float(ys.mean())/4-24) < 3


def test_native_cell_reserves_diagonal_superscript_corner():
    from linguafloat.document_graphic_layout import native_cell_layout
    with f.open() as pdf:
        page = pdf.new_page(width=120, height=70)
        region = dict(id='label', box=[72,27,90,35], source_boxes=[[72,27,90,35]])
        area = dict(regions=[region], cells=[dict(box=[15,26,105,50],row=1,column=2)],
            lines=[dict(words=[dict(text='label',box=region['box']),
                dict(text='10-7',box=[58,33,76,43])])])
        lane = f.Rect(native_cell_layout(page, area)['label']['layout_box'])
        assert lane.x0 >= 76.5
        assert not lane.intersects(f.Rect(58,33,76,43))
        assert lane.width >= 20 and lane.height >= 7


def test_wide_header_prefers_available_upper_tier_to_tiny_middle_gap():
    from linguafloat.document_graphic_layout import native_cell_layout
    with f.open() as pdf:
        page = pdf.new_page(width=120, height=70)
        region = dict(id='label', box=[20,22,80,30], source_boxes=[[20,22,80,30]])
        area = dict(regions=[region], cells=[dict(box=[15,18,100,45],row=1,column=2)],
            lines=[dict(words=[dict(text='header',box=region['box']),
                dict(text='10-7',box=[25,29.5,45,39]),
                dict(text='%',box=[60,29.5,76,39])])])
        lane = f.Rect(native_cell_layout(page, area)['label']['layout_box'])
        assert lane.width >= 60
        assert lane.y1 < 29.5 and lane.height >= 7
