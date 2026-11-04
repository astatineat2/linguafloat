from types import SimpleNamespace as NS
import numpy as np
import pymupdf as f
import pytest
from linguafloat.document_graphics import apply_graphics
from linguafloat.document_graphic_layout import plan_area,plot_bounds
from linguafloat.document_core import DocumentJob, JobControl


def translator(**options):
    job=DocumentJob(source="fixture.pdf",output_dir="fixture-output",provider={},
                    translate_tables=True,translate_figures=True,target="zh-Hans",**options)
    return NS(job=job,control=JobControl(),translate_fragment=lambda text,*a,**k:{"Quantity":"物理量","Value":"数值","long description":"很长的绕组参数说明需要换行显示完整内容并且保证单元格中的每个文字都得到保留","height":"高度","field strength":"磁场强度"}[text])


def test_table_uniform_size_wrap_and_protected_values(tmp_path):
    with f.open() as src:
        p=src.new_page(width=340,height=220)
        regions=[];lines=[]
        for i,(text,x,y) in enumerate([("Quantity",30,40),("Value",230,40),("long description",30,85),("height",30,130)]):
            p.insert_text((x,y),text,fontsize=10)
            box=list(p.search_for(text)[0])
            regions.append(dict(id=str(i),box=box,text=text,rotation=0,kind="table",reason=""))
            lines.append(dict(box=box,text=text,words=[dict(box=box,text=text)]))
        for text,y in [("200mm",85),("4mm",130)]:
            p.insert_text((230,y),text,fontsize=10)
            box=list(p.search_for(text)[0]);lines.append(dict(box=box,text=text,words=[dict(box=box,text=text)]))
        regions[0]['box'][1]-=3;regions[2]['box'][3]+=5
        area=dict(id="t1",kind="table",box=[20,20,310,150],regions=regions,lines=lines)
        analyzer=NS(discover=lambda *args:[area],emit=lambda e:None)
        with f.open(stream=src.tobytes(),filetype="pdf") as dst:
            records=apply_graphics(p,dst[0],translator(),analyzer,"d")
            assert all(r['status']=='translated' for r in records),records
            assert len({r['size'] for r in records})==1
            assert records[2]['rows']>1
            assert records[0]['size']==10
            for r in records:assert r['size']>=6
            clip=f.Rect(225,60,300,140)
            assert np.max(np.abs(np.frombuffer(p.get_pixmap(clip=clip).samples,np.uint8).astype(int)-
                         np.frombuffer(dst[0].get_pixmap(clip=clip).samples,np.uint8).astype(int)))<=3
            spans=[s for b in dst[0].get_text('dict')['blocks'] for l in b.get('lines',[]) for s in l['spans'] if s['text'] in ('物理量','数值')]
            assert len(spans)==2 and abs(spans[0]['origin'][1]-spans[1]['origin'][1])<.01


def chart():
    doc=f.open();p=doc.new_page(width=360,height=260)
    p.draw_rect(f.Rect(90,40,300,180),width=.6)
    p.draw_line((90,110),(300,110),width=.3)
    p.draw_line((195,40),(195,180),width=.3)
    p.draw_line((110,160),(280,60),color=(1,0,0))
    p.insert_text((130,205),'field strength',fontsize=9)
    p.insert_text((195,205),'H (A/m)',fontsize=9)
    p.insert_text((55,170),'field strength',fontsize=9,rotate=90)
    p.insert_text((55,105),'B (T)',fontsize=9,rotate=90)
    regions=[];lines=[]
    for i,box in enumerate(p.search_for('field strength')):
        regions.append(dict(id=str(i),kind='figure',text='field strength',box=list(box),rotation=90 if box.height>box.width else 0,reason=''))
    for word in p.get_text('words'):
        lines.append(dict(text=word[4],box=list(word[:4]),words=[dict(text=word[4],box=list(word[:4]))]))
    return doc,dict(id='figure',kind='figure',box=[40,25,315,220],regions=regions,lines=lines)


def test_axis_complete_title_center_and_rotation_preserves_plot():
    src,area=chart()
    try:
        p=src[0];plot=plot_bounds(p,area)
        assert plot and abs(plot.x0-90)<1 and abs(plot.y1-180)<1
        plan=plan_area(p,area)
        assert {r['axis'] for r in plan}=={'x','y'}
        assert all(r.get('suffix_box') for r in plan)
        with f.open(stream=src.tobytes(),filetype='pdf') as dst:
            records=apply_graphics(p,dst[0],translator(),NS(discover=lambda *a:[area],emit=lambda e:None),'x')
            assert all(r['status']=='translated' for r in records),records
            for record in records:
                box=f.Rect(record['layout_box'])
                if record['axis']=='x':assert abs((box.x0+box.x1)/2-195)<1
                else:assert abs((box.y0+box.y1)/2-110)<1
            text=dst[0].get_text()
            assert 'H (A/m)' in text and 'B (T)' in text and 'field strength' not in text
            clip=f.Rect(85,35,305,185)
            before=np.frombuffer(p.get_pixmap(clip=clip,matrix=f.Matrix(3,3)).samples,np.uint8).astype(int)
            after=np.frombuffer(dst[0].get_pixmap(clip=clip,matrix=f.Matrix(3,3)).samples,np.uint8).astype(int)
            assert np.max(np.abs(before-after))<=3
    finally:src.close()


def test_unreliable_axis_boundary_is_preserved_without_request():
    with f.open() as src:
        p=src.new_page(width=300,height=220)
        p.insert_text((30,180),'field strength',rotate=90)
        area=dict(id='f',kind='figure',box=[10,10,290,210],regions=[dict(id='y',kind='figure',box=list(p.search_for('field strength')[0]),text='field strength',rotation=90)])
        t=translator();t.translate_fragment=lambda *a,**k:pytest.fail('No reliable geometry; do not send API')
        before=p.get_pixmap().samples
        result=apply_graphics(p,p,t,NS(discover=lambda *a:[area],emit=lambda e:None),'x')
        assert result[0]['status']=='needs_review' and '绘图区' in result[0]['reason']
        assert p.get_pixmap().samples==before
