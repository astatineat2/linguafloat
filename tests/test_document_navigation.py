"""Offline navigation geometry, transaction and native-page regressions."""
from pathlib import Path
import os
import socket

import pymupdf as fitz
import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('Navigation tests must not access the network')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)


def toolbar(*, links=True, unrelated=False, combined=False):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    for i, (x, label) in enumerate([(80, 'Usage statistics'), (250, 'Related papers'), (440, 'Extra materials')]):
        page.insert_text((x, 150), label, fontsize=8)
        page.draw_rect(fitz.Rect(x-15, 142, x-7, 150), fill=(0, 0, 0))
        if links:
            page.insert_link(dict(kind=fitz.LINK_URI,
                **{'from': fitz.Rect(x, 141, x+fitz.get_text_length(label, fontsize=8), 153)},
                uri=f'https://example.org/paper/{i if unrelated else 1}?action={i}'))
    if combined:
        # Three link targets inside ONE native line/span, with large gaps.
        page.add_redact_annot(page.rect)
        page.apply_redactions(images=0, graphics=0)
        text = 'Usage statistics' + ' '*48 + 'Related papers' + ' '*60 + 'Extra materials'
        page.insert_text((80, 150), text, fontsize=8)
        for old in page.get_links():
            page.delete_link(old)
        for i, label in enumerate(['Usage statistics', 'Related papers', 'Extra materials']):
            box = page.search_for(label)[0]
            page.insert_link(dict(kind=fitz.LINK_URI, **{'from': box}, uri=f'https://example.org/paper/1?action={i}'))
    return doc


def test_link_evidence_and_same_line_segmentation():
    from linguafloat.document_navigation import plan_navigation
    for combined in (False, True):
        with toolbar(combined=combined) as doc:
            groups = plan_navigation(doc[0])
            assert len(groups) == 1
            assert [i['text'] for i in groups[0]['items']] == ['Usage statistics', 'Related papers', 'Extra materials']
    for kwargs in ({'links': False}, {'unrelated': True}):
        with toolbar(**kwargs) as doc:
            assert plan_navigation(doc[0]) == []


def test_success_preserves_icons_links_origins_and_common_size():
    from linguafloat.document_navigation import plan_navigation, render_navigation
    with toolbar() as source, fitz.open() as output:
        output.insert_pdf(source)
        page = output[0]
        group = plan_navigation(source[0])[0]
        translations = dict(zip((i['id'] for i in group['items']), ['使用统计', '相关论文', '附加材料']))
        icons = [fitz.Rect(i['box'][0]-15, 140, i['box'][0]-6, 154) for i in group['items']]
        before = [page.get_pixmap(clip=b).samples for b in icons]
        links = sorted((l['uri'], tuple(l['from'])) for l in page.get_links())
        result = render_navigation(source[0], page, group, translations)
        assert result['status'] == 'translated'
        assert sorted((l['uri'], tuple(l['from'])) for l in page.get_links()) == links
        assert [page.get_pixmap(clip=b).samples for b in icons] == before
        spans = [s for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]
        for item, chinese in zip(group['items'], translations.values()):
            span = next(s for s in spans if s['text'] == chinese)
            assert span['origin'] == pytest.approx(item['origin'], abs=.001)
            assert span['size'] == pytest.approx(8, abs=.001)
            assert item['text'] not in page.get_text()


@pytest.mark.parametrize('bad', ['missing', 'long', 'multiline', 'empty', 'unchanged', 'extra'])
def test_invalid_group_leaves_page_unchanged(bad):
    from linguafloat.document_navigation import plan_navigation, render_navigation, NavigationError
    with toolbar() as source, fitz.open() as output:
        output.insert_pdf(source)
        page = output[0]
        group = plan_navigation(source[0])[0]
        texts = {i['id']: '导航项目' for i in group['items']}
        key = group['items'][0]['id']
        if bad == 'missing':
            del texts[key]
        elif bad == 'extra':
            texts['unexpected'] = '多余'
        else:
            texts[key] = {'long': '过长译文'*100, 'multiline': '一\n二', 'empty': '',
                          'unchanged': group['items'][0]['text']}[bad]
        before = page.get_pixmap().samples
        contents = [output.xref_stream(x) for x in page.get_contents()]
        with pytest.raises(NavigationError):
            render_navigation(source[0], page, group, texts)
        assert page.get_pixmap().samples == before
        assert [output.xref_stream(x) for x in page.get_contents()] == contents


def test_changed_destination_is_rejected_before_erasure():
    from linguafloat.document_navigation import plan_navigation, render_navigation, NavigationError
    with toolbar() as source, fitz.open() as output:
        output.insert_pdf(source)
        page = output[0]
        page.insert_text((85, 149), 'Other body text', fontsize=8)
        group = plan_navigation(source[0])[0]
        before = page.get_pixmap().samples
        with pytest.raises(NavigationError):
            render_navigation(source[0], page, group, {i['id']: '导航项目' for i in group['items']})
        assert page.get_pixmap().samples == before


