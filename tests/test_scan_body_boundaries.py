"""BODY scan ownership, atomic form/caption replacement, and ink safety."""
from types import SimpleNamespace as NS

import numpy as np
import pymupdf as fitz
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_scan_layout import build_regions, ink_patch, translate_scan_page
from linguafloat.document_scan_recovery import recover_regions


def line(page, text, x, y, color=(0, 0, 0)):
    page.insert_text((x, y), text, fontsize=10, color=color)
    box = list(page.search_for(text)[-1])
    return dict(text=text, box=box, confidence=.999, horizontal=True)


def analyzer_for(lines):
    return NS(translator=NS(control=JobControl()), emit=lambda _: None,
              ocr=NS(recognize=lambda *a, **k: [dict(l, words=[l]) for l in lines]))


def test_figure_and_margin_protection_survives_local_recovery():
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=250)
        label = line(page, "Control group", 30, 50)
        footer = line(page, "Downloaded from publisher", 30, 220)
        regions = build_regions(page, "scan_image", [label, footer], [
            dict(label="figure", confidence=.99, box=[20, 20, 260, 100]),
            dict(label="abandon", confidence=.99, box=[20, 200, 260, 240])])
        recovered = recover_regions(page, regions, analyzer_for([label, footer]), "test")
        assert recovered and all(r.get("preserved") for r in recovered)
        assert all(r.get("reason") for r in recovered)


def test_native_caption_is_one_atomic_region_outside_figure():
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=200)
        lines = [line(page, "Figure 2. Measured protein IL-6.", 30, 130),
                 line(page, "The control increased by 12.5%.", 30, 145)]
        regions = build_regions(page, "mixed", lines, [
            dict(label="figure", confidence=.99, box=[20, 20, 380, 100]),
            dict(label="figure_caption", confidence=.99, box=[25, 115, 375, 150])])
        captions = [r for r in regions if r.get("label") == "figure_caption"]
        assert len(captions) == 1 and not captions[0]["reason"]
        assert captions[0].get("atomic")
        assert "IL-6" in captions[0]["text"] and "12.5%" in captions[0]["text"]


def test_ruled_fields_are_grouped_without_crossing_rules_or_splitting_failure():
    with fitz.open() as doc:
        page = doc.new_page(width=350, height=220)
        for box in ([90, 30, 320, 75], [90, 95, 320, 160]):
            page.draw_rect(box, color=(.81, .82, .83), width=.6)
        lines = [line(page, "No software was used.", 98, 50),
                 line(page, "Reliable opening sentence.", 98, 115),
                 line(page, "Uncertain final sentence.", 98, 135)]
        lines[-1]["confidence"] = .6
        regions = build_regions(page, "scan_vector", lines,
                                [dict(label="table", confidence=.99, box=[80, 20, 330, 170])])
        recovered = recover_regions(page, regions, analyzer_for(lines), "fields")
        fields = [r for r in recovered if r.get("label") == "form_field"]
        assert len(fields) == 2 and all(r.get("atomic") for r in fields)
        assert not fields[0]["reason"]
        assert fields[1]["reason"] and len(fields[1]["lines"]) == 2
        assert not fitz.Rect(fields[0]["box"]).intersects(fitz.Rect(fields[1]["box"]))


def test_verified_colored_glyphs_are_not_treated_as_chart_ink():
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=180)
        text = line(page, "Reporting Summary", 30, 55, color=(.9, .1, .15))
        region = dict(box=list(fitz.Rect(text["box"]) + (-2, -2, 2, 2)),
                      lines=[text], text=text["text"], reason="", label="title")
        patch, reason = ink_patch(page, region)
        assert patch is not None, reason
        # A nearby colored rule must not acquire permission from the glyphs.
        page.draw_line((30, 58), (160, 58), color=(.9, .1, .15), width=1)
        assert ink_patch(page, region)[0] is None


def test_low_confidence_color_and_colored_shapes_remain_ambiguous():
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=180)
        text = line(page, "Reporting Summary", 30, 55, color=(.9, .1, .15))
        text["confidence"] = .7
        region = dict(box=list(fitz.Rect(text["box"]) + (-2, -2, 2, 2)), lines=[text], text=text["text"])
        assert ink_patch(page, region)[0] is None
        text["confidence"] = .999
        page.draw_rect([95, 48, 100, 54], fill=(.9, .1, .15), color=None)
        assert ink_patch(page, region)[0] is None


