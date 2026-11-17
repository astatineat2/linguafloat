"""Offline geometry regressions: mock translations do not prove semantics."""
from types import SimpleNamespace as NS
from pathlib import Path

import pymupdf as f
import pytest
import numpy as np

from linguafloat.document_core import JobControl
from linguafloat.document_graphics import GraphicAnalyzer, apply_graphics, join_vertical_labels, glyph_patch
from linguafloat.document_graphic_layout import plan_area, fit_label


def analyzer(box, kind='table', ocr=()):
    a = GraphicAnalyzer(None, JobControl(), lambda _: None)
    a.discover_areas = lambda *args: [dict(id='area', kind=kind, box=box)]
    a.ocr = NS(recognize=lambda *args, **kwargs: list(ocr))
    return a


def translator(values):
    return NS(job=NS(translate_tables=True, translate_figures=True,
                    skip_references=False, target='zh-Hans'),
              control=JobControl(), translate_fragment=lambda text, *a, **k: values[text])


def test_centered_header_does_not_cut_compound_column():
    with f.open() as doc:
        p = doc.new_page(width=250, height=150)
        for text, x, y in [('compounds', 74, 40), ('Caffeic acid', 54, 60),
                           ('Vanillic acid', 54, 80), ('179.0346', 141, 60),
                           ('167.03394', 141, 80)]:
            p.insert_text((x, y), text, fontsize=8)
        area = analyzer([50, 25, 200, 90]).discover(p, 'table')[0]
        region = next(r for r in plan_area(p, area) if r['text'] == 'Caffeic acid')
        assert f.Rect(region['layout_box']).width > 75
        assert region['layout_box'][2] < 141
        assert fit_label(p, region, '咖啡酸', 'zh-Hans', JobControl(), 8, p)


def test_overflow_cell_does_not_block_other_cells_or_change_values():
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=250, height=150)
        for text, x, y in [('Caffeic acid', 30, 50), ('Vanillic acid', 30, 80),
                           ('179.0346', 140, 50), ('167.03394', 140, 80)]:
            p.insert_text((x, y), text, fontsize=8)
        dst.insert_pdf(src)
        a = analyzer([25, 30, 205, 95])
        records = apply_graphics(p, dst[0], translator({'Caffeic acid': '咖啡酸',
            'Vanillic acid': '长译文' * 100}), a, 'isolated')
        assert {r['text']: r['status'] for r in records} == {
            'Caffeic acid': 'translated', 'Vanillic acid': 'needs_review'}
        assert '咖啡酸' in dst[0].get_text() and 'Vanillic acid' in dst[0].get_text()
        clip = f.Rect(135, 35, 205, 90)
        before = np.frombuffer(p.get_pixmap(clip=clip).samples, np.uint8).astype(int)
        after = np.frombuffer(dst[0].get_pixmap(clip=clip).samples, np.uint8).astype(int)
        assert np.max(np.abs(before-after)) <= 3  # PDF rewrite rounding only.
        assert {'179.0346', '167.03394'} <= set(dst[0].get_text().split())


def test_tiny_ocr_label_can_use_blank_space_but_keeps_source_box():
    with f.open() as doc:
        p = doc.new_page(width=160, height=100)
        r = dict(id='small', text='metabolism', kind='figure', box=[30, 40, 68, 44],
                 source='ocr', source_boxes=[[30, 40, 68, 44]], confidence=1, rotation=0)
        area = dict(id='panel', kind='figure', box=[20, 25, 100, 70], regions=[r])
        region = plan_area(p, area)[0]
        assert region['box'] == r['box'] and region['source_boxes'] == r['source_boxes']
        assert fit_label(p, region, '代谢过程', 'zh-Hans', JobControl(), 6, p)
        assert f.Rect(region['layout_box']).height >= 6


def test_expansion_stops_before_neighbour_ink():
    with f.open() as doc:
        p = doc.new_page(width=160, height=100)
        p.draw_line((25, 45), (90, 45), width=1)
        r = dict(id='small', text='metabolism', box=[30, 40, 68, 44],
                 source='ocr', confidence=1, rotation=0)
        area = dict(id='panel', kind='figure', box=[20, 25, 100, 70], regions=[r])
        region = plan_area(p, area)[0]
        assert region['layout_box'][3] <= 44.5


def test_native_rotated_single_word_runs_join_in_reading_order():
    with f.open() as doc:
        p = doc.new_page(width=160, height=160)
        p.insert_text((40, 125), 'Relative', fontsize=7, rotate=90)
        p.insert_text((40, 97), 'abundance', fontsize=7, rotate=90)
        area = analyzer([20, 30, 140, 145], 'figure').discover(p, 'rotation')[0]
        assert [r['text'] for r in area['regions']] == ['Relative abundance']
        assert area['regions'][0]['rotation'] == 90


def test_walnut_rotated_word_geometry_overrides_ocr_horizontal_flag():
    # Actual p3 OCR marks this bottom-to-top run "horizontal" after its own
    # recognition rotation. Page-space word geometry retains the direction.
    line = dict(text='KEGG pathways', confidence=.99858,
        box=[301, 162.5, 306.75, 188.5], horizontal=True, source='ocr', words=[
            dict(text='KEGG', confidence=1, box=[301, 178.5, 306.25, 188.5]),
            dict(text='pathways', confidence=.99998, box=[301.25, 162.5, 306.75, 178.5])])
    result = join_vertical_labels([line])[0]
    assert result['rotation'] == 90 and result['horizontal'] is False
    assert result['text'] == 'KEGG pathways' and result['confidence'] == .99858


