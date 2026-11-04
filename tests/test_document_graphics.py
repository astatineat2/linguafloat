import json
from types import SimpleNamespace as NS

import numpy as np
import pymupdf as fitz
import pytest

from linguafloat.document_core import DocumentTranslator, DocumentError, JobStore, JobControl, Cancelled
from linguafloat.document_graphics import natural_word, glyph_patch, graphic_overlay, apply_graphics, DocumentEnhancer, visible_native_words, preview_page, GraphicAnalyzer
from linguafloat.document_frontmatter import centered_overlay, paragraph_in_plan
from test_document_core import job_for


@pytest.mark.parametrize("text",["200mm","D2","N1","GHz","mm","A/m","H","10-6","x=2","Fe-Ni","NaCl","YBCO"])
def test_scientific_values_are_not_translation_targets(text):
    assert not natural_word(text)


def fixture(tmp_path):
    path=tmp_path/'graphic.pdf'
    with fitz.open() as doc:
        p=doc.new_page(width=300,height=220)
        p.insert_text((35,60),'outer diameter',fontsize=10)
        p.insert_text((170,60),'200mm',fontsize=10)
        p.draw_line((30,70),(250,70),color=(0,0,0),width=.6)
        p.draw_line((30,120),(250,100),color=(0,0,1),width=1)
        box=list(p.search_for('outer diameter')[0])
        doc.save(path)
    region=dict(id='label',kind='table',box=box,text='outer diameter',rotation=0,confidence=1,reason='')
    analyzer=NS(discover=lambda *a:[dict(id='table',kind='table',box=[25,40,260,80],regions=[region])],emit=lambda _:None)
    return path,region,analyzer


def test_translation_reuses_cache_and_preserves_data_pixels(tmp_path):
    path,region,analyzer=fixture(tmp_path)
    job=job_for(path,pdf_mode='layout',translate_tables=True)
    store=JobStore(tmp_path/'cache.sqlite')
    requests=[]
    def create(**kwargs):
        requests.append(kwargs)
        return NS(output_text='外径')
    client=NS(responses=NS(create=create))
    try:
        for cached in (False,True):
            job.relayout_only=cached
            t=DocumentTranslator(job,'',store,JobControl(),lambda _:None,client)
            with fitz.open(path) as source,fitz.open(path) as target:
                records=apply_graphics(source[0],target[0],t,analyzer,'digest')
                assert records[0]['status']=='translated'
                text=target[0].get_text()
                assert '外径' in text and '200mm' in text and 'outer diameter' not in text
                for clip in (fitz.Rect(160,40,250,68),fitz.Rect(20,68,270,74),fitz.Rect(20,90,270,130)):
                    actual=np.frombuffer(target[0].get_pixmap(clip=clip,matrix=fitz.Matrix(3,3)).samples,dtype=np.uint8).astype(int)
                    original=np.frombuffer(source[0].get_pixmap(clip=clip,matrix=fitz.Matrix(3,3)).samples,dtype=np.uint8).astype(int)
                    # MuPDF can round unchanged glyph positions when rewriting a stream.
                    assert np.max(np.abs(actual-original))<=3
        assert len(requests)==1
        job.relayout_only=True
        t=DocumentTranslator(job,'',store,JobControl(),lambda _:None,client)
        with fitz.open(path) as source,fitz.open(path) as target:
            with pytest.raises(DocumentError,match='缓存不完整'):
                apply_graphics(source[0],target[0],t,analyzer,'digest',{'label':{'source':'inner diameter'}})
        assert len(requests)==1
    finally:store.close()


def test_manual_correction_and_disabled_feature_send_no_requests(tmp_path):
    path,region,analyzer=fixture(tmp_path)
    job=job_for(path,pdf_mode='layout')
    t=NS(job=job,control=JobControl(),translate_fragment=lambda *a,**k:pytest.fail('Unexpected API'))
    with fitz.open(path) as source,fitz.open(path) as target:
        assert apply_graphics(source[0],target[0],t,analyzer,'x')==[]
        job.translate_tables=True
        records=apply_graphics(source[0],target[0],t,analyzer,'x',{'label':{'translation':'外径'}})
        assert records[0]['status']=='translated'


def test_cancel_and_overflow_do_not_damage_original(tmp_path):
    path,region,analyzer=fixture(tmp_path)
    job=job_for(path,pdf_mode='layout',translate_tables=True)
    t=NS(job=job,control=JobControl(),translate_fragment=lambda *a,**k:'长译文'*400)
    with fitz.open(path) as source,fitz.open(path) as target:
        before=target[0].get_pixmap().samples
        assert apply_graphics(source[0],target[0],t,analyzer,'x')[0]['status']=='needs_review'
        assert target[0].get_pixmap().samples==before
        t.control.cancelled.set()
        with pytest.raises(Cancelled):apply_graphics(source[0],target[0],t,analyzer,'x')


def test_colored_text_and_connected_rules_are_not_erased():
    with fitz.open() as doc:
        p=doc.new_page(width=250,height=150)
        p.insert_text((25,55),'Colored label',color=(1,0,0),fontsize=12)
        box=list(p.search_for('Colored label')[0])
        patch,reason=glyph_patch(p,dict(box=box))
        assert patch is None and '彩色' in reason