def test_overlapping_erasers_cannot_delete_an_already_translated_region():
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=180)
        a, b = line(page, "First sentence", 30, 50), line(page, "Second sentence", 30, 70)
        regions = [dict(text=l["text"], box=list(fitz.Rect(l["box"])+(-2,-2,2,2)),
                        lines=[l], reason="", erase_box=[25, 30, 220, 80]) for l in (a, b)]
        calls = []
        t = NS(job=NS(target="en"), control=JobControl(), translate=lambda s: calls.append(s) or "Translated")
        output, retained, placed = translate_scan_page(doc, 0, "scan_image", NS(regions=lambda *a: regions), "test", t, lambda _: None)
        with output:
            assert len(retained) == 2 and not placed and not calls
            assert output[0].get_pixmap().samples == page.get_pixmap().samples


def test_atomic_caption_data_change_leaves_all_original_ink():
    with fitz.open() as doc:
        page = doc.new_page(width=350, height=180)
        caption = line(page, "Figure 2. IL-6 increased by 12.5%.", 30, 50)
        region = dict(text=caption["text"], box=list(fitz.Rect(caption["box"])+(-2,-2,2,2)),
                      lines=[caption], reason="", label="figure_caption", atomic=True)
        t = NS(job=NS(target="en"), control=JobControl(), translate=lambda _: "Figure 2. IL-6 increased by 15%.")
        output, retained, placed = translate_scan_page(doc, 0, "scan_image", NS(regions=lambda *a: [region]), "test", t, lambda _: None)
        with output:
            assert len(retained) == 1 and not placed
            assert output[0].get_pixmap().samples == page.get_pixmap().samples


def test_scanned_form_and_unruled_table_have_distinct_owners():
    from linguafloat.document_scan_layout import ScanAnalyzer
    with fitz.open() as doc:
        page=doc.new_page(width=350,height=270)
        page.draw_rect([70,30,300,80],width=.5)
        rows=[line(page,'Report field value.',80,50),line(page,'Other table item',90,165)]
        tables=[dict(kind='table',id='one',box=[20,20,310,90]),
                dict(kind='table',id='two',box=[20,130,310,190])]
        analyzer=ScanAnalyzer(NS(job=NS(translate_tables=True)),lambda e:None)
        analyzer.table_analyzer=NS(discover_areas=lambda *a:tables)
        regions=analyzer.build(page,'scan_vector',rows,[dict(label='table',confidence=.99,box=t['box']) for t in tables],'test')
        assert analyzer.form_owned_tables == {0:{'one'}}
        assert [(r['label'],r.get('preserved',False)) for r in regions]==[('owned_table',True),('form_field',False)]


def test_checklist_rows_keep_labels_attached_to_their_own_controls():
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=240)
        lines = []
        for x in (25, 220):
            for row, text in enumerate(('Antibodies', 'Eukaryotic cell lines', 'Clinical data')):
                y = 60 + row * 18
                page.draw_rect([x, y-9, x+8, y-1], width=.5)
                page.draw_rect([x+12, y-9, x+20, y-1], width=.5)
                lines.append(line(page, text, x+24, y, color=(0, 0, 0)))
        areas = [dict(label='plain text', confidence=.6, box=[20, 43, 390, 105])]
        regions = build_regions(page, 'scan_vector', lines, areas)
        recovered = recover_regions(page, regions, analyzer_for(lines), 'choices')
        choices = [r for r in recovered if r.get('label') == 'form_choice']
        assert len(choices) == 6
        assert sorted(r['text'] for r in choices) == sorted(l['text'] for l in lines)
        assert all(r.get('atomic') and len(r['lines']) == 1 for r in choices)
        for region in choices:
            assert not region['reason']
            assert all(not fitz.Rect(region['box']).intersects(fitz.Rect(control))
                       for control in region['controls'])
        calls = []
        translator = NS(job=NS(target='en'), control=JobControl(),
                        translate=lambda text: calls.append(text) or text)
        output, retained, placed = translate_scan_page(doc, 0, 'scan_vector',
            NS(regions=lambda *a: recovered), 'choices', translator, lambda _: None)
        with output:
            assert not retained and len(placed) == 6
            assert len(calls) == 6 and all('Antibodies Eukaryotic' not in t for t in calls)
            for region in choices:
                for control in region['controls']:
                    box = fitz.Rect(control) + (-.5, -.5, .5, .5)
                    a = np.frombuffer(page.get_pixmap(clip=box).samples, np.uint8).astype(int)
                    b = np.frombuffer(output[0].get_pixmap(clip=box).samples, np.uint8).astype(int)
                    # MuPDF serializes RGB stroke values during redaction.
                    assert np.abs(a-b).max() <= 1
                    assert any(p['rect'] == fitz.Rect(control) for p in output[0].get_drawings())


