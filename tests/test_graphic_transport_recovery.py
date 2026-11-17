import json
import ssl
from types import SimpleNamespace as NS

import httpx
import openai
import pymupdf as f
import pytest

from linguafloat.api_compat import error_details
from linguafloat.document_core import DocumentError, DocumentRequestError, DocumentTranslator, JobControl, JobStore
from linguafloat.document_graphics import apply_graphics, scientific_tokens
from test_document_core import job_for


def requester(tmp_path, respond, **options):
    job=job_for(tmp_path/'source.pdf',translate_figures=True,translate_tables=True)
    job.provider.update(retries=2,document_rpm=10000,**options)
    store=JobStore(tmp_path/'requests.sqlite')
    client=openai.OpenAI(api_key='synthetic',base_url=job.provider['base_url'],max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    return DocumentTranslator(job,'',store,JobControl(),lambda _:None,client),store


def response(text):
    return httpx.Response(200,json=dict(id='local',object='response',created_at=1,status='completed',
        output=[dict(type='message',id='m',role='assistant',status='completed',
                     content=[dict(type='output_text',text=text,annotations=[])])]))


def test_graphic_transport_exhaustion_does_not_poison_the_body(tmp_path,monkeypatch):
    monkeypatch.setattr('linguafloat.document_scheduler.retry_delay',lambda *_:0)
    def fail(req):raise httpx.ConnectError('synthetic',request=req)
    t,store=requester(tmp_path,fail)
    try:
        with pytest.raises(DocumentRequestError):
            t.translate_fragment('height','local context',part='图表文字',recover_transport=True)
        assert t.fatal_error is None
        assert store.db.execute('select count(*) from cache').fetchone()[0]==0
    finally:t.client.close();store.close()


def test_graphic_connection_failure_keeps_success_and_stops_remaining_requests(tmp_path,monkeypatch):
    monkeypatch.setattr('linguafloat.document_scheduler.retry_delay',lambda *_:0)
    calls=[]
    def send(req):
        text=json.loads(json.loads(req.content)['input'][-1]['content'])['fragment']
        calls.append(text)
        if text=='height':raise httpx.ConnectError('synthetic',request=req)
        return response('外径')
    t,store=requester(tmp_path,send)
    with f.open() as src:
        p=src.new_page(width=300,height=240)
        areas=[]
        for i,(text,y) in enumerate([('outer diameter',50),('height',110),('field strength',170)]):
            p.insert_text((40,y),text,fontsize=10)
            box=list(p.search_for(text)[0]);r=dict(id=str(i),kind='table',box=box,text=text,confidence=1,rotation=0,reason='')
            areas.append(dict(id=f't{i}',kind='table',box=[30,y-20,250,y+15],regions=[r],lines=[]))
        analyzer=NS(discover=lambda *_:areas,emit=lambda _:None)
        try:
            with f.open(stream=src.tobytes(),filetype='pdf') as dst:
                records=apply_graphics(p,dst[0],t,analyzer,'fixture')
                assert [r['status'] for r in records]==['translated','needs_review','needs_review']
                assert '外径' in dst[0].get_text()
                assert 'height' in dst[0].get_text() and 'field strength' in dst[0].get_text()
                assert calls==['outer diameter','height','height','height']
                assert records[1]['request_failure']['transport_error_type']=='ConnectError'
                assert records[2]['request_attempted'] is False
                assert store.db.execute('select count(*) from cache').fetchone()[0]==1
                # State crosses page/area boundaries; no repeated failing calls.
                apply_graphics(p,dst[0],t,analyzer,'fixture')
                assert len(calls)==4
        finally:t.client.close();store.close()


def test_auth_errors_remain_fatal_in_graphics(tmp_path):
    t,store=requester(tmp_path,lambda req:httpx.Response(401,json={'error':{'message':'synthetic'}}))
    try:
        with pytest.raises(DocumentRequestError):
            t.translate_fragment('height','local context',part='图表文字',recover_transport=True)
        assert isinstance(t.fatal_error,DocumentRequestError)
    finally:t.client.close();store.close()


def test_body_connection_error_still_stops_the_task(tmp_path,monkeypatch):
    monkeypatch.setattr('linguafloat.document_scheduler.retry_delay',lambda *_:0)
    def fail(req):raise httpx.ConnectError('synthetic',request=req)
    t,store=requester(tmp_path,fail)
    try:
        with pytest.raises(DocumentRequestError):t.translate('Body paragraph')
        assert isinstance(t.fatal_error,DocumentRequestError)
    finally:t.client.close();store.close()


def test_fragment_rejects_identifiers_imported_from_context_before_caching(tmp_path):
    t,store=requester(tmp_path,lambda req:response('植物乳杆菌 DX7 的活性'))
    try:
        with pytest.raises(DocumentError,match='片段'):
            t.translate_fragment('activity of','L. plantarum DX7',part='图表文字',recover_transport=True)
        assert store.db.execute('select count(*) from cache').fetchone()[0]==0
    finally:t.client.close();store.close()


def test_source_corroborated_binomial_names_protect_species_but_not_prose():
    with f.open() as doc:
        p=doc.new_page()
        p.insert_text((30,40),'L. plantarum DX7 and E. coli K12. Crystal structure and Spin ordering.')
        tokens=scientific_tokens(p)
        assert {'plantarum','coli'}<=tokens
        assert not {'structure','ordering','activity'} & tokens


def test_connection_diagnostics_keep_system_code_without_secret_text():
    req=httpx.Request('POST','https://private.invalid')
    low=ssl.SSLError(1,'secret-key and paper content')
    transport=httpx.ConnectError('secret-key and paper content',request=req);transport.__cause__=low
    exc=openai.APIConnectionError(request=req);exc.__cause__=transport
    text,details=error_details(exc,key='secret-key',source_texts=('paper content',))
    assert details['system_error_codes']==[1]
    assert 'secret-key' not in text+json.dumps(details) and 'paper content' not in text+json.dumps(details)


def test_transient_graphic_connection_failure_can_succeed_and_cache(tmp_path,monkeypatch):
    monkeypatch.setattr('linguafloat.document_scheduler.retry_delay',lambda *_:0)
    calls=[]
    def send(req):
        calls.append(1)
        if len(calls)==1:raise httpx.ConnectError('synthetic',request=req)
        return response('高度')
    t,store=requester(tmp_path,send)
    try:
        assert t.translate_fragment('height','local',recover_transport=True)=='高度'
        assert t.translate_fragment('height','local',recover_transport=True)=='高度'
        assert len(calls)==2 and t.fatal_error is None and t.graphic_failure is None
        assert store.db.execute('select count(*) from cache').fetchone()[0]==1
    finally:t.client.close();store.close()


def test_cancelled_graphic_request_does_not_become_partial(tmp_path,monkeypatch):
    from linguafloat.document_core import Cancelled
    monkeypatch.setattr('linguafloat.document_scheduler.retry_delay',lambda *_:0)
    calls=[]
    def send(req):
        calls.append(1)
        t.control.cancelled.set()
        raise httpx.ConnectError('synthetic',request=req)
    t,store=requester(tmp_path,send)
    try:
        with pytest.raises(Cancelled):t.translate_fragment('height','local',recover_transport=True)
        assert len(calls)==1 and t.graphic_failure is None
        assert store.db.execute('select count(*) from cache').fetchone()[0]==0
    finally:t.client.close();store.close()


def test_invalid_cached_label_is_discarded_without_a_paid_retry(tmp_path):
    calls=[]
    def send(req):
        calls.append(1)
        return response('高度')
    t,store=requester(tmp_path,send)
    try:
        assert t.translate_fragment('height','local',recover_transport=True)=='高度'
        key=store.db.execute('select key from cache').fetchone()[0]
        store.put(key,'DX7 的高度')
        with pytest.raises(DocumentError,match='片段'):
            t.translate_fragment('height','local',recover_transport=True)
        assert len(calls)==1 and store.get(key) is None
    finally:t.client.close();store.close()


def test_network_failure_stage_is_safe_and_does_not_leak_to_next_attempt(tmp_path,monkeypatch):
    monkeypatch.setattr('linguafloat.document_scheduler.retry_delay',lambda *_:0)
    calls=[];events=[]
    def send(req):
        calls.append(1)
        if len(calls)==1:
            t._trace_http(req)
            trace=req.extensions['trace']
            trace('connection.start_tls.started',{'headers':'secret-header','body':'private-paper'})
            trace('connection.start_tls.failed',{'exception':'private-url'})
        raise httpx.ConnectError('synthetic',request=req)
    t,store=requester(tmp_path,send);t.retries=1;t.emit=events.append
    try:
        with pytest.raises(DocumentRequestError) as caught:
            t.translate_fragment('height','local',recover_transport=True)
        first=next(e['request'] for e in events if e.get('request',{}).get('state')=='retry')
        assert first['network_failure_stage']=='TLS 握手'
        assert 'network_failure_stage' not in caught.value.details
        raw=json.dumps(events,ensure_ascii=False)
        assert not any(v in raw for v in ('secret-header','private-paper','private-url'))
    finally:t.client.close();store.close()
