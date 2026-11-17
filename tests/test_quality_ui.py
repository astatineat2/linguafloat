import json
from test_credentials_ui import application, controller
from linguafloat.config import ConfigStore


def test_existing_profile_requires_font_ack_without_modifying_old_data(tmp_path):
    path = tmp_path/'settings.json'
    path.write_text('{"document_translate_graphics": true}', encoding='utf-8')
    original = path.read_bytes()
    settings = ConfigStore(path).load()
    assert not settings.document_font_confirmed
    assert settings.document_chinese_font == 'simsun'
    assert settings.document_latin_font == 'times'
    assert settings.document_translate_graphics
    assert path.read_bytes() == original
    assert ConfigStore(tmp_path/'new.json').load().document_font_confirmed


def test_new_task_body_only_and_font_snapshot_after_explicit_ack(controller, tmp_path, monkeypatch):
    c, _, _ = controller
    page = c.window.documents
    source = tmp_path/'中文 路径.pdf'; source.touch()
    page.add_files([str(source)])
    monkeypatch.setattr('linguafloat.document_ui.worker_command', lambda: (source, source))
    monkeypatch.setattr(c.documents, 'next_job', lambda: None)
    page.font_ack.setChecked(False)
    c.documents.start()
    assert not c.documents.queue
    assert '确认译文字体' in page.report.toPlainText()
    page.font_ack.setChecked(True)
    c.documents.start()
    job, _, _ = c.documents.queue[0]
    assert job.workflow == 'body' and not job.translate_figures and job.translate_tables
    assert job.chinese_font == 'simsun' and job.latin_font == 'times'
    page.chinese_font.setCurrentIndex(1)
    assert job.chinese_font == 'simsun'
    c.documents.queue.clear(); page.set_busy(False)


def test_graphics_missing_material_never_queues_body(controller):
    c, _, _ = controller
    c.documents.start_graphics('no-such-task')
    assert not c.documents.queue
    assert '已完成或部分完成' in c.window.documents.report.toPlainText()
