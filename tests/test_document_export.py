import json
from zipfile import ZipFile

import pymupdf as fitz
import pytest
from docx import Document
from docx.oxml.ns import qn

from linguafloat.document_export import create_manuscript, choices, export_file


@pytest.mark.parametrize('with_manuscript', [True, False])
def test_word_routes_use_editable_academic_styles(tmp_path, with_manuscript):
    source = tmp_path / 'academic.pdf'
    with fitz.open() as pdf:
        pdf.set_metadata({'title': 'A Study of Spin'})
        page = pdf.new_page(width=500, height=680)
        for y, text, size, font in [
            (60, 'A Study of Spin', 19, 'hebo'), (105, 'Abstract', 13, 'hebo'),
            (130, 'The sample contained 30 g and a link.', 9, 'helv'),
            (160, 'Keywords: spin, oxide', 9, 'helv'), (210, '1. Results', 15, 'hebo'),
            (250, '1.1. Materials', 13, 'hebo'), (290, '1.1.1. Films', 12, 'hebo'),
            (335, 'Figure 1. Result', 8, 'helv'),
        ]:
            page.insert_text((42, y), text, fontsize=size, fontname=font)
        page.insert_text((42, 310), '\u6d4b\u91cf\u539a\u5ea6', fontname='china-s', fontsize=7)
        pdf.save(source)
    info = dict(outputs=[str(source)])
    if with_manuscript:
        info['export_manuscript'] = create_manuscript(source, tmp_path, 'job', {}, [dict(source=0,start=0,count=1)])
    target = tmp_path / 'academic.docx'
    export_file('job', info, 'docx', target)
    doc = Document(target)
    by_text = {p.text: p for p in doc.paragraphs}
    for text, role, size, chinese in [
        ('A Study of Spin', 'Title', 16, 'SimHei'), ('Abstract', None, 14, 'SimSun'),
        ('The sample contained 30 g and a link.', None, 14, 'SimSun'),
        ('Keywords: spin; oxide', None, 14, 'SimSun'),
        ('1. Results', 'Heading 1', 14, 'SimSun'), ('1.1. Materials', 'Heading 2', 12, 'SimSun'),
        ('1.1.1. Films', 'Heading 3', 12, 'SimSun'), ('Figure 1. Result', 'Caption', 7.5, 'SimSun'),
        ('\u6d4b\u91cf\u539a\u5ea6', 'Normal', 12, 'SimSun'),
    ]:
        p = by_text[text]
        if role:
            assert p.style.name == role
        assert p.style.font.size.pt == size
        fonts = p.style.element.xpath('./w:rPr/w:rFonts')[0]
        assert fonts.get(qn('w:eastAsia')) == chinese
        assert fonts.get(qn('w:ascii')) == fonts.get(qn('w:hAnsi')) == 'Times New Roman'
        assert not any('theme' in name.lower() for name in fonts.attrib)
        assert str(p.style.font.color.rgb) == '000000'
    assert doc.styles['Normal'].paragraph_format.line_spacing == 1.5
    margin = doc.sections[0]
    assert (round(margin.top_margin.cm, 2), round(margin.left_margin.cm, 2)) == (2.54, 3.17)
    assert by_text['Abstract'].runs[0].style.font.bold
    assert by_text['Abstract'].runs[0].style.font.size.pt == 14


def test_word_clears_false_raised_prose_but_preserves_scientific_scripts():
    from io import BytesIO
    from linguafloat.document_export import _write

    def run(text, flags=0, subscript=False, uri=None):
        return dict(text=text, flags=flags, subscript=subscript, uri=uri)

    spans = [run('RuO'), run('2', subscript=True), run(' x', 2), run('24', 1),
             run(' '), run('\u6d4b\u91cf\u503c\u4e3a', 1), run('0.36 nm', 1),
             run('RuO', 1), run('2', 1, subscript=True), run(' = 0.5 nm', 1),
             run('\u63a5\u8fd1\u53c2\u8003\u503c', 1), run(' '),
             run('DOI', uri='https://doi.org/10.0000/example'), run(' x+\U0001d709\U0001d447')]
    out = BytesIO()
    _write(None, dict(pages=[dict(source_page=1, blocks=[dict(kind='paragraph', runs=spans)])], issues={}), 'docx', out, False)
    paragraph, = Document(out).paragraphs
    assert paragraph.text == ''.join(s['text'] for s in spans)
    assert paragraph.runs[1].font.subscript
    assert paragraph.runs[2].italic
    assert paragraph.runs[3].font.superscript
    assert all(not r.font.superscript for r in paragraph.runs[5:11])
    assert paragraph.runs[8].font.subscript
    assert not paragraph.runs[9].font.subscript
    assert paragraph.hyperlinks[0].url == 'https://doi.org/10.0000/example'
    assert paragraph.runs[-2].text == ' x+'
    assert paragraph.runs[-1].text == '\U0001d709\U0001d447'
    assert paragraph.runs[-1].style.font.name == 'Cambria Math'


