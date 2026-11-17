"""Offline native-grid regressions; model and translation calls are fixtures."""
from types import SimpleNamespace as NS

import numpy as np
import pymupdf as f
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_graphics import GraphicAnalyzer, apply_graphics
from linguafloat.document_graphic_layout import plan_area


def analyzer(kind='table', ocr=()):
    a = GraphicAnalyzer(None, JobControl(), lambda _: None)
    a.model = NS(predict=lambda *a, **kw: [NS(names={0: kind}, boxes=[
        NS(cls=0, conf=.96, xyxy=[40, 40, 560, 360])])])
    a.ocr = NS(recognize=lambda *a, **kw: list(ocr))
    return a


def grid(page, caption='Table 1 | Magnetic groups and crystal structures.'):
    for x in (20, 100, 190, 280):
        page.draw_line((x, 20), (x, 180), width=.6)
    for y in (20, 75, 125, 180):
        page.draw_line((20, y), (280, y), width=.6)
    for text, x, y in [('Crystal', 30, 40), ('structure', 30, 48.4),
                       ('Fe', 120, 45), ('NiF2', 210, 45),
                       ('Spin', 30, 92), ('ordering', 30, 100.4),
                       ('Collinear', 110, 92), ('uncompensated', 110, 100.4),
                       ('Collinear', 200, 92), ('compensated', 200, 100.4),
                       ('Spin-only group', 25, 140),
                       ('Non-trivial spin', 25, 157), ('point group', 25, 165.4),
                       ('SO(2)', 120, 150), ('Mn3Sn', 210, 150)]:
        page.insert_text((x, y), text, fontsize=7)
    page.insert_text((20, 197), caption, fontsize=7)


def test_standalone_grid_joins_native_lines_and_never_ocr_complete_native_cells():
    with f.open() as doc:
        p = doc.new_page(width=320, height=220)
        grid(p)
        a = analyzer()
        a.ocr.recognize = lambda *a, **kw: pytest.fail('Native grid must not invoke OCR')
        area = a.discover(p, 'native')[0]
        texts = {r['text'] for r in area['regions']}
        assert texts == {'Crystal structure', 'Spin ordering', 'Collinear uncompensated',
                         'Collinear compensated', 'Spin-only group', 'Non-trivial spin point group'}
        planned = plan_area(p, area)
        assert all(r.get('geometry') == 'table-rules' for r in planned)
        assert all(f.Rect(r['cell_box']).contains(f.Rect(r['layout_box'])) for r in planned)
        for i, region in enumerate(planned):
            assert all(not f.Rect(region['layout_box']).intersects(f.Rect(other['layout_box']))
                       for other in planned[i+1:])


def test_grid_borders_prevent_adjacent_row_labels_from_merging():
    with f.open() as doc:
        p = doc.new_page(width=320, height=220)
        grid(p)
        # Baselines would pass the old label-joining heuristic, but a rule
        # between them means these are separate cells.
        p.insert_text((250, 72), 'Upper', fontsize=6)
        p.insert_text((250, 79), 'lower', fontsize=6)
        area = analyzer().discover(p, 'borders')[0]
        texts = {r['text'] for r in area['regions']}
        assert 'Upper lower' not in texts
        assert {'Upper', 'lower'} <= texts


def test_incomplete_native_grid_keeps_ocr_fallback():
    with f.open() as doc:
        p = doc.new_page(width=320, height=220)
        grid(p)
        p.add_redact_annot(p.search_for('NiF2')[0], fill=False)
        p.apply_redactions(images=0, graphics=0)
        a = analyzer(ocr=[dict(text='Raster label', box=[210, 35, 250, 45], confidence=1,
                              words=[dict(text='Raster', box=[210, 35, 230, 45]),
                                     dict(text='label', box=[232, 35, 250, 45])]),
                          dict(text='Noise', box=[125, 55, 150, 65], confidence=1)])
        area = a.discover(p, 'partial')[0]
        assert 'Raster label' in {r['text'] for r in area['regions']}
        assert 'Noise' not in {r['text'] for r in area['regions']}