def test_stacked_horizontal_words_are_not_rotated_by_union_shape():
    line = dict(text='cell division', confidence=1, source='ocr', horizontal=True,
        box=[30, 30, 50, 80], words=[dict(text='cell', box=[30, 30, 50, 35]),
                                      dict(text='division', box=[30, 75, 50, 80])])
    result = join_vertical_labels([line])[0]
    assert result.get('rotation', 0) == 0


def test_low_ocr_confidence_still_blocks_blank_space_replacement():
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=160, height=100)
        p.insert_text((30, 44), 'metabolism', fontsize=5)
        # Raster-only page: preserve confidence gate rather than native override.
        dst.new_page(width=160, height=100).insert_image(p.rect, pixmap=p.get_pixmap())
        a = analyzer([20, 25, 100, 70], 'figure', [dict(text='metabolism',
            box=[30, 40, 68, 44], confidence=.8, horizontal=True)])
        before = dst[0].get_pixmap().samples
        records = apply_graphics(dst[0], dst[0], translator({}), a, 'low')
        assert records[0]['status'] == 'needs_review'
        assert dst[0].get_pixmap().samples == before


def test_independent_groups_cannot_paint_over_previously_accepted_label(monkeypatch):
    import linguafloat.document_graphic_layout as layout
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=180, height=100)
        p.insert_text((20, 50), 'Alpha', fontsize=8)
        p.insert_text((120, 50), 'Beta', fontsize=8)
        a = analyzer([15, 25, 165, 75], 'figure')
        area = a.discover(p, 'collision')[0]
        # Two independently planned labels attempting the same blank lane.
        monkeypatch.setattr(layout, 'plan_area', lambda page, area: [dict(r,
            layout_box=[70, 35, 95, 50], preferred_size=6, group=r['id'])
            for r in area['regions']])
        dst.insert_pdf(src)
        records = apply_graphics(p, dst[0], translator({'Alpha': '甲乙', 'Beta': '丙丁'}),
                                 NS(discover=lambda *args: [area], emit=lambda _: None), 'collision')
        assert [r['status'] for r in records] == ['translated', 'needs_review']
        assert '甲乙' in dst[0].get_text() and 'Beta' in dst[0].get_text()
        assert '丙丁' not in dst[0].get_text()


def test_native_erase_preserves_superscript_overlapping_word_bbox():
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=180, height=100)
        p.insert_text((40, 50), 'components', fontsize=8)
        p.insert_text((60, 60), '10', fontsize=8)
        p.insert_text((70, 55), '7', fontsize=5)
        box = list(p.search_for('components')[0])
        assert f.Rect(box).intersects(p.search_for('7')[0])
        region = dict(box=box, source_boxes=[box], source='native')
        patch, reason = glyph_patch(p, region)
        assert patch and not reason
        dst.insert_pdf(src)
        for erase in region.get('erase_boxes', region['source_boxes']):
            dst[0].add_redact_annot(f.Rect(erase), fill=False, cross_out=False)
        dst[0].apply_redactions(images=0, graphics=0, text=0)
        assert 'components' not in dst[0].get_text()
        assert '7' in dst[0].get_text() and '10' in dst[0].get_text()


@pytest.mark.parametrize('chinese,family', [('simsun', 'SimSun'), ('msyh', 'Microsoft YaHei')])
@pytest.mark.parametrize('rotation', [0, 90])
def test_job_font_policy_reaches_mixed_rotated_overlays(chinese, family, rotation):
    if not Path('C:/Windows/Fonts/simsun.ttc').is_file():
        pytest.skip('Installed Windows fonts required; never download fonts')
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=240, height=220)
        p.insert_text((50, 160), 'Concentration', fontsize=10, fontname='hebi', rotate=rotation)
        a = analyzer([20, 20, 220, 195], 'figure')
        t = translator({'Concentration': '浓度 ABC'})
        t.job.chinese_font, t.job.latin_font = chinese, 'times'
        dst.insert_pdf(src)
        records = apply_graphics(p, dst[0], t, a, 'fonts')
        assert records[0]['status'] == 'translated', records
        faces = records[0]['font_policy']['used_faces']
        assert {face['family'] for face in faces} == {family, 'Times New Roman'}
        assert all(face['bold'] and face['italic'] for face in faces)
        assert next(face for face in faces if face['family'] == family)['synthetic_italic']
        text = dst[0].get_text()
        assert '浓度 ABC' in text and 'Concentration' not in text
        lines = [l for b in dst[0].get_text('dict')['blocks'] for l in b.get('lines', [])]
        assert any(abs(l['dir'][1]) > .9 for l in lines) == bool(rotation)


def test_invalid_partial_font_policy_does_not_silently_fallback():
    from linguafloat.document_core import DocumentError
    with f.open() as doc:
        p = doc.new_page()
        t = translator({})
        t.job.chinese_font, t.job.latin_font = 'simsun', ''
        with pytest.raises(DocumentError):
            apply_graphics(p, p, t, analyzer([20, 20, 200, 200]), 'invalid-font')


def test_selected_font_missing_glyph_preserves_original():
    if not Path('C:/Windows/Fonts/simsun.ttc').is_file():
        pytest.skip('Installed Windows fonts required; never download fonts')
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=180, height=100)
        p.insert_text((30, 50), 'Concentration', fontsize=10)
        dst.insert_pdf(src)
        before = dst[0].get_pixmap().samples
        t = translator({'Concentration': '\U0010ffff'})
        t.job.chinese_font, t.job.latin_font = 'simsun', 'times'
        records = apply_graphics(p, dst[0], t, analyzer([20, 20, 160, 80], 'figure'), 'missing-glyph')
        assert records[0]['status'] == 'needs_review'
        assert 'U+10FFFF' in records[0]['reason']
        assert dst[0].get_pixmap().samples == before
