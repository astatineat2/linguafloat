"""Dense scientific labels: fit complete text without sacrificing retained ink."""
from types import SimpleNamespace as NS
from pathlib import Path

import pymupdf as f
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_graphics import GraphicAnalyzer, apply_graphics, join_vertical_labels
from linguafloat.document_graphic_layout import blank_label_lane


def analyzer(bounds):
    result = GraphicAnalyzer(None, JobControl(), lambda _: None)
    result.discover_areas = lambda *a: [dict(id='panel', kind='figure', box=bounds)]
    result.ocr = NS(recognize=lambda *a, **k: [])
    return result


def translator(values):
    return NS(control=JobControl(), job=NS(translate_figures=True, translate_tables=True,
        skip_references=False, target='zh-Hans'),
        translate_fragment=lambda text, *a, **k: values[text])


@pytest.mark.parametrize('rotation', [0, 90, 270])
def test_short_label_uses_reading_direction_whitespace(rotation):
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=240, height=240)
        p.insert_text((100, 120), 'Yield', fontsize=6, rotate=rotation)
        p.draw_line((160, 60), (160, 180), width=.5, color=(1, 0, 0))
        dst.insert_pdf(src)
        records = apply_graphics(p, dst[0], translator({'Yield': '单位面积产量'}),
                                 analyzer([40, 40, 200, 200]), 'whitespace')
        assert records[0]['status'] == 'translated', records
        assert records[0]['size'] >= 6
        assert '单位面积产量' in dst[0].get_text() and 'Yield' not in dst[0].get_text()
        lines = [line for block in dst[0].get_text('dict')['blocks'] for line in block.get('lines', [])]
        expected = {0: (1, 0), 90: (0, -1), 270: (0, 1)}[rotation]
        assert any(line['dir'] == expected for line in lines)
        clip = f.Rect(158, 50, 162, 190)
        assert p.get_pixmap(clip=clip, matrix=f.Matrix(4,4)).samples == dst[0].get_pixmap(clip=clip, matrix=f.Matrix(4,4)).samples


def test_unrelated_plot_labels_do_not_share_overflow_failure():
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=260, height=200)
        p.draw_rect(f.Rect(50,30,230,160), width=.5)
        p.insert_text((70,60), 'Alpha', fontsize=8)
        p.insert_text((170,120), 'Beta', fontsize=8)
        p.insert_text((120,90), '10', fontsize=7)
        dst.insert_pdf(src)
        records = apply_graphics(p, dst[0], translator({'Alpha':'甲乙', 'Beta':'完整译文'*100}),
                                 analyzer([10,10,250,190]), 'groups')
        assert {r['text']: r['status'] for r in records} == {'Alpha':'translated', 'Beta':'needs_review'}
        assert 'Beta' in dst[0].get_text() and '甲乙' in dst[0].get_text()
        clip = f.Rect(115,75,140,95)
        # Compare RGBA directly: white-background compositing rounds some
        # antialiased edges by one level after adding a transparency group.
        assert p.get_pixmap(clip=clip, matrix=f.Matrix(4,4), alpha=True).samples == dst[0].get_pixmap(clip=clip, matrix=f.Matrix(4,4), alpha=True).samples
        assert dst[0].search_for('10') == p.search_for('10')


def test_overlapping_rotated_runs_are_not_joined_as_one_label():
    carrier = dict(text='Relative abundance', source='native', rotation=90, confidence=1,
        horizontal=False, box=[30,60,36,100], words=[
            dict(text='Relative',box=[30,80,36,100]), dict(text='abundance',box=[30,60,36,79])])
    other = dict(text='population', source='native', rotation=90, confidence=1,
        horizontal=False, box=[30,65,36,90], words=[dict(text='population', box=[30,65,36,90])])
    assert len(join_vertical_labels([carrier, other])) == 2


