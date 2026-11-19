import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from linguafloat.document_core import DocumentError, DocumentJob, JobControl, JobStore
from linguafloat.document_failures import diagnostic
from linguafloat.document_graphics import DocumentEnhancer
from linguafloat.document_navigation import NavigationError
from linguafloat.document_postprocess import FOLDER
from linguafloat.document_worker import execute


LAYOUT_NOTE = "\u6392\u7248\u8bf4\u660e.txt"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Worker metadata tests must stay offline")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def publish_job(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((40, 60), "RETAINED SOURCE CONTENT")
        document.save(source)
    job = DocumentJob(str(source), str(tmp_path / "outputs"),
        {"base_url": "https://offline.invalid/v1", "model": "offline", "api_mode": "responses"},
        job_id="issue-metadata", pdf_mode="layout", workflow="body", mono=True, bilingual=False)
    translator = SimpleNamespace(calls=0, hits=5, local_issues=[])
    store = JobStore(tmp_path / "isolated.sqlite3")
    events = []

    def run(report, warnings):
        def process(job, translator, work, emit):
            output = work / "retained.pdf"
            output.write_bytes(source.read_bytes())
            (work / "layout-report.json").write_text(json.dumps(report), encoding="utf-8")
            artifact = work / FOLDER
            artifact.mkdir()
            (artifact / "manifest.json").write_text("{}", encoding="utf-8")
            return [output], list(warnings)

        monkeypatch.setattr("linguafloat.document_pdf.translate_layout_pdf", process)
        execute(job, "", store, JobControl(), events.append, translator)
        return events[-1]

    try:
        yield SimpleNamespace(run=run, job=job, translator=translator, store=store, events=events,
                              source=source, original=source.read_bytes())
    finally:
        store.close()


def test_worker_publishes_multibox_navigation_issue_without_losing_metadata(publish_job):
    boxes = [[337.4, 197.7, 385.9, 206.7], [51.5, 229.1, 100.1, 244.1],
             [159.6, 233.4, 215.2, 241.4], [291.4, 233.4, 384.0, 241.4],
             [446.8, 233.4, 529.8, 241.4]]
    navigation = dict(diagnostic(NavigationError("private upstream text"), "navigation"),
        id="P1-F1", kind="navigation", page=1, group="nav-0-0", status="untranslated",
        coordinates="top-left", boxes=boxes, source="PRIVATE SOURCE", translation="PRIVATE TRANSLATION")
    enhancer = DocumentEnhancer.__new__(DocumentEnhancer)
    enhancer.records, enhancer.graphics = [navigation], []
    report = {"warnings": ["Retained navigation needs review"]}
    enhancer.report(report)
    original_report = json.loads(json.dumps(report))
    existing_issue = dict(id="existing", page=2, reason="Existing local failure", stage="validation")
    publish_job.translator.local_issues.append(existing_issue)
    warnings = ["Source retained after navigation safety check"]

    result = publish_job.run(report, warnings)

    assert result["type"] == "finished"
    assert result["warnings"] == warnings
    assert result["untranslated_regions"] == len(result["issues"]) == 2
    assert result["requests"] == 0 and result["cache_hits"] == 5
    folder = Path(result["outputs"][0]).parent
    assert Path(result["outputs"][0]).read_bytes() == publish_job.original
    assert publish_job.source.read_bytes() == publish_job.original
    with fitz.open(result["outputs"][0]) as output:
        assert "RETAINED SOURCE CONTENT" in output[0].get_text()
    issue = result["local_failures"][1]
    assert issue["boxes"] == boxes and "box" not in issue
    for key in ("id", "page", "group", "kind", "coordinates", "status", "stage", "reason",
                "trace", "error_type", "fallback", "retry_result"):
        assert issue[key] == navigation[key]
    assert result["local_failures"][0]["reason"] == existing_issue["reason"]
    assert json.loads(json.dumps(report)) == original_report
    exported = json.loads((folder / "layout-report.json").read_text(encoding="utf-8"))
    assert exported["warnings"] == report["warnings"]
    assert exported["untranslated_regions"]["0"][0]["boxes"] == boxes
    log = next(folder.glob("*.jsonl")).read_text(encoding="utf-8")
    assert [json.loads(line) for line in log.splitlines()] == [
        dict(job_id=publish_job.job.job_id,
             source_id=hashlib.sha256(publish_job.job.source.encode()).hexdigest(), **entry)
        for entry in result["local_failures"]]
    manifest = json.loads((folder / FOLDER / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["body_issues"] == result["local_failures"]
    status, details = publish_job.store.db.execute("SELECT status, details FROM jobs").fetchone()
    assert status == "completed"
    assert json.loads(details)["local_failures"] == result["local_failures"]
    explanation = (folder / LAYOUT_NOTE).read_text(encoding="utf-8-sig")
    for box in boxes:
        assert ", ".join(f"{value:.1f}" for value in box) in explanation
    assert navigation["reason"] in explanation
    for value in (exported, result["local_failures"], manifest, log):
        assert "PRIVATE" not in str(value) and "private upstream text" not in str(value)


@pytest.mark.parametrize("coordinates", ["top-left", "bottom-left"])
def test_worker_keeps_single_box_issue_and_coordinate_origin(publish_job, coordinates):
    region = dict(id="P1-U1", box=[10, 20, 30, 40], reason="Retained body paragraph",
                  coordinates=coordinates, status="untranslated", marked=True)
    result = publish_job.run({"untranslated_regions": {"0": [region]}}, ["Body warning"])
    assert result["untranslated_regions"] == 1
    assert result["warnings"] == ["Body warning"]
    assert result["local_failures"][0].items() >= dict(region, page=1).items()
    folder = Path(result["outputs"][0]).parent
    explanation = (folder / LAYOUT_NOTE).read_text(encoding="utf-8-sig")
    assert "10.0, 20.0, 30.0, 40.0" in explanation
    assert ("\u5de6\u4e0a\u89d2" if coordinates == "top-left" else "\u5de6\u4e0b\u89d2") in explanation


@pytest.mark.parametrize("geometry", [{}, {"boxes": []}, {"boxes": [[1, 2, 3]]},
    {"box": [1, 2, float("nan"), 4]}, {"boxes": [[1, 2, 3, 4], [1, 2, "bad", 4]]}])
def test_worker_refuses_unlocatable_retained_issues(publish_job, geometry):
    report = {"untranslated_regions": {"0": [dict(geometry, reason="Must retain this issue")]}}
    with pytest.raises(DocumentError, match="\u672a\u7ffb\u8bd1\u533a\u57df\u5750\u6807\u65e0\u6548"):
        publish_job.run(report, ["Retained content needs review"])
    assert not list(Path(publish_job.job.output_dir).iterdir())
    assert not any(event["type"] == "finished" for event in publish_job.events)
    assert publish_job.source.read_bytes() == publish_job.original