def test_metadata_centering_separate_affiliations_and_superscripts():
    plan=dict(kind='affiliations',box=[30,50,470,90],lines=[dict(spans=[dict(size=10,text='Department')])])
    with fitz.open() as doc:
        p=doc.new_page(width=500,height=300)
        fitted=centered_overlay(p,plan,[('1','第一所研究机构 150001'),('2','第二所研究机构 100081')],'zh-Hans',JobControl())
        assert fitted and fitted[1]['rows']==2
        with fitz.open(stream=fitted[0],filetype='pdf') as result:
            lines=[line for b in result[0].get_text('dict')['blocks'] for line in b.get('lines',[])]
            assert len(lines)==2
            for line in lines:
                assert abs((line['bbox'][0]+line['bbox'][2])/2-250)<1
            trace=result[0].get_texttrace()
            assert trace[0]['chars'][0][2][1]<trace[1]['chars'][0][2][1]
            assert trace[2]['chars'][0][2][1]<trace[3]['chars'][0][2][1]


def test_copyright_two_lines_and_long_url_never_truncated():
    plan=dict(kind='copyright',box=[30,40,470,75],lines=[dict(spans=[dict(size=8,text='copyright')])])
    url='https://example.org/publications/rights/index.html'
    with fitz.open() as doc:
        p=doc.new_page(width=500,height=300)
        fitted=centered_overlay(p,plan,[('','允许个人使用，转载请获得许可。'),('',url)],'zh-Hans',JobControl())
        assert fitted and fitted[1]['rows']==2
        with fitz.open(stream=fitted[0],filetype='pdf') as result:assert url in result[0].get_text()
        assert centered_overlay(p,plan,[('',url*50)],'zh-Hans',JobControl()) is None


def test_report_uses_one_page_key_for_old_and_new_unresolved_regions():
    e=DocumentEnhancer.__new__(DocumentEnhancer)
    e.records=[]
    e.graphics=[dict(page=2,id='r',box=[1,2,3,4],text='label',status='needs_review',reason='overflow')]
    report=dict(untranslated_regions={1:[dict(source='body')]})
    e.report(report)
    assert len(json.loads(json.dumps(report))['untranslated_regions']['1'])==2


def test_metadata_matching_does_not_remove_adjacent_body():
    plans=[dict(box=[30,40,470,80])]
    assert paragraph_in_plan(NS(box=NS(x=80,y=225,x2=400,y2=255)),300,plans)
    assert not paragraph_in_plan(NS(box=NS(x=30,y=100,x2=470,y2=210)),300,plans)


def test_hidden_ocr_is_not_preferred_over_visible_scan():
    with fitz.open() as doc:
        p=doc.new_page(width=250,height=150)
        p.insert_text((25,40),'Hidden wrong words',render_mode=3)
        p.insert_text((25,80),'Visible label')
        assert [w[4] for w in visible_native_words(p,p.rect)]==['Visible','label']


def test_local_preview_manual_translation_keeps_unfilled_regions_without_api(tmp_path,monkeypatch):
    path,region,analyzer=fixture(tmp_path)
    from linguafloat.document_graphics import source_digest
    monkeypatch.setattr(GraphicAnalyzer,'discover',analyzer.discover)
    events=[]
    preview_page(dict(source=str(path),page=0,database=str(tmp_path/'preview.sqlite'),manual_preview=True,
                      overrides={source_digest(path):{'label':{'translation':'外径'}}}),events.append)
    output=events[-1]
    assert output['type']=='graphics_preview' and output['records'][0]['status']=='translated'
    assert output['total']==1 and len(output['image'])>100


def test_rotated_axis_and_raster_label_keep_other_data(tmp_path):
    path,region,analyzer=fixture(tmp_path)
    with fitz.open(path) as source,fitz.open() as scan:
        p=scan.new_page(width=300,height=220)
        p.insert_image(p.rect,pixmap=source[0].get_pixmap(matrix=fitz.Matrix(3,3)))
        job=job_for(path,pdf_mode='layout',translate_tables=True)
        t=NS(job=job,control=JobControl(),translate_fragment=lambda *a,**k:'外径')
        assert apply_graphics(scan[0],scan[0],t,analyzer,'x')[0]['status']=='translated'
        assert '外径' in p.get_text()
        rotated=dict(box=[10,90,24,190],rotation=90)
        fitted=graphic_overlay(p,rotated,'磁通密度','zh-Hans',JobControl())
        assert fitted and fitted[1]['size']>=6


def test_graphics_excluded_from_body_and_enhanced_in_final_outputs(tmp_path):
    from linguafloat.document_layout import LayoutPolicy
    from linguafloat.document_pdf import assemble_layout_pdf
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page, PdfParagraph
    path,region,analyzer=fixture(tmp_path)
    policy=LayoutPolicy(JobControl())
    policy.graphic_regions[0]=[dict(box=[25,40,260,80])]
    body=PdfParagraph(box=Box(20,20,280,50),unicode='Body')
    graphic=PdfParagraph(box=Box(35,153,130,180),unicode='outer diameter')
    page=Page(page_number=0,cropbox=Cropbox(Box(0,0,300,220)),pdf_paragraph=[body,graphic])
    policy.exclude_frontmatter(page)
    assert page.pdf_paragraph==[body]
    job=job_for(path,pdf_mode='layout',translate_tables=True)
    t=NS(job=job,control=JobControl(),translate_fragment=lambda *a,**k:'外径')
    output=tmp_path/'outputs';output.mkdir()
    paths=assemble_layout_pdf(job,[0],path,output,{},t.control,
        retained_regions={0:[dict(box=[25,140,260,180],kind='graphic_source')]},
        enhance=lambda source,target:apply_graphics(source,target,t,analyzer,'x'))
    with fitz.open(paths[0]) as mono,fitz.open(paths[1]) as dual:
        assert mono[0].rect==fitz.Rect(0,0,300,220)
        assert '外径' in mono[0].get_text() and '200mm' in mono[0].get_text()
        assert 'outer diameter' not in mono[0].get_text()
        assert 'outer diameter' in dual[0].get_text() and '外径' in dual[0].get_text()
