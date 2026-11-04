import pytest
from PySide6.QtCore import QPoint, QRect, Qt

from test_credentials_ui import application, controller
from linguafloat.ui import MainWindow, apply_ui_font
from linguafloat.config import AppSettings


@pytest.mark.parametrize("size", [(860, 620), (1060, 840)])
@pytest.mark.parametrize("points", [12, 14, 16])
def test_document_page_main_actions_visible(application, size, points):
    apply_ui_font(application, points)
    window = MainWindow(AppSettings(), "missing")
    window.resize(*size)
    window.pages.setCurrentIndex(2)
    window.show()
    application.processEvents()
    page = window.documents
    for widget in (page.start_button, page.trial_button, page.pause_button, page.cancel_button, page.open_button):
        rect = QRect(widget.mapTo(window, QPoint()), widget.size())
        assert window.rect().contains(rect)
        assert widget.width() >= widget.minimumSizeHint().width()
    assert page.scroll.horizontalScrollBar().maximum() == 0
    assert page.mono.isChecked() and page.bilingual.isChecked()
    window.allow_close()
    window.close()
    apply_ui_font(application, 12)


def test_document_config_follows_active_provider_without_separate_model(controller):
    c, _, _ = controller
    page = c.window.documents
    assert page.profiles[page.active_id].model == "test-model"
    c.window.set_active_profile("second")
    assert page.profiles[page.active_id].model == "second-model"
    assert not hasattr(page,"model") and not hasattr(page,"provider")


def test_reference_skip_persists_and_is_snapshotted(controller, tmp_path, monkeypatch):
    c, _, store = controller
    page = c.window.documents
    assert not page.skip_references.isChecked()
    page.skip_references.setChecked(True)
    assert store.load().document_skip_references
    restored = MainWindow(store.load(), "missing")
    from linguafloat.document_ui import DocumentController
    DocumentController(restored.documents, store.load(), c.documents.credentials,
                       tmp_path / "restored.sqlite", parent=restored)
    assert restored.documents.skip_references.isChecked()
    restored.allow_close()
    restored.close()
    source = tmp_path / "references.pdf"
    source.touch()
    page.add_files([str(source)])
    monkeypatch.setattr(c.documents, "next_job", lambda: None)
    c.documents.start()
    assert len(c.documents.queue) == 1
    job, _, _ = c.documents.queue[0]
    assert job.skip_references and not page.skip_references.isEnabled()
    page.skip_references.setChecked(False)
    assert job.skip_references  # A later edit cannot alter an existing task.
    c.documents.queue.clear()
    page.set_busy(False)
    assert page.skip_references.isEnabled()


def test_document_queue_validation_does_not_start_worker(controller, tmp_path):
    c, _, _ = controller
    file = tmp_path / "a.docx"
    file.touch()
    page = c.window.documents
    page.add_files([str(file), str(file)])
    assert page.files.topLevelItemCount() == 1
    page.pages.setText("1")
    c.documents.start()
    assert c.documents.process is None
    assert "Word" in page.status.text()
    assert page.start_button.isEnabled()


def test_relayout_queues_without_reading_api_key(controller, tmp_path, monkeypatch):
    c, _, _ = controller
    source = tmp_path / "paper.pdf"
    source.touch()
    page = c.window.documents
    page.add_files([str(source)])
    page.relayout_only.setChecked(True)
    def forbidden(*_):
        pytest.fail("Cache-only re-layout should not read credentials")
    monkeypatch.setattr(c.documents.credentials, "get_api_key", forbidden)
    monkeypatch.setattr(c.documents, "next_job", lambda: None)
    c.documents.start()
    assert len(c.documents.queue) == 1
    job, key, _ = c.documents.queue[0]
    assert job.relayout_only and not key
    assert not page.relayout_only.isEnabled()
    c.documents.queue.clear()
    page.set_busy(False)
    assert page.relayout_only.isEnabled()


def test_remove_completed_record_preserves_files_and_active_row(controller, tmp_path):
    c, _, _ = controller
    page = c.window.documents
    paths = [tmp_path / name for name in ("finished.pdf", "active.pdf")]
    for path in paths:
        path.write_bytes(b"fixture")
    page.add_files(map(str, paths))
    identities = list(page.rows)
    page.update_row(identities[0], "已完成", 100)
    c.documents.queue = [(None, "", identities[1])]
    for row in page.rows.values():
        row["item"].setSelected(True)
    c.documents.remove_selected()
    assert list(page.rows) == [identities[1]]
    assert all(path.read_bytes() == b"fixture" for path in paths)
    assert "先取消" in page.status.text()
    page.update_row(identities[0], "失败", details="late callback")
    assert page.files.topLevelItemCount() == 1
    c.documents.queue.clear()