def test_small_graphical_squares_do_not_define_choices_without_repeated_rows():
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=200)
        page.draw_rect([25, 50, 33, 58], width=.5)
        text = line(page, 'A normal paragraph with a nearby square.', 38, 60)
        regions = build_regions(page, 'scan_vector', [text],
            [dict(label='plain text', confidence=.99, box=[20, 40, 390, 100])])
        assert not any(r.get('label') == 'form_choice' for r in regions)


def test_compact_choice_note_uses_source_paragraph_geometry_not_an_empty_row():
    from linguafloat.document_scan_layout import fit_policy_paragraph
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=200)
        source_groups = [[dict(box=[30, 40, 200, 49.2])],
                         [dict(box=[30, 49.6, 300, 58.8])]]
        region = dict(box=[29.5, 39.5, 300.5, 59.3], label='form_choice',
                      source_paragraph_lines=source_groups)
        face = fitz.Font('helv')
        font = NS(ascender=face.ascender, descender=face.descender, buffer=face.buffer,
                  text_length=face.text_length, padding=lambda _: (0, 0, 0, 0))
        text = 'The complete translated choice.\n\nThe complete translated explanatory note.'
        fitted = fit_policy_paragraph(page, region, text, font, 7, 1.3)
        assert fitted is not None, 'a synthetic empty row displaced the complete choice note'
        with fitz.open(stream=fitted, filetype='pdf') as output:
            assert ''.join(output[0].get_text().split()) == ''.join(text.split())


@pytest.mark.parametrize('label,gap', [('plain text', .4), ('form_choice', 10)])
def test_real_body_or_choice_paragraph_gap_is_not_compacted(label, gap):
    from linguafloat.document_scan_layout import fit_policy_paragraph
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=200)
        groups = [[dict(box=[30, 40, 200, 49.2])],
                  [dict(box=[30, 49.2+gap, 300, 58.4+gap])]]
        region = dict(box=[29.5, 39.5, 300.5, 59.3], label=label,
                      source_paragraph_lines=groups)
        face = fitz.Font('helv')
        font = NS(ascender=face.ascender, descender=face.descender, buffer=face.buffer,
                  text_length=face.text_length, padding=lambda _: (0, 0, 0, 0))
        assert fit_policy_paragraph(page, region, 'First translated paragraph.\n\nSecond paragraph.',
                                    font, 7, 1.3) is None


@pytest.mark.parametrize('same_controls', [True, False])
def test_merging_choice_notes_preserves_source_groups_and_control_identity(same_controls):
    from linguafloat.document_scan_layout import separate_regions
    with fitz.open() as doc:
        page = doc.new_page(width=350, height=180)
        rows = [line(page, 'The first complete choice', 30, 50),
                line(page, 'Its explanatory note', 30, 61)]
        controls = [[10, 40, 18, 48]]
        regions = [dict(text=row['text'], box=list(fitz.Rect(row['box'])+(-1.5,-1.5,1.5,1.5)),
                        lines=[row], reason='', label='form_choice', controls=controls if i == 0 or same_controls
                        else [[10, 60, 18, 68]]) for i, row in enumerate(rows)]
        separate_regions(page, regions)
        if same_controls:
            assert len(regions) == 1
            assert regions[0]['source_paragraph_lines'] == [[rows[0]], [rows[1]]]
            assert regions[0]['controls'] == controls
        else:
            assert len(regions) == 2
