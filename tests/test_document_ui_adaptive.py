from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
import pytest

from test_credentials_ui import application
from linguafloat.config import AppSettings, ConfigStore
from linguafloat.document_ui import DocumentPage


@pytest.fixture
def page(application):
    widget = DocumentPage()
    widget.resize(1100, 1100)
    widget.show()
    QTest.qWait(30)
    yield widget
    widget.close()
    widget.deleteLater()


def widths(tree):
    return [tree.columnWidth(column) for column in range(tree.columnCount())]


@pytest.mark.parametrize("name,index", [("files", 0), ("history", 1)])
def test_adaptive_columns_fill_viewport_and_keep_data_readable(page, name, index):
    page.file_tabs.setCurrentIndex(index)
    tree = getattr(page, name)
    for width in (1100, 800, 1400):
        page.resize(width, 1100)
        QTest.qWait(30)
        assert sum(widths(tree)) == tree.viewport().width()
        assert tree.horizontalScrollBar().maximum() == 0
        for column in range(1, tree.columnCount()):
            assert tree.columnWidth(column) >= tree.fontMetrics().horizontalAdvance(tree.headerItem().text(column)) + 24
    assert page.preferences()["document_column_widths"] == {}


def test_long_names_are_elided_without_expanding_columns_and_rows_can_scroll(page, tmp_path):
    # Let the enclosing scroll area's initial width reach the table before sampling.
    for _ in range(20):
        QTest.qWait(25)
        if sum(widths(page.files)) == page.files.viewport().width():
            before_viewport = page.files.viewport().width()
            QTest.qWait(50)
            if before_viewport == page.files.viewport().width():
                break
    before = widths(page.files)
    paths = [tmp_path / ("long_research_title_" * 6 + f"_{i}.pdf") for i in range(30)]
    page.add_files([str(paths[0])])
    QTest.qWait(30)
    assert widths(page.files) == before
    assert page.files.textElideMode() == Qt.TextElideMode.ElideMiddle
    assert page.files.topLevelItem(0).toolTip(0) == str(paths[0].resolve())
    page.add_files([str(path) for path in paths[1:]])
    QTest.qWait(30)
    assert page.files.verticalScrollBar().maximum() > 0
    assert sum(widths(page.files)) == page.files.viewport().width()


def test_manual_resize_persists_and_adaptive_button_only_resets_current_tab(page, tmp_path):
    store = ConfigStore(tmp_path / "columns.json")
    settings = AppSettings()

    def save(preferences):
        settings.document_column_widths = preferences["document_column_widths"]
        store.save(settings)

    page.preferences_changed.connect(save)
    for name, index in (("files", 0), ("history", 1)):
        page.file_tabs.setCurrentIndex(index)
        QTest.qWait(30)
        tree = getattr(page, name)
        header = tree.header()
        point = QPoint(header.sectionViewportPosition(0) + tree.columnWidth(0) - 1, header.height() // 2)
        QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(header.viewport(), point + QPoint(70, 0), delay=20)
        QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=point + QPoint(70, 0))
        assert not page.reset_columns.isChecked()
    manual = {name: widths(getattr(page, name)) for name in ("files", "history")}
    page.resize(1300, 1100)
    QTest.qWait(220)
    assert page.preferences()["document_column_widths"] == manual
    assert store.load().document_column_widths == manual

    page.reset_columns.click()
    QTest.qWait(220)
    assert page.reset_columns.accessibleName() == "自适应列宽"
    assert page.reset_columns.isChecked()
    assert store.load().document_column_widths == {"files": manual["files"]}
    page.resize(1000, 1100)
    QTest.qWait(30)
    assert sum(widths(page.history)) == page.history.viewport().width()
    assert widths(page.files) == manual["files"]

    restored = DocumentPage()
    try:
        restored.restore_preferences(store.load())
        restored.resize(1200, 1100)
        restored.file_tabs.setCurrentIndex(1)
        restored.show()
        QTest.qWait(30)
        assert widths(restored.files) == manual["files"]
        assert sum(widths(restored.history)) == restored.history.viewport().width()
        assert restored.preferences()["document_column_widths"] == {"files": manual["files"]}
    finally:
        restored.close()
        restored.deleteLater()