def test_pdf_only_history_exports_translation_columns(tmp_path):
    path = tmp_path / 'archived.双语.pdf'
    with fitz.open() as doc:
        p = doc.new_page(width=1020, height=726)
        p.insert_text((12, 17), 'Original 1 | Translation 1/1')
        p.insert_text((40, 60), 'Do not export this original')
        p.insert_text((790, 60), 'Right column')
        p.insert_text((550, 85), 'Left column')
        doc.save(path)
    info = dict(outputs=[str(path)])
    target = tmp_path / 'archived.docx'
    assert 'docx' in choices(info, 'job')
    export_file('job', info, 'docx', target)
    assert [p.text for p in Document(target).paragraphs] == ['Left column', 'Right column']


def test_word_places_a_matched_table_title_above_editable_three_line_table():
    from io import BytesIO
    from linguafloat.document_export import _write

    blocks = [dict(kind='table', box=[20, 20, 240, 90], rows=[['Sample', 'Value'], ['A', '30']]),
              dict(kind='caption', box=[20, 94, 140, 101], runs=[dict(text='Table 1. Values', flags=0, uri=None)])]
    out = BytesIO()
    _write(None, dict(pages=[dict(source_page=1, blocks=blocks)], issues={}), 'docx', out, False)
    doc = Document(out)
    assert [node.tag for node in doc.element.body[:2]] == [qn('w:p'), qn('w:tbl')]
    table, = doc.tables
    assert [[cell.text for cell in row.cells] for row in table.rows] == blocks[0]['rows']
    assert table._tbl.xpath('./w:tblPr/w:tblBorders/w:top/@w:val') == ['single']
    assert table._tbl.xpath('./w:tblPr/w:tblBorders/w:insideV/@w:val') == ['nil']
    assert table.cell(0, 0)._tc.xpath('./w:tcPr/w:tcBorders/w:bottom/@w:val') == ['single']


def fixture(tmp_path, artwork=True):
    mono = tmp_path / 'paper.译文.pdf'
    with fitz.open() as doc:
        p = doc.new_page(width=400, height=500)
        p.insert_text((25, 40), 'Study results', fontsize=18)
        for x, y, text in [(225, 90, 'Right column'), (25, 90, 'Left 30 g/day'),
                           (25, 120, 'mail@example.org')]:
            p.insert_text((x, y), text)
        box = p.search_for('mail@example.org')[0]
        p.insert_link(dict(kind=fitz.LINK_URI, uri='mailto:mail@example.org', **{'from':box}))
        if artwork:
            p.draw_rect((25, 180, 180, 240)); p.insert_text((35, 210), 'Plot label')
            p.insert_text((25, 260), 'Figure 1. Result')
        doc.save(mono)
    report = {'normal_preserved_regions': {'0':[{'box':[25,260,180,320]}]}} if artwork else {}
    manifest = create_manuscript(mono, tmp_path, 'job', report, [{'source':0,'start':0,'count':1}])
    return mono, dict(outputs=[str(mono)], export_manuscript=manifest)


def test_editable_pair_and_exact_copy(tmp_path):
    mono, info = fixture(tmp_path)
    target = tmp_path/'editable.docx'
    export_file('job', info, 'docx', target)
    doc = Document(target); text = '\n'.join(p.text for p in doc.paragraphs)
    assert text.index('Left 30 g/day') < text.index('mail@example.org') < text.index('Right column')
    assert len(doc.inline_shapes) == 1 and 'Plot label' not in text and 'Figure 1. Result' in text
    assert 'mailto:mail@example.org' in [r.target_ref for r in doc.part.rels.values()]
    export_file('job', info, 'markdown', tmp_path/'notes.zip')
    with ZipFile(tmp_path/'notes.zip') as z:
        md=z.read('translation.md').decode(); assert 'assets/' in md and 'mailto:mail@example.org' in md
        assert len([p for p in z.namelist() if p.startswith('assets/')]) == 1
    export_file('job', info, 'output:0', tmp_path/'copy.pdf')
    assert mono.read_bytes() == (tmp_path/'copy.pdf').read_bytes()
    with pytest.raises(FileExistsError): export_file('job', info, 'docx', target)


def test_refuse_unbound_or_changed_result_and_plain_markdown(tmp_path):
    mono, info = fixture(tmp_path, False)
    export_file('job', info, 'markdown', tmp_path/'notes.md')
    assert '30 g/day' in (tmp_path/'notes.md').read_text('utf8')
    assert set(choices({'outputs':[str(mono)]})) == {'output:0', 'docx', 'markdown'}
    with pytest.raises(ValueError): export_file('another', info, 'docx', tmp_path/'wrong.docx')
    mono.write_bytes(b'changed')
    with pytest.raises(ValueError): export_file('job', info, 'docx', tmp_path/'wrong.docx')
    assert not (tmp_path/'wrong.docx').exists()


