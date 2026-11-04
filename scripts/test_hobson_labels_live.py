"""User-authorized: exactly three short Chinese labels, at most 3 HTTP attempts."""
from pathlib import Path
import json,sys
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from linguafloat.config import ConfigStore,CredentialStore
from linguafloat.document_core import DocumentJob,DocumentTranslator,JobStore,JobControl

OUT=ROOT/'artifacts/hobson-v0.9.2'
if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    report=OUT/'live-labels.json'
    if report.exists():raise SystemExit('Existing live results: do not repeat paid requests.')
    profile=ConfigStore(OUT/'config.json').load().active_profile()
    key=CredentialStore().get_api_key(profile.id,profile.env_key)
    if not key:raise SystemExit('No saved credential; no API call performed.')
    provider=asdict(profile);provider.update(output_token_limit=128,retries=0,document_concurrency=1)
    source=next(Path('C:/Users/Astatine/Zotero/storage/3U8UUC6T').glob('*.pdf'))
    job=DocumentJob(str(source),str(OUT),provider,target='zh-Hans',pages='4',request_timeout=60)
    store=JobStore(OUT/'live-labels.sqlite3');store.status(job,'running')
    translator=DocumentTranslator(job,key,store,JobControl(),lambda e:None)
    results=dict(scope='original page 4, three headers; Chinese; max 3 HTTP requests; 128 output tokens/request',
                 provider=profile.name,model=profile.model,api_mode=profile.api_mode,items=[])
    try:
        for text in ('Coil efficiency,','Target field','Target field harmonic'):
            translator.location='PDF 第 4 页 · 表头'
            try:
                value=translator.translate_fragment(text,'Scientific table cell or figure label. Translate natural language only; preserve scientific terminology. Return only the translated label.',part='图表文字')
                results['items'].append(dict(source=text,translation=value,status='success'))
            except Exception as error:
                # Error diagnostics from the adapter are credential-redacted.
                results['items'].append(dict(source=text,status='failed',error=str(error)))
            results.update(calls=translator.calls,usage=translator.token_usage)
            report.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        store.status(job,'completed',results)
        print(json.dumps(results,ensure_ascii=False))
    finally:store.close()