def test_mixed_cell_keeps_formula_and_native_image_outside_replacement_lane():
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=320, height=220)
        grid(p)
        p.insert_text((120, 165), 'Magnetic order', fontsize=7)
        pix = f.Pixmap(f.csRGB, f.IRect(0, 0, 5, 5), False)
        pix.clear_with(180)
        p.insert_image(f.Rect(155, 30, 180, 60), pixmap=pix)
        a = analyzer()
        area = a.discover(p, 'mixed')[0]
        region = next(r for r in plan_area(p, area) if r['text'] == 'Magnetic order')
        assert not f.Rect(region['layout_box']).intersects(p.search_for('SO(2)')[0])
        dst.insert_pdf(src)
        t = NS(control=JobControl(), job=NS(translate_figures=False, translate_tables=True,
            target='zh-Hans', skip_references=False), translate_fragment=lambda *a, **k: '磁序')
        records = apply_graphics(p, dst[0], t, a, 'mixed')
        assert all(r['status'] == 'translated' for r in records), records
        assert 'SO(2)' in dst[0].get_text()
        clip = f.Rect(115, 140, 150, 153)
        before = np.frombuffer(p.get_pixmap(clip=clip).samples, np.uint8).astype(int)
        after = np.frombuffer(dst[0].get_pixmap(clip=clip).samples, np.uint8).astype(int)
        assert np.max(np.abs(before-after)) <= 3
        clip = f.Rect(150, 25, 185, 65)
        assert p.get_pixmap(clip=clip).samples == dst[0].get_pixmap(clip=clip).samples


@pytest.mark.parametrize('kind,caption', [('figure', 'Box Fig. 1 | Magnetic groups and crystal structures.'),
                                         ('table', 'Table 1 | Magnetic groups and crystal structures.')])
@pytest.mark.parametrize('figures,tables', [(False, False), (False, True), (True, False), (True, True)])
def test_switches_preservation_and_body_context(kind, caption, figures, tables):
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=320, height=220)
        grid(p, caption)
        # Deliberately replay the model's table classification of Box Fig. 1.
        a = analyzer('table')
        dst.insert_pdf(src)
        calls = []
        def translate(text, context, **kw):
            calls.append((text, context))
            return '晶体结构' if text == 'Crystal structure' else '自旋排列'
        t = NS(control=JobControl(), job=NS(translate_figures=figures, translate_tables=tables,
               target='zh-Hans', skip_references=False), translate_fragment=translate,
               graphic_context=NS(packet=lambda page, text, cap: {
                   'body_translation': '已完成正文的术语', 'caption_source': cap}))
        records = apply_graphics(p, dst[0], t, a, 'switches')
        enabled = figures if kind == 'figure' else tables
        assert bool(calls) == enabled
        if not enabled:
            assert not records
            assert p.get_pixmap().samples == dst[0].get_pixmap().samples
            return
        assert all(r['kind'] == kind and r['geometry'] == 'table-rules' for r in records)
        assert all(r['status'] == 'translated' for r in records), records
        assert all('已完成正文的术语' in c for _, c in calls)
        assert all(caption in c for _, c in calls)
        assert not {'Fe', 'NiF2', 'Mn3Sn', 'SO(2)'} & {text for text, _ in calls}
        assert all(token in dst[0].get_text() for token in ('Fe', 'NiF2', 'Mn3Sn', 'SO(2)'))
        for clip in (f.Rect(105, 21, 278, 74), f.Rect(105, 126, 278, 179)):
            before = np.frombuffer(p.get_pixmap(clip=clip, matrix=f.Matrix(3, 3)).samples, np.uint8)
            after = np.frombuffer(dst[0].get_pixmap(clip=clip, matrix=f.Matrix(3, 3)).samples, np.uint8)
            assert np.max(np.abs(before.astype(int)-after.astype(int))) <= 3
