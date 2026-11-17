"""Offline artifact provenance, exact page mapping, and follow-up rejection."""
import json
import shutil
import socket
from dataclasses import replace
from types import SimpleNamespace as NS

import pymupdf as fitz
import pytest

from linguafloat.document_core import DocumentJob, DocumentError, JobControl, JobStore, selected_pages
from linguafloat.document_context import GraphicContext, SourceContext
from linguafloat import document_postprocess as post


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*a, **k):
        raise AssertionError("No network in graphics binding tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / "input.pdf"
    with fitz.open() as pdf:
        for n in range(4):
            pdf.new_page().insert_text((40, 80), f"SOURCE PAGE {n+1}")
        pdf.save(source)
    store = JobStore(tmp_path / "isolated.sqlite")
    def make(identity="body", *, pages="", trial=False, references=False, tables=False):
        output = tmp_path / identity
        output.mkdir()
        boundary = {"page": 2, "page_boxes": {"2": [[0, 0, 595, 842]]}, "whole_pages": [2]} if references else {}
        job = DocumentJob(str(source), str(tmp_path), dict(id="local", model="mock", api_mode="responses",
            base_url="https://offline.invalid/v1"), job_id=identity, workflow="body", pdf_mode="layout",
            pages=pages, trial=trial, reference_boundary=boundary, skip_references=references,
            translate_tables=tables)
        mapping = []
        with fitz.open(source) as original, fitz.open() as body:
            for n in selected_pages(pages, len(original), trial):
                mapping.append(dict(source=n, start=len(body), count=1, selected=not (references and n == 2)))
                body.insert_pdf(original, from_page=n, to_page=n)
            post.save_body_artifact(output, job, body, mapping, NS())
        folder = output / post.FOLDER
        store.status(job, "completed", dict(graphics_source=str(folder), reference_boundary=boundary,
            graphics_manifest_sha256=post.digest(folder / "manifest.json")))
        return job, folder
    yield make, store
    store.close()


def rewrite(folder, change):
    path = folder / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def child(job, folder, **kwargs):
    return replace(job, workflow="graphics", parent_job_id=job.job_id, job_id="child",
                   graphics_source=str(folder), translate_figures=True, **kwargs)


@pytest.mark.parametrize("variant", ["swap", "skip", "selected", "ref_selected", "trial", "bool_trial", "range"])
def test_invalid_map_rejected_by_loader(setup, variant):
    make, _ = setup
    job, folder = make(references=variant == "ref_selected")
    def damage(data):
        if variant == "swap":
            data["pages"][0]["source"], data["pages"][1]["source"] = 1, 0
        elif variant == "skip":
            data["pages"][0]["source"] = 4
        elif variant in {"selected", "ref_selected"}:
            i = 2 if variant == "ref_selected" else 0
            data["pages"][i]["selected"] = not data["pages"][i]["selected"]
        elif variant == "range":
            data["selected_pages"] = "2-4"
        else:
            data["trial"] = True if variant == "trial" else 1
    rewrite(folder, damage)
    with pytest.raises(DocumentError):
        post.load_body_artifact(folder, source=job.source, provider=job.provider)


@pytest.mark.parametrize("trial", [False, True])
def test_trial_sparse_selection_and_reference_map_are_explicit(setup, trial):
    make, store = setup
    job, folder = make(pages="1,3-4", trial=trial, references=True)
    data = post.validate_graphics_source(store, job.job_id, folder, source=job.source, provider=job.provider)
    assert data["trial"] is trial
    assert [r["source"] for r in data["pages"]] == ([0, 2] if trial else [0, 2, 3])
    assert [r["selected"] for r in data["pages"]] == ([True, False] if trial else [True, False, True])


def test_image_child_can_follow_body_with_translated_tables(setup):
    make, store = setup
    job, folder = make(tables=True)
    followup = child(job, folder, translate_tables=False)
    data = post.validate_graphics_source(store, job.job_id, folder, source=job.source,
                                        provider=job.provider, job=followup)
    assert data['table_policy'] == 'independent-tables-v1'


def test_legacy_preserve_table_manifest_stays_truthful(setup):
    make, store = setup
    job, folder = make()
    rewrite(folder, lambda data: data.pop('table_policy'))
    raw = json.loads(store.db.execute('SELECT details FROM jobs WHERE id=?', (job.job_id,)).fetchone()[0])
    raw['configuration'].pop('table_policy')
    raw['graphics_manifest_sha256'] = post.digest(folder/'manifest.json')
    store.db.execute('UPDATE jobs SET details=? WHERE id=?', (json.dumps(raw), job.job_id))
    store.db.commit()
    data = post.validate_graphics_source(store, job.job_id, folder, source=job.source, provider=job.provider)
    assert data.get('table_policy', 'preserve-tables-v1') == 'preserve-tables-v1'
    assert not raw['configuration']['translate_tables']
    raw['configuration']['table_policy'] = 'independent-tables-v1'
    store.db.execute('UPDATE jobs SET details=? WHERE id=?', (json.dumps(raw), job.job_id))
    store.db.commit()
    with pytest.raises(DocumentError):
        post.validate_graphics_source(store, job.job_id, folder, source=job.source, provider=job.provider)


def test_schema_one_without_trial_is_not_guessed(setup):
    make, _ = setup
    _, folder = make()
    def old(data):
        data["schema"] = 1
        data.pop("trial", None)
    rewrite(folder, old)
    with pytest.raises(DocumentError, match="重新生成正文"):
        post.load_body_artifact(folder)


def test_other_task_artifact_for_same_source_is_rejected(setup):
    make, store = setup
    job, folder = make("first")
    _, other = make("second")
    with pytest.raises(DocumentError):
        post.validate_graphics_source(store, job.job_id, other, source=job.source, provider=job.provider)
    # Corrupting the history pointer must not bypass the persisted manifest binding.
    store.record_usage(job, {"graphics_source": str(other)})
    with pytest.raises(DocumentError):
        post.validate_graphics_source(store, job.job_id, other, source=job.source, provider=job.provider)


@pytest.mark.parametrize("setting,value", [("target", "en"), ("trial", True), ("pages", "1"),
    ("skip_references", True), ("bilingual", False), ("bilingual_layout", "alternating"),
    ("reference_boundary", {"page": 0}), ("request_timeout", 60)])
def test_child_configuration_cannot_override_body_evidence(setup, setting, value):
    make, store = setup
    job, folder = make()
    followup = child(job, folder, **{setting: value})
    with pytest.raises(DocumentError):
        post.validate_graphics_source(store, job.job_id, folder, source=job.source,
                                      provider=followup.provider, job=followup)


@pytest.mark.parametrize("change", ["manifest", "db_target", "db_trial", "db_reference", "missing_binding", "provider_options"])
def test_manifest_and_db_configuration_are_both_required(setup, change):
    make, store = setup
    job, folder = make()
    provider = dict(job.provider)
    if change == "manifest":
        rewrite(folder, lambda data: data.update(target="en"))
    elif change == "provider_options":
        provider["reasoning_effort"] = "high"
    else:
        info = json.loads(store.db.execute("SELECT details FROM jobs WHERE id=?", (job.job_id,)).fetchone()[0])
        if change == "db_target":
            info["configuration"]["target"] = "en"
        elif change == "db_trial":
            info["trial"] = True
        elif change == "db_reference":
            info["reference_boundary"] = {"whole_pages": [0]}
        else:
            info.pop("graphics_manifest_sha256")
        store.db.execute("UPDATE jobs SET details=? WHERE id=?", (json.dumps(info), job.job_id))
        store.db.commit()
    with pytest.raises(DocumentError):
        post.validate_graphics_source(store, job.job_id, folder, source=job.source, provider=provider)


def test_repeated_children_follow_a_validated_ancestor_chain(setup, tmp_path):
    make, store = setup
    root, folder = make(trial=True)
    parent = root
    for n in range(3):
        next_folder = tmp_path / f"child-{n}" / post.FOLDER
        shutil.copytree(folder, next_folder)
        followup = replace(child(parent, folder), job_id=f"child-{n}")
        store.status(followup, "partial" if n == 1 else "completed", dict(
            graphics_source=str(next_folder), reference_boundary=root.reference_boundary,
            graphics_manifest_sha256=post.digest(next_folder / "manifest.json")))
        data = post.validate_graphics_source(store, followup.job_id, next_folder,
                                            source=root.source, provider=root.provider)
        assert data["parent_job_id"] == root.job_id
        parent, folder = followup, next_folder
    # Source artifact copies suffice, but a missing/cyclic ancestor is not inferred.
    info = json.loads(store.db.execute("SELECT details FROM jobs WHERE id='child-0'").fetchone()[0])
    info["configuration"]["parent_job_id"] = parent.job_id
    store.db.execute("UPDATE jobs SET details=? WHERE id='child-0'", (json.dumps(info),))
    store.db.commit()
    with pytest.raises(DocumentError):
        post.validate_graphics_source(store, parent.job_id, folder, source=root.source, provider=root.provider)


def test_worker_rejects_before_translator_construction(setup, monkeypatch):
    from linguafloat import document_worker as worker
    make, store = setup
    root, _ = make("one")
    _, other = make("two")
    job = child(root, other)
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid artifact reached translator construction")
    monkeypatch.setattr(worker, "DocumentTranslator", forbidden)
    with pytest.raises(DocumentError):
        worker.execute(job, "not-a-real-key", store, JobControl(), lambda _: None)


def test_ui_validation_needs_no_pdf_runtime(setup, monkeypatch):
    import builtins
    make, store = setup
    job, folder = make(trial=True)
    original_import = builtins.__import__
    def without_runtime(name, *args, **kwargs):
        if name.startswith(('pymupdf', 'fitz', 'babeldoc', 'linguafloat.document_pdf')):
            raise AssertionError('GUI imported document runtime: ' + name)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', without_runtime)
    data = post.validate_graphics_source(store, job.job_id, folder, source=job.source,
                                        provider=job.provider, verify_pdf=False)
    assert data['trial'] is True
    rewrite(folder, lambda data: data['pages'][0].update(source=1))
    with pytest.raises(DocumentError):
        post.validate_graphics_source(store, job.job_id, folder, source=job.source,
                                      provider=job.provider, verify_pdf=False)


def test_worker_publication_pins_final_manifest_and_allows_repeated_children(setup, monkeypatch, tmp_path):
    from pathlib import Path
    from linguafloat import document_worker as worker
    make, store = setup
    original, _ = make()
    root = replace(original, job_id='published-body')
    def translator(job):
        return NS(job=job, control=JobControl(), calls=0, hits=0,
                  graphic_context=GraphicContext(), source_context=SourceContext(),
                  set_source_context=lambda blocks: list(blocks))
    def body_processor(job, t, output, emit):
        with fitz.open(job.source) as pdf:
            post.save_body_artifact(output, job, pdf,
                [dict(source=n, start=n, count=1, selected=True) for n in range(4)], t)
        outputs = [output/'mono.pdf', output/'dual.pdf']
        for path in outputs:
            shutil.copy2(job.source, path)
        t.local_issues = [dict(page=1, reason='synthetic retained region', box=[10, 10, 20, 20])]
        return list(map(str, outputs)), []
    monkeypatch.setattr('linguafloat.document_pdf.translate_layout_pdf', body_processor)
    monkeypatch.setattr('linguafloat.document_graphics.apply_graphics', lambda *a, **kw: [])
    parent = root
    folders = []
    hashes = []
    for n in range(3):
        t = translator(parent)
        events = []
        worker.execute(parent, '', store, t.control, events.append, t)
        result = next(e for e in events if e['type'] == 'finished')
        folder = Path(result['graphics_source'])
        folders.append(folder)
        hashes.append(post.digest(folder/'manifest.json'))
        assert result['graphics_manifest_sha256'] == hashes[-1]
        assert result['requests'] == 0
        data = post.validate_graphics_source(store, parent.job_id, folder,
                                            source=root.source, provider=root.provider)
        assert data['body_issues'][0]['reason'] == 'synthetic retained region'
        parent = replace(child(parent, folder), job_id=f'published-child-{n}')
        if n == 0:
            def forbidden_body(*a, **kw):
                pytest.fail('Follow-up re-entered body translation')
            monkeypatch.setattr('linguafloat.document_pdf.translate_layout_pdf', forbidden_body)
    assert len(set(folders)) == 3 and len(set(hashes)) == 1
    assert all(post.digest(folder/'manifest.json') == hashes[0] for folder in folders)


@pytest.mark.parametrize('when', ['before', 'during'])
def test_cancelled_followup_never_publishes_or_changes_parent(setup, monkeypatch, tmp_path, when):
    from linguafloat import document_worker as worker
    from linguafloat.document_core import Cancelled
    make, store = setup
    root, folder = make()
    job = child(root, folder)
    control = JobControl()
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    def apply(*a, **kw):
        control.cancelled.set()
        control.checkpoint()
    monkeypatch.setattr('linguafloat.document_graphics.apply_graphics', apply)
    t = NS(job=job, control=control, calls=0, hits=0, graphic_context=GraphicContext(),
           set_source_context=lambda blocks: list(blocks))
    if when == 'before':
        control.cancelled.set()
    events = []
    with pytest.raises(Cancelled):
        worker.execute(job, '', store, control, events.append, t)
    assert not any(e['type'] == 'finished' for e in events)
    assert not list(tmp_path.glob('input.图表补译*'))
    assert not list(tmp_path.glob('.linguafloat-*'))
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


@pytest.mark.parametrize('status', ['running', 'failed', 'cancelled', 'missing'])
def test_incomplete_or_missing_parent_is_rejected(setup, status):
    make, store = setup
    root, folder = make()
    if status == 'missing':
        store.db.execute('DELETE FROM jobs WHERE id=?', (root.job_id,))
        store.db.commit()
    else:
        store.status(root, status)
    with pytest.raises(DocumentError):
        post.validate_graphics_source(store, root.job_id, folder,
                                      source=root.source, provider=root.provider)


@pytest.mark.parametrize('variant', ['valid_trial', 'cross_task', 'swapped_pages', 'configuration'])
def test_start_graphics_preflight_and_task_association_without_qt_runtime(setup, monkeypatch, variant):
    # Execute the actual controller method with widget doubles: this runtime has
    # no Qt, and the GUI has no PDF runtime. Native dialog rendering is separate.
    import ast
    import builtins
    import copy
    import uuid
    from dataclasses import asdict, make_dataclass
    from pathlib import Path
    make, store = setup
    root, folder = make(trial=True)
    store.record_usage(root, {'outputs': [str(folder / 'body.pdf')], 'started_at': 1000})
    if variant == 'cross_task':
        _, other = make('other', trial=True)
        store.record_usage(root, {'graphics_source': str(other)})
    elif variant == 'swapped_pages':
        rewrite(folder, lambda data: data['pages'][0].update(source=1))
    values = dict(root.provider, document_timeout=180, env_key='')
    if variant == 'configuration':
        values['model'] = 'different-model'
    profile = make_dataclass('Profile', [(k, object) for k in values])(**values)
    class Widget:
        DialogCode = NS(Accepted=1)
        StandardButton = NS(Ok=1, Cancel=2, Yes=3, No=4)
        ButtonRole = NS(ActionRole=1)
        def __init__(self, *a):
            self.accepted = self.rejected = NS(connect=lambda callback: None)
            self.clicked = self.toggled = NS(connect=lambda callback: None)
            self.checked = False
        def setChecked(self, value): self.checked = value
        def isChecked(self): return self.checked
        def button(self, value): return self
        def addButton(self, *a): return self
        def exec(self): return 1
        def __getattr__(self, name): return lambda *a: None
    reports, credentials, launched = [], [], []
    rows = [NS(data=lambda col, role: str(Path(root.source).with_name('other.pdf')) if role == 32 else 'other-row'),
            NS(data=lambda col, role: root.source if role == 32 else 'source-row')]
    page = NS(busy=False, profiles={profile.id: profile}, graphics_figures=True, graphics_tables=True,
              report=NS(setPlainText=reports.append), preferences=lambda: {},
              preferences_changed=NS(emit=lambda _: None), add_files=lambda files: None,
              files=NS(topLevelItemCount=lambda: len(rows), topLevelItem=lambda i: rows[i]))
    page.set_busy = lambda value: setattr(page, 'busy', value)
    def get_key(*args):
        credentials.append(args)
        return 'offline-key'
    controller = NS(page=page, database=store.path, credentials=NS(get_api_key=get_key), queue=[],
                    next_job=lambda: launched.append(True))
    path = Path(post.__file__).with_name('document_ui.py')
    module = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'DocumentController')
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in {'start_graphics', 'graphics_record'}]
    from datetime import datetime
    namespace = dict(JobStore=JobStore, DocumentError=DocumentError, DocumentJob=DocumentJob,
        copy=copy, json=json, asdict=asdict, Path=Path, uuid=uuid, datetime=datetime,
        Qt=NS(ItemDataRole=NS(UserRole=32), TextFormat=NS(PlainText=0)),
        worker_command=lambda: (Path(root.source), Path(root.source)))
    namespace.update({name: Widget for name in ('QDialog', 'QVBoxLayout', 'QLabel', 'QCheckBox', 'QDialogButtonBox')})
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), 'exec'), namespace)
    controller.graphics_record = lambda *a, **k: namespace['graphics_record'](controller, *a, **k)
    original_import = builtins.__import__
    def without_runtime(name, *args, **kwargs):
        if name.startswith(('pymupdf', 'fitz', 'babeldoc', 'linguafloat.document_pdf')):
            raise AssertionError('GUI imported PDF runtime: ' + name)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', without_runtime)
    namespace['start_graphics'](controller, root.job_id)
    if variant == 'valid_trial':
        assert not reports and len(controller.queue) == 1 and launched
        job, key, row_id = controller.queue[0]
        assert row_id == 'source-row' and job.trial is True
        assert job.parent_job_id == root.job_id and job.job_id != root.job_id
        assert job.graphics_source == str(folder) and key == 'offline-key'
    else:
        assert reports and not controller.queue and not credentials and not launched