def test_arrangement_and_progress_are_explicit(controller, tmp_path):
    c, _, _ = controller
    page = c.window.documents
    path = tmp_path / "paper.pdf"
    path.touch()
    page.add_files([str(path)])
    identity = next(iter(page.rows))
    page.update_row(identity, "解析中")
    assert page.rows[identity]["bar"].maximum() == 0
    page.update_row(identity, "翻译中", 45)
    assert page.rows[identity]["bar"].value() == 45
    page.update_row(identity, "暂停")
    assert page.rows[identity]["bar"].maximum() == 100
    assert "暂停" in page.rows[identity]["bar"].format()
    assert page.side_by_side.isChecked()
    page.alternating.click()
    assert page.alternating.isChecked() and not page.side_by_side.isChecked()


def test_daily_configuration_is_not_in_global_header(controller, application):
    c, _, _ = controller
    w = c.window
    w.show()
    w.pages.setCurrentIndex(2)
    application.processEvents()
    assert not w.active_provider_combo.isVisible()
    w.pages.setCurrentIndex(0)
    application.processEvents()
    assert w.active_provider_combo.isVisible()


def test_document_timeout_persists_per_provider_without_changing_ordinary_timeout(controller):
    c, _, store = controller
    previous = c.settings.active_profile().timeout
    assert c.window.document_timeout_spin.value() == 180
    c.window.document_timeout_spin.setValue(300)
    c.window._emit_save()
    saved = store.load()
    assert saved.active_profile().document_timeout == 300
    assert saved.active_profile().timeout == previous
    assert saved.providers[1].document_timeout == 180
    assert not hasattr(c.window.documents,"request_timeout")


def test_document_rate_settings_persist_per_provider(controller):
    c, _, store = controller
    window = c.window
    window.document_concurrency_spin.setValue(6)
    window.document_rpm_spin.setValue(25)
    window.document_tpm_spin.setValue(50000)
    window._emit_save()
    profile = store.load().providers[0]
    assert (profile.document_concurrency, profile.document_rpm, profile.document_tpm) == (6, 25, 50000)
    assert store.load().providers[1].document_concurrency == 2


@pytest.mark.parametrize("size", [(860, 620), (1060, 840)])
def test_document_rate_controls_visible_without_advanced(controller, application, size):
    c, _, _ = controller
    window = c.window
    window.resize(*size)
    window.pages.setCurrentIndex(1)
    window.show()
    window.advanced_toggle.setChecked(False)
    application.processEvents()
    for control in (window.document_concurrency_spin, window.document_rpm_spin, window.document_tpm_spin):
        window.config_scroll.ensureWidgetVisible(control)
        application.processEvents()
        assert control.isVisible()
        assert window.rect().contains(QRect(control.mapTo(window, QPoint()), control.size()))
    assert window.config_scroll.horizontalScrollBar().maximum() == 0


def test_history_distinguishes_request_counts_from_durations(controller):
    import json
    from PySide6.QtCore import Qt
    c, _, _ = controller
    info = dict(performance=dict(phases={"翻译中": 30},
        operations={"HTTP 请求": 60, "HTTP 429 限流": 0, "请求重试": 0},
        operation_counts={"HTTP 请求": 4, "HTTP 429 限流": 1, "请求重试": 1,
                          "翻译分段数": 3, "分段原文字符": 9000}))
    page = c.window.documents
    page.set_history([("test", "sample.pdf", "completed", 0, json.dumps(info))])
    detail = page.history.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole + 2)
    assert "HTTP 请求 4 次，平均每次 15.00 秒" in detail
    assert "HTTP 429 限流 1 次" in detail
    assert "请求重试 1 次" in detail
    assert "HTTP 429 限流：0.00 秒" not in detail
    assert "普通文本分段 3 个，平均每段 3000 字符" in detail


def test_output_directory_persists_from_picker_and_edit(controller, tmp_path, monkeypatch):
    from linguafloat.document_ui import DocumentPage, DocumentController, QFileDialog
    c, _, config = controller
    page = c.window.documents
    chosen = str(tmp_path / "chosen results")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: chosen)
    page.choose_output()
    assert config.load().document_output_dir == chosen
    reopened = DocumentPage()
    document_controller = DocumentController(reopened, config.load(), c.credentials, c.documents.database)
    assert reopened.output_dir.text() == chosen
    edited = str(tmp_path / "edited results")
    page.output_dir.setText(edited)
    page.output_dir.editingFinished.emit()
    assert config.load().document_output_dir == edited
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "")
    page.choose_output()
    assert config.load().document_output_dir == edited
    page.output_dir.clear()
    page.output_dir.editingFinished.emit()
    assert config.load().document_output_dir == edited
    reopened.close()


