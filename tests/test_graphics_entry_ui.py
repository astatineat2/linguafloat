from dataclasses import asdict

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QToolButton, QDialog, QDialogButtonBox, QTreeWidget
from test_credentials_ui import application, controller
from linguafloat.config import AppSettings
from linguafloat.help_ui import HelpDialog
from linguafloat.document_core import DocumentJob, JobStore


def test_help_has_no_duplicate_header_close(application):
    dialog = HelpDialog(AppSettings())
    dialog.show()
    application.processEvents()
    try:
        assert not [b for b in dialog.findChildren(QToolButton) if b.accessibleName() == '关闭弹窗']
        assert not dialog.status.text()
        assert not dialog.status.isVisible()
        dialog.status.setText('正在检查更新')
        assert dialog.status.isVisible()
        dialog.status.setText('')
        assert not dialog.status.isVisible()
        assert any(b.text() == '关闭' for b in dialog.findChildren(__import__('PySide6.QtWidgets', fromlist=['QPushButton']).QPushButton))
    finally:
        dialog.close()


def test_graphics_disabled_without_eligible_body_and_lives_in_footer(controller):
    c, _, _ = controller
    page = c.window.documents
    assert not page.graphics_button.isEnabled()
    assert page.graphics_button.text() == '图表翻译'
    assert page.graphics_button.parentWidget() is page.start_button.parentWidget()
    page.set_busy(True)
    assert not page.graphics_button.isEnabled()
    page.set_busy(False)
    assert not page.graphics_button.isEnabled()


@pytest.fixture
def history(controller, tmp_path, monkeypatch):
    c, _, _ = controller
    source = tmp_path / 'same.pdf'
    source.touch()
    output = tmp_path / 'body.pdf'
    output.touch()
    artifact = tmp_path / '.graphics-source'
    artifact.mkdir()
    profile = c.settings.active_profile()
    from linguafloat import document_postprocess as post
    monkeypatch.setattr(post, 'validate_graphics_source', lambda *a, **k: dict(
        target='zh-Hans', source_language='en', selected_pages='2,6', trial=False,
        chinese_font='simsun', latin_font='times', mono=True, bilingual=True,
        bilingual_layout='side_by_side', skip_references=True, reference_boundary={}))
    store = JobStore(c.documents.database)
    for index, (identity, status, mode) in enumerate([
        ('complete', 'completed', 'body'), ('partial', 'partial', 'body'),
        ('missing', 'completed', 'body'), ('child', 'completed', 'graphics'),
        ('failed', 'failed', 'body')]):
        job = DocumentJob(str(source), str(tmp_path), asdict(profile), job_id=identity,
                          workflow=mode, pdf_mode='layout')
        store.status(job, status, dict(outputs=[] if identity == 'missing' else [str(output)],
            graphics_source=str(artifact), started_at=1000 + index))
        store.db.execute('UPDATE jobs SET updated=? WHERE id=?', (1000 + index, identity))
    store.db.commit()
    store.close()
    c.documents.refresh_history()
    return c.documents


def test_first_eligible_displayed_history_and_busy_refresh(history):
    page = history.page
    seen = []
    page.graphics_requested.disconnect()
    page.graphics_requested.connect(seen.append)
    assert page.graphics_button.isEnabled()
    page.request_graphics()
    assert seen == ['partial']
    assert page.history.currentItem().data(0, Qt.ItemDataRole.UserRole) == 'partial'
    page.set_busy(True)
    page.request_graphics()
    assert seen == ['partial'] and not page.graphics_button.isEnabled()
    page.set_busy(False)
    assert page.graphics_button.isEnabled()


@pytest.mark.parametrize('choice', ['no', 'close', 'yes'])
def test_confirmation_is_required_before_queue(history, application, monkeypatch, choice):
    launched = []
    monkeypatch.setattr(history, 'next_job', lambda: launched.append(True))
    def respond():
        dialog = application.activeModalWidget()
        assert isinstance(dialog, QDialog)
        if choice == 'close':
            dialog.close()
        else:
            buttons = dialog.findChild(QDialogButtonBox)
            buttons.button(QDialogButtonBox.StandardButton.Yes if choice == 'yes'
                           else QDialogButtonBox.StandardButton.No).click()
    QTimer.singleShot(0, respond)
    history.start_graphics('partial')
    assert bool(history.queue) is (choice == 'yes'), history.page.report.toPlainText()
    assert bool(launched) is (choice == 'yes')
    if choice == 'yes':
        job = history.queue[0][0]
        assert job.parent_job_id == 'partial' and job.workflow == 'graphics'
        assert job.pages == '2,6' and job.translate_figures and job.translate_tables
        history.queue.clear()
        history.page.set_busy(False)


def test_custom_picker_uses_eligible_history_and_updates_selection(history, application):
    def choose():
        dialog = application.activeModalWidget()
        tree = dialog.findChild(QTreeWidget)
        assert tree.topLevelItemCount() == 2
        item = next(tree.topLevelItem(i) for i in range(2)
                    if tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == 'complete')
        tree.setCurrentItem(item)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
    QTimer.singleShot(0, choose)
    assert history.choose_graphics_history() == 'complete'
    assert history.page.history.currentItem().data(0, Qt.ItemDataRole.UserRole) == 'complete'
