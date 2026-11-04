from types import SimpleNamespace as NS
import pymupdf as f
import pytest
from linguafloat.document_core import JobControl
from linguafloat.document_graphics import classify_content,apply_graphics
from linguafloat.document_graphic_layout import plan_area


def test_subscript_and_defined_acronym_not_natural_language():
    with f.open() as doc:
        p=doc.new_page()
        p.insert_text((30,40),'Shielding SE',fontsize=10)
        x=30+f.get_text_length('Shielding SE',fontsize=10)
        p.insert_text((x,42),'A',fontsize=7)
        p.insert_text((30,65),'Noise density (BNSD)',fontsize=10)
        for text in ('SEA','BNSD'):
            r=dict(id=text,text=text,box=[40,90,60,110],confidence=.6,reason='low confidence')
            assert classify_content(p,dict(lines=[]),r,NS())[0]=='protected'
        # Uppercase prose is not automatically treated as an acronym.
        assert classify_content(p,{},dict(text='TIME',box=[40,90,60,110],confidence=1),NS()) is None


def test_math_operator_protected_but_normal_maximum_translated():
    with f.open() as doc:
        p=doc.new_page()
        r=dict(id='max',text='max',box=[40,40,65,50],confidence=.99,reason='')
        area=dict(id='a',kind='table',box=[30,30,200,150],regions=[r],lines=[dict(box=[40,40,160,50],text='max (|∆(B/r)|)')])
        t=NS(control=JobControl(),job=NS(translate_tables=True,translate_figures=True,target='ja'),
             translate_fragment=lambda *a,**k:pytest.fail('Math must not call translator'))
        records=apply_graphics(p,p,t,NS(discover=lambda *a:[area]),'x')
        assert records[0]['status']=='protected'
        assert classify_content(p,dict(lines=[]),dict(r,text='Maximum'),NS()) is None


def test_ruled_multitier_header_wraps_before_shrinking():
    with f.open() as doc:
        p=doc.new_page(width=300,height=160)
        p.draw_rect(f.Rect(20,30,280,80));p.draw_line((100,30),(100,80));p.draw_line((200,30),(200,80))
        labels=[('Target field',25,50),('Coil efficiency',105,50)]
        regions=[];lines=[]
        for i,(text,x,y) in enumerate(labels):
            p.insert_text((x,y),text,fontsize=9)
            box=list(p.search_for(text)[0])
            regions.append(dict(id=str(i),box=box,text=text,confidence=1,reason='',rotation=0))
            lines.append(dict(text=text,box=box,words=[dict(text=text,box=box)]))
        p.insert_text((105,70),'(T/A)',fontsize=9)
        b=list(p.search_for('(T/A)')[0]);lines.append(dict(text='(T/A)',box=b,words=[dict(text='(T/A)',box=b)]))
        area=dict(id='t',kind='table',box=[18,28,282,82],regions=regions,lines=lines)
        plan=plan_area(p,area)
        assert all(r['geometry']=='table-rules' for r in plan)
        assert plan[0]['row']==plan[1]['row']==1
        assert plan[0]['column']==1 and plan[1]['column']==2
        assert plan[0]['layout_box'][1]==plan[1]['layout_box'][1]
        assert plan[0]['layout_box'][3]>plan[1]['layout_box'][3]
        t=NS(control=JobControl(),job=NS(translate_tables=True,translate_figures=True,target='zh-Hans'),
             translate_fragment=lambda text,*a,**k:'目标磁场参数详细说明' if text=='Target field' else '线圈效率')
        with f.open(stream=doc.tobytes(),filetype='pdf') as output:
            records=apply_graphics(p,output[0],t,NS(discover=lambda *a:[area],emit=lambda e:None),'x')
            assert all(r['status']=='translated' and r['size']==9 for r in records)
            assert records[0]['rows']==2
            assert '(T/A)' in output[0].get_text()