def test_fixed_font_and_persistent_history(controller, tmp_path, application):
    from linguafloat.document_core import JobStore
    from test_document_core import job_for
    c, _, config = controller
    assert not hasattr(c.window, "font_size_combo")
    assert application.font().pointSize() == 14
    source = tmp_path / "previous.pdf"
    source.write_bytes(b"12345")
    store = JobStore(c.documents.database)
    job = job_for(source)
    store.status(job, "running")
    store.record_usage(job, {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                             "reported_requests": 1, "unreported_requests": 1})
    store.status(job, "completed")
    store.close()
    c.documents.refresh_history()
    row = c.window.documents.history.topLevelItem(0)
    assert row.text(0) == "previous.pdf" and row.text(1) == "PDF"
    assert row.text(4) == "100" and row.text(5) == "50"
    assert all("部分" not in row.text(column) for column in range(4, 8))
    assert "用量不完整" in row.data(0, Qt.ItemDataRole.UserRole + 2)
    assert row.toolTip(0) == str(source)
    assert all(row.toolTip(column) == row.text(column) for column in range(1, 6))
    assert "用量不完整" in row.toolTip(6)
    c.window.documents.add_files([str(source)])
    c.window.documents.files.topLevelItem(0).setSelected(True)
    c.documents.remove_selected()
    c.documents.refresh_history()
    assert c.window.documents.history.topLevelItemCount() == 1
    c.settings.font_size = 12
    config.save(c.settings)
    assert config.load().font_size == 14


def test_history_distinguishes_preserved_original_regions(controller):
    import json
    c, _, _ = controller
    page = c.window.documents
    page.set_history([("partial-layout", "C:/Example/paper.pdf", "completed", 1780000000,
                       json.dumps(dict(untranslated_regions=3)))])
    assert "3 处保留原文" in page.history.topLevelItem(0).text(6)
    assert page.history.topLevelItem(0).text(6).startswith("部分完成，需处理")
    c.documents.batch_ids = ["partial-layout"]
    c.documents.batch_results = {"partial-layout": "部分完成"}
    c.documents.update_batch()
    assert "完成 0" in page.batch_status.text() and "部分完成 1" in page.batch_status.text()


def test_removed_columns_keep_tokens_and_errors_without_costs(controller,tmp_path):
    c,_,config=controller
    page=c.window.documents
    for name in ("input_price","output_price","price_multiplier","provider","model","follow_model","graphics_preview"):
        assert not hasattr(page,name)
    path=tmp_path/"sample.pdf";path.touch();page.add_files([str(path)])
    identity=next(iter(page.rows))
    page.update_billing(identity,dict(input_tokens=100,output_tokens=50,pricing=dict(input=2,output=8,multiplier=.1)))
    row=page.rows[identity]
    assert page.files.columnCount()==4 and page.history.columnCount()==7
    tip=row["bar"].toolTip()
    assert "输入 Token" in tip and "100" in tip and "输出 Token" in tip
    assert all(text not in tip for text in ("费用","预计","倍率","$"))
    page.update_row(identity,"失败",details="第 2 页 · 表格区域 4 · 字形缺失")
    page.files.setCurrentItem(row["item"])
    assert "字形缺失" in page.report.toPlainText()
    page.copy_report()


def test_document_tab_does_not_show_unrelated_completion_and_activity_updates_row(controller, tmp_path):
    import json
    from types import SimpleNamespace
    from linguafloat.document_core import DocumentJob
    c, _, _ = controller
    c.window.set_activity("翻译完成。")
    c.window.pages.setCurrentIndex(2)
    assert c.window.activity.isHidden()
    page = c.window.documents
    source = tmp_path / "paper.pdf"
    source.touch()
    page.add_files([str(source)])
    identity = next(iter(page.rows))
    c.documents.row_id = identity
    c.documents.job = DocumentJob(str(source),str(tmp_path),{})
    page.update_row(identity,"解析中",38)
    payload = (json.dumps(dict(type="activity",stage="翻译中",stage_key="requests",
        message="原始第 2 页 · 请求 R0001 · 等待 API 返回")) + "\n").encode()
    c.documents.process = SimpleNamespace(readAllStandardOutput=lambda: payload)
    try:
        c.documents.read_events()
        assert page.rows[identity]["state"] == "翻译中"
        assert page.rows[identity]["bar"].maximum() == 0
        assert "R0001" in page.status.text()
    finally:
        c.documents.process = None