def test_extended_lane_cannot_cross_a_pale_rule_or_protected_token():
    with f.open() as src:
        p = src.new_page(width=160,height=100)
        p.draw_line((93,30),(93,60),color=(.85,.85,.85),width=.2)
        region = dict(id='label', text='Yield', box=[60,40,80,46], rotation=0)
        area = dict(box=[20,20,140,80], regions=[region], lines=[
            dict(text='10⁷',box=[35,40,45,46])])
        box = f.Rect(blank_label_lane(p, area, region, 6))
        assert box.x0 >= (45+60)/2
        assert 90 < box.x1 < 93
        assert region['box'] == [60,40,80,46]


def test_native_microtext_is_not_replaced_below_six_points():
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=200,height=100)
        p.insert_text((70,50), 'Yield', fontsize=4)
        dst.insert_pdf(src)
        records = apply_graphics(p, dst[0], translator({'Yield':'产量'}),
                                 analyzer([20,20,180,80]), 'minimum')
        assert records[0]['status'] == 'translated'
        assert records[0]['size'] >= 6


@pytest.mark.parametrize('font', ['simsun', 'msyh'])
def test_clockwise_axis_preserves_unit_and_selected_font(font):
    if not Path('C:/Windows/Fonts/simsun.ttc').is_file():
        pytest.skip('Local Windows fonts required; do not download')
    with f.open() as src, f.open() as dst:
        p = src.new_page(width=260, height=220)
        p.draw_rect(f.Rect(80,40,220,180), width=.5)
        p.draw_line((95,160),(200,60), color=(0,0,1), width=.5)
        p.insert_text((45,50), 'Concentration', fontsize=8, rotate=270)
        p.insert_text((45,110), '(T)', fontsize=8, rotate=270)
        dst.insert_pdf(src)
        t = translator({'Concentration':'浓度'})
        t.job.chinese_font, t.job.latin_font = font, 'times'
        records = apply_graphics(p,dst[0],t,analyzer([20,20,235,200]),'clockwise-unit')
        assert records[0]['status'] == 'translated', records
        assert records[0]['axis'] == 'y' and records[0]['suffix_box']
        assert dst[0].get_text().count('(T)') == 1 and '浓度' in dst[0].get_text()
        lines = [line for block in dst[0].get_text('dict')['blocks'] for line in block.get('lines', [])]
        assert all(line['dir'] == (0,1) for line in lines)
        clip = f.Rect(78,38,222,182)
        assert p.get_pixmap(clip=clip, matrix=f.Matrix(4,4),alpha=True).samples == dst[0].get_pixmap(clip=clip,matrix=f.Matrix(4,4),alpha=True).samples


def test_top_to_bottom_ocr_requires_word_direction_evidence():
    line = dict(text='Relative abundance', source='ocr', horizontal=True, confidence=.99,
        box=[30,60,36,100], words=[dict(text='Relative',box=[30,60,36,79]),
                                 dict(text='abundance',box=[30,80,36,100])])
    result = join_vertical_labels([line])[0]
    assert result['rotation'] == 270 and not result['horizontal']
    assert result['text'] == 'Relative abundance' and result['confidence'] == .99


def test_raster_parenthesized_abbreviation_below_name_is_protected():
    with f.open() as artwork, f.open() as src, f.open() as dst:
        p = artwork.new_page(width=240,height=140)
        p.insert_text((70,55),'Reactive enzyme',fontsize=10)
        p.insert_text((90,68),'(RXY)',fontsize=10)
        lines=[dict(text=text,box=list(p.search_for(text)[0]),confidence=1,horizontal=True)
               for text in ('Reactive enzyme','(RXY)')]
        for line in lines:
            line['words']=[dict(text=w[4],box=list(w[:4]),confidence=1)
                           for w in p.get_text('words',clip=f.Rect(line['box']))]
        page = src.new_page(width=240,height=140)
        page.insert_image(page.rect,pixmap=p.get_pixmap(matrix=f.Matrix(3,3)))
        dst.insert_pdf(src)
        a=analyzer([30,25,200,100])
        a.ocr=NS(recognize=lambda *a,**k:lines)
        calls=[]
        def translate(text,*a,**k):
            calls.append(text)
            return '反应酶'
        t=translator({});t.translate_fragment=translate
        records=apply_graphics(page,dst[0],t,a,'defined-acronym')
        assert calls == ['Reactive enzyme']
        assert next(r for r in records if r['text']=='(RXY)')['status']=='protected'
        clip=f.Rect(lines[1]['box'])
        assert page.get_pixmap(clip=clip,matrix=f.Matrix(3,3),alpha=True).samples == dst[0].get_pixmap(clip=clip,matrix=f.Matrix(3,3),alpha=True).samples


