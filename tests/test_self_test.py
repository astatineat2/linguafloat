from test_app_controller import FakeThreadPool
from test_credentials_ui import application, controller


def test_self_test_reports_ocr_translation_and_disabled_listeners(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    c.window.self_test_input.setText("Hello world.")
    c.cache.put(c.settings.active_profile().cache_scope, "Hello world.", "old result")
    c._run_self_test()
    assert not c.window.self_test_button.isEnabled()
    assert "正在识别" in c.window.self_test_result.toPlainText()
    c._run_self_test()
    assert len(c.thread_pool.tasks) == 1
    c.thread_pool.tasks[0].signals.succeeded.emit(0, b"", "Hello world.")
    assert len(c.thread_pool.tasks) == 2  # A fresh request even when cached.
    task = c.thread_pool.tasks[1]
    assert task.timeout == 12 and task.max_retries == 0
    task.signals.succeeded.emit(task.cache_scope, task.source_text, "你好，世界。")
    report = c.window.self_test_result.toPlainText()
    assert "OCR：通过" in report and "译文：你好，世界。" in report
    assert "监听：未启用" in report
    assert "均可用" not in report
    assert c.window.self_test_button.isEnabled() and not c._self_test_pending


def test_self_test_failure_keeps_local_results_and_allows_retry(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    c._run_self_test()
    c.thread_pool.tasks[0].signals.failed.emit(0, "模型缺失")
    task = c.thread_pool.tasks[1]
    task.signals.failed.emit(task.cache_scope, task.source_text, "HTTP 401")
    report = c.window.self_test_result.toPlainText()
    assert "模型缺失" in report and "HTTP 401" in report
    assert c.window.self_test_button.isEnabled() and not c._self_test_pending


def test_empty_self_test_does_not_start_or_disable_button(controller):
    c, _, _ = controller
    c.thread_pool = FakeThreadPool()
    c.window.self_test_input.clear()
    c._run_self_test()
    assert not c.thread_pool.tasks and not c._self_test_pending
    assert c.window.self_test_button.isEnabled()
