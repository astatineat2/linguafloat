"""Table headers are compact cell-wide text, not independent positioned labels."""
import pymupdf as f
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_graphic_layout import fit_label


def test_vector_header_clip_does_not_duplicate_neighbouring_script():
    with f.open() as source, f.open() as target:
        page = source.new_page(width=160, height=100)
        page.insert_text((50,40), 'DX7', fontsize=8)
        page.insert_text((63,46), '7', fontsize=5)
        spans = [s for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
        parts = [dict(text=s['text'],clip=s['bbox'],baseline=s['origin'][1]) for s in spans]
        region = dict(box=[20,20,140,70],cell_box=[20,20,140,70],layout_box=[21,21,139,69],
            header_cell=True,header_parts=parts,_header_members=[],column=1)
        result = fit_label(target.new_page(width=160,height=100),region,'','zh-Hans',JobControl(),8,page)
        assert result
        with f.open(stream=result[0],filetype='pdf') as overlay:
            assert overlay[0].get_text().count('7') == 2


@pytest.mark.parametrize('size', [8, 6])
def test_separately_extracted_exponent_keeps_native_relative_position(size):
    with f.open() as source, f.open() as target:
        page = source.new_page(width=160,height=100)
        page.insert_text((50,45),'10',fontsize=8)
        page.insert_text((61,41),'-7',fontsize=5)
        spans = [s for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
        parts = [dict(text=s['text'],clip=s['bbox'],baseline=s['origin'][1]) for s in spans]
        region = dict(box=[20,20,140,70],cell_box=[20,20,140,70],layout_box=[21,21,139,69],
            header_cell=True,header_parts=parts,_header_members=[],column=1)
        fitted = fit_label(target.new_page(width=160,height=100),region,'','zh-Hans',JobControl(),size,page)
        assert fitted
        with f.open(stream=fitted[0],filetype='pdf') as overlay:
            spans = [s for b in overlay[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
            base = next(s for s in spans if s['text']=='10')
            script = next(s for s in spans if s['text']=='-7')
            assert abs(script['origin'][0]-base['origin'][0]-11)<.01
            assert abs(script['origin'][1]-base['origin'][1]+4)<.01


@pytest.mark.parametrize('selected_font', [False,True])
def test_header_missing_glyph_is_local_failure(selected_font):
    from types import SimpleNamespace as NS
    from linguafloat.document_fonts import FontPolicyError
    with f.open() as source, f.open() as target:
        page = source.new_page(width=160,height=100)
        page.insert_text((30,40),'Original header',fontsize=8)
        target.insert_pdf(source)
        before = target[0].get_pixmap().samples
        region = dict(box=[20,20,140,70],cell_box=[20,20,140,70],layout_box=[21,21,139,69],
            header_cell=True,header_parts=[dict(text='\U0001f9ec',bold=False,italic=False)],
            _header_members=[],column=1)
        if selected_font:
            def unavailable(*a, **kw):
                raise FontPolicyError('font missing required glyph')
            region['_font_resolver'] = NS(runs=unavailable)
        assert fit_label(target[0],region,'','zh-Hans',JobControl(),8,page) is None
        assert region.get('_fit_failure')
        assert before == target[0].get_pixmap().samples


@pytest.mark.parametrize('successful_columns', [1,2])
def test_header_failure_keeps_successful_sibling_fragments_in_source_cell(successful_columns):
    from linguafloat.document_graphic_layout import compose_native_headers
    with f.open() as pdf:
        page = pdf.new_page(width=220, height=100)
        page.insert_text((25,35), 'name', fontsize=8)
        page.insert_text((25,48), 'description', fontsize=8)
        page.insert_text((125,35), 'name', fontsize=8)
        words = page.get_text('words')
        cells = [dict(box=[20,20,110,60],row=1,column=1,native_reliable=True),
                 dict(box=[120,20,210,60],row=1,column=2,native_reliable=True)]
        regions = [dict(id=str(i),box=list(w[:4]),source_boxes=[list(w[:4])],
            text=w[4],source='native') for i,w in enumerate(words)]
        area = dict(id='t',kind='table',cells=cells,regions=regions,lines=[])
        prepared = []
        for i in ((0,2) if successful_columns==2 else (0,)):
            region = dict(regions[i],native_cell=True,row=1,column=1 if i==0 else 2,
                          cell_box=cells[0 if i==0 else 1]['box'])
            prepared.append((region,None,dict(region), '\u540d\u79f0'))
        records = []
        compose_native_headers(page,area,prepared,records)
        assert len(records) == 1
        assert records[0]['id'] == '0' and records[0]['status'] == 'needs_review'


def test_mixed_native_header_composes_before_layout_and_keeps_vectors():
    from types import SimpleNamespace as NS
    from linguafloat.document_graphics import GraphicAnalyzer, apply_graphics
    with f.open() as pdf, f.open() as output:
        page = pdf.new_page(width=240, height=130)
        for x in (20, 120, 220):
            page.draw_line((x, 20), (x, 110))
        for y in (20, 65, 90, 110):
            page.draw_line((20, y), (220, y))
        for x in (25, 125):
            page.insert_text((x, 33), 'components', fontsize=8)
            page.insert_text((x, 44), '(peak area', fontsize=8)
            page.insert_text((x+40, 55), '10', fontsize=8)
            page.insert_text((x+49, 51), '-7)', fontsize=5)
            page.insert_text((x, 80), '1.25', fontsize=8)
            page.insert_text((x, 103), '2.50', fontsize=8)
        output.insert_pdf(pdf)
        analyzer = GraphicAnalyzer(None, JobControl(), lambda _: None)
        analyzer.discover_areas = lambda *a: [dict(id='t', kind='table', box=[20,20,220,110])]
        analyzer.ocr = NS(recognize=lambda *a, **kw: [])
        def translate(text, *a, **kw):
            return {'components':'\u7ec4\u5206', '(peak area':'(\u5cf0\u9762\u79ef'}[text]
        translator = NS(control=analyzer.control, translate_fragment=translate,
            job=NS(translate_tables=True, translate_figures=False, target='zh-Hans',
                   skip_references=False, chinese_font='', latin_font=''))
        records = apply_graphics(page, output[0], translator, analyzer, 'mixed')
        assert all(r['status'] == 'translated' for r in records)
        assert all(r.get('header_cell') for r in records)
        assert {r['column'] for r in records} == {1, 2}
        assert all(r['size'] == 8 for r in records)
        for column in (1, 2):
            report = next(r for r in records if r['column'] == column)
            assert report['rows'] <= 3
            assert '10' in report['header_content']
        spans = [s for b in output[0].get_text('dict')['blocks']
                 for line in b.get('lines', []) for s in line['spans']]
        assert any(s['text'] == '-7)' and abs(s['size']-5)<.01 for s in spans)
        base = next(s for s in spans if s['text'] == '10')
        upper = next(s for s in spans if s['text'] == '-7)')
        assert abs(base['origin'][1]-upper['origin'][1]-4) < .01
        assert output[0].get_text().count('1.25') == 2
        assert output[0].get_text().count('2.50') == 2


def test_native_header_centers_complete_visible_text_in_cell():
    with f.open() as pdf:
        page = pdf.new_page(width=240, height=100)
        region = dict(id='header', box=[24, 40, 60, 48],
            source_boxes=[[24, 40, 60, 48]], cell_box=[20, 20, 180, 65],
            layout_box=[21, 21, 179, 64], row=1, column=1,
            native_cell=True, header_cell=True)
        fitted = fit_label(page, region, '\u5316\u5408\u7269', 'zh-Hans', JobControl(), 7)
        assert fitted is not None
        with f.open(stream=fitted[0], filetype='pdf') as overlay:
            chars = [c for b in overlay[0].get_text('rawdict')['blocks']
                     for line in b.get('lines', []) for span in line['spans']
                     for c in span['chars'] if not c['c'].isspace()]
            box = f.Rect(chars[0]['bbox'])
            for char in chars[1:]:
                box |= f.Rect(char['bbox'])
            assert abs((box.x0+box.x1)/2-100) < 1
            assert abs((box.y0+box.y1)/2-42.5) < 2