@pytest.mark.parametrize('graphics', [False, True])
def test_enhancer_text_repairs_run_when_graphics_deferred(monkeypatch, graphics):
    import linguafloat.document_graphics as module
    import linguafloat.document_frontmatter as frontmatter
    import linguafloat.document_navigation as navigation
    events=[]
    def front(source,target,translator):
        events.append('frontmatter')
        target.insert_text((30,40),'Front matter')
        return [dict(page=1,status='translated',kind='frontmatter')]
    def nav(source,target,translator):
        events.append('navigation')
        assert 'Front matter' in target.get_text()
        target.insert_text((30,60),'Navigation')
        return [dict(page=1,status='translated',kind='navigation')]
    monkeypatch.setattr(frontmatter,'repair_frontmatter',front)
    monkeypatch.setattr(navigation,'repair_navigation',nav)
    monkeypatch.setattr(module,'apply_graphics',lambda *a,**k: events.append('graphics') or [])
    with f.open() as src, f.open() as dst:
        src.new_page(width=200,height=100);dst.insert_pdf(src)
        enhancer=object.__new__(module.DocumentEnhancer)
        enhancer.translator=translator({})
        enhancer.translator.job.graphic_overrides={}
        enhancer.records=[];enhancer.graphics=[]
        enhancer.analyzer=object();enhancer.digest='fixture';enhancer.emit=lambda _:None
        enhancer.apply(src[0],dst[0],graphics=graphics)
        assert events == ['frontmatter','navigation']+(['graphics'] if graphics else [])
        assert [r['kind'] for r in enhancer.records] == ['frontmatter','navigation']
        assert [r['id'] for r in enhancer.records] == ['P1-F1','P1-F2']
        assert 'Navigation' in dst[0].get_text()


def test_navigation_failure_marks_each_box_without_graphic_calls(monkeypatch):
    import linguafloat.document_graphics as module
    import linguafloat.document_frontmatter as frontmatter
    import linguafloat.document_navigation as navigation
    import linguafloat.document_failures as failures
    boxes=[[30,35,85,45],[150,35,210,45]]
    monkeypatch.setattr(frontmatter,'repair_frontmatter',lambda *a:[])
    monkeypatch.setattr(navigation,'repair_navigation',lambda *a:[dict(
        page=1,status='untranslated',kind='navigation',group='nav-1',
        boxes=boxes,coordinates='top-left',reason='完整译文无法安全排入')])
    graphic_calls=[]
    monkeypatch.setattr(module,'apply_graphics',lambda *a,**k:graphic_calls.append(1) or [])
    marked=[];original=failures.mark_pdf
    def mark(page,regions,*a,**k):
        marked.extend(regions)
        return original(page,regions,*a,**k)
    monkeypatch.setattr(failures,'mark_pdf',mark)
    with f.open() as src, f.open() as dst:
        src.new_page(width=260,height=120);dst.insert_pdf(src)
        enhancer=object.__new__(module.DocumentEnhancer)
        enhancer.translator=translator({});enhancer.records=[];enhancer.graphics=[]
        enhancer.analyzer=object();enhancer.digest='fixture';enhancer.emit=lambda _:None
        enhancer.apply(src[0],dst[0],graphics=False)
        assert graphic_calls == []
        assert [r['box'] for r in marked] == boxes
        assert [r['id'] for r in marked] == ['P1-F1-B1','P1-F1-B2']
        assert enhancer.records[0]['boxes'] == boxes
        assert enhancer.records[0]['group'] == 'nav-1' and 'box' not in enhancer.records[0]