def test_actual_native_page_fixed_chinese(tmp_path):
    from linguafloat.document_navigation import plan_navigation, render_navigation
    path = Path(os.environ.get('WALNUT_SOURCE_PDF', str(Path.home() / 'Downloads' /
        'Walnut Pellicle Polyphenols Trigger Lactiplantibacillus plantarum DX7 to Produce Indole-3-Lactic Acid.pdf')))
    if not path.exists():
        pytest.skip('Local Walnut source unavailable')
    fixed = {'Read Online': '在线阅读', 'ACCESS': '访问', 'Metrics & More': '指标与更多',
             'Article Recommendations': '文章推荐', 'Supporting Information': '补充信息'}
    with fitz.open(path) as source, fitz.open() as output:
        groups = plan_navigation(source[0])
        assert len(groups) == 1
        group = groups[0]
        assert {i['text'] for i in group['items']} == set(fixed)
        assert len(group['preserve']) == 6  # layered icon restored as one region
        output.insert_pdf(source, from_page=0, to_page=0)
        before_links = sorted((l['kind'], l.get('uri', ''), tuple(l['from'])) for l in output[0].get_links())
        # Replay the existing graphic_source restoration path, including the
        # two overlapping text layers in the supporting-information icon.
        page = output[0]
        for region in group['preserve']:
            rect = fitz.Rect(region['box'])
            page.add_redact_annot(rect, fill=False, cross_out=False)
            # Production restoration may remove links; the navigation module
            # must restore its own exact URI annotations from source evidence.
            page.apply_redactions(images=0, graphics=0, text=0)
            page.draw_rect(rect, color=None, fill=(1, 1, 1))
            page.show_pdf_page(rect, source, 0, clip=rect)
        # Match the documented integration call after legacy restoration.
        page = output.reload_page(page)
        icon = fitz.Rect(430, 224, 445, 240)
        before_icon = output[0].get_pixmap(matrix=fitz.Matrix(3, 3), clip=icon).samples
        assert before_icon == source[0].get_pixmap(matrix=fitz.Matrix(3, 3), clip=icon).samples
        result = render_navigation(source[0], output[0], group, {i['id']: fixed[i['text']] for i in group['items']})
        assert result['status'] == 'translated'
        output.save(tmp_path / 'navigation-fixed-local.pdf')
        output[0].get_pixmap(matrix=fitz.Matrix(3, 3), clip=fitz.Rect(25, 188, 590, 250)).save(tmp_path / 'navigation-fixed-local.png')
        for value in fixed.values():
            assert value in output[0].get_text()
        with fitz.open(tmp_path / 'navigation-fixed-local.pdf') as saved:
            assert sorted((l['kind'], l.get('uri', ''), tuple(l['from'])) for l in saved[0].get_links()) == before_links
            assert saved[0].get_pixmap(matrix=fitz.Matrix(3, 3), clip=icon).samples == before_icon


def test_graphic_collision_rejects_whole_group_without_mutation():
    from linguafloat.document_navigation import plan_navigation, render_navigation, NavigationError
    with toolbar() as source, fitz.open() as output:
        output.insert_pdf(source)
        page = output[0]
        page.draw_rect(fitz.Rect(79, 137, 170, 155), fill=(.8, .8, .8), overlay=False)
        group = plan_navigation(source[0])[0]
        before = page.get_pixmap().samples
        with pytest.raises(NavigationError):
            render_navigation(source[0], page, group, {i['id']: '导航项目' for i in group['items']})
        assert page.get_pixmap().samples == before


def test_adapter_uses_fragment_interface_and_reports_local_failure():
    from types import SimpleNamespace
    from linguafloat.document_navigation import repair_navigation
    with toolbar() as source, fitz.open() as output:
        output.insert_pdf(source)
        requested = []
        def translate(text, context, *, part):
            requested.append((text, context, part))
            return '中文导航'
        translator = SimpleNamespace(job=SimpleNamespace(target='zh-Hans', skip_references=False),
                                     control=SimpleNamespace(checkpoint=lambda: None), translate_fragment=translate)
        records = repair_navigation(source[0], output[0], translator)
        assert records[0]['status'] == 'translated'
        assert [r[0] for r in requested] == ['Usage statistics', 'Related papers', 'Extra materials']
        assert all(r[2] == 'navigation' for r in requested)
    with toolbar() as source, fitz.open() as output:
        output.insert_pdf(source)
        before = output[0].get_pixmap().samples
        translator.translate_fragment = lambda *a, **kw: ''
        records = repair_navigation(source[0], output[0], translator)
        assert records[0]['status'] == 'untranslated'
        assert output[0].get_pixmap().samples == before


def test_cancellation_propagates_and_reference_pages_do_not_request():
    from types import SimpleNamespace
    from linguafloat.document_core import Cancelled
    from linguafloat.document_navigation import repair_navigation
    def cancel():
        raise Cancelled('test')
    with toolbar() as source, fitz.open() as output:
        output.insert_pdf(source)
        translator = SimpleNamespace(job=SimpleNamespace(target='zh-Hans', skip_references=False),
                                     control=SimpleNamespace(checkpoint=cancel))
        before = output[0].get_pixmap().samples
        with pytest.raises(Cancelled):
            repair_navigation(source[0], output[0], translator)
        assert output[0].get_pixmap().samples == before
        translator.job.skip_references = True
        translator.job.reference_boundary = {'page': 0, 'boxes': [[0, 0, 600, 800]]}
        assert repair_navigation(source[0], output[0], translator) == []


def test_commit_error_is_fatal_in_adapter(monkeypatch):
    from types import SimpleNamespace
    from linguafloat.document_navigation import repair_navigation, NavigationCommitError
    with toolbar() as source, fitz.open() as output:
        output.insert_pdf(source)
        def fail(*args, **kwargs):
            raise RuntimeError('failed during commit')
        monkeypatch.setattr(fitz.Page, 'show_pdf_page', fail)
        translator = SimpleNamespace(job=SimpleNamespace(target='zh-Hans', skip_references=False),
            control=SimpleNamespace(checkpoint=lambda: None), translate_fragment=lambda *a, **kw: '中文导航')
        with pytest.raises(NavigationCommitError):
            repair_navigation(source[0], output[0], translator)
