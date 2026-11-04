"""Real nine-page relayout through the document worker; HTTP is forbidden."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from linguafloat.document_core import DocumentJob,DocumentTranslator,JobStore,JobControl
from linguafloat.document_worker import execute
OUT=ROOT/'artifacts/hobson-v0.9.2'
if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    task=json.loads((OUT/'task-identity.json').read_text(encoding='utf-8'))
    profiles=json.loads((OUT/'config.json').read_text(encoding='utf-8'))['providers']
    profile=next(p for p in profiles if p['id']==task['details']['configuration']['provider_id'])
    job=DocumentJob(task['source'],str(OUT/'pipeline'),profile,target='ja',pdf_mode='layout',
                    mono=False,bilingual=True,relayout_only=True,translate_tables=True,translate_figures=True)
    store=JobStore(OUT/'documents/jobs.sqlite3');control=JobControl();events=[]
    def emit(event):
        events.append(event)
        if event.get('type') in ('progress','finished'):print(json.dumps(event,ensure_ascii=False),flush=True)
    translator=DocumentTranslator(job,'',store,control,emit)
    def forbidden(*a,**k):raise AssertionError('No HTTP authorized for this relayout')
    translator.adapter.request=forbidden
    try:
        execute(job,'',store,control,emit,translator=translator)
    finally:
        (OUT/'pipeline-events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
        store.close()
