import json
from pathlib import Path
from types import SimpleNamespace as NS

import pymupdf as f

from linguafloat.document_core import DocumentRequestError, JobControl, JobStore
from linguafloat.document_worker import execute
from test_document_core import job_for


def test_worker_publishes_valid_partial_outputs_without_claiming_complete(tmp_path,monkeypatch):
    src=tmp_path/'source.pdf'
    with f.open() as d:
        d.new_page().insert_text((50,50),'Original label')
        d.save(src)
    job=job_for(src,pdf_mode='layout',skip_references=False,mono=True,bilingual=False)
    error=DocumentRequestError('synthetic',details={'error_type':'APIConnectionError','transport_error_type':'ConnectError'})
    t=NS(calls=1,hits=2,graphic_failure=error,token_usage={},client=NS(close=lambda:None))
    def process(job,translator,work,emit):
        output=work/'partial.pdf';output.write_bytes(src.read_bytes())
        (work/'layout-report.json').write_text(json.dumps({'untranslated_regions':{'0':[
            dict(box=[45,35,180,55],reason='connection exhausted',request_attempted=True,
                 request_failure=error.details,coordinates='top-left')]}}))
        return [output],[]
    monkeypatch.setattr('linguafloat.document_pdf.translate_layout_pdf',process)
    events=[];store=JobStore(tmp_path/'jobs.sqlite')
    try:
        execute(job,'',store,JobControl(),events.append,t)
        status,raw=store.db.execute('select status,details from jobs where id=?',(job.job_id,)).fetchone()
        details=json.loads(raw)
        assert status=='partial'
        assert details['finished_at']>=details['started_at']
        assert details['transport_partial'] is True
        assert details['untranslated_regions']==1
        assert '部分完成' in details['warnings'][-1]
        assert all(Path(p).is_file() for p in details['outputs'])
        assert next(e for e in events if e.get('type')=='finished')['transport_partial'] is True
    finally:store.close()
