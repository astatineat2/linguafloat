"""Offline regressions reduced from Nature s41586-025-09883-2, pp. 3/6.

The model/OCR fixtures replay boundary failures; extraction and replacement use
real PDF text, coordinates and pixels. No model download or translation API.
"""
from types import SimpleNamespace as NS

import numpy as np
import pymupdf as f
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_graphics import GraphicAnalyzer, apply_graphics


def analyzer(box, *, kind="figure", confidence=.96, ocr_lines=()):
    a = GraphicAnalyzer(None, JobControl(), lambda _: None)
    prediction = NS(names={0: kind}, boxes=[NS(cls=0, conf=confidence,
                                              xyxy=[v * 2 for v in box])])
    a.model = NS(predict=lambda *args, **kw: [prediction])
    a.ocr = NS(recognize=lambda *args, **kw: list(ocr_lines))
    return a


def labels(page):
    # Real line spacing and 7pt size on p.3; separate PDF text blocks are common.
    for text, x, y in [("Crystal", 40, 60), ("structure", 37, 68.4),
                       ("Spin", 44, 100), ("ordering", 38, 108.4),
                       ("Collinear", 120, 100), ("compensated", 115, 108.4),
                       ("Spin-only group", 35, 140), ("SO(2)", 120, 140),
                       ("Mn3Sn", 210, 60)]:
        page.insert_text((x, y), text, fontsize=7)


def test_native_labels_survive_empty_ocr_and_join_by_geometry():
    with f.open() as doc:
        p = doc.new_page(width=300, height=240)
        labels(p)
        areas = analyzer([25, 35, 255, 155]).discover(p, "fixture")
        texts = {r["text"] for r in areas[0]["regions"]}
        assert {"Crystal structure", "Spin ordering", "Collinear compensated",
                "Spin-only group"} <= texts
        assert not texts & {"Crystal", "structure", "Spin", "ordering", "Mn3Sn", "SO(2)"}
        assert all(r["source"] == "native" and r["rotation"] == 0 for r in areas[0]["regions"])
        assert "Mn3Sn" in {w["text"] for l in areas[0]["lines"] for w in l["words"]}


def test_caption_recovers_misclassified_figure_without_ocr_and_excludes_prose():
    with f.open() as doc:
        p = doc.new_page(width=300, height=240)
        labels(p)
        p.insert_text((25, 175), "Box Fig. 1 | Spin groups and magnetic groups.", fontsize=7)
        p.insert_text((25, 205), "Outside body continues normally.", fontsize=9)
        a = analyzer([25, 35, 255, 155], kind="table", confidence=.713573)
        a.ocr.recognize = lambda *a, **kw: pytest.fail("Protection must not run OCR")
        areas = a.discover_areas(p, "fixture")
        assert len(areas) == 1 and areas[0]["kind"] == "figure"
        protected = a.protection_regions(p, "fixture", translate_tables=False)
        assert protected[0]["coordinates"] == "top-left"
        box = f.Rect(protected[0]["box"])
        assert box.contains(p.search_for("Mn3Sn")[0])
        assert not box.intersects(p.search_for("Box Fig.")[0])
        assert not box.intersects(p.search_for("Outside body")[0])


def test_independent_table_still_obeys_table_switch():
    with f.open() as doc:
        p = doc.new_page(width=300, height=240)
        p.insert_text((25, 175), "Table 1 | Measured values.", fontsize=7)
        a = analyzer([25, 35, 255, 155], kind="table")
        assert a.protection_regions(p, "fixture", translate_tables=False) == []
        assert a.protection_regions(p, "fixture", translate_tables=True)[0]["kind"] == "table"


def test_partial_native_line_does_not_swallow_raster_words():
    with f.open() as doc:
        p = doc.new_page(width=300, height=150)
        p.insert_text((30, 50), "Native", fontsize=7)
        words = [dict(text="Natlve", box=[30, 42, 51, 52], confidence=.8),
                 dict(text="raster", box=[65, 42, 88, 52], confidence=.99)]
        a = analyzer([20, 20, 200, 100], ocr_lines=[dict(text="Natlve raster",
                     box=[30, 42, 88, 52], confidence=.8, horizontal=True, words=words)])
        lines = a.discover(p, "fixture")[0]["lines"]
        text = [w["text"] for line in lines for w in line["words"]]
        assert text.count("Native") == 1 and "raster" in text and "Natlve" not in text


