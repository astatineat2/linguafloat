import json
import threading
import time
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from linguafloat.api_compat import error_details
from linguafloat.document_core import DocumentError, DocumentRequestError, DocumentTranslator, JobControl, JobStore
from linguafloat.document_scheduler import RequestGate
from test_document_core import job_for


def test_connection_cause_is_recorded_without_inventing_supplier_id():
    request = httpx.Request('POST', 'https://mock.invalid/v1/responses')
    try:
        raise httpx.ReadTimeout('secret source text', request=request)
    except httpx.ReadTimeout as cause:
        error = openai.APITimeoutError(request=request)
        error.__cause__ = cause
    text, details = error_details(error, request_id='R0013')
    assert details['transport_error_type'] == 'ReadTimeout'
    assert details['timeout_phase'] == 'read'
    assert details['local_request_id'] == 'R0013'
    assert not details.get('supplier_request_id')
    assert 'secret source text' not in text + json.dumps(details)


def test_timeout_reduces_concurrency_gradually_but_429_keeps_cooldown(tmp_path):
    store = JobStore(tmp_path/'gate.sqlite')
    try:
        gate = RequestGate(dict(document_concurrency=6), store, JobControl(), time.sleep)
        gate.backoff(2, rate_limited=False)
        assert gate.limit == 5 and gate.cooldown == 0
        gate.backoff(7)
        assert gate.limit == 1 and gate.cooldown > time.monotonic()
    finally:
        store.close()


def test_retry_backoff_releases_the_slot_before_waiting(tmp_path, monkeypatch):
    job = job_for(tmp_path/'paper.pdf')
    job.provider['retries'] = 1
    store = JobStore(tmp_path/'requests.sqlite')
    calls = []
    translator = None
    def create(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise openai.APIConnectionError(request=httpx.Request('POST','https://mock.invalid'))
        return NS(output_text='译文')
    translator = DocumentTranslator(job, 'dummy', store, JobControl(), lambda _:None,
                                    NS(responses=NS(create=create)))
    observed = []
    def wait(seconds):
        if seconds == 2:
            observed.append(translator.gate.inflight)
        else:
            time.sleep(seconds)
    monkeypatch.setattr('linguafloat.document_scheduler.retry_delay',lambda *_:2)
    translator.wait = wait
    try:
        assert translator.translate('synthetic') == '译文'
        assert observed == [0]
    finally:
        store.close()


def test_gate_fifo_and_fatal_wakes_waiting_requests(tmp_path):
    store = JobStore(tmp_path/'gate.sqlite')
    gate = RequestGate(dict(document_concurrency=1),store,JobControl(),time.sleep)
    first = gate.acquire([])
    waiting = threading.Event()
    result = []
    def run():
        try:
            gate.acquire([], on_wait=lambda _:waiting.set())
        except DocumentError:
            result.append('stopped')
    thread = threading.Thread(target=run,daemon=True)
    thread.start()
    try:
        assert waiting.wait(1)
        gate.stop(DocumentRequestError('fatal'))
        thread.join(1)
        assert result == ['stopped']
        assert store.db.execute('select count(*) from request_quota').fetchone()[0] == 1
    finally:
        gate.release(first)
        store.close()


def test_attempt_elapsed_is_separate_from_logical_elapsed(tmp_path, monkeypatch):
    job = job_for(tmp_path/'paper.pdf')
    store = JobStore(tmp_path/'requests.sqlite')
    events=[]
    translator=DocumentTranslator(job,'dummy',store,JobControl(),events.append,NS())
    clock=[100.0]
    monkeypatch.setattr('linguafloat.document_core.time.monotonic',lambda:clock[0])
    try:
        translator.trace_request('R0013','started','test','digest',0,2)
        clock[0]=162
        translator.trace_request('R0013','waiting','test','digest',0,2)
        event=events[-1]
        assert event['request']['elapsed_seconds']==162
        assert event['request']['attempt_elapsed_seconds']==62
        assert '本次 62 秒' in event['message'] and '累计 162 秒' in event['message']
    finally:
        store.close()


def test_gate_sends_waiters_in_arrival_order(tmp_path):
    store=JobStore(tmp_path/'fifo.sqlite')
    gate=RequestGate(dict(document_concurrency=1),store,JobControl(),time.sleep)
    held=gate.acquire([])
    order=[]
    threads=[]
    try:
        for label in ('older retry','new work','later retry'):
            queued=threading.Event()
            def run(label=label,queued=queued):
                slot=gate.acquire([],on_wait=lambda _:queued.set())
                order.append(label)
                gate.release(slot)
            thread=threading.Thread(target=run,daemon=True)
            thread.start(); threads.append(thread)
            assert queued.wait(1)
        gate.release(held)
        for thread in threads: thread.join(5)
        assert order==['older retry','new work','later retry']
    finally:
        store.close()


def test_fatal_interrupts_paused_gate_without_sending(tmp_path):
    store=JobStore(tmp_path/'paused.sqlite')
    control=JobControl(); control.paused.set()
    gate=RequestGate({},store,control,time.sleep)
    errors=[]
    def run():
        try: gate.acquire([])
        except DocumentError: errors.append(1)
    thread=threading.Thread(target=run,daemon=True)
    thread.start()
    try:
        gate.stop(DocumentRequestError('fatal'))
        thread.join(1)
        assert errors==[1] and gate.inflight==0
        assert not store.db.execute('select * from request_quota').fetchall()
    finally:
        control.paused.clear(); store.close()


def test_real_loopback_transport_records_headers_body_and_supplier_id(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            time.sleep(.03)
            payload=json.dumps(dict(id='r',object='response',created_at=1,status='completed',
                output=[dict(type='message',id='m',role='assistant',status='completed',
                content=[dict(type='output_text',text='译文',annotations=[])])])).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('x-request-id','supplier-loopback-1')
            self.end_headers(); self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    job=job_for(tmp_path/'paper.pdf')
    job.provider.update(base_url=f'http://127.0.0.1:{server.server_port}/v1',retries=0)
    store=JobStore(tmp_path/'transport.sqlite')
    events=[]
    t=DocumentTranslator(job,'local-only',store,JobControl(),events.append)
    try:
        assert t.translate('synthetic')=='译文'
        event=next(e['request'] for e in events if e.get('request',{}).get('state')=='success')
        assert event['supplier_request_id']=='supplier-loopback-1'
        assert event['network_timings']['等待响应头']>=.02
        assert event['network_timings']['响应体读取']>=0
        assert 'local-only' not in json.dumps(event)
    finally:
        t.client.close(); store.close(); server.shutdown(); server.server_close()


def test_failed_job_keeps_success_and_cache_counts_for_terminal_recovery(tmp_path):
    job=job_for(tmp_path/'paper.pdf'); job.provider['retries']=0
    store=JobStore(tmp_path/'counts.sqlite');store.status(job,'running')
    fail=[False]
    def create(**kwargs):
        if fail[0]:raise openai.APIConnectionError(request=httpx.Request('POST','https://mock.invalid'))
        return NS(output_text='译文')
    t=DocumentTranslator(job,'dummy',store,JobControl(),lambda _:None,NS(responses=NS(create=create)))
    try:
        t.translate('first');t.translate('first');fail[0]=True
        with pytest.raises(DocumentError):t.translate('second')
        details=json.loads(store.db.execute('select details from jobs where id=?',(job.job_id,)).fetchone()[0])
        assert details['requests']==1 and details['cache_hits']==1
    finally:store.close()
