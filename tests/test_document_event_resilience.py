import json
from types import SimpleNamespace as NS

from PySide6.QtCore import QProcess
from linguafloat.config import AppSettings
from linguafloat.document_core import DocumentJob, JobStore
from linguafloat.document_ui import DocumentPage, DocumentController
from test_credentials_ui import application


def active_page(tmp_path):
    page=DocumentPage()
    controller=DocumentController(page,AppSettings(),None,tmp_path/'ui.sqlite')
    source=tmp_path/'test.pdf'; source.touch()
    page.add_files([str(source)])
    row_id=next(iter(page.rows))
    controller.row_id=row_id
    controller.job=DocumentJob(str(source),str(tmp_path),dict(base_url='http://127.0.0.1:9',model='mock',api_mode='responses'),job_id='current')
    controller.batch_ids=[row_id]; controller.batch_results={}
    class Process:
        payload=b''
        def readAllStandardOutput(self):
            data,self.payload=self.payload,b''
            return data
        def deleteLater(self): pass
        def state(self): return QProcess.ProcessState.NotRunning
    controller.process=Process()
    page.set_busy(True)
    return page,controller


def feed(controller,*events):
    controller.process.payload=b''.join((json.dumps(e)+'\n').encode() for e in events)
    controller.read_events()


def test_current_selection_replaces_old_history_details(application,tmp_path):
    page,c=active_page(tmp_path)
    try:
        page.report.setPlainText('OLD failure')
        page.rows[c.row_id]['details']='CURRENT waiting'
        page.show_details(c.row_id)
        page.update_row(c.row_id,'翻译中',details='CURRENT new activity')
        assert page.report.toPlainText()=='CURRENT new activity'
    finally: page.close()


def test_terminal_event_cannot_be_overwritten_by_late_heartbeat(application,tmp_path):
    page,c=active_page(tmp_path)
    try:
        feed(c,dict(type='error',message='timeout'),dict(type='activity',stage='翻译中',message='OLD waiting'))
        assert page.rows[c.row_id]['state']=='失败'
        assert 'OLD waiting' not in page.status.text()
    finally: page.close()


def test_bad_event_does_not_hide_following_terminal_event(application,tmp_path):
    page,c=active_page(tmp_path)
    try:
        feed(c,dict(type='progress',value='not a number'),dict(type='error',message='timeout'))
        assert page.rows[c.row_id]['state']=='失败'
        store=JobStore(c.database)
        try:
            records=store.db.execute('select details from diagnostic_events').fetchall()
            assert records and 'ValueError' in records[0][0]
        finally: store.close()
    finally: page.close()


def test_process_exit_recovers_db_terminal_without_overwriting_it(application,tmp_path):
    page,c=active_page(tmp_path)
    store=JobStore(c.database)
    store.status(c.job,'running'); store.status(c.job,'failed',dict(message='persisted timeout'))
    store.close()
    try:
        c.process_finished()
        assert page.rows[c.row_id]['state']=='失败'
        assert 'persisted timeout' in page.report.toPlainText()
        assert page.start_button.isEnabled()
        store=JobStore(c.database)
        try:
            raw=store.db.execute('select details from jobs where id=?',(c.job.job_id,)).fetchone()[0]
            assert json.loads(raw)['message']=='persisted timeout'
        finally: store.close()
    finally: page.close()


def test_stale_job_event_is_ignored(application,tmp_path):
    page,c=active_page(tmp_path)
    try:
        feed(c,dict(type='error',job_id='previous',message='OLD timeout'))
        assert not c.terminal and page.rows[c.row_id]['state']=='待处理'
    finally: page.close()


def test_live_worker_is_not_failed_just_because_db_is_terminal(application,tmp_path):
    page,c=active_page(tmp_path)
    c.process.state=lambda:QProcess.ProcessState.Running
    store=JobStore(c.database)
    store.status(c.job,'failed',dict(message='written before worker exit')); store.close()
    try:
        c._reconcile_process()
        assert not c.terminal and c.process is not None and page.busy
    finally: page.close()


def test_running_db_record_is_not_mistaken_for_success(application,tmp_path):
    page,c=active_page(tmp_path)
    store=JobStore(c.database); store.status(c.job,'running'); store.close()
    try:
        c.process_finished()
        assert page.rows[c.row_id]['state']=='进程异常退出'
        assert page.start_button.isEnabled()
    finally: page.close()


def test_late_process_exit_does_not_finish_the_next_job(application,tmp_path):
    page,c=active_page(tmp_path)
    try:
        active=c.process
        c.process_finished(process=NS())
        assert c.process is active and page.busy
    finally: page.close()


def test_failure_stops_indeterminate_bar_and_keeps_full_reason_in_details(application,tmp_path):
    page,c=active_page(tmp_path)
    try:
        feed(c,dict(type='activity',stage='翻译中',message='waiting'),
             dict(type='error',message='timeout\nsynthetic stack location'))
        assert page.progress.maximum()==100
        assert 'synthetic stack location' not in page.status.text()
        assert 'synthetic stack location' in page.report.toPlainText()
    finally:page.close()


def test_partial_terminal_recovers_from_db_and_history_labels_it_partial(application,tmp_path):
    page,c=active_page(tmp_path)
    store=JobStore(c.database)
    store.status(c.job,'running')
    store.status(c.job,'partial',dict(transport_partial=True,untranslated_regions=3,
        outputs=[str(tmp_path/'partial.pdf')],warnings=['部分完成；等待主动补译']))
    records=store.history();store.close()
    try:
        c._restore_terminal()
        assert c.terminal
        assert page.rows[c.row_id]['state'].startswith('部分完成')
        page.set_history(records)
        assert page.history.topLevelItem(0).text(6).startswith('部分完成')
        assert c.batch_results[c.row_id]=='部分完成'
    finally:page.close()