def test_legacy_fixed_widths_survive_restore_refresh_and_window_resize(page, tmp_path):
    legacy = {"files": [430, 56, 120, 280], "history": [390, 56, 100, 90, 100, 100, 240]}
    page.restore_preferences(AppSettings(document_column_widths=legacy))
    page.add_files([str(tmp_path / "paper.pdf")])
    page.set_history([("job", "paper.pdf", "completed", 0, "{}")])
    page.resize(1400, 1200)
    QTest.qWait(30)
    assert {name: widths(getattr(page, name)) for name in legacy} == legacy
    assert page.preferences()["document_column_widths"] == legacy


def test_double_click_fit_switches_to_fixed_width_until_adaptive_is_clicked(page, tmp_path):
    page.add_files([str(tmp_path / ("long_research_title_" * 6 + ".pdf"))])
    QTest.qWait(30)
    header = page.files.header()
    point = QPoint(page.files.columnWidth(0) - 1, header.height() // 2)
    QTest.mouseDClick(header.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.qWait(30)
    manual = widths(page.files)
    assert "files" in page.preferences()["document_column_widths"]
    page.resize(1400, 1100)
    QTest.qWait(30)
    assert widths(page.files) == manual
    page.reset_columns.click()
    QTest.qWait(30)
    assert page.preferences()["document_column_widths"] == {}
    assert sum(widths(page.files)) == page.files.viewport().width()


def test_empty_and_populated_tabs_have_equal_expanding_table_heights(page, tmp_path):
    def heights():
        result = []
        for name, index in (("files", 0), ("history", 1)):
            page.file_tabs.setCurrentIndex(index)
            QTest.qWait(30)
            result.append(getattr(page, name).height())
        assert result[0] == result[1]
        return result[0]

    empty_height = heights()
    page.add_files([str(tmp_path / "paper.pdf")])
    page.set_history([("job", "paper.pdf", "completed", 0, "{}")])
    assert heights() == empty_height
    page.resize(1100, 1400)
    assert heights() > empty_height
    page.remove_row(next(iter(page.rows)))
    page.set_history([])
    assert heights() > empty_height


def test_details_stay_between_list_and_settings_and_scroll_inside(page):
    page.report.setPlainText("\n".join(f"第 {i} 页：翻译完成" for i in range(100)))
    QTest.qWait(30)
    content = page.scroll.widget()
    layout = content.layout()
    assert layout.indexOf(page.report) == layout.indexOf(page.file_tabs) + 1
    assert page.report.mapTo(content, QPoint()).y() < page.source_language.mapTo(content, QPoint()).y()
    assert page.report.verticalScrollBar().maximum() > 0
    compact = page.report.height()
    page.expand_details.setChecked(True)
    QTest.qWait(30)
    assert compact < page.report.height() <= 280
    assert page.report.verticalScrollBar().maximum() > 0
    page.expand_details.setChecked(False)
    QTest.qWait(30)
    assert page.report.height() == compact


def test_summary_keeps_phase_and_real_progress_before_long_names(page):
    source = "long_research_title_" * 40 + ".pdf"
    message = "原始第 2 页 · 请求 R0001 · 等待 API 返回"
    page.status.set_task(source, "翻译中 · 阶段 38%", message)
    page.resize(800, 1100)
    QTest.qWait(30)
    visible = QLabel.text(page.status).splitlines()
    assert visible[0] == "翻译中 · 阶段 38%"
    filename, location, request = "\n".join(visible[1:]).rsplit("\n", 2)
    assert "".join(filename.splitlines()) == source
    assert location == "原始第 2 页"
    assert request == "请求 R0001 · 等待 API 返回"
    assert not page.status.toolTip()
    assert page.status.height() >= page.status.fontMetrics().lineSpacing() * 4
    page.status.setText("无法读取当前配置的密钥，请在配置设置中检查。")
    page.resize(1100, 1100)
    QTest.qWait(30)
    assert "阶段 38%" not in page.status.text()
    assert "密钥" in page.status.text()
