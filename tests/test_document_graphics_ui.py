import json
import pytest
from test_credentials_ui import application,controller
from linguafloat.config import ConfigStore
from linguafloat.document_ui import DocumentPage,DocumentController


@pytest.mark.parametrize("tables,figures,expected",[(False,False,False),(True,False,True),(False,True,True),(True,True,True)])
def test_legacy_graphics_and_timeout_migration(tmp_path,tables,figures,expected):
    path=tmp_path/"config.json"
    raw=dict(document_translate_tables=tables,document_translate_figures=figures,document_timeout=275,
        document_prices={"old":{"input":2,"output":8,"multiplier":.1}},
        providers=[dict(id="one",name="First",base_url="https://example.com/v1",model="model"),
                   dict(id="two",name="Second",base_url="https://example.com/v1",model="model",document_timeout=120)])
    path.write_text(json.dumps(raw),encoding="utf-8")
    store=ConfigStore(path);settings=store.load()
    assert settings.document_translate_graphics is expected
    assert [p.document_timeout for p in settings.providers]==[275,120]
    assert settings.document_pdf_mode=="layout"
    settings.document_translate_graphics=False
    store.save(settings)
    saved=store.load()
    assert not saved.document_translate_graphics
    assert saved.document_prices["old"]["input"]==2


def test_document_preferences_restart_without_task_options(controller,tmp_path):
    c,_,store=controller
    page=c.window.documents
    page.source_language.setCurrentIndex(page.source_language.findData("ja"))
    page.target.setCurrentIndex(page.target.findData("en"))
    page.pdf_mode.setCurrentIndex(page.pdf_mode.findData("reading"))
    page.translate_graphics.setChecked(True)
    page.mono.setChecked(False)
    page.alternating.setChecked(True)
    page.pages.setText("2-3")
    page.relayout_only.setChecked(True)
    restored=DocumentPage()
    controller2=DocumentController(restored,store.load(),c.credentials,tmp_path/"new.sqlite")
    assert restored.preferences()==page.preferences()
    assert not restored.pages.text() and not restored.relayout_only.isChecked()
    assert not restored.busy
    restored.close()


def test_merged_toggle_task_snapshot_and_current_provider(controller,tmp_path,monkeypatch):
    c,_,store=controller;page=c.window.documents
    page.translate_graphics.setChecked(True)
    source=tmp_path/"paper.pdf";source.touch();page.add_files([str(source)])
    profile=c.settings.active_profile();profile.document_timeout=250
    monkeypatch.setattr(c.documents,"next_job",lambda:None)
    c.documents.start()
    assert len(c.documents.queue)==1
    job=c.documents.queue[0][0]
    assert job.translate_tables and job.translate_figures and job.request_timeout==250
    assert job.provider["model"]=="test-model" and not job.pricing
    profile.model="changed-after-start";profile.document_timeout=300
    c.window.set_active_profile("second")
    assert job.provider["model"]=="test-model" and job.request_timeout==250
    assert not page.translate_graphics.isEnabled()
    c.documents.queue.clear();page.set_busy(False)
    page.translate_graphics.setChecked(False)
    c.documents.start()
    job=c.documents.queue[0][0]
    assert job.provider["model"]=="second-model" and not job.translate_tables and not job.translate_figures
    c.documents.queue.clear();page.set_busy(False)


def test_new_task_clears_stale_error_even_when_preflight_fails(controller):
    c,_,_=controller;page=c.window.documents
    page.report.setPlainText("旧任务 HTTP 400")
    c.documents.start()
    text=page.report.toPlainText()
    assert "旧任务" not in text and "请先添加文件" in text


def test_graphics_option_does_not_block_office_in_mixed_batch(controller,tmp_path,monkeypatch):
    c,_,_=controller;page=c.window.documents
    page.translate_graphics.setChecked(True)
    for name in ("paper.pdf","document.docx","slides.pptx"):
        source=tmp_path/name;source.touch();page.add_files([str(source)])
    monkeypatch.setattr(c.documents,"next_job",lambda:None)
    c.documents.start()
    jobs=[job for job,_,_ in c.documents.queue]
    assert len(jobs)==3
    assert [(job.translate_tables,job.translate_figures) for job in jobs]==[(True,True),(False,False),(False,False)]
    c.documents.queue.clear();page.set_busy(False)


def test_new_file_does_not_reuse_previous_page_range(controller,tmp_path):
    c,_,_=controller;page=c.window.documents
    page._has_started_task=True;page.pages.setText("3-4");page.relayout_only.setChecked(True)
    source=tmp_path/"new.pdf";source.touch();page.add_files([str(source)])
    assert not page.pages.text() and not page.relayout_only.isChecked()
