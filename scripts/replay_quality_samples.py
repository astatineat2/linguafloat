"""Run the frozen local PDF samples against the checkout, with no network or credentials."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from check_pdf_sample import compare
import pymupdf as f
import httpx


def replay(corpus, output, case):
    from linguafloat.document_worker import execute
    from linguafloat.document_core import DocumentJob,JobStore,JobControl
    from babeldoc.docvision.doclayout import OnnxModel
    folder=corpus/case; live=folder/'direct'; original,=live.glob('*.pdf')
    with closing(sqlite3.connect((live/'pilot.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        cfg=json.loads(db.execute('select details from jobs where id=?',(json.loads((live/'run.json').read_text('utf8'))['job'],)).fetchone()[0])['configuration']
    profile=json.loads((Path(os.environ['APPDATA'])/'LinguaFloat/config.json').read_text('utf8'))
    provider=next(dict(p) for p in profile['providers'] if p['id']==cfg['provider_id'])
    provider.update(cfg.get('request_policy',{}), model=cfg['model'],api_mode=cfg['api_mode'])
    if hashlib.sha256(provider['base_url'].rstrip('/').encode()).hexdigest()!=cfg['endpoint_sha256']:raise ValueError('Historical profile mismatch')
    baseline,=folder.rglob('*.译文.pdf');dest=output/case;dest.mkdir(parents=True,exist_ok=False)
    net=[]
    def deny(*args,**kwargs):net.append(True);raise RuntimeError('Offline gate forbids network')
    def guard(fn):
        def connect(sock,address):
            if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1'):return fn(sock,address)
            return deny()
        return connect
    model=OnnxModel(str(Path.home()/'.cache/babeldoc/models/doclayout_yolo_docstructbench_imgsz1024.onnx'))
    with TemporaryDirectory(dir=dest) as tmp:
        temp=Path(tmp);shutil.copytree(live/'layout-cache',temp/'layout-cache')
        with closing(sqlite3.connect((live/'pilot.sqlite3').as_uri()+'?mode=ro',uri=True)) as a,closing(sqlite3.connect(temp/'cache.db')) as b:a.backup(b)
        store=JobStore(temp/'cache.db');job=DocumentJob(str(original),str(dest),provider,job_id=uuid4().hex,pdf_mode='layout',workflow='body',translate_tables=True,skip_references=True,relayout_only=True)
        try:
            with patch.object(httpx.Client,'send',deny),patch.object(socket.socket,'connect',guard(socket.socket.connect)),patch.object(socket.socket,'connect_ex',guard(socket.socket.connect_ex)),patch.object(OnnxModel,'from_pretrained',return_value=model):execute(job,'',store,JobControl(),lambda _:None)
        finally:store.close()
    return inspect_saved(folder,dest,len(net))


def inspect_saved(folder,dest,network_attempts):
    case=folder.name;original,=(folder/'direct').glob('*.pdf');baseline,=folder.rglob('*.译文.pdf')
    mono,=dest.rglob('*.译文.pdf');dual,=dest.rglob('*.双语.pdf')
    with f.open(original) as src,f.open(baseline) as before,f.open(mono) as after,f.open(dual) as paired:
        if [len(d) for d in [src,before,after,paired]]!=[1]*4:raise ValueError('Unexpected pagination')
        page=after[0];sheet=paired[0];r=f.Rect(sheet.rect.width-page.rect.width,sheet.rect.height-page.rect.height,sheet.rect.width,sheet.rect.height)
        issues=compare(src[0],page);old=compare(src[0],before[0]);same=sheet.get_text(clip=r).split()==page.get_text().split()
        links=lambda pg,clip:Counter(l.get('uri') for l in pg.get_links() if l.get('uri') and clip.contains(l['from']))
        if not same or links(sheet,r)!=links(page,page.rect):issues.append(dict(rule='bilingual-panel-mismatch',severity='fail'))
        sheet.get_pixmap(matrix=f.Matrix(1.5,1.5)).save(dest/'after.png')
    pending=[json.loads(line) for path in dest.rglob('未翻译问题.jsonl') for line in path.read_text('utf8').splitlines() if line]
    result=dict(case=case,source_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),before=old,after=issues,retained=len(pending),retained_reasons=dict(Counter(i.get('reason','') for i in pending)),network_attempts=network_attempts,bilingual=str(dual))
    (dest/'check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8');print('case',case,'before',dict(Counter(i['rule'] for i in old)),'after',dict(Counter(i['rule'] for i in issues)),'retained',len(pending),'network',network_attempts,flush=True)
    return bool(issues or pending or network_attempts is None or network_attempts)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('corpus',type=Path);parser.add_argument('output',type=Path);parser.add_argument('--case',choices=['01','19','22'],action='append');parser.add_argument('--audit-only',action='store_true',help='Inspect existing outputs; does not test a new worker build');args=parser.parse_args();logging.disable(logging.CRITICAL)
    results=[]
    for c in args.case or ['01','19','22']:
        if args.audit_only:
            dest=args.output/c;record=json.loads((dest/'check.json').read_text('utf8'));results.append(inspect_saved(args.corpus/c,dest,record.get('network_attempts')))
        else:results.append(replay(args.corpus,args.output,c))
    sys.exit(int(any(results)))
