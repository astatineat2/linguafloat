"""Repair the identified real output using its original Japanese cache, zero HTTP."""
import json,sys
from pathlib import Path
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import pymupdf as f
from linguafloat.document_core import DocumentJob,DocumentTranslator,JobStore,JobControl
from linguafloat.document_graphics import GraphicAnalyzer,apply_graphics,source_digest
from linguafloat.document_pdf import append_bilingual

OUT=ROOT/'artifacts/hobson-v0.9.2'
if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    identity=json.loads((OUT/'task-identity.json').read_text(encoding='utf-8'))
    config=identity['details']['configuration']
    settings=json.loads((OUT/'config.json').read_text(encoding='utf-8'))
    profile=next(p for p in settings['providers'] if p['id']==config['provider_id'])
    assert profile['model']==config['model'] and config['target']=='ja'
    job=DocumentJob(identity['source'],str(OUT),profile,target=config['target'],
                    pdf_mode='layout',relayout_only=True,translate_tables=True,translate_figures=True)
    store=JobStore(OUT/'documents/jobs.sqlite3');control=JobControl()
    translator=DocumentTranslator(job,'',store,control,lambda e:None)
    def forbidden(*a,**k):raise AssertionError('Relayout must never call API')
    translator.adapter.request=forbidden
    analyzer=GraphicAnalyzer(store,control,lambda e:None)
    records=[];digest=source_digest(job.source)
    previous=json.loads((OUT/'original-report.json').read_text(encoding='utf-8'))
    prior=identity['details']['outputs'][0]
    with f.open(job.source) as source,f.open(prior) as old,f.open() as output:
        assert len(source)==len(old)==9
        for n in range(len(source)):
            with f.open() as fragment:
                p=fragment.new_page(width=source[n].rect.width,height=source[n].rect.height)
                p.show_pdf_page(p.rect,old,n,clip=f.Rect(632,26,1244,818))
                areas=analyzer.discover(source[n],digest)
                for area in areas:
                    rect=f.Rect(area['box'])
                    p.add_redact_annot(rect,fill=False,cross_out=False)
                    p.apply_redactions(images=0,graphics=0,text=0)
                    p.draw_rect(rect,color=None,fill=(1,1,1),overlay=True)
                    p.show_pdf_page(rect,source,n,clip=rect)
                records.extend(apply_graphics(source[n],p,translator,analyzer,digest))
                append_bilingual(output,source,n,fragment,'side_by_side')
                for name,box in [('table',f.Rect(130,127,476,255))] if n==3 else []:
                    p.get_pixmap(matrix=f.Matrix(3,3),clip=box).save(OUT/f'{name}-after.png')
                    old[n].get_pixmap(matrix=f.Matrix(3,3),clip=box+(632,26,632,26)).save(OUT/f'{name}-before.png')
                if n in (2,3,4,5,6,8):
                    p.get_pixmap(matrix=f.Matrix(1.5,1.5)).save(OUT/f'after-p{n+1}.png')
        output.set_metadata(dict(title='Hobson 2022 — original Japanese output, local graphics repair v0.9.2',
                                 subject='Original task 40ada299; original translated body reused; zero new API requests.'))
        output.save(OUT/'Hobson.保留版式修复.双语.pdf',garbage=4,deflate=True)
    mapping={r['id']:r for r in records}
    audit=[]
    for old in previous['graphics_regions']:
        if old['status']!='needs_review':continue
        new=mapping[old['id']]
        audit.append(dict(page=old['page'],id=old['id'],source=old['text'],before_reason=old['reason'],
                          after_status=new['status'],after_reason=new.get('reason'),translation=new.get('translation'),
                          size=new.get('size'),box=old['box']))
    result=dict(task_id=identity['id'],original_target=config['target'],validation='original real API Japanese translations reused, zero new HTTP',
                calls=translator.calls,cache_hits=translator.hits,records=records,ten_issues=audit,
                remaining=[r for r in records if r['status']=='needs_review'])
    (OUT/'repair-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    store.close()
    print(json.dumps(dict(calls=result['calls'],cache_hits=result['cache_hits'],remaining=result['remaining'],
                         audit=[(r['page'],r['source'],r['after_status'],r['size']) for r in audit]),ensure_ascii=False))