def test_lieb_lattice_uses_native_direction_not_tall_box_and_keeps_formula_pixels():
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=300, height=210)
        p.insert_text((120.223, 99.026), "Lieb", fontsize=6)
        p.insert_text((117.889, 105.026), "lattice", fontsize=6)
        p.insert_text((210, 100), "Mn3Sn", fontsize=6)
        p.insert_text((120, 135), "SO(2)", fontsize=7)
        a = analyzer([30, 30, 270, 155])
        regions = a.discover(p, "fixture")[0]["regions"]
        label = next(r for r in regions if r["text"] == "Lieb lattice")
        assert label["rotation"] == 0 and len(label["source_boxes"]) == 2
        dst.insert_pdf(src)
        t = NS(control=JobControl(), job=NS(translate_figures=True, translate_tables=False,
               target="zh-Hans", skip_references=False),
               translate_fragment=lambda text, *args, **kw: "里布晶格")
        records = apply_graphics(p, dst[0], t, a, "fixture")
        assert next(r for r in records if r["text"] == "Lieb lattice")["status"] == "translated"
        for clip in (f.Rect(205, 88, 245, 108), f.Rect(115, 122, 155, 141)):
            before = np.frombuffer(p.get_pixmap(clip=clip, matrix=f.Matrix(4, 4)).samples, np.uint8)
            after = np.frombuffer(dst[0].get_pixmap(clip=clip, matrix=f.Matrix(4, 4)).samples, np.uint8)
            assert np.max(np.abs(before.astype(int) - after.astype(int))) <= 3


@pytest.mark.parametrize('offset', [0, .3, 4.5, 6])
def test_overlapping_native_duplicates_are_not_multiline_continuations(offset):
    with f.open() as doc:
        p = doc.new_page(width=300, height=160)
        p.insert_text((100, 60), 'lattice', fontsize=6)
        p.insert_text((100, 60+offset), 'lattice', fontsize=6)
        regions = analyzer([25, 25, 250, 130]).discover(p, 'duplicate')[0]['regions']
        assert regions
        assert all(r['text'] == 'lattice' for r in regions)
        assert all(len(r['source_boxes']) == 1 for r in regions)


def test_native_three_line_label_uses_last_baseline_for_each_join():
    with f.open() as doc:
        p = doc.new_page(width=300, height=160)
        for text, y in [('Electronvolt', 60), ('alternating-sign', 66), ('spin splitting', 72)]:
            width = f.get_text_length(text, fontsize=6)
            p.insert_text((150-width/2, y), text, fontsize=6)
        regions = analyzer([25, 25, 250, 130]).discover(p, 'three-lines')[0]['regions']
        assert [r['text'] for r in regions] == ['Electronvolt alternating-sign spin splitting']
        assert regions[0]['rotation'] == 0


def test_defined_acronym_and_symbols_are_obstacles_not_translation_targets():
    with f.open() as doc:
        p = doc.new_page(width=300, height=200)
        p.insert_text((30, 40), "TRSB at B = M = 0", fontsize=6)
        p.insert_text((30, 180), "Time reversal symmetry breaking (TRSB).", fontsize=7)
        areas = analyzer([25, 25, 220, 80]).discover(p, "fixture")
        regions = areas[0]["regions"]
        assert [r["text"] for r in regions] == ["at"]
        for region in regions:
            assert not any(f.Rect(b).intersects(p.search_for(token)[0])
                           for b in region["source_boxes"] for token in ("TRSB", "B", "M", "0"))


def test_stacked_labels_with_different_alignment_are_not_joined():
    with f.open() as doc:
        p = doc.new_page(width=300, height=160)
        p.insert_text((30, 50), "Left", fontsize=7)
        p.insert_text((120, 58), "Right", fontsize=7)
        texts = {r["text"] for r in analyzer([20, 20, 200, 100]).discover(p, "fixture")[0]["regions"]}
        assert texts == {"Left", "Right"}


