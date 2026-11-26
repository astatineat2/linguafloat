import json
import os
import sqlite3
from pathlib import Path
from shutil import copyfile
import time

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QInputDialog, QLineEdit, QTreeWidget
from linguafloat.workbench import AppDialog
from test_document_export import fixture as manuscript_fixture
from test_credentials_ui import application, controller


def wait_for_export(page):
    stop = time.monotonic() + 45
    while page.export_process is not None and time.monotonic() < stop:
        QApplication.processEvents()
        time.sleep(.01)
    assert page.export_status.text().startswith('已导出：'), page.export_status.text()


def test_windows_liu_pdf_only_export_and_review(controller, application, monkeypatch):
    from docx import Document
    from zipfile import ZipFile
    from linguafloat.document_export import digest
    from linguafloat.document_review_ui import ReviewDialog

    if not (old := os.environ.get('LINGUAFLOAT_LIU_PDF')) or os.environ.get('QT_QPA_PLATFORM') != 'windows':
        pytest.skip('Local Windows Liu history acceptance')
    old = Path(old)
    manifest = old.parent / '.graphics-source' / 'manifest.json'
    data = json.loads(manifest.read_bytes())
    identity, original = data['parent_job_id'], Path(data['source'])
    before = digest(original), digest(old)
    evidence = Path(os.environ['LINGUAFLOAT_LIU_EVIDENCE']).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    window = controller[0].window
    window.resize(1052, 872)
    window.show()
    window.workbench.navigation.setCurrentRow(2)
    page = window.documents
    info = {'outputs': [str(old)], 'workflow': 'body', 'graphics_manifest_sha256': digest(manifest)}
    page.set_history([(identity, str(original), 'completed', 0, json.dumps(info))])

    def capture(name, widget=window):
        QTest.qWait(150)
        image = widget.screen().grabWindow(int(widget.winId()))
        assert not image.isNull() and image.save(str(evidence / f'{name}.png'))

    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(original), '*.pdf'))
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: (args[3][0], True))
    page.export_pdf_picker.click()
    assert page._export_pdf == original and not page._export_id
    for prefix in ('external', 'history'):
        if prefix == 'history':
            monkeypatch.setattr(AppDialog, 'exec', lambda dialog: AppDialog.DialogCode.Accepted)
            page.export_picker.click()
            assert page._export_id == identity and page._export_pdf is None
        assert all(b.isEnabled() for b in page.export_buttons.values())
        for kind, suffix in [('word', '.docx'), ('markdown', '.md')]:
            dest = evidence / (prefix + suffix)
            monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(dest), suffix))
            page.export_buttons[kind].click()
            wait_for_export(page)
        text = '\n'.join(p.text for p in Document(evidence / (prefix + '.docx')).paragraphs)
        assert '405603' in text and 'Original 1 |' not in text
        with ZipFile(evidence / (prefix + '.zip')) as bundle:
            markdown = bundle.read('translation.md').decode('utf-8')
            assert '405603' in markdown and 'Original 1 |' not in markdown and bundle.testzip() is None
        if prefix == 'external':
            assert 0 < text.index('[1] Adiga') < text.index('[21] Vetrone')
        else:
            assert '本研究' in text and 'View the article online for updates and enhancements.' not in text
        capture(prefix)

    review = page.review
    assert review.ai.text() == 'AI 校对' and not review.ai.isChecked() and not hasattr(review, 'cancel')
    review.open.click()
    until = time.monotonic() + 30
    while review.process and time.monotonic() < until:
        application.processEvents()
        time.sleep(.02)
    report = review.reports[identity]
    assert report['ai']['state'] == 'disabled'
    findings = report['local']['issues']
    assert any(i['kind'] == 'layout-overlap' and i['source_page'] == 9 for i in findings)
    assert '版面 5 项' in review.status.text()
    capture('workbench')
    dialog = ReviewDialog(review, original.name, old, report['local'], report['ai'])
    dialog.show()
    rows = [dialog.issues.topLevelItem(i) for i in range(dialog.issues.topLevelItemCount())]
    row = next(i for i in rows if i.text(0) == '9' and '重叠' in i.text(1))
    dialog.issues.setCurrentItem(row)
    assert dialog.preview.current_page == 8 and not dialog.preview.image.isNull()
    capture('review-page9', dialog)
    dialog.close()
    assert (digest(original), digest(old)) == before


