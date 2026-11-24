"""Isolated candidate worker for the blind QA runner. No writes to application history."""
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import threading
from unittest.mock import patch
from uuid import uuid4


def credential(profile):
    key = os.getenv(profile.get('env_key') or '')
    if key: return key
    try:
        from linguafloat.config import CredentialStore
    except ModuleNotFoundError as exc:
        if exc.name != 'keyring' or os.name != 'nt': raise
    else: return CredentialStore().get_api_key(profile['id'],profile.get('env_key',''))
    # Document-only runtime omits keyring. Read only this profile's Windows credential.
    import ctypes as ct
    from ctypes import wintypes as w
    class Cred(ct.Structure):
        _fields_=[('flags',w.DWORD),('type',w.DWORD),('target',w.LPWSTR),('comment',w.LPWSTR),
                  ('time',w.FILETIME),('size',w.DWORD),('blob',ct.c_void_p),('persist',w.DWORD),
                  ('count',w.DWORD),('attrs',ct.c_void_p),('alias',w.LPWSTR),('user',w.LPWSTR)]
    api=ct.WinDLL('advapi32'); ptr=ct.POINTER(Cred)(); service='LinguaFloat OpenAI API'
    for name in (service,profile['id']+'@'+service):
        if api.CredReadW(ct.c_wchar_p(name),1,0,ct.byref(ptr)):
            try:
                if ptr.contents.user==profile['id']: return ct.string_at(ptr.contents.blob,ptr.contents.size).decode('utf-16-le')
            finally: api.CredFree(ptr)
    return ''


def reserve(req, allowed, args, previous, case, lock):
    cap=json.loads(req.content).get('max_output_tokens',0)
    with lock:
        size=len(req.content)+cap
        if (req.method!='POST' or req.url!=allowed or not 0<cap<=4096 or
            previous['requests']+case['requests']>=args.requests or
            previous['reserved']+case['reserved']+size>args.reserve):
            raise ValueError('请求范围或本轮预算不允许，未发送')
        case['requests']+=1; case['reserved']+=size


def generate(source, dest, args, previous, case):
    import httpx
    from openai import OpenAI
    from babeldoc.docvision.doclayout import OnnxModel
    from linguafloat import __version__
    from linguafloat.api_compat import normalize_base_url
    from linguafloat.document_core import DocumentJob,DocumentTranslator,JobControl,JobStore
    from linguafloat.document_worker import execute
    base=Path(os.environ['APPDATA'])/'LinguaFloat'
    cfg=json.loads((base/'config.json').read_text('utf8')); profile=dict(next(x for x in cfg['providers'] if x['id']==cfg['active_provider']))
    if profile['api_mode']!='responses': raise ValueError('当前受限生成器只验证过 responses，不会自动更换供应商')
    online=bool(args.requests); key=credential(profile) if online else ''
    if online and not key: raise ValueError('当前供应商未配置可用凭据；未发请求')
    url=normalize_base_url(profile['base_url']).rstrip('/'); allowed=httpx.URL(url+'/responses')
    if online and allowed.scheme!='https': raise ValueError('在线验证只允许 HTTPS 接口')
    profile.update(retries=0,output_token_limit=4096)
    modelpath=Path.home()/'.cache/babeldoc/models/doclayout_yolo_docstructbench_imgsz1024.onnx'
    if not modelpath.is_file(): raise ValueError('缺少本地布局模型，未自动下载')
    job=DocumentJob(str(source),str(dest),profile,job_id=uuid4().hex,pdf_mode='layout',workflow='body',
                    translate_tables=True,translate_figures=False,skip_references=True,relayout_only=not online)
    case.update(version=__version__,job_id=job.job_id,model=profile['model'],provider=profile['id'],
                endpoint_sha256=hashlib.sha256(url.encode()).hexdigest(),worker=execute.__code__.co_filename)
    lock=threading.RLock(); permitted=threading.local(); control=JobControl(); dest.mkdir()
    class Transport(httpx.HTTPTransport):
        def handle_request(self, request):
            try: reserve(request,allowed,args,previous,case,lock)
            except ValueError:
                control.cancelled.set(); raise
            permitted.value=True
            try: return super().handle_request(request)
            finally: permitted.value=False
    def guard(original):
        def connect(sock, address):
            if not getattr(permitted,'value',False) and not (isinstance(address,tuple) and address[0] in ('127.0.0.1','::1')):
                raise RuntimeError('禁止额外的网络连接')
            return original(sock,address)
        return connect
    with ExitStack() as stack:
        for name in ('connect','connect_ex'):
            stack.enter_context(patch.object(socket.socket,name,guard(getattr(socket.socket,name))))
        model=OnnxModel(str(modelpath)); stack.enter_context(patch.object(OnnxModel,'from_pretrained',return_value=model))
        store=stack.enter_context(closing(JobStore(dest/'cache.sqlite3'))); lookup=store.get
        history=[]
        for db in dict.fromkeys([args.cache,base/'documents/jobs.sqlite3']):
            if db and db.is_file(): history.append(stack.enter_context(closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,check_same_thread=False))))
        def get(k):
            value=lookup(k)
            if value is not None: return value
            with lock:
                for db in history:
                    hit=db.execute('select value from cache where key=?',(k,)).fetchone()
                    if hit: return hit[0]
            return None
        store.get=get
        client=stack.enter_context(OpenAI(api_key=key or 'offline',base_url=url,max_retries=0,
            http_client=httpx.Client(transport=Transport(retries=0,trust_env=False),trust_env=False,follow_redirects=False,timeout=150)))
        translator=DocumentTranslator(job,key,store,control,lambda _:None,client)
        try: execute(job,key,store,control,lambda _:None,translator)
        finally:
            case.update(cache_hits=translator.hits,tokens=translator.token_usage['total_tokens'],usage=translator.token_usage.copy())
        status,details=store.db.execute('select status, details from jobs where id=?',(job.job_id,)).fetchone()
        if status!='completed': raise ValueError('候选生成未完成：'+status)
        paths=[Path(v) for v in json.loads(details)['outputs']]
        return next(v for v in paths if v.name.endswith('.译文.pdf')),next(v for v in paths if v.name.endswith('.双语.pdf'))
