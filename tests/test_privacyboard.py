"""Task presentation contracts; no OCR, network, or formal profile access."""
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from linguafloat.document_ui import DocumentPage, TaskSummary, task_tone
from test_credentials_ui import application


@pytest.mark.parametrize("state,tone", [
    ("待处理", "secondary"), ("排队", "secondary"), ("OCR 中", "accent"),
    ("暂停", "warning"), ("取消中", "warning"), ("已取消", "warning"),
    ("已完成", "success"), ("已完成，3 处未翻译", "warning"), ("失败", "error")])
def test_task_states_keep_semantics_and_full_caption(application, tmp_path, state, tone):
    page = DocumentPage()
    page.add_files([str(tmp_path / "a-long-research-paper-name.pdf")])
    identity = next(iter(page.rows))
    page.update_row(identity, state, details="第 2 页 · 测试原因")
    row = page.rows[identity]
    assert task_tone(state) == tone
    assert row["bar"].label.full_text == state
    assert row["item"].text(3) == state
    row["bar"].setFormat("排版中 · 阶段 50%")
    assert row["bar"].label.full_text == "排版中 · 阶段 50%"
    page.show_details(identity)
    assert "测试原因" in page.report.toPlainText()
    assert page.files_empty.isHidden()
    page.remove_row(identity)
    assert not page.files_empty.isHidden()
    page.deleteLater()
    application.processEvents()


def test_full_error_text_remains_available(application):
    page = DocumentPage()
    message = "输出写入失败。" * 100
    page.status.setText(message)
    assert page.status.text() == message and page.status.toolTip() == message
    page.report.setPlainText(message)
    assert not page.copy_details.isHidden()
    page.report.clear()
    assert page.copy_details.isHidden()
    page.deleteLater()


def test_summary_defers_zero_width_measurement_and_preserves_wrapped_text(application):
    summary = TaskSummary("等待添加文件")
    source = "long_research_filename_" * 12 + ".pdf"
    message = "原始第 12 页 · 第 7 段 · 请求 R0042 · " + "正在等待完整翻译结果，" * 12 + " · 已用时 987 秒"
    try:
        summary.resize(0, summary.height())
        summary.setText("输出写入失败。" * 100)
        assert summary.toolTip() == summary.text()
        assert summary.height() == summary.fontMetrics().lineSpacing() * 4 + 8
        summary.set_task(source, "翻译中", message)
        assert source in summary.text()
        assert summary.height() == summary.fontMetrics().lineSpacing() * 4 + 8
        summary.resize(640, summary.height())
        summary.show()
        QTest.qWait(30)
        prefix, separator, _ = summary.text().partition("\n原始第 12 页")
        assert separator
        assert "".join(prefix.splitlines()[1:]) == source
        assert summary.text().endswith("已用时 987 秒")
        measured = summary.fontMetrics().boundingRect(
            QRect(0, 0, summary.contentsRect().width() - 4, 16777215),
            int(Qt.TextFlag.TextWordWrap), summary.text())
        assert measured.height() <= summary.contentsRect().height()
        assert summary.height() > summary.fontMetrics().lineSpacing() * 4 + 8
        height = summary.height()
        summary.set_task(source, "翻译中", message)
        QTest.qWait(30)
        assert summary.height() == height
    finally:
        summary.close()
        summary.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("name,index", [("files", 0), ("history", 1)])
def test_selected_details_reveal_after_focus_and_stay_put(application, tmp_path, name, index):
    page = DocumentPage()
    try:
        source = str(tmp_path / "paper.pdf")
        page.add_files([source])
        identity = next(iter(page.rows))
        page.update_row(identity, "翻译中", details="第 2 页 · 测试详情")
        page.set_history([("history-job", source, "completed", 0, "{}")])
        page.resize(860, 620)
        page.file_tabs.setCurrentIndex(index)
        page.show()
        QTest.qWait(30)
        tree = getattr(page, name)
        tree.clearSelection()
        tree.setCurrentItem(None)
        page.scroll.verticalScrollBar().setValue(0)
        item = tree.topLevelItem(0)
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=tree.visualItemRect(item).center())
        QTest.qWait(50)
        viewport = page.scroll.viewport()
        top = page.report.mapTo(viewport, QPoint()).y()
        assert page.report.toPlainText()
        assert top >= 0
        assert top + min(page.report.height(), viewport.height()) <= viewport.height()
        position = page.scroll.verticalScrollBar().value()
        for _ in range(3):
            if name == "history":
                page.show_history_details()
            else:
                page.show_details(identity)
        assert page._details_reveal_timer.isActive()
        QTest.qWait(50)
        assert not page._details_reveal_timer.isActive()
        assert page.scroll.verticalScrollBar().value() == position
    finally:
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