def test_export_on_default_tab_picks_history_and_writes_markdown(controller, tmp_path, monkeypatch):
    page = controller[0].window.documents
    _, info = manuscript_fixture(tmp_path, False)
    page.set_history([('job', 'source.pdf', 'completed', 0, json.dumps(info))])
    assert page.file_tabs.currentIndex() == 0
    assert not page.export_picker.isHidden()
    assert not page.export_buttons['markdown'].isEnabled()

    def select(dialog):
        tree = dialog.findChild(QTreeWidget)
        assert tree.topLevelItemCount() == 1 and tree.currentItem().text(0) == 'source.pdf'
        assert tree.currentItem().text(3) == 'PDF / Word / Markdown'
        return AppDialog.DialogCode.Accepted

    monkeypatch.setattr(AppDialog, 'exec', select)
    page.export_picker.click()
    assert page.file_tabs.currentIndex() == 0
    assert 'source.pdf' in page.export_source.toolTip()
    assert page.history.currentItem() is None
    assert page.report.toPlainText() == ''
    assert all(button.isEnabled() for button in page.export_buttons.values())
    target = tmp_path / 'notes.md'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), 'MD'))
    page.export_buttons['markdown'].click()
    wait_for_export(page)
    data = target.read_bytes()
    assert '30 g/day' in data.decode('utf-8')
    page.export_buttons['markdown'].click()
    assert '已存在' in page.export_status.text()
    assert page.export_process is None and target.read_bytes() == data


def test_old_history_enables_pdf_recovery_and_keeps_pdf_copy(controller, tmp_path, monkeypatch):
    import pymupdf
    page = controller[0].window.documents
    source = tmp_path / 'paper.pdf'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((60, 80), 'Recover an editable paragraph from this PDF.')
        doc.save(source)
    target = tmp_path / 'new.pdf'
    page.file_tabs.setCurrentIndex(1)
    page.set_history([('old', 'source.pdf', 'completed', 0, json.dumps({'outputs': [str(source)]}))])
    page.select_export_result('old')
    assert all(button.isEnabled() for button in page.export_buttons.values())
    assert '从 PDF 恢复' in page.export_status.text()
    assert page.export_buttons['pdf'].isEnabled()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), 'PDF'))
    page.export_buttons['pdf'].click()
    wait_for_export(page)
    assert source.read_bytes() == target.read_bytes()
    target = tmp_path / 'recovered.md'
    page.export_buttons['markdown'].click()
    wait_for_export(page)
    assert 'Recover an editable paragraph' in target.read_text('utf8')


def test_stale_result_offers_verified_location(controller, tmp_path, monkeypatch):
    page = controller[0].window.documents
    source = tmp_path / 'original'
    source.mkdir()
    original, info = manuscript_fixture(source, False)
    moved = tmp_path / 'elsewhere'
    source.rename(moved)
    unrelated = tmp_path / original.name
    unrelated.write_bytes(original.name.encode('utf-8'))
    page.set_history([('job', 'renamed-folder.pdf', 'completed', 0, json.dumps(info))])
    page.select_export_result('job')
    assert '找不到译文 PDF' in page.export_status.text()
    assert not page.export_locate.isHidden()
    assert not any(button.isEnabled() for button in page.export_buttons.values())
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(unrelated), 'PDF'))
    page.export_locate.click()
    assert '无法核实' in page.export_status.text()
    assert not any(button.isEnabled() for button in page.export_buttons.values())
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(moved / original.name), 'PDF'))
    page.export_locate.click()
    assert page.export_locate.isHidden()
    assert all(button.isEnabled() for button in page.export_buttons.values())
    assert page._export_records['job'][2]['outputs'] == [str(original)]


