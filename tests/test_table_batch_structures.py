"""Offline native structures reduced from the two November batch papers."""
from types import SimpleNamespace as NS

import pymupdf as f
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_graphics import GraphicAnalyzer, natural_word
from linguafloat.document_graphic_layout import plan_area


def analyzer(detections=()):
    instance = GraphicAnalyzer(None, JobControl(), lambda _: None)
    instance.model = NS(predict=lambda *a, **kw: [NS(names={0: 'figure', 1: 'table'},
        boxes=[NS(cls=kind, conf=.97, xyxy=[v*2 for v in box]) for kind, box in detections])])
    instance.ocr = NS(recognize=lambda *a, **kw: [])
    return instance


def shaded_header_table(page):
    # Merged first tier and individually shaded second tier, as on source p5.
    edges = (25, 100, 195, 240, 305, 370)
    for left, right in zip(edges, edges[1:]):
        page.draw_rect(f.Rect(left, 45, right, 60), color=None, fill=(.9, .9, .9))
        page.draw_rect(f.Rect(left, 60, right, 80), color=None, fill=(.9, .9, .9))
    page.draw_line((25, 168), (370, 168), width=.5)
    for text, x, y in [('location', 250, 55), ('aminotransferase', 27, 73),
                       ('gene annotation', 102, 73), ('BLASTP', 197, 73),
                       ('SDMCC050455', 242, 73), ('SDMCC050493', 307, 73)]:
        page.insert_text((x, y), text, fontsize=6)
    for row, y in enumerate((90, 108, 126, 144)):
        for text, x in [(f'AraT{row+1}', 30), ('amino transferase', 102),
                        ('pyridoxal enzyme', 197), ('+', 260), ('97.98%', 320)]:
            page.insert_text((x, y), text, fontsize=6)
    page.insert_text((25, 33), 'Table 1. Sequence analysis of putative enzymes.', fontsize=7)


def test_native_shaded_header_recovers_whole_missed_table_without_owning_figure():
    with f.open() as document:
        page = document.new_page(width=400, height=320)
        shaded_header_table(page)
        page.insert_text((25, 250), 'Original figure label', fontsize=7)
        page.insert_text((25, 286), 'Figure 3. Genetic structure.', fontsize=7)
        instance = analyzer([(0, [25, 200, 370, 275])])
        areas = instance.discover_areas(page, 'missed-native-table')
        tables = [a for a in areas if a['kind'] == 'table']
        assert len(tables) == 1
        box = f.Rect(tables[0]['box'])
        assert box.y1 == pytest.approx(168, abs=1)
        assert box.contains(page.search_for('AraT4')[0])
        assert not box.intersects(page.search_for('Table 1.')[0])
        assert [a['kind'] for a in instance.protection_regions(page, 'missed-native-table')] == ['figure']
        assert {a['kind'] for a in instance.protection_regions(page, 'missed-native-table', translate_tables=True)} == {'figure', 'table'}
        table = instance.discover(page, 'missed-native-table', kinds={'table'})[0]
        texts = {r['text'] for r in table['regions']}
        assert {'aminotransferase', 'location', 'gene annotation'} <= texts
        assert len(table['cells']) >= 25
        assert any(r['box'][1] > 130 for r in table['regions'])


def test_native_grid_inside_disabled_figure_keeps_figure_ownership():
    with f.open() as document:
        page = document.new_page(width=400, height=320)
        shaded_header_table(page)
        instance = analyzer([(0, [20, 20, 380, 190])])
        assert [a['kind'] for a in instance.discover_areas(page, 'embedded')] == ['figure']
        assert instance.discover(page, 'embedded', kinds={'table'}) == []


def test_cross_page_defined_identifiers_and_author_year_are_not_natural_targets():
    with f.open() as document:
        definitions = document.new_page(width=420, height=200)
        definitions.insert_text((20, 35), 'Indole lactic acid (ILA) and indole aldehyde (IAld).', fontsize=8)
        page = document.new_page(width=420, height=220)
        rows = [('ILA: 0.16~0.40', 25, 55), ('IAld: 381.96', 25, 70),
                ('Yao et al. (2024); Zhou et al. (2022)', 180, 55),
                ('Indole: 80~128', 25, 90), ('TIME', 25, 110),
                ('Galloyl-HHDP-glucose', 25, 130), ('Reference', 180, 110)]
        for text, x, y in rows:
            page.insert_text((x, y), text, fontsize=7)
        table = analyzer([(1, [20, 40, 400, 150])]).discover(page, 'definitions', kinds={'table'})[0]
        texts = {r['text'] for r in table['regions']}
        assert not {'ILA:', 'IAld:', 'Yao', 'Zhou', 'Yao et al.', 'Zhou et al.'} & texts
        assert {'Indole:', 'TIME', 'Galloyl-HHDP-glucose', 'Reference'} <= texts
        assert {'ILA:', 'IAld:', 'Yao', '2024'} <= {
            w['text'].strip('();') for line in table['lines'] for w in line['words']}
        assert natural_word('TIME') and natural_word('Galloyl-HHDP-glucose')


def test_reference_needs_a_year_and_does_not_protect_same_named_label():
    with f.open() as document:
        page = document.new_page(width=300, height=200)
        for text, x, y in [('Smith et al.', 25, 55), ('(2024)', 25, 63.56),
                           ('Smith', 25, 100), ('Jones et al.', 130, 100)]:
            page.insert_text((x, y), text, fontsize=7)
        # A real PDF citation can wrap within a text block, as in the source.
        table = analyzer([(1, [20, 40, 280, 130])]).discover(page, 'citations', kinds={'table'})[0]
        texts = [r['text'] for r in table['regions']]
        assert texts.count('Smith') == 1
        assert 'Jones' in texts


def test_unruled_table_joins_wrapped_prose_with_full_source_lane():
    with f.open() as document:
        page = document.new_page(width=300, height=260)
        for text, x, y in [('Bacterial tryptophan', 126.31, 165),
                           ('metabolism and product', 126.31, 173.56),
                           ('profiles are strain-', 126.31, 182.12),
                           ('specific.', 126.31, 190.68),
                           ('(2) Key genes', 114.35, 199.24),
                           ('Yao et al. (2024)', 220, 165)]:
            page.insert_text((x, y), text, fontsize=7)
        area = analyzer([(1, [40, 140, 290, 220])]).discover(page, 'continuation', kinds={'table'})[0]
        matches = [r for r in plan_area(page, area) if 'specific.' in r['text']]
        assert len(matches) == 1
        region = matches[0]
        assert 'strain-' in region['text']
        assert 'Key genes' not in region['text'] and 'Yao' not in region['text']
        assert f.Rect(region['layout_box']).height >= f.Rect(region['box']).height - 1
        assert len(region['source_boxes']) > 1