def test_relocates_legacy_pdf_only_with_bound_task_manifest(tmp_path):
    from linguafloat.document_export import digest, resolve_result

    original = tmp_path / 'old' / 'test' / 'paper.job' / 'paper.双语.pdf'
    migrated = tmp_path / 'new' / 'test' / original.parent.name / original.name
    unrelated = tmp_path / 'other' / original.name
    for pdf in (migrated, unrelated):
        pdf.parent.mkdir(parents=True)
        with fitz.open() as doc:
            doc.new_page()
            doc.save(pdf)
    manifest = migrated.parent / '.graphics-source' / 'manifest.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({'parent_job_id': 'job'}), encoding='utf-8')
    info = {'outputs': [str(original)], 'graphics_manifest_sha256': digest(manifest)}
    assert resolve_result('job', info, [tmp_path / 'new'])['outputs'] == [str(migrated)]
    assert set(choices(resolve_result('job', info, located=migrated), 'job')) == {'output:0', 'docx', 'markdown'}
    assert resolve_result('job', info, located=unrelated)['outputs'] == [str(original)]
    assert resolve_result('other-job', info, located=migrated)['outputs'] == [str(original)]
    assert resolve_result('job', {'outputs': [str(original)]}, located=migrated)['outputs'] == [str(original)]
    manifest.write_text('{}', encoding='utf-8')
    assert resolve_result('job', info, [tmp_path / 'new'])['outputs'] == [str(original)]


def test_moved_editable_result_requires_task_identity_and_output_hash(tmp_path):
    from linguafloat.document_export import resolve_result

    source = tmp_path / 'original'
    source.mkdir()
    pdf, info = fixture(source, False)
    moved = tmp_path / 'moved'
    source.rename(moved)
    candidate = moved / pdf.name
    bound = resolve_result('job', info, located=candidate)
    assert bound['outputs'] == [str(candidate)]
    assert {'output:0', 'docx', 'markdown'} == choices(bound, 'job').keys()
    assert resolve_result('wrong', info, located=candidate)['outputs'] == [str(pdf)]
    candidate.write_bytes(b'%PDF-incorrect result')
    assert resolve_result('job', info, located=candidate)['outputs'] == [str(pdf)]
    assert 'markdown' not in choices(bound, 'job')


def test_protected_identifier_is_text_and_wide_table_keeps_caption(tmp_path):
    from linguafloat.document_export import _ordered
    mono=tmp_path/'mono.pdf'
    with fitz.open() as d:
        p=d.new_page(width=400,height=400); p.insert_text((20,60),'https://example.org/id')
        box=p.search_for('https://example.org/id')[0]; p.insert_link(dict(kind=fitz.LINK_URI,uri='https://example.org/id',**{'from':box})); d.save(mono)
        region=dict(box=list(box*~p.transformation_matrix),preserve_reason='identifier')
    create_manuscript(mono,tmp_path,'id',{'normal_preserved_regions':{'0':[region]}},[dict(start=0,count=1,source=0)])
    with ZipFile(tmp_path/'.translation-v1.zip') as z: assert 'https://example.org/id' in z.read('manuscript.json').decode()
    sample=[dict(kind=k,box=b) for k,b in [('paragraph',[240,60,370,220]),('paragraph',[30,60,160,190]),('caption',[30,240,140,260]),('image',[30,265,370,350])]]
    assert [v['kind'] for v in _ordered(sample,400)]==['paragraph','paragraph','caption','image']


def test_unmapped_glyph_stays_inline_instead_of_dropping_text(tmp_path,monkeypatch):
    original=fitz.Page.get_text
    def unmapped(page,option='text',*args,**kwargs):
        result=original(page,option,*args,**kwargs)
        if option=='rawdict':
            for block in result['blocks']:
                for line in block.get('lines',[]):
                    for span in line['spans']:
                        for ch in span['chars']:
                            if ch['c']=='/': ch['c']='\x01'
        return result
    monkeypatch.setattr(fitz.Page,'get_text',unmapped)
    mono,info=fixture(tmp_path,False)
    export_file('job',info,'docx',tmp_path/'out.docx')
    doc=Document(tmp_path/'out.docx'); assert len(doc.inline_shapes)==1
    assert any('30 gday' in p.text for p in doc.paragraphs)
    export_file('job',info,'markdown',tmp_path/'out.zip')
    with ZipFile(tmp_path/'out.zip') as z:
        text=z.read('translation.md').decode(); assert '30 g![原文字形](assets/' in text and '.png)day' in text
