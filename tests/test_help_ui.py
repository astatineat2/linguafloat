from types import SimpleNamespace

from test_credentials_ui import application, controller
from linguafloat import help_ui


def test_guide_can_be_reopened_and_does_not_call_translation(controller, application):
    app, _, _ = controller
    app.settings.update_auto_check = False
    app.settings.first_run_complete = False
    app.help.start()
    assert app.help.dialog.isVisible()
    assert app.help.dialog.tabs.currentIndex() == 1
    application.processEvents()
    assert app.help.dialog.guide.isVisibleTo(app.help.dialog)
    assert "开始使用 LinguaFloat" in app.help.dialog.guide.toPlainText()
    assert app.help.dialog.guide.viewport().height() > 100
    app.help.dialog.close()
    assert app.settings.first_run_complete
    app.help.show(guide=True)
    assert app.help.dialog.isVisible()
    app.help.dialog.close()


def test_failed_check_has_explicit_reason_and_no_install_action(controller):
    app, _, _ = controller
    app.help._failed("GitHub 暂时限制访问频率")
    assert "限制" in app.help.dialog.status.text()
    assert not app.help.dialog.install_button.isEnabled()


def test_cancel_update_wait_preserves_active_document_batch(controller):
    app, _, _ = controller
    page = app.window.documents
    page.set_busy(True)
    app.help.install_pending = True
    app._update_waiting = True
    app.documents.update_pending = True
    app.help.cancel_action()
    assert page.busy
    assert not page.start_button.isEnabled()
    assert not app._update_waiting and not app.documents.update_pending
    assert "继续运行" in app.help.dialog.status.text()


def test_install_wait_never_cancels_running_document_process(controller, monkeypatch):
    app, _, _ = controller
    process = object()
    app.documents.process = process
    app.help.install_pending = True
    calls = []
    monkeypatch.setattr(app, "quit", lambda **kw: calls.append(kw))
    app.help._try_install()
    assert not calls and app.documents.process is process
    app.documents.process = None


def test_update_wait_blocks_new_document_jobs_without_changing_existing_queue(controller):
    app, _, _ = controller
    app.documents.update_pending = True
    queued = object()
    app.documents.queue = [queued]
    app.documents.start()
    assert app.documents.queue == [queued]
    assert "未创建新任务" in app.window.documents.status.text()
    app.documents.queue = []


def test_update_wait_delivers_existing_translation_but_blocks_new_requests():
    from test_app_controller import make_controller
    app, profile = make_controller()
    app._translate("Hello")
    app._update_waiting = True
    app._translation_succeeded(profile.cache_scope, "Hello", "你好")
    assert app.overlay.translations[-1] == ("Hello", "你好")
    app._translate("Do not start another request")
    assert len(app.thread_pool.tasks) == 1


def test_update_preference_survives_save_and_reopen(controller):
    app, _, store = controller
    app.help.dialog.auto_check.setChecked(False)
    assert not store.load().update_auto_check


def test_compact_help_layout_fits_supported_window_sizes(controller, application):
    app, _, _ = controller
    for width, height in [(860, 620), (1060, 840)]:
        app.window.resize(width, height)
        app.window.show()
        application.processEvents()
        assert app.window.width() == width
        assert app.window.rect().contains(app.window.help_button.mapTo(app.window, app.window.help_button.rect().bottomRight()))
    app.window.hide()