def test_native_direction_survives_image_blocks_and_shaded_background():
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=300, height=200)
        pix = f.Pixmap(f.csRGB, f.IRect(0, 0, 8, 8), False)
        pix.clear_with(230)
        p.insert_image(f.Rect(200, 30, 220, 50), pixmap=pix)
        p.draw_rect(f.Rect(25, 60, 180, 100), fill=(238/255, 236/255, 222/255), color=None)
        p.insert_text((40, 60), "Crystal", fontsize=7)
        p.insert_text((37, 68.4), "structure", fontsize=7)
        p.insert_text((35, 160), "Energy", fontsize=6, rotate=90)
        a = analyzer([20, 20, 240, 170])
        regions = a.discover(p, "fixture")[0]["regions"]
        assert next(r for r in regions if r['text'] == 'Energy')['rotation'] == 90
        label = next(r for r in regions if r['text'] == 'Crystal structure')
        assert label['rotation'] == 0 and label['native_size'] == pytest.approx(7)
        dst.insert_pdf(src)
        t = NS(control=JobControl(), job=NS(translate_figures=True, translate_tables=False,
               target='zh-Hans', skip_references=False),
               translate_fragment=lambda *a, **kw: pytest.fail('Manual offline fixture only'))
        corrections = {r['id']: {'skip': True} for r in regions}
        corrections[label['id']] = {'translation': '晶体结构'}
        records = apply_graphics(p, dst[0], t, a, 'fixture', corrections)
        assert next(r for r in records if r['text'] == 'Crystal structure')['status'] == 'translated'
        clip = f.Rect(100, 65, 175, 95)
        assert p.get_pixmap(clip=clip).samples == dst[0].get_pixmap(clip=clip).samples


def test_disabled_graphics_never_discovers_or_ocr():
    t = NS(job=NS(translate_figures=False, translate_tables=False))
    a = NS(discover=lambda *args: pytest.fail('Disabled enhancement must return immediately'))
    assert apply_graphics(None, None, t, a, 'fixture') == []


def test_real_nature_graphic_labels_and_immutable_formulas(monkeypatch):
    """Opt in with NATURE_GRAPHIC_PDF; uses installed model and no network."""
    import os
    import socket
    from pathlib import Path
    path = os.environ.get('NATURE_GRAPHIC_PDF')
    if not path:
        pytest.skip('Set NATURE_GRAPHIC_PDF for the local source-PDF regression')
    def offline(*args, **kwargs):
        pytest.fail('Network/real translation is forbidden in the graphic regression')
    monkeypatch.setattr(socket.socket, 'connect', offline)
    monkeypatch.setattr(socket, 'create_connection', offline)
    from babeldoc.docvision.doclayout import OnnxModel
    a = GraphicAnalyzer(None, JobControl(), lambda _: None)
    a.model = OnnxModel(str(Path.home()/'.cache/babeldoc/models/doclayout_yolo_docstructbench_imgsz1024.onnx'))
    translations = {'Crystal structure': '晶体结构', 'Spin ordering': '自旋排序',
                    'Collinear compensated': '共线补偿', 'Collinear uncompensated': '共线非补偿',
                    'Spin-only group': '纯自旋群', 'Lieb lattice': '里布晶格', 'at': '在'}
    with f.open(path) as src:
        for number in (2, 5):
            p = src[number]
            areas = a.discover(p, 'nature-offline', kinds={'figure'})
            assert len(areas) == 1 and areas[0]['kind'] == 'figure'
            regions = areas[0]['regions']
            texts = {r['text'] for r in regions}
            expected = {'Crystal structure', 'Spin ordering', 'Collinear compensated',
                        'Collinear uncompensated', 'Spin-only group'} if number == 2 else {'Lieb lattice', 'at'}
            assert expected <= texts
            assert not texts & {'Mn3Sn', 'TRSB', 'SO(2)'}
            corrections = {r['id']: ({'translation': translations[r['text']]} if r['text'] in expected
                                     else {'skip': True}) for r in regions}
            t = NS(control=JobControl(), job=NS(translate_figures=True, translate_tables=False,
                   target='zh-Hans', skip_references=False), translate_fragment=offline)
            with f.open() as dst:
                dst.insert_pdf(src, from_page=number, to_page=number)
                records = apply_graphics(p, dst[0], t, NS(discover=lambda *args: areas, emit=lambda _: None),
                                         'nature-offline', corrections)
                assert all(r['status'] == 'translated' for r in records if r['text'] in expected), records
                clips = ([515, 238, 540, 251], [377, 290, 551, 304], [383, 308, 551, 325]) if number == 2 else (
                         [137, 146, 153.5, 156], [161, 146, 190, 156])
                for clip in clips:
                    before = np.frombuffer(p.get_pixmap(clip=f.Rect(clip), matrix=f.Matrix(4, 4)).samples, np.uint8)
                    after = np.frombuffer(dst[0].get_pixmap(clip=f.Rect(clip), matrix=f.Matrix(4, 4)).samples, np.uint8)
                    assert np.max(np.abs(before.astype(int)-after.astype(int))) <= 3
