import json
import socket
from dataclasses import replace
from types import SimpleNamespace as NS

import pymupdf as f
import pytest

from linguafloat.document_core import DocumentJob, DocumentError, JobControl, JobStore
from linguafloat.document_context import GraphicContext, SourceContext
from linguafloat.document_postprocess import save_body_artifact, load_body_artifact, translate_graphics_only, digest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*a, **kw):
        raise AssertionError('No network in follow-up tests')
    for name in ('connect', 'connect_ex'):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


@pytest.fixture
def bound_store(tmp_path):
    store = JobStore(tmp_path / 'binding.sqlite')
    yield store
    store.close()


def register(store, job, folder):
    store.status(job, 'completed', dict(graphics_source=str(folder),
        reference_boundary=job.reference_boundary,
        graphics_manifest_sha256=digest(folder / 'manifest.json')))


def artifact(tmp_path):
    source = tmp_path / '原始 文档.pdf'
    with f.open() as pdf:
        p = pdf.new_page(); p.insert_text((30, 40), 'original body')
        pdf.save(source)
    job = DocumentJob(str(source), str(tmp_path), dict(id='local', base_url='https://test.invalid/v1',
        model='mock', api_mode='responses'), job_id='body-parent', pdf_mode='layout',
        workflow='body', chinese_font='', latin_font='')
    t = NS(job=job, control=JobControl(), graphic_context=GraphicContext(), source_context=SourceContext())
    with f.open(source) as pdf:
        save_body_artifact(tmp_path, job, pdf, [dict(source=0, start=0, count=1, selected=True)], t)
    return job, tmp_path / '.graphics-source'


def test_followup_reuses_body_and_never_enters_body_pipeline(tmp_path, monkeypatch, bound_store):
    original, folder = artifact(tmp_path)
    register(bound_store, original, folder)
    job = replace(original, workflow='graphics', parent_job_id='body-parent', job_id='child',
                  translate_figures=True, graphics_source=str(folder))
    calls = []
    def apply(source, target, translator, analyzer, digest, overrides):
        calls.append(source.number)
        assert 'original body' in target.get_text()
        return []
    monkeypatch.setattr('linguafloat.document_graphics.apply_graphics', apply)
    t = NS(job=job, control=JobControl(), graphic_context=GraphicContext(), source_context=SourceContext(),
           set_source_context=lambda blocks: list(blocks))
    out = tmp_path / 'supplement'; out.mkdir()
    original_bytes = (folder/'body.pdf').read_bytes()
    outputs, warnings = translate_graphics_only(job, t, out, lambda _: None, store=bound_store)
    assert calls == [0] and len(outputs) == 2 and not warnings
    assert (folder/'body.pdf').read_bytes() == original_bytes
    assert json.loads((out/'layout-report.json').read_text())['body_translation_requests'] == 0
    with f.open(outputs[0]) as pdf:
        assert 'original body' in pdf[0].get_text()


def test_tampered_or_changed_source_rejected(tmp_path):
    job, folder = artifact(tmp_path)
    (folder/'body.pdf').write_bytes(b'not a body')
    with pytest.raises(DocumentError, match='校验失败'):
        load_body_artifact(folder, source=job.source, provider=job.provider)


def test_changed_provider_does_not_silently_retranslate(tmp_path):
    job, folder = artifact(tmp_path)
    with pytest.raises(DocumentError, match='模型已改变'):
        load_body_artifact(folder, provider=dict(job.provider, model='different'))


def test_legacy_task_missing_artifact_has_explicit_error(tmp_path):
    with pytest.raises(DocumentError, match='不会自动重翻'):
        load_body_artifact(tmp_path/'absent')


def test_invalid_page_map_rejected_before_graphic_requests(tmp_path, monkeypatch, bound_store):
    original, folder = artifact(tmp_path)
    register(bound_store, original, folder)
    p=folder/'manifest.json'; manifest=json.loads(p.read_text(encoding='utf-8')); manifest['pages'][0]['count']=2
    p.write_text(json.dumps(manifest))
    job=replace(original, workflow='graphics', parent_job_id='body-parent', graphics_source=str(folder), translate_figures=True)
    def forbidden(*a, **kw): pytest.fail('must not request graphics')
    monkeypatch.setattr('linguafloat.document_graphics.apply_graphics', forbidden)
    t=NS(job=job, control=JobControl(), graphic_context=GraphicContext(), set_source_context=lambda _:None)
    with pytest.raises(DocumentError, match='页面映射'):
        translate_graphics_only(job,t,tmp_path,lambda _:None,store=bound_store)


def test_body_workflow_cannot_silently_translate_graphics(tmp_path):
    original, folder=artifact(tmp_path)
    with pytest.raises(DocumentError, match='正文任务不自动'):
        replace(original,translate_figures=True).validate()


def test_worker_graphics_publication_keeps_parent_and_tracks_link(tmp_path, monkeypatch):
    from linguafloat.document_worker import execute
    original, folder=artifact(tmp_path)
    store=JobStore(tmp_path/'isolated.sqlite')
    register(store, original, folder)
    job=replace(original,workflow='graphics',parent_job_id='body-parent',job_id='child12345678',
                graphics_source=str(folder),translate_figures=True)
    monkeypatch.setattr('linguafloat.document_graphics.apply_graphics',lambda *a,**kw:[])
    t=NS(job=job,control=JobControl(),graphic_context=GraphicContext(),set_source_context=lambda _:None,
         calls=0,hits=0)
    events=[]; before=(folder/'body.pdf').read_bytes()
    try:
        execute(job,'',store,t.control,events.append,t)
        result=next(e for e in events if e['type']=='finished')
        assert result['parent_job_id']=='body-parent' and result['requests']==0
        assert '图片补译-1' in result['outputs'][0]
        assert (folder/'body.pdf').read_bytes()==before
        assert load_body_artifact(result['graphics_source'])['parent_job_id']=='body-parent'
    finally:store.close()


def test_bilingual_only_child_exports_its_own_supplement(tmp_path, monkeypatch):
    from linguafloat.document_worker import execute
    from linguafloat.document_export import export_file
    original,folder=artifact(tmp_path)
    original=replace(original,mono=False)
    mp=folder/'manifest.json'; m=json.loads(mp.read_bytes()); m['mono']=False
    layout=folder/'export-layout.json'; layout.write_text('{}'); m['export_layout_sha256']=digest(layout)
    mp.write_text(json.dumps(m),'utf8')
    store=JobStore(tmp_path/'isolated.sqlite'); register(store,original,folder)
    job=replace(original,workflow='graphics',parent_job_id=original.job_id,job_id='child-supp',graphics_source=str(folder),translate_figures=True)
    def apply(source,target,*args):
        target.insert_text((30,100),'Supplement only'); return []
    monkeypatch.setattr('linguafloat.document_graphics.apply_graphics',apply)
    t=NS(job=job,control=JobControl(),graphic_context=GraphicContext(),set_source_context=lambda _:None,calls=0,hits=0)
    events=[]
    try:
        execute(job,'',store,t.control,events.append,t)
        result=next(e for e in events if e['type']=='finished'); assert len(result['outputs'])==1
        export_file(job.job_id,result,'markdown',tmp_path/'child.md')
        assert 'Supplement only' in (tmp_path/'child.md').read_text('utf8')
        with f.open(folder/'body.pdf') as pdf: assert 'Supplement only' not in pdf[0].get_text()
    finally:store.close()