def test_windows_export_workbench(controller, application, tmp_path):
    if application.platformName() != 'windows':
        pytest.skip('Requires a visible Windows desktop')
    window = controller[0].window
    page = window.documents
    mono, info = manuscript_fixture(tmp_path, False)
    folder = tmp_path / 'before-move'
    folder.mkdir()
    original, moved_info = manuscript_fixture(folder, False)
    # The fixture is keyed to 'job'; use the same identity only after the normal export case.
    moved = tmp_path / 'new-location'
    folder.rename(moved)
    records = [
        ('job', 'editable.pdf', 'completed', 0, json.dumps(info)),
        ('old', 'archived.pdf', 'completed', 0, json.dumps({'outputs': [str(mono)]})),
    ]
    actual = os.environ.get('LINGUAFLOAT_QA_HISTORY')
    if actual:
        with sqlite3.connect(Path(actual).as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT id,source,status,updated,details FROM jobs WHERE id=?',
                             ('f0767a215fc94db084eafe4e06b3eb13',)).fetchone()
        assert row is not None
        records[1] = row
    page.set_history(records)
    evidence = Path(os.environ.get('LINGUAFLOAT_EXPORT_QA', tmp_path))
    evidence.mkdir(parents=True, exist_ok=True)
    window.resize(1052, 872)
    window.show()
    window.workbench.navigation.setCurrentRow(2)
    QTest.qWait(200)
    area = window.screen().availableGeometry()
    window.move(area.left() + 10, area.top() + 32)

    def capture(name, widget=window):
        QTest.qWait(100)
        image = widget.screen().grabWindow(int(widget.winId()))
        assert not image.isNull() and image.save(str(evidence / f'{name}.png'))

    def interact(button, dialog_type, act):
        outcomes = []

        def drive():
            dialog = application.activeModalWidget()
            try:
                assert isinstance(dialog, dialog_type), dialog
                act(dialog)
                outcomes.append(None)
            except Exception as exc:
                outcomes.append(exc)
                if dialog:
                    dialog.reject()

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(drive)
        timer.start(150)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert outcomes == [None], outcomes

    def pick(index):
        def choose(dialog):
            tree = dialog.findChild(QTreeWidget)
            assert tree and tree.topLevelItemCount() > index
            tree.setCurrentItem(tree.topLevelItem(index))
            if index == 0 and tree.topLevelItemCount() > 1:
                capture('02-picker', dialog)
            buttons = dialog.findChild(QDialogButtonBox)
            QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)

        interact(page.export_picker, AppDialog, choose)
        assert page.file_tabs.currentIndex() == 0

    def act_file(button, path, action):
        def choose(dialog):
            dialog.setDirectory(str(path.parent))
            filename = dialog.findChild(QLineEdit, 'fileNameEdit')
            filename.setText(path.name)
            buttons = dialog.findChild(QDialogButtonBox)
            QTest.mouseClick(buttons.button(action), Qt.MouseButton.LeftButton)

        interact(button, QFileDialog, choose)

    def save(kind, path):
        act_file(page.export_buttons[kind], path, QDialogButtonBox.StandardButton.Save)
        wait_for_export(page)

    def inside_viewport(widget):
        viewport = page.scroll.viewport()
        return viewport.rect().contains(QRect(widget.mapTo(viewport, QPoint()), widget.size()))

    attribute = Qt.ApplicationAttribute.AA_DontUseNativeDialogs
    previous = application.testAttribute(attribute)
    application.setAttribute(attribute, True)
    try:
        assert page.file_tabs.currentIndex() == 0 and page.export_source.text() == '未选择结果'
        assert page.scroll.verticalScrollBar().value() == 0
        assert inside_viewport(page.export_section), (window.size(), page.export_section.geometry())
        assert page.file_tabs.geometry().bottom() < page.export_section.geometry().top()
        capture('01-default')
        pick(0)
        assert 'editable.pdf' in page.export_source.text()
        assert not page.report.isVisible() and page.history.currentItem() is None
        assert all(button.isEnabled() for button in page.export_buttons.values())
        target = tmp_path / 'editable.md'
        save('markdown', target)
        assert '30 g/day' in target.read_text(encoding='utf-8')
        if evidence.resolve() != tmp_path.resolve():
            copyfile(target, evidence / 'editable.md')
        capture('03-markdown')
        pick(1)
        assert Path(records[1][1]).name in page.export_source.toolTip()
        assert all(button.isEnabled() for button in page.export_buttons.values())
        assert '从 PDF 恢复' in page.export_status.text()
        assert inside_viewport(page.export_section)
        capture('04-pdf-only')
        copy = tmp_path / 'archived-copy.pdf'
        save('pdf', copy)
        resolved = page.export_result_info(records[1][0])
        assert Path(resolved['outputs'][0]).read_bytes() == copy.read_bytes()
        if actual:
            assert resolved['outputs'] != json.loads(records[1][4])['outputs']
        page.set_history([('job', 'moved.pdf', 'completed', 0, json.dumps(moved_info))])
        pick(0)
        assert page.export_locate.isVisible() and '找不到译文 PDF' in page.export_status.text()
        capture('05-missing')
        act_file(page.export_locate, moved / original.name, QDialogButtonBox.StandardButton.Open)
        assert page.export_buttons['markdown'].isEnabled() and page.export_locate.isHidden()
        moved_target = tmp_path / 'moved.md'
        save('markdown', moved_target)
        assert '30 g/day' in moved_target.read_text(encoding='utf-8')
        print(f'Windows workbench {window.width()}x{window.height()}: {evidence}')
    finally:
        application.setAttribute(attribute, previous)
