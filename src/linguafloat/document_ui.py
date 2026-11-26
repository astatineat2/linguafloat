from __future__ import annotations

import copy
import html
import json
import sys
import uuid
from datetime import datetime
from dataclasses import asdict
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import QEvent, QObject, QProcess, QRect, QTimer, QUrl, Signal, Qt, QSize
from PySide6.QtGui import QDesktopServices, QPainter, QColor, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QSizePolicy,
    QHeaderView, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea, QTextBrowser,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractItemView, QRadioButton,
    QButtonGroup, QToolButton, QSpinBox, QDoubleSpinBox, QTabWidget, QFrame, QStackedLayout,
    QDialog, QDialogButtonBox, QInputDialog)

from linguafloat.document_core import DocumentError, DocumentJob, JobStore, LANGUAGES, SUPPORTED, billing_display
from linguafloat.document_export import choices as export_choices, resolve_result
from linguafloat.document_review_ui import ReviewPanel
from linguafloat.theme import COLORS, section_heading, set_state
from linguafloat.ui_layout import FieldGrid


class TaskProgress(QProgressBar):
    """Keep progress data separate from its readable, elided status caption."""
    def __init__(self, label):
        super().__init__()
        self.label = label
        self.setObjectName("taskProgress")
        self.setTextVisible(False)
        self.setFixedHeight(4)

    def setFormat(self, text):
        super().setFormat(text)
        self.label.full_text = text
        self.label.setAccessibleName(text)
        self.label.setText(self.label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, self.label.width()))


class ExportSelectionLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setAccessibleName("选中的译文")

    def set_result(self, text):
        self.setToolTip(text)
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        text = self.toolTip() or "未选择结果"
        self.setText(self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, self.width()))


class TaskSummary(QLabel):
    """Group real task fields into four base lines, growing when they wrap."""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._task = None
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text):
        self._task = None
        if text != self.text():
            super().setText(text)
        self.setToolTip(text)
        self.setAccessibleName(text)
        self._fit_height()

    def set_task(self, source, state, message=""):
        self._task = (source, state, message)
        full_text = "\n".join(part for part in (state, source, message) if part)
        self.setToolTip("")
        self.setAccessibleName(full_text)
        self._refresh_task()

    def _refresh_task(self):
        if self._task:
            source, state, message = self._task
            filename = self._wrapped_filename(source)
            current, request = [], []
            for field in message.replace("\n", " · ").split("·"):
                field = field.strip()
                if not field:
                    continue
                if request or field.startswith(("请求", "已用时", "耗时", "等待", "重试", "超时", "HTTP")):
                    request.append(field)
                else:
                    current.append(field)
            text = "\n".join(part for part in
                (state, filename, " · ".join(current), " · ".join(request)) if part)
            if text != self.text():
                super().setText(text)
        self._fit_height()

    def _wrapped_filename(self, source):
        width = self.contentsRect().width() - 4
        if width <= 0:
            return source
        # Break even unspaced filenames without discarding any of their characters.
        layout = QTextLayout(source, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        encoded = source.encode("utf-16-le")
        lines = []
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            # QTextLine offsets count UTF-16 units, including surrogate pairs.
            start = line.textStart() * 2
            end = start + line.textLength() * 2
            lines.append(encoded[start:end].decode("utf-16-le"))
        layout.endLayout()
        return "\n".join(lines)

    def _fit_height(self):
        metrics = self.fontMetrics()
        width = self.contentsRect().width() - 4
        height = metrics.lineSpacing() * 4
        if width > 0:
            bounds = metrics.boundingRect(QRect(0, 0, width, 16777215),
                                          int(Qt.TextFlag.TextWordWrap), self.text())
            height = max(height, bounds.height())
        height += 8
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._refresh_task()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, "_task"):
            self._refresh_task()


def task_tone(text):
    if any(term in text for term in ("未翻译", "部分", "暂停", "取消", "中断")):
        return "warning"
    if any(term in text for term in ("失败", "异常", "错误")):
        return "error"
    if "完成" in text:
        return "success"
    if text in ("待处理", "排队", "等待中"):
        return "secondary"
    return "accent"


class ArrangementButton(QRadioButton):
    def __init__(self, label, side_by_side):
        super().__init__(label)
        self.side_by_side = side_by_side
        self.setObjectName("arrangement")
        self.setMinimumHeight(34)
        self.setMinimumWidth(130)
        self.setAccessibleName(label)
        self.setToolTip("每页左侧原文、右侧译文" if side_by_side else "原文页与译文页交替排列")

    def hitButton(self, point):
        return self.rect().contains(point)


class DocumentPage(QWidget):
    start_requested = Signal(bool)
    output_directory_changed = Signal(str)
    pause_requested = Signal()
    cancel_requested = Signal()
    remove_requested = Signal()
    preferences_changed = Signal(dict)
    graphics_requested = Signal(str)
    graphics_refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profiles = {}
        self.active_id = ""
        self._details_owner = None
        self._column_trees = {}
        self._auto_columns = {"files": True, "history": True}
        self._setting_columns = False
        self._column_save_timer = QTimer(self)
        self._column_save_timer.setSingleShot(True)
        self._column_save_timer.setInterval(180)
        self._column_save_timer.timeout.connect(lambda: self.preferences_changed.emit(self.preferences()))
        self._column_fit_timer = QTimer(self)
        self._column_fit_timer.setSingleShot(True)
        self._column_fit_timer.timeout.connect(self._fit_auto_columns)
        self._details_reveal_timer = QTimer(self)
        self._details_reveal_timer.setSingleShot(True)
        self._details_reveal_timer.setInterval(0)
        self._details_reveal_timer.timeout.connect(self._reveal_details)
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(12)
        title_row = QHBoxLayout()
        title = QLabel("文件翻译")
        title.setObjectName("pageTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.add_button = QPushButton("添加文件")
        self.add_button.setIcon(qta.icon("mdi6.file-plus-outline"))
        self.add_button.clicked.connect(self.choose_files)
        self.rows = {}
        self.busy = False
        self.font_confirmed = True
        self.graphics_figures = self.graphics_tables = True
        self.clear_button = QPushButton("移除所选")
        self.clear_button.setIcon(qta.icon("mdi6.trash-can-outline"))
        self.clear_button.clicked.connect(self.remove_requested)
        self.clear_button.setToolTip("仅移除列表记录，不删除磁盘文件；处理中的文件请先取消")
        self.trial_button = QPushButton("试译")
        self.trial_button.clicked.connect(lambda: self.start_requested.emit(True))
        self.start_button = QPushButton("开始翻译")
        self.start_button.setObjectName("primary")
        self.start_button.setIcon(qta.icon("mdi6.translate"))
        self.start_button.clicked.connect(lambda: self.start_requested.emit(False))
        self.pause_button = QPushButton("暂停")
        self.pause_button.setIcon(qta.icon("mdi6.pause"))
        self.pause_button.clicked.connect(self.pause_requested)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.cancel_requested)
        from linguafloat.ui_layout import ActionBar
        self.import_actions = ActionBar((self.add_button, self.clear_button))
        self.start_button.setIcon(qta.icon("mdi6.translate", color="#ffffff"))
        title_row.addWidget(self.import_actions)
        outer.addLayout(title_row)
        self.status = TaskSummary("等待添加文件")
        self.status.setObjectName("documentStatus")
        self.batch_status = QLabel("共 0 个文件")
        self.progress = QProgressBar()
        self.progress.setObjectName("taskProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(5)
        self.progress.setTextVisible(False)
        summary = QFrame()
        summary.setObjectName("taskSummary")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(14, 10, 14, 10)
        summary_layout.setSpacing(6)
        self.batch_status.setObjectName("muted")
        self.batch_status.setWordWrap(True)
        summary_layout.addWidget(self.status)
        summary_layout.addWidget(self.batch_status)
        summary_layout.addWidget(self.progress)
        outer.addWidget(summary)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 4)
        layout.setSpacing(12)
        self.files = QTreeWidget()
        self.files.setObjectName("pendingFiles")
        self.files.setAlternatingRowColors(True)
        self.files.setHeaderLabels(["文件名", "格式", "大小", "状态 / 进度"])
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.files.setRootIsDecorated(False)
        self.files.setMinimumHeight(156)
        self.files.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._configure_columns(self.files, "files")
        self.files.itemDoubleClicked.connect(self.open_item)
        self.files.itemSelectionChanged.connect(lambda: self.show_details(self.files.selectedItems()[0].data(0,Qt.ItemDataRole.UserRole+1)) if self.files.selectedItems() else None)
        files_shell = QWidget()
        files_stack = QStackedLayout(files_shell)
        files_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        files_stack.setContentsMargins(0, 0, 0, 0)
        files_stack.addWidget(self.files)
        self.files_empty = QFrame()
        self.files_empty.setObjectName("emptyFiles")
        self.files_empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        empty_layout = QVBoxLayout(self.files_empty)
        empty_layout.setContentsMargins(20, 24, 20, 24)
        empty_layout.setSpacing(6)
        empty_layout.addStretch()
        empty_title = QLabel("还没有待翻译文件")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        empty_layout.addWidget(empty_title)
        empty_hint = QLabel("拖入 PDF、Word、PPT 或图片，或点击上方“添加文件”")
        empty_hint.setObjectName("muted")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_hint.setWordWrap(True)
        empty_layout.addWidget(empty_hint)
        empty_layout.addStretch()
        files_stack.addWidget(self.files_empty)
        self.files_empty.raise_()
        self._refresh_empty_state()
        self.file_tabs = QTabWidget()
        self.file_tabs.setDocumentMode(True)
        self.file_tabs.addTab(files_shell, "待翻译文件")
        self.history = QTreeWidget()
        self.history.setRootIsDecorated(False)
        self.history.setHeaderLabels(["文件名", "类型", "大小", "耗时", "输入 Token", "输出 Token", "状态"])
        self.history.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.history.setMinimumHeight(156)
        self.history.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.history.setAlternatingRowColors(True)
        self._configure_columns(self.history, "history")
        self.history.itemSelectionChanged.connect(self.show_history_details)
        self.history.itemDoubleClicked.connect(self.open_history_result)
        self.file_tabs.addTab(self.history, "历史记录")
        self.reset_columns = QToolButton()
        self.reset_columns.setIcon(qta.icon("mdi6.table-column-width", color=COLORS["secondary"]))
        self.reset_columns.setAccessibleName("自适应列宽")
        self.reset_columns.setCheckable(True)
        self.reset_columns.setChecked(True)
        self.reset_columns.setFixedSize(32, 32)
        self.reset_columns.setToolTip("自适应列宽：随窗口铺满当前列表；手动调整后保留固定列宽")
        self.reset_columns.clicked.connect(self.reset_current_columns)
        self.file_tabs.setCornerWidget(self.reset_columns)
        self.file_tabs.currentChanged.connect(self._sync_column_button)
        self.file_tabs.currentChanged.connect(lambda _: self._column_fit_timer.start(0))
        layout.addWidget(self.file_tabs, 1)
        self.export_process = None
        self._export_id = None
        self._export_pdf = None
        self._export_records = {}
        self._export_locations = {}
        self.export_section = QWidget()
        export_layout = QVBoxLayout(self.export_section)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.setSpacing(6)
        export_layout.addWidget(section_heading("译文导出", "mdi6.export-variant"))
        export_row = QHBoxLayout()
        self.export_picker = QPushButton("选择历史结果")
        self.export_picker.setIcon(qta.icon("mdi6.history"))
        self.export_picker.clicked.connect(self.pick_export_result)
        export_row.addWidget(self.export_picker)
        self.export_pdf_picker = QPushButton('选择 PDF')
        self.export_pdf_picker.setIcon(qta.icon('mdi6.file-find-outline'))
        self.export_pdf_picker.clicked.connect(self.pick_export_pdf)
        export_row.addWidget(self.export_pdf_picker)
        self.export_source = ExportSelectionLabel()
        export_row.addWidget(self.export_source, 1)
        self.export_buttons = {}
        for kind, title, icon in (("pdf", "PDF", "file-pdf-box"), ("word", "Word", "file-word-outline"),
                                  ("markdown", "Markdown", "language-markdown-outline")):
            button = QPushButton(title)
            button.setIcon(qta.icon(f"mdi6.{icon}"))
            button.clicked.connect(lambda _, kind=kind: self.export_history(kind))
            self.export_buttons[kind] = button
            export_row.addWidget(button)
        export_layout.addLayout(export_row)
        self.export_status = QLabel()
        self.export_status.setTextFormat(Qt.TextFormat.PlainText)
        self.export_status.setObjectName("muted")
        self.export_status.setWordWrap(True)
        status_row = QHBoxLayout()
        status_row.addWidget(self.export_status, 1)
        self.export_locate = QPushButton("定位结果文件")
        self.export_locate.setIcon(qta.icon("mdi6.folder-search-outline"))
        self.export_locate.clicked.connect(self.locate_export_result)
        status_row.addWidget(self.export_locate)
        export_layout.addLayout(status_row)
        layout.addWidget(self.export_section)
        self.refresh_export_control()
        self._review_sources = {}
        self.review = ReviewPanel(self, worker_command)
        layout.addWidget(self.review)
        self.report = QTextBrowser()
        self.report.setObjectName("taskDetails")
        self.report.setFixedHeight(112)
        self.report.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.report.setOpenExternalLinks(False)
        self.report.setPlaceholderText("选择任务可查看处理阶段、用量和问题详情")
        layout.addWidget(self.report)
        details_actions = QHBoxLayout()
        self.expand_details = QCheckBox("展开详情")
        self.expand_details.toggled.connect(lambda enabled: self.report.setFixedHeight(280 if enabled else 112))
        self.copy_details = QPushButton("复制详情")
        self.copy_details.clicked.connect(self.copy_report)
        details_actions.addWidget(self.expand_details)
        details_actions.addStretch()
        self.graphics_button = QPushButton("图表翻译")
        self.graphics_button.setIcon(qta.icon("mdi6.chart-box-outline"))
        self.graphics_button.setToolTip("从正文结果选择任务，按需补译图内文字和独立表格。")
        self.graphics_button.clicked.connect(self.request_graphics)
        details_actions.addWidget(self.copy_details)
        layout.addLayout(details_actions)
        self.report.document().contentsChanged.connect(self._show_report_controls)
        self._show_report_controls()
        layout.addWidget(section_heading("翻译设置", "mdi6.translate"))
        form = FieldGrid()
        language_row = QHBoxLayout()
        self.source_language = QComboBox()
        self.target = QComboBox()
        for code, name in LANGUAGES.items():
            self.source_language.addItem(name, code)
            self.target.addItem(name, code)
        self.source_language.setCurrentIndex(self.source_language.findData("en"))
        language_row.addWidget(self.source_language, 1)
        language_row.addWidget(QLabel("→"))
        language_row.addWidget(self.target, 1)
        form.add_field("原文 / 译文", language_row)
        self.pages = QLineEdit()
        self.pages.setPlaceholderText("全部页，或 1-3,5")
        self.pages.setToolTip("留空处理全部页面；例如 1-3,5。Word 不支持页码范围。")
        form.add_field("页码范围", self.pages)
        self.pdf_mode = QComboBox()
        self.pdf_mode.addItem("阅读版 / 本地 OCR", "reading")
        self.pdf_mode.addItem("保留版式", "layout")
        self.pdf_mode.setToolTip("阅读版：重新排版并支持长译文续页。保留版式：保持原页尺寸；扫描件仅替换可验证区域，未处理处保留原文并报告。")
        self.pdf_mode.setCurrentIndex(self.pdf_mode.findData("layout"))
        form.add_field("译文排版", self.pdf_mode)
        fonts = QHBoxLayout()
        self.chinese_font = QComboBox()
        self.chinese_font.addItem("宋体", "simsun")
        self.chinese_font.addItem("微软雅黑", "msyh")
        self.latin_font = QComboBox()
        self.latin_font.addItem("Times New Roman", "times")
        fonts.addWidget(self.chinese_font)
        fonts.addWidget(self.latin_font)
        form.add_field("译文字体 · PDF 保留版式", fonts, wide=True)
        self.font_ack = QCheckBox("我已确认新任务使用以上字体；已有任务快照保持不变")
        self.font_ack.setVisible(False)
        self.font_ack.toggled.connect(self.acknowledge_fonts)
        form.add_field("", self.font_ack, wide=True)
        formats = QHBoxLayout()
        self.mono = QCheckBox("纯译文")
        self.bilingual = QCheckBox("双语对照")
        self.mono.setChecked(True)
        self.bilingual.setChecked(True)
        formats.addWidget(self.mono)
        formats.addWidget(self.bilingual)
        formats.addStretch()
        form.add_field("输出文件", formats)
        self.relayout_only = QCheckBox("仅重新排版")
        self.relayout_only.setToolTip("复用已有译文，不调用翻译 API")
        self.translate_graphics=QCheckBox("图片翻译（历史偏好）")
        self.translate_graphics.setToolTip("兼容旧版偏好；新正文不自动补译图表。")
        self.translate_graphics.hide()  # Retain the old preference; never auto-start graphics.
        options = ActionBar((self.relayout_only,))
        form.add_field("内容处理", options, wide=True)
        graphics_hint = QLabel("图表内部保留原文，独立题注随正文翻译。完成后可按需使用“图表翻译”。")
        graphics_hint.setWordWrap(True)
        graphics_hint.setObjectName("muted")
        form.add_field("图表处理", graphics_hint, wide=True)
        self.pdf_mode.currentIndexChanged.connect(self.refresh_graphics_controls)
        self.refresh_graphics_controls()
        arrangements = QHBoxLayout()
        self.arrangement_group = QButtonGroup(self)
        self.side_by_side = ArrangementButton("左右并排", True)
        self.alternating = ArrangementButton("原译交替", False)
        self.side_by_side.setChecked(True)
        for button in (self.side_by_side, self.alternating):
            self.arrangement_group.addButton(button)
            arrangements.addWidget(button)
        form.add_field("双语排列 · PDF / 扫描件", arrangements, wide=True)
        self.bilingual.toggled.connect(lambda enabled: [b.setEnabled(enabled and not self.busy) for b in (self.side_by_side, self.alternating)])
        directory = QHBoxLayout()
        self.output_dir = QLineEdit(str(Path.home() / "Documents" / "LinguaFloat"))
        self.output_dir.editingFinished.connect(lambda: self.output_directory_changed.emit(self.output_dir.text()))
        self.browse = QPushButton()
        self.browse.setIcon(qta.icon("mdi6.folder-outline"))
        self.browse.setToolTip("选择输出文件夹")
        self.browse.clicked.connect(self.choose_output)
        directory.addWidget(self.output_dir, 1)
        directory.addWidget(self.browse)
        form.add_field("保存位置", directory, wide=True)
        layout.addWidget(form)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        footer = QWidget()
        footer.setObjectName("footer")
        bottom = QVBoxLayout(footer)
        bottom.setContentsMargins(0, 10, 0, 0)
        bottom.setSpacing(8)
        self.usage = QLabel("成功请求 0 · 缓存命中 0")
        self.usage.setObjectName("muted")
        self.usage.setWordWrap(True)
        self.open_button = QPushButton("打开结果文件夹")
        self.open_button.setIcon(qta.icon("mdi6.folder-open-outline"))
        self.open_button.clicked.connect(self.open_output)
        bottom.addWidget(self.usage)
        self.action_bar = ActionBar((self.trial_button, self.start_button,
                                    self.graphics_button, self.pause_button, self.cancel_button, self.open_button))
        bottom.addWidget(self.action_bar)
        outer.addWidget(footer)
        self.last_output = ""
        self.set_busy(False)
        self._column_fit_timer.start(0)

    def _show_report_controls(self):
        visible = bool(self.report.toPlainText().strip())
        for control in (self.report, self.expand_details, self.copy_details):
            control.setVisible(visible)

    def _minimum_columns(self, tree, name):
        samples = (["文件名.pdf", "DOCX", "999.9 MB", "翻译中 99%"] if name == "files" else
                   ["文件名.pdf", "DOCX", "999.9 MB", "99分59秒", "999,999", "999,999", "部分完成"])
        metrics = tree.fontMetrics()
        return [max(56, metrics.horizontalAdvance(sample) + 24,
                    tree.header().fontMetrics().horizontalAdvance(tree.headerItem().text(i)) + 24)
                for i, sample in enumerate(samples)]

    def _default_columns(self, tree, name):
        minimums = self._minimum_columns(tree, name)
        widths = minimums.copy()
        metrics = tree.fontMetrics()
        for column in range(1, tree.columnCount()):
            widest = max((metrics.horizontalAdvance(tree.topLevelItem(row).text(column)) + 24
                          for row in range(tree.topLevelItemCount())), default=minimums[column])
            if name == "files" and column == 3:
                widest = max([widest] + [metrics.horizontalAdvance(row["bar"].format()) + 24
                                         for row in self.rows.values()])
            widths[column] = max(minimums[column], min(260 if column == len(widths) - 1 else 180, widest))
        widths[0] = max(minimums[0], 280)
        available = tree.viewport().width()
        deficit = max(0, sum(widths) - available)
        # Shorten names first, then verbose statuses; keep numeric columns readable.
        for column in (0, len(widths) - 1, *range(1, len(widths) - 1)):
            reduction = min(deficit, widths[column] - minimums[column])
            widths[column] -= reduction
            deficit -= reduction
        surplus = max(0, available - sum(widths))
        widths[0] += surplus * 3 // 5
        widths[-1] += surplus - surplus * 3 // 5
        return widths

    def _apply_columns(self, tree, widths):
        self._setting_columns = True
        try:
            for column, width in enumerate(widths):
                tree.setColumnWidth(column, width)
        finally:
            self._setting_columns = False

    def _fit_auto_columns(self):
        for name, tree in self._column_trees.items():
            if self._auto_columns[name] and tree.isVisible():
                self._apply_columns(tree, self._default_columns(tree, name))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FontChange):
            self._column_fit_timer.start(0)
        return super().eventFilter(watched, event)

    def _sync_column_button(self, *_):
        if not hasattr(self, "reset_columns"):
            return
        name = "history" if self.file_tabs.currentWidget() is self.history else "files"
        self.reset_columns.setChecked(self._auto_columns[name])

    def _column_resized(self, name, column, width):
        if self._setting_columns:
            return
        self._auto_columns[name] = False
        tree = self._column_trees[name]
        minimum = self._minimum_columns(tree, name)[column]
        if width < minimum:
            widths = [tree.columnWidth(i) for i in range(tree.columnCount())]
            widths[column] = minimum
            self._apply_columns(tree, widths)
        self._sync_column_button()
        self._column_save_timer.start()

    def _configure_columns(self, tree, name):
        self._column_trees[name] = tree
        header = tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(56)
        header.setMaximumSectionSize(4096)
        header.setToolTip("拖动分隔线调整列宽；双击分隔线适配内容")
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._apply_columns(tree, self._default_columns(tree, name))
        tree.viewport().installEventFilter(self)
        tree.installEventFilter(self)
        header.sectionResized.connect(lambda column, _old, width: self._column_resized(name, column, width))
        header.sectionHandleDoubleClicked.connect(lambda column: self.fit_column(tree, column))

    def fit_column(self, tree, column):
        name = "history" if tree is self.history else "files"
        self._auto_columns[name] = False
        self._sync_column_button()
        self._column_save_timer.start()
        tree.resizeColumnToContents(column)
        # A height-only item size hint can hide its text width from Qt's sizing.
        metrics = tree.fontMetrics()
        text_width = max((metrics.horizontalAdvance(tree.topLevelItem(row).text(column)) + 32
                          for row in range(tree.topLevelItemCount())), default=0)
        width = max(tree.columnWidth(column), text_width,
                    tree.header().fontMetrics().horizontalAdvance(tree.headerItem().text(column)) + 32)
        if tree is self.files and column == 3:
            width = max([width] + [tree.fontMetrics().horizontalAdvance(row['bar'].format()) + 32
                                  for row in self.rows.values()])
        tree.setColumnWidth(column, width)

    def reset_current_columns(self):
        name = "history" if self.file_tabs.currentWidget() is self.history else "files"
        tree = getattr(self, name)
        self._auto_columns[name] = True
        self._apply_columns(tree, self._default_columns(tree, name))
        self._sync_column_button()
        self._column_fit_timer.start(0)
        self._column_save_timer.start()

    def set_history(self, records, active_job_id=None):
        selected = self.history.currentItem()
        selected_id = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        self.history.blockSignals(True)
        self.history.clear()
        self._export_records.clear()
        self._review_sources.clear()
        for identity, source, state, updated, raw in records:
            info = json.loads(raw)
            label = {"completed": "完成", "partial": "部分完成", "failed": "失败", "cancelled": "取消"}.get(state,
                     "处理中" if identity == active_job_id else "中断")
            if state == "completed" and info.get("untranslated_regions"):
                label = f"已完成，{info['untranslated_regions']} 处未翻译"
            size = info.get("file_size")
            size_text = "未记录" if size is None else (f"{size / 1024:.1f} KB" if size < 1024 ** 2 else f"{size / 1024 ** 2:.1f} MB")
            elapsed = info.get("elapsed_seconds")
            elapsed_text = "进行中" if identity == active_job_id and state == "running" else "未记录"
            if elapsed is not None:
                minutes, seconds = divmod(int(elapsed), 60)
                elapsed_text = f"{minutes}分{seconds:02d}秒" if minutes else f"{elapsed:.1f}秒"
            total = info.get("total_tokens")
            unknown = info.get("unreported_requests", 0)
            token_text = "未记录" if total is None else f"{total:,}"
            if unknown:
                token_text = f"{total:,} 部分" if info.get("reported_requests") else "用量未返回"
            item = QTreeWidgetItem([Path(source).name, info.get("file_type", Path(source).suffix[1:].upper()),
                                   size_text, elapsed_text, *billing_display(info, compact=True)[:2], ("试译" if info.get("trial") else "") + label])
            item.setData(0, Qt.ItemDataRole.UserRole, identity)
            eligible = getattr(self, "graphics_eligibility", lambda _: False)(identity)
            item.setData(0, Qt.ItemDataRole.UserRole + 3, bool(eligible))
            item.setForeground(6, QColor(COLORS[task_tone(label)]))
            item.setData(0, Qt.ItemDataRole.UserRole + 1, info)
            stamp = datetime.fromtimestamp(info.get("started_at", updated)).strftime("%Y-%m-%d %H:%M:%S")
            if info.get("outputs"):
                self._export_records[identity] = (Path(source).name, stamp, info)
                self._review_sources[identity] = source
            detail = f"{source}\n开始时间：{stamp}\n状态：{label}\n耗时：{elapsed_text}（包含暂停；不含排队）"
            detail += f"\n输入 Token：{info.get('input_tokens', '未记录')}\n输出 Token：{info.get('output_tokens', '未记录')}\n合计 Token：{token_text}"
            if unknown:
                detail += f"\n用量不完整：{unknown} 次请求未返回完整用量，上述数值仅为已知部分，请以供应商账单为准。"
            if total == 0 and not unknown:
                detail += "\n本次没有已知 API Token 消耗（本地缓存不重复计费）。"
            detail += "\n" + "\n".join([*info.get("outputs", []), *info.get("warnings", []), *info.get("issues", []), info.get("message", "")])
            if info.get("configuration"):
                configuration = info["configuration"]
                detail += (f"\n任务配置：{configuration.get('provider_name') or configuration.get('provider_id')} · "
                           f"{configuration.get('model')} · {configuration.get('api_mode')}"
                           f"\n页码：{configuration.get('pages') or '全部'}；仅重新排版：{configuration.get('relayout_only')}"
                           f"\n接口指纹：{configuration.get('endpoint_sha256', '')[:12]}")
            for name, path in info.get("runtime", {}).items():
                detail += f"\n{name}：{path}"
            performance = info.get("performance", {})
            if performance:
                detail += "\n\n分阶段有效耗时（排除暂停）：\n" + "\n".join(
                    f"{name}：{seconds:.2f} 秒" for name, seconds in performance.get("phases", {}).items())
                detail += "\n操作累计耗时（并发时有重叠，不与阶段相加）：\n" + "\n".join(
                    f"{name}：{seconds:.2f} 秒" for name, seconds in performance.get("operations", {}).items()
                    if name not in {"HTTP 429 限流", "请求超时", "请求重试", "超时拆分恢复", "翻译分段数", "分段原文字符"})
                counts = performance.get("operation_counts", {})
                segments = counts.get("翻译分段数", 0)
                if segments:
                    average_chars = counts.get("分段原文字符", 0) / segments
                    detail += f"\n普通文本分段 {segments} 个，平均每段 {average_chars:.0f} 字符（版式引擎分段另计）"
                attempts = counts.get("HTTP 请求", 0)
                if attempts:
                    average = performance.get("operations", {}).get("HTTP 请求", 0) / attempts
                    detail += f"\nHTTP 请求 {attempts} 次，平均每次 {average:.2f} 秒（含失败尝试）"
                    detail += "\n" + "；".join(f"{name} {counts.get(name, 0)} 次" for name in
                        ("HTTP 429 限流", "请求超时", "请求重试", "超时拆分恢复"))
            item.setData(0, Qt.ItemDataRole.UserRole + 2, detail)
            item.setToolTip(0, source)
            for column in range(1, self.history.columnCount()):
                item.setToolTip(column, detail if column == 6 else item.text(column))
            self.history.addTopLevelItem(item)
            if identity == selected_id:
                self.history.setCurrentItem(item)
        self.history.blockSignals(False)
        if selected_id and self._details_owner == ("history", selected_id):
            self.show_history_details()
        self._column_fit_timer.start(0)
        self.refresh_graphics_controls()
        self.refresh_export_control()

    def show_history_details(self):
        item = self.history.currentItem()
        if item:
            self._details_owner = ("history", item.data(0, Qt.ItemDataRole.UserRole))
            self.report.setPlainText(item.data(0, Qt.ItemDataRole.UserRole + 2))
            self._details_reveal_timer.start()
        elif self.file_tabs.currentIndex() == 1:
            self._details_owner = None
            self.report.clear()

    def acknowledge_fonts(self, checked):
        self.font_confirmed = checked
        self.preferences_changed.emit(self.preferences())

    def request_graphics(self):
        if self.busy:
            return
        self.file_tabs.setCurrentIndex(1)
        self.graphics_refresh_requested.emit()
        for i in range(self.history.topLevelItemCount()):
            item = self.history.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole + 3):
                self.history.setCurrentItem(item)
                self.graphics_requested.emit(item.data(0, Qt.ItemDataRole.UserRole))
                return
        self.report.setPlainText("没有材料完整的正文结果可供图片补译。请检查原文件、正文输出和原任务配置。")

    def _reveal_details(self):
        # Run after the list's focus/selection handling has finished scrolling.
        if not self.isVisible() or not self.report.isVisibleTo(self):
            return
        self.scroll.widget().layout().activate()
        viewport = self.scroll.viewport()
        if viewport.height() <= 0:
            return
        top = self.report.mapTo(viewport, self.report.rect().topLeft()).y()
        height = min(self.report.height(), viewport.height())
        delta = top if top < 0 else max(0, top + height - viewport.height())
        if delta:
            scrollbar = self.scroll.verticalScrollBar()
            scrollbar.setValue(scrollbar.value() + delta)

    def copy_report(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.report.toPlainText())

    @staticmethod
    def billing_tooltip(info):
        headings = ("输入 Token", "输出 Token")
        return "<table cellspacing='8'><tr>" + "".join(f"<th>{label}</th>" for label in headings) + "</tr><tr>" + "".join(
            f"<td align='center'>{html.escape(value)}</td>" for value in billing_display(info)[:2]) + "</tr></table>"

    def update_billing(self, row_id, info):
        row = self.rows.get(row_id)
        if not row:
            return
        row.setdefault("billing", {}).update(info)
        tip = html.escape(row['item'].text(3)) + "<br>" + self.billing_tooltip(row["billing"])
        row["item"].setToolTip(3, tip)
        row["bar"].setToolTip(tip)
        row["bar"].label.setToolTip(tip)

    def open_history_result(self, item, _=0):
        info = item.data(0, Qt.ItemDataRole.UserRole + 1)
        outputs = info.get("outputs", [])
        if outputs and Path(outputs[0]).parent.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(outputs[0]).parent)))

    @staticmethod
    def export_version(info):
        return "图表补译版" if info.get("workflow") == "graphics" else "正文版"

    def pick_export_result(self):
        if not self._export_records:
            self.show_export_status("没有已保存的历史结果。")
            return
        from linguafloat.workbench import AppDialog

        dialog = AppDialog(self)
        dialog.setWindowTitle("选择历史结果")
        dialog.resize(min(780, max(570, self.window().width() - 64)), 405)
        layout = QVBoxLayout(dialog)
        tree = QTreeWidget()
        tree.setObjectName("exportHistory")
        tree.setAccessibleName("历史译文")
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        tree.setHeaderLabels(["文件名", "版本", "开始时间", "可导出格式"])
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for identity, (name, stamp, _) in self._export_records.items():
            info = self.export_result_info(identity)
            formats = self.export_formats(info, identity)
            summary = self.export_format_labels(formats) or "结果文件缺失"
            item = QTreeWidgetItem([name, self.export_version(info), stamp, summary])
            item.setData(0, Qt.ItemDataRole.UserRole, identity)
            item.setToolTip(0, name)
            tree.addTopLevelItem(item)
            if identity == self._export_id:
                tree.setCurrentItem(item)
        if tree.currentItem() is None:
            tree.setCurrentItem(tree.topLevelItem(0))
        layout.addWidget(tree, 1)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        choose = actions.button(QDialogButtonBox.StandardButton.Ok)
        choose.setText("选择")
        choose.setObjectName("primary")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        tree.itemDoubleClicked.connect(lambda *_: dialog.accept())
        layout.addWidget(actions)
        if dialog.exec() == QDialog.DialogCode.Accepted and tree.currentItem():
            self.select_export_result(tree.currentItem().data(0, Qt.ItemDataRole.UserRole))

    def select_export_result(self, identity):
        self._export_id = identity
        self._export_pdf = None
        self.show_export_status("")
        self.refresh_export_control()
        self.review.refresh()

    def pick_export_pdf(self):
        selected, _ = QFileDialog.getOpenFileName(self, '选择 PDF', self.output_dir.text(), 'PDF (*.pdf)')
        if selected:
            self._export_id = None
            self._export_pdf = Path(selected)
            self.show_export_status('')
            self.refresh_export_control()
            self.review.refresh()

    def export_result_info(self, identity):
        if identity not in self._export_records:
            return {}
        info = self._export_records[identity][2]
        configured = Path(self.output_dir.text())
        app_root = (Path(sys.executable).parent if getattr(sys, 'frozen', False)
                    else Path(__file__).resolve().parents[2])
        roots = (configured, configured.parent, app_root, app_root.parent, app_root.parent.parent)
        if located := self._export_locations.get(identity):
            resolved = resolve_result(identity, info, located=located)
            if resolved is not info:
                return resolved
        return resolve_result(identity, info, roots)

    def locate_export_result(self):
        identity = self._export_id
        if identity not in self._export_records or self.export_process is not None:
            return
        info = self._export_records[identity][2]
        folder = Path(self.output_dir.text())
        selected, _ = QFileDialog.getOpenFileName(self, "定位结果文件", str(folder), "译文文件 (*.pdf *.docx)")
        if not selected:
            return
        recovered = resolve_result(identity, info, located=selected)
        if recovered is info:
            self.show_export_status("无法核实这是该任务的译文。请选择原任务的结果文件，并保留同目录的任务清单或编辑底稿。")
            return
        self._export_locations[identity] = Path(selected)
        self.refresh_export_control()

    @staticmethod
    def export_formats(info, identity=None):
        options = export_choices(info, identity)
        return {
            "pdf": {key: entry for key, entry in options.items() if entry[1].lower() == ".pdf"},
            "word": {"docx": options["docx"]} if "docx" in options else
                    {key: entry for key, entry in options.items() if entry[1].lower() == ".docx"},
            "markdown": {"markdown": options["markdown"]} if "markdown" in options else {},
        }

    @staticmethod
    def export_format_labels(formats):
        md = formats["markdown"].get("markdown")
        labels = ("PDF" if formats["pdf"] else "", "Word" if formats["word"] else "",
                  ("Markdown (ZIP)" if md[1] == ".zip" else "Markdown") if md else "")
        return " / ".join(label for label in labels if label)

    def show_export_status(self, text, tooltip=""):
        self.export_status.setText(text)
        self.export_status.setToolTip(tooltip)
        self.export_status.setVisible(bool(text))

    def refresh_export_control(self):
        identity = self._export_id
        record = self._export_records.get(identity)
        pdf = self._export_pdf
        info = {'outputs': [str(pdf)]} if pdf else self.export_result_info(identity)
        source = pdf.name if pdf else (f"{record[0]} · {self.export_version(info)}" if record else "")
        self.export_source.set_result(source)
        formats = self.export_formats(info, identity)
        idle = self.export_process is None
        self.export_picker.setEnabled(idle)
        self.export_pdf_picker.setEnabled(idle)
        missing = [Path(path) for path in info.get("outputs", []) if not Path(path).is_file()]
        self.export_locate.setVisible(bool(missing and record))
        self.export_locate.setEnabled(idle)
        for key, button in self.export_buttons.items():
            button.setEnabled(idle and bool(formats[key]))
        markdown = formats["markdown"].get("markdown")
        fallback = bool(markdown and markdown[0].startswith('从 PDF'))
        self.export_buttons["markdown"].setToolTip("含图片资源时导出 ZIP" if fallback or (markdown and markdown[1] == '.zip') else '')
        if not idle:
            return
        if missing:
            missing_label = "译文 PDF" if all(path.suffix.lower() == ".pdf" for path in missing) else "译文文件"
            reason = f"找不到部分{missing_label}。" if any(formats.values()) else f"找不到{missing_label}。"
            self.show_export_status(reason)
        elif record and not any(formats.values()):
            self.show_export_status("没有可另存的结果文件。")
        elif not record and not pdf:
            self.show_export_status("")
        elif not self.export_status.text().startswith(("已导出：", "未导出：", "目标文件已存在")):
            self.show_export_status('从 PDF 恢复可编辑文本，不重新翻译。' if fallback else
                                    "可导出：" + self.export_format_labels(formats))

    def export_history(self, format_key):
        identity = self._export_id
        record = self._export_records.get(identity)
        pdf = self._export_pdf
        if (not record and not pdf) or self.export_process is not None:
            return
        info = {'outputs': [str(pdf)]} if pdf else self.export_result_info(identity)
        options = self.export_formats(info, identity)[format_key]
        if not options:
            self.refresh_export_control()
            return
        version = self.export_version(info)
        stem = pdf.stem if pdf else Path(record[0]).stem
        if len(options) == 1:
            kind = next(iter(options))
        else:
            labels = {f"{i}. {entry[0]}": key for i, (key, entry) in enumerate(options.items(), 1)}
            label, accepted = QInputDialog.getItem(self, "导出译文", "选择结果文件", list(labels), 0, False)
            if not accepted:
                return
            kind = labels[label]
        label, suffix = options[kind]
        if pdf and not kind.startswith('output:'):
            scopes = {'整页（普通单语 PDF，包括双栏论文）': 'whole', '仅左侧（左右双语 PDF）': 'left',
                      '仅右侧（左右双语 PDF）': 'right'}
            chosen, accepted = QInputDialog.getItem(self, '转换范围', '选择要转换的内容', list(scopes), 0, False)
            if not accepted:
                return
            info['pdf_scope'] = scopes[chosen]
        first = Path(info["outputs"][int(kind.split(":")[1])] if kind.startswith("output:") else info["outputs"][0])
        name = (f"另存_{label}" if kind.startswith("output:") else
                (f"{stem}{suffix}" if pdf else f"{stem}.{version}.{identity[:8]}{suffix}"))
        dest, _ = QFileDialog.getSaveFileName(self, "导出译文", str(first.parent / name),
                                              f"{suffix[1:].upper()} (*{suffix})", options=QFileDialog.Option.DontConfirmOverwrite)
        if not dest:
            return
        if Path(dest).exists():
            self.show_export_status("目标文件已存在。原文件未改动。", dest)
            return
        process = QProcess(self)
        self.export_process = process
        self.refresh_export_control()
        python, entry = worker_command()
        process.setProgram(str(python))
        process.setArguments(["-X", "utf8", str(entry)])
        request = dict(operation="export_result", identity=identity, format=kind, destination=dest,
                       info={k: info[k] for k in ("outputs", "export_manuscript", "pdf_scope") if k in info})

        def started():
            process.write((json.dumps(request) + "\n").encode())
            process.closeWriteChannel()

        def done(*_):
            if self.export_process is not process:
                return
            try:
                result = json.loads(bytes(process.readAllStandardOutput()))
            except (ValueError, UnicodeError):
                result = {"message": process.errorString() or "未收到导出结果。"}
            self.export_process = None
            process.deleteLater()
            self.refresh_export_control()
            if result.get("type") == "exported":
                saved = Path(result.get('path', dest))
                note = result.get('note', '')
                self.show_export_status(f"已导出：{saved.name}" + (f"。{note}" if note else ''), str(saved))
            else:
                self.show_export_status("未导出：" + result.get("message", "未知错误"))

        process.started.connect(started)
        process.finished.connect(done)
        process.errorOccurred.connect(lambda error: done() if error == QProcess.ProcessError.FailedToStart else None)
        self.show_export_status("正在离线导出…")
        process.start()

    def set_profiles(self, profiles, active_id):
        self.profiles = {p.id: p for p in profiles}
        self.active_id = active_id
        profile = self.profiles.get(active_id)
        stamp = (profile.id, profile.base_url, profile.model, profile.api_mode) if profile else None
        if stamp != getattr(self, '_review_profile', None):
            self.review.ai.setChecked(False)
            self._review_profile = stamp
        self.graphics_refresh_requested.emit()

    def preferences(self):
        return dict(document_source_language=self.source_language.currentData(),
            document_target_language=self.target.currentData(),document_pdf_mode=self.pdf_mode.currentData(),
            document_translate_graphics=self.translate_graphics.isChecked(),
            document_chinese_font=self.chinese_font.currentData(), document_latin_font=self.latin_font.currentData(),
            document_font_confirmed=self.font_confirmed,
            document_graphics_figures=self.graphics_figures, document_graphics_tables=self.graphics_tables,
            document_mono=self.mono.isChecked(),
            document_bilingual=self.bilingual.isChecked(),
            document_bilingual_layout="side_by_side" if self.side_by_side.isChecked() else "alternating",
            document_column_widths={name: [getattr(self, name).columnWidth(i) for i in range(getattr(self, name).columnCount())]
                                    for name in ("files", "history") if not self._auto_columns[name]})

    def restore_preferences(self,settings):
        # Missing entries mean adaptive sizing; existing width lists stay manual.
        for name, tree in self._column_trees.items():
            widths = settings.document_column_widths.get(name)
            valid = (isinstance(widths, list) and len(widths) == tree.columnCount()
                     and all(type(width) is int and 56 <= width <= 4096 for width in widths))
            self._auto_columns[name] = not valid
            self._apply_columns(tree, widths if valid else self._default_columns(tree, name))
        self._sync_column_button()
        self._column_fit_timer.start(0)
        for widget,name in ((self.source_language,"document_source_language"),(self.target,"document_target_language"),
                            (self.pdf_mode,"document_pdf_mode")):
            widget.setCurrentIndex(max(0,widget.findData(getattr(settings,name))))
        self.translate_graphics.setChecked(settings.document_translate_graphics)
        self.chinese_font.setCurrentIndex(max(0, self.chinese_font.findData(settings.document_chinese_font)))
        self.font_confirmed = settings.document_font_confirmed
        self.font_ack.setChecked(self.font_confirmed)
        self.font_ack.setVisible(not self.font_confirmed)
        self.graphics_figures, self.graphics_tables = settings.document_graphics_figures, settings.document_graphics_tables
        self.mono.setChecked(settings.document_mono)
        self.bilingual.setChecked(settings.document_bilingual)
        (self.side_by_side if settings.document_bilingual_layout=="side_by_side" else self.alternating).setChecked(True)
        for widget in (self.source_language,self.target,self.pdf_mode,self.chinese_font,self.latin_font):
            widget.currentIndexChanged.connect(lambda: self.preferences_changed.emit(self.preferences()))
        for widget in (self.translate_graphics,self.mono,self.bilingual,self.side_by_side,self.alternating):
            widget.toggled.connect(lambda: self.preferences_changed.emit(self.preferences()))

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "添加待翻译文件", "", "文档 (*.pdf *.docx *.pptx *.png *.jpg *.jpeg *.tif *.tiff)")
        self.add_files(paths)

    def add_files(self, paths):
        previous_count = self.files.topLevelItemCount()
        existing = {self.files.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.files.topLevelItemCount())}
        for path in paths:
            path = str(Path(path).resolve())
            if path in existing or Path(path).suffix.lower() not in SUPPORTED:
                continue
            size = Path(path).stat().st_size if Path(path).is_file() else 0
            size_text = f"{size / 1024:.1f} KB" if size < 1024 ** 2 else f"{size / 1024 ** 2:.1f} MB"
            row_id = uuid.uuid4().hex
            item = QTreeWidgetItem([Path(path).name, Path(path).suffix[1:].upper(), size_text, "待处理"])
            item.setSizeHint(0, QSize(0, 56))
            item.setData(0, Qt.ItemDataRole.UserRole + 1, row_id)
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            item.setToolTip(0, path)
            item.setToolTip(1, item.text(1))
            item.setToolTip(2, size_text)
            self.files.addTopLevelItem(item)
            self.rows[row_id] = {"item": item, "state": "待处理", "value": 0}
            from linguafloat.config_dashboard import ElidedLabel
            label = ElidedLabel("待处理")
            cell = QWidget()
            # Index widgets must paint their own background over the item text.
            cell.setObjectName("taskCell")
            cell.setAutoFillBackground(True)
            cell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(8, 6, 8, 6)
            cell_layout.setSpacing(5)
            bar = TaskProgress(label)
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("待处理")
            cell_layout.addWidget(label)
            cell_layout.addWidget(bar)
            self.files.setItemWidget(item, 3, cell)
            self.rows[row_id]["bar"] = bar
            self.update_billing(row_id, {"usage_state": "pending"})
            existing.add(path)
        self.batch_status.setText(f"共 {self.files.topLevelItemCount()} 个文件")
        self._refresh_empty_state()
        if self.files.topLevelItemCount() > previous_count:
            if not self.busy:
                self.status.setText("已添加文件，等待开始")
                set_state(self.status, "idle")
            self.file_tabs.setCurrentIndex(0)
            if previous_count==0 and getattr(self,"_has_started_task",False):
                self.pages.clear()
                self.relayout_only.setChecked(False)

    def show_details(self, row_id):
        row = self.rows.get(row_id)
        if row:
            self._details_owner = ("files", row_id)
            self.report.setPlainText(row.get("details", row["state"]))
            self._details_reveal_timer.start()

    def update_row(self, row_id, state, value=None, details=None):
        row = self.rows.get(row_id)
        if not row:
            return
        row["state"] = state
        if value is not None:
            row["value"] = value
        if details is not None:
            row["details"] = details
            if self.file_tabs.currentIndex() == 0 and self._details_owner == ("files", row_id):
                self.report.setPlainText(details)
        row["item"].setText(3, state)
        bar = row["bar"]
        bar.setRange(0, 0 if value is None and state in {"解析中", "OCR 中", "翻译中", "排版中", "字体处理中", "保存中"} else 100)
        bar.setValue(row["value"])
        bar.setFormat(state + (f" {value}%" if value is not None else ""))
        tone = task_tone(state)
        set_state(bar, {"warning": "paused", "accent": "running"}.get(tone, tone))
        set_state(bar.label, {"warning": "paused", "accent": "running"}.get(tone, tone))
        self.update_billing(row_id, {})
        self._column_fit_timer.start(0)

    def remove_row(self, row_id):
        row = self.rows.pop(row_id, None)
        if row:
            self.files.takeTopLevelItem(self.files.indexOfTopLevelItem(row["item"]))
            self._refresh_empty_state()

    def _refresh_empty_state(self):
        if hasattr(self, "files_empty"):
            empty = self.files.topLevelItemCount() == 0
            self.files_empty.setVisible(empty)
            self._column_fit_timer.start(0)

    def dragEnterEvent(self, event):
        if self.add_button.isEnabled() and any(url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in SUPPORTED
                for url in event.mimeData().urls()):
            self.files_empty.setProperty("dragging", True)
            self.files_empty.style().polish(self.files_empty)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.files_empty.setProperty("dragging", False)
        self.files_empty.style().polish(self.files_empty)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.files_empty.setProperty("dragging", False)
        self.files_empty.style().polish(self.files_empty)
        if self.add_button.isEnabled():
            self.add_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()

    def choose_output(self):
        directory = QFileDialog.getExistingDirectory(self, "保存位置", self.output_dir.text())
        if directory:
            self.output_dir.setText(directory)
            self.output_directory_changed.emit(directory)

    def open_output(self):
        path = self.last_output or self.output_dir.text()
        if Path(path).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_item(self, item, _=0):
        self.show_details(item.data(0,Qt.ItemDataRole.UserRole + 1))
        path = item.data(1, Qt.ItemDataRole.UserRole)
        if path and Path(path).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def set_busy(self, busy):
        self.busy = busy
        if not busy and self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        for control in (self.add_button, self.start_button, self.trial_button,
                        self.target, self.source_language, self.pages,
                        self.pdf_mode, self.mono, self.bilingual, self.output_dir, self.browse, self.relayout_only,
                        self.chinese_font, self.latin_font, self.font_ack, self.graphics_button):
            control.setEnabled(not busy)
        for button in (self.side_by_side, self.alternating):
            button.setEnabled(not busy and self.bilingual.isChecked())
        self.pause_button.setEnabled(busy)
        self.cancel_button.setEnabled(busy)
        self.pause_button.setText("暂停")
        self.refresh_graphics_controls()

    def refresh_graphics_controls(self):
        enabled=not self.busy and self.pdf_mode.currentData()=="layout"
        self.translate_graphics.setEnabled(enabled)
        self.graphics_button.setEnabled(not self.busy and any(
            self.history.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole + 3)
            for i in range(self.history.topLevelItemCount())))


def worker_command():
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).parent
        return root / "document-runtime" / "python.exe", root / "document-runtime" / "worker_entry.py"
    root = Path(__file__).resolve().parents[2]
    return root / ".document-runtime" / "Scripts" / "python.exe", root / "scripts" / "document_worker_entry.py"


class DocumentController(QObject):
    def __init__(self, page, settings, credentials, database, parent=None):
        super().__init__(parent)
        self.page, self.settings, self.credentials = page, settings, credentials
        self.database = Path(database)
        page.document_database=self.database
        page.review.ai_key = self.review_credentials
        self.process = None
        self.queue = []
        self.paused = self.cancelled = self.terminal = False
        self.job = self.row_id = None
        self.batch_ids = []
        self.pending = b""
        self.total_requests = self.total_hits = 0
        self.job_requests = self.job_hits = 0
        page.restore_preferences(settings)
        if settings.document_output_dir:
            page.output_dir.setText(settings.document_output_dir)
        page.start_requested.connect(self.start)
        page.graphics_requested.connect(self.start_graphics)
        page.graphics_refresh_requested.connect(self.refresh_history)
        page.graphics_eligibility = self.graphics_record
        page.pause_requested.connect(self.pause)
        page.cancel_requested.connect(self.cancel)
        page.remove_requested.connect(self.remove_selected)
        page.file_tabs.currentChanged.connect(self._tab_changed)
        self._reconcile_timer = QTimer(self)
        self._reconcile_timer.setInterval(1000)
        self._reconcile_timer.timeout.connect(self._reconcile_process)
        self._reconcile_timer.start()
        if self.database.exists():
            store = JobStore(self.database)
            try:
                for source, status, details in reversed(store.recent()):
                    info = json.loads(details)
                    label = {"completed": "完成", "partial": "部分完成", "failed": "失败", "cancelled": "已取消"}.get(status, "中断，可重新添加并复用缓存")
                    if status == "completed" and info.get("untranslated_regions"):
                        label = f"已完成，{info['untranslated_regions']} 处未翻译"
                    if status in {"completed", "partial"} and info.get("outputs"):
                        page.last_output = str(Path(info["outputs"][0]).parent)
            finally:
                store.close()
        self.refresh_history()

    def _tab_changed(self, index):
        if index == 1:
            self.refresh_history()
            self.page.show_history_details()
        else:
            item = self.page.files.currentItem()
            row_id = item.data(0, Qt.ItemDataRole.UserRole + 1) if item else self.row_id
            if row_id in self.page.rows:
                self.page.show_details(row_id)
            else:
                self.page._details_owner = None
                self.page.report.clear()

    def _reconcile_process(self):
        # No heartbeat deadline: a long live request is not evidence of a dead worker.
        if self.process is not None and self.process.state() == QProcess.ProcessState.NotRunning:
            self.process_finished(process=self.process)

    def refresh_history(self):
        store = JobStore(self.database)
        try:
            self.page.set_history(store.history(), self.job.job_id if self.process and self.job else None)
        finally:
            store.close()
        self.page.review.refresh()

    def review_credentials(self):
        profile = self.page.profiles.get(self.page.active_id)
        if not profile:
            return None
        try:
            key = self.credentials.get_api_key(profile.id, profile.env_key)
        except Exception:
            return None
        return (asdict(profile), key) if key else None

    def start(self, trial=False):
        if getattr(self, "update_pending", False):
            self.page.status.setText("已准备安装更新，请等待当前批次完成；本次未创建新任务。")
            return
        if self.page.busy:
            return
        if not self.page.font_confirmed:
            self.page.report.setPlainText("升级后的新任务需先确认译文字体。请选择宋体或微软雅黑，并勾选字体升级确认；未创建任务。")
            return
        self.page.output_directory_changed.emit(self.page.output_dir.text())
        self.queue = []
        try:
            profile = copy.deepcopy(self.page.profiles[self.page.active_id])
            key = "" if self.page.relayout_only.isChecked() else self.credentials.get_api_key(profile.id, profile.env_key)
            if not key and not self.page.relayout_only.isChecked():
                raise DocumentError("当前 API 配置没有密钥，请先在配置设置中保存。")
            if not self.page.output_dir.text().strip():
                raise DocumentError("请选择输出目录。")
            for i in range(self.page.files.topLevelItemCount()):
                item = self.page.files.topLevelItem(i)
                layout_pdf = (self.page.pdf_mode.currentData()=="layout" and
                    Path(item.data(0,Qt.ItemDataRole.UserRole)).suffix.lower() not in {".docx",".pptx"})
                job = DocumentJob(source=item.data(0, Qt.ItemDataRole.UserRole), output_dir=self.page.output_dir.text().strip(),
                    provider=asdict(profile), target=self.page.target.currentData(), source_language=self.page.source_language.currentData(),
                    pages=self.page.pages.text(), trial=trial, mono=self.page.mono.isChecked(), bilingual=self.page.bilingual.isChecked(),
                    pdf_mode=self.page.pdf_mode.currentData(), job_id=uuid.uuid4().hex,
                    bilingual_layout="side_by_side" if self.page.side_by_side.isChecked() else "alternating",
                    request_timeout=profile.document_timeout,
                    relayout_only=self.page.relayout_only.isChecked(),
                    workflow="body" if layout_pdf else "legacy",
                    chinese_font=self.page.chinese_font.currentData() if layout_pdf else "",
                    latin_font=self.page.latin_font.currentData() if layout_pdf else "",
                    translate_tables=False,
                    translate_figures=False,
                    graphic_overrides={}, pricing={}, skip_references=True)
                job.validate()
                self.queue.append((job, key, item.data(0, Qt.ItemDataRole.UserRole + 1)))
            if not self.queue:
                raise DocumentError("请先添加文件。")
            python, entry = worker_command()
            if not python.is_file() or not entry.is_file():
                raise DocumentError("文档运行环境缺失，请使用包含 document-runtime 文件夹的完整便携版。")
        except Exception as exc:
            self.queue.clear()
            self.page.status.setText(str(exc) if isinstance(exc, DocumentError) else "无法读取当前配置的密钥，请在配置设置中检查。")
            set_state(self.page.status, "error")
            self.page.report.setPlainText("启动检查失败：" + self.page.status.text())
            return
        self.cancelled = self.paused = False
        self.page._has_started_task=True
        self.total_requests = self.total_hits = 0
        self.page.usage.setText("成功请求 0 · 缓存命中 0")
        self.page.report.clear()
        self.page._details_owner = ("files", self.row_id)
        self.page.set_busy(True)
        self.batch_ids = [row_id for _, _, row_id in self.queue]
        self.batch_results = {}
        for row_id in self.batch_ids:
            self.page.update_row(row_id, "待处理", 0, "等待处理")
        self.next_job()

    def graphics_record(self, identity, *, strict=False):
        """Use the same immutable artifact gate as the worker, without credentials."""
        store = JobStore(self.database)
        try:
            row = store.db.execute("SELECT source,status,details FROM jobs WHERE id=?", (identity,)).fetchone()
            if not row or row[1] not in {"completed", "partial"}:
                raise DocumentError("仅已完成或部分完成的正文任务可以补译图片。")
            source, _, raw = row
            info = json.loads(raw)
            configuration = info.get("configuration", {})
            if configuration.get("workflow") != "body" or configuration.get("parent_job_id") or info.get("parent_job_id"):
                raise DocumentError("请选择正文任务，不是图片补译子任务。")
            if not Path(source).is_file() or not any(Path(p).is_file() for p in info.get("outputs", [])):
                raise DocumentError("原文件或正文输出已缺失，不能启动图片补译。")
            profile = copy.deepcopy(self.page.profiles.get(configuration.get("provider_id")))
            if profile is None:
                raise DocumentError("原任务供应商配置不存在，请先恢复配置。")
            artifact = info.get("graphics_source", "")
            if not artifact:
                raise DocumentError("该旧任务未保存完整的图片补译材料；不会自动重翻正文。")
            from linguafloat.document_postprocess import validate_graphics_source
            manifest = validate_graphics_source(store, identity, artifact, source=source,
                provider=asdict(profile), verify_pdf=False)
            return source, info, profile, artifact, manifest
        except (DocumentError, OSError, ValueError, KeyError, TypeError):
            if strict:
                raise
            return None
        finally:
            store.close()

    def choose_graphics_history(self):
        self.refresh_history()
        dialog = QDialog(self.page)
        dialog.setWindowTitle("选择正文历史任务")
        dialog.resize(780, 380)
        layout = QVBoxLayout(dialog)
        tree = QTreeWidget()
        tree.setHeaderLabels(["文件名", "任务时间", "页码范围", "状态 / 任务编号"])
        tree.setRootIsDecorated(False)
        for i in range(self.page.history.topLevelItemCount()):
            original = self.page.history.topLevelItem(i)
            identity = original.data(0, Qt.ItemDataRole.UserRole)
            record = self.graphics_record(identity)
            if not record:
                continue
            source, info, _, _, manifest = record
            when = datetime.fromtimestamp(info['started_at']).strftime('%Y-%m-%d %H:%M:%S')
            item = QTreeWidgetItem([Path(source).name, when, manifest['selected_pages'] or '全部',
                                   original.text(6) + ' · ' + identity[:12]])
            item.setToolTip(0, source)
            item.setData(0, Qt.ItemDataRole.UserRole, identity)
            tree.addTopLevelItem(item)
        tree.setColumnWidth(0, 300)
        tree.setColumnWidth(1, 165)
        layout.addWidget(tree)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("选择")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        tree.itemSelectionChanged.connect(lambda: buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(tree.currentItem())))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and tree.currentItem():
            identity = tree.currentItem().data(0, Qt.ItemDataRole.UserRole)
            for i in range(self.page.history.topLevelItemCount()):
                item = self.page.history.topLevelItem(i)
                if item.data(0, Qt.ItemDataRole.UserRole) == identity:
                    self.page.history.setCurrentItem(item)
                    break
            return identity
        return None

    def start_graphics(self, identity):
        if self.page.busy or getattr(self, "update_pending", False):
            return
        try:
            source, info, profile, artifact, manifest = self.graphics_record(identity, strict=True)
            dialog = QDialog(self.page)
            dialog.setWindowTitle("图表翻译")
            dialog.setMinimumWidth(410)
            layout = QVBoxLayout(dialog)
            when = datetime.fromtimestamp(info['started_at']).strftime('%Y-%m-%d %H:%M:%S')
            description = QLabel(f"是否对以下正文结果进行图表翻译？\n{Path(source).name}\n"
                f"任务时间：{when} · {identity[:12]}\n页码范围：{manifest['selected_pages'] or '全部'}\n"
                "只处理勾选的内容；保留正文与题注，结果编号另存。")
            description.setWordWrap(True)
            description.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(description)
            figures = QCheckBox("图内文字")
            figures.setChecked(self.page.graphics_figures)
            layout.addWidget(figures)
            tables = QCheckBox("独立表格")
            tables.setEnabled(manifest.get('table_policy') != 'independent-tables-v1')
            tables.setChecked(tables.isEnabled() and self.page.graphics_tables)
            if not tables.isEnabled():
                tables.setToolTip("此历史正文已处理过表格，不重复覆盖。")
            layout.addWidget(tables)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
            yes = buttons.button(QDialogButtonBox.StandardButton.Yes)
            yes.setText("是")
            buttons.button(QDialogButtonBox.StandardButton.No).setText("否")
            custom = buttons.addButton("自定义文件", QDialogButtonBox.ButtonRole.ActionRole)
            custom.clicked.connect(lambda: dialog.done(2))
            def scope_changed():
                yes.setEnabled(figures.isChecked() or tables.isChecked())
            figures.toggled.connect(scope_changed)
            tables.toggled.connect(scope_changed)
            scope_changed()
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            result = dialog.exec()
            if result == 2:
                selected = self.choose_graphics_history()
                if selected:
                    self.start_graphics(selected)
                return
            if result != QDialog.DialogCode.Accepted:
                return
            # Recheck files and binding after the modal, before credentials/queue.
            source, info, profile, artifact, manifest = self.graphics_record(identity, strict=True)
            key = self.credentials.get_api_key(profile.id, profile.env_key)
            if not key:
                raise DocumentError("原任务供应商没有可用密钥，请在配置设置中检查。")
            job = DocumentJob(source=source, output_dir=str(Path(artifact).parent.parent), provider=asdict(profile),
                target=manifest["target"], source_language=manifest["source_language"], pages=manifest["selected_pages"],
                trial=manifest["trial"],
                pdf_mode="layout", workflow="graphics", parent_job_id=identity, graphics_source=artifact,
                chinese_font=manifest["chinese_font"], latin_font=manifest["latin_font"],
                mono=manifest["mono"], bilingual=manifest["bilingual"], bilingual_layout=manifest["bilingual_layout"],
                skip_references=manifest["skip_references"], reference_boundary=manifest["reference_boundary"],
                translate_figures=figures.isChecked(), translate_tables=tables.isChecked(),
                request_timeout=profile.document_timeout, job_id=uuid.uuid4().hex)
            job.validate()
            python, entry = worker_command()
            if not python.is_file() or not entry.is_file():
                raise DocumentError("完整文档运行环境缺失。")
            self.page.graphics_figures = figures.isChecked()
            if tables.isEnabled():
                self.page.graphics_tables = tables.isChecked()
            self.page.preferences_changed.emit(self.page.preferences())
            self.page.add_files([source])
            item = next(self.page.files.topLevelItem(i) for i in range(self.page.files.topLevelItemCount())
                        if Path(self.page.files.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)).resolve() == Path(source).resolve())
            row_id = item.data(0, Qt.ItemDataRole.UserRole + 1)
            self.queue = [(job, key, row_id)]
            self.cancelled = self.paused = False
            self.total_requests = self.total_hits = 0
            self.batch_ids, self.batch_results = [row_id], {}
            self.page.set_busy(True)
            self.next_job()
        except (DocumentError, KeyError, ValueError) as error:
            self.page.report.setPlainText(str(error))

    def update_batch(self):
        completed = sum(state == "完成" for state in self.batch_results.values())
        partial = sum(state == "部分完成" for state in self.batch_results.values())
        failed = sum(state == "失败" for state in self.batch_results.values())
        cancelled = sum(state == "取消" for state in self.batch_results.values())
        self.page.batch_status.setText(f"本批 {len(self.batch_ids)} 个 · 完成 {completed}"
                                       + (f" · 部分完成 {partial}" if partial else "")
                                       + f" · 失败 {failed} · 取消 {cancelled}")

    def remove_selected(self):
        protected = {identity for _, _, identity in self.queue}
        if self.process:
            protected.add(self.row_id)
        blocked = False
        for item in self.page.files.selectedItems():
            identity = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if identity in protected:
                blocked = True
                continue
            self.page.remove_row(identity)
        if blocked:
            self.page.status.setText("处理中或排队的文件请先取消，任务停止后再移除所选。磁盘文件不会删除。")

    @property
    def item(self):
        row = self.page.rows.get(self.row_id)
        return row["item"] if row else None

    def next_job(self):
        if self.cancelled or not self.queue:
            self.page.set_busy(False)
            if not self.cancelled:
                self.page.report.append("本批任务已结束。")
                if not self.page.rows and self.batch_results:
                    self.page.file_tabs.setCurrentIndex(1)
            return
        self.job, key, self.row_id = self.queue.pop(0)
        self.page.rows[self.row_id]["job_id"] = self.job.job_id
        store = JobStore(self.database)
        try:
            store.status(self.job, "running")
        finally:
            store.close()
        self.job_requests = self.job_hits = 0
        self.page.rows[self.row_id]["billing"] = {}
        self.page.report.clear()
        self.page.update_billing(self.row_id, {"usage_state": "waiting", "pricing": self.job.pricing})
        self.page.update_row(self.row_id, "解析中")
        self.update_batch()
        self.item.setData(1, Qt.ItemDataRole.UserRole, None)
        self.page.status.set_task(Path(self.job.source).name, "正在启动", "准备解析文档")
        set_state(self.page.status, "running")
        self.page.progress.setValue(0)
        self.pending = b""
        self.terminal = False
        python, entry = worker_command()
        process = QProcess(self)
        self.process = process
        self.refresh_history()
        process.setProgram(str(python))
        process.setArguments(["-I", "-u", str(entry)])
        process.readyReadStandardOutput.connect(lambda job_id=self.job.job_id: self.read_events(process, job_id))
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        payload = json.dumps({"job": asdict(self.job), "api_key": key, "database": str(self.database)}, ensure_ascii=False).encode() + b"\n"
        process.started.connect(lambda: process.write(payload))
        process.finished.connect(lambda *args: self.process_finished(*args, process=process))
        process.errorOccurred.connect(lambda error: self.process_error(error) if self.process is process else None)
        process.start()

    def _diagnostic(self, kind, error):
        if self.job is None:
            return
        try:
            store = JobStore(self.database)
            try:
                store.diagnostic_event(self.job, dict(component="document_ui", event_type=kind,
                                                       error_type=type(error).__name__))
            finally:
                store.close()
        except Exception:
            pass  # Diagnostics must never block terminal cleanup.

    def read_events(self, process=None, job_id=None):
        if self.process is None or process is not None and process is not self.process:
            return
        if job_id is not None and (self.job is None or job_id != self.job.job_id):
            return
        self.pending += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("invalid event")
                if event.get("job_id") is not None and event["job_id"] != self.job.job_id:
                    continue
                if self.terminal or self.cancelled:
                    continue
                self._handle_event(event)
            except Exception as error:
                self._diagnostic("ipc_event", error)
                # Keep draining: a malformed/failed event must not hide the terminal event.

    def _handle_event(self, event):
        kind = event.get("type")
        if kind == "progress" and not self.paused:
            value = event.get("value")
            value = min(99, max(0, int(value))) if value is not None else None
            scoped = event.get("progress_scope") == "stage"
            if value is not None and not scoped:
                value = max(value, self.page.rows.get(self.row_id, {}).get("value", 0))
            self.page.progress.setRange(0, 0 if value is None or scoped else 100)
            if value is not None and not scoped:
                self.page.progress.setValue(value)
            message = event.get("message", "处理中")
            stage = event.get("stage", "翻译中")
            self.page.update_row(self.row_id, stage, value, message)
            if scoped and value is not None:
                self.page.rows[self.row_id]["bar"].setFormat(f"{stage} · 阶段 {value}%")
            caption = stage
            if value is not None:
                caption += f" · {'阶段 ' if scoped else ''}{value}%"
            self.page.status.set_task(Path(self.job.source).name, caption, message)
        elif kind == "usage":
            self.page.update_billing(self.row_id, event)
            self.job_requests, self.job_hits = event["requests"], event["cache_hits"]
            self.page.usage.setText(f"成功请求 {self.total_requests + self.job_requests} · 缓存命中 {self.total_hits + self.job_hits}")
            if self.page.file_tabs.currentIndex() == 1:
                self.refresh_history()
        elif kind == "activity" and not self.paused:
            message = event.get("message", "等待 API 返回")
            if event.get("stage"):
                self.page.update_row(self.row_id, event["stage"], None, message)
                self.page.progress.setRange(0, 0)
            stage = event.get("stage") or self.page.rows.get(self.row_id, {}).get("state", "处理中")
            self.page.status.set_task(Path(self.job.source).name, stage, message)
            self.page.update_row(self.row_id, stage, details=message)
        elif kind == "finished":
            self.terminal = True
            retained = event.get("untranslated_regions", 0)
            self.page.update_billing(self.row_id, {"usage_state": "finished", **event.get("usage", {})})
            state = f"已完成，{retained} 处未翻译" if retained else ("试译完成" if self.job.trial else "已完成")
            if event.get('transport_partial'):
                state = f'部分完成，{retained} 处保留原文'
            self.page.update_row(self.row_id, state, 100,
                                 "\n".join([*event.get("outputs", []), *event.get("warnings", []), *event.get("issues", [])]))
            self.batch_results[self.row_id] = "部分完成" if retained or event.get('transport_partial') else "完成"
            self.update_batch()
            self.page.progress.setRange(0, 100)
            self.page.progress.setValue(100)
            notes = [*event.get("warnings", []), *event.get("issues", [])]
            self.page.status.set_task(Path(self.job.source).name, state,
                                      notes[0] if notes else f"已生成 {len(event.get('outputs', []))} 个结果文件")
            set_state(self.page.status, "paused" if retained or event.get('transport_partial') else "success")
            self.job_requests, self.job_hits = event.get("requests", 0), event.get("cache_hits", 0)
            self.page.usage.setText(f"成功请求 {self.total_requests + self.job_requests} · 缓存命中 {self.total_hits + self.job_hits}")
            if event.get("performance", {}).get("operation_counts", {}).get("译后中间数据缓存命中"):
                self.page.usage.setText(self.page.usage.text() + " · 已复用译后文档")
            paths = event.get("outputs", [])
            if paths:
                self.page.last_output = str(Path(paths[0]).parent)
                self.item.setData(1, Qt.ItemDataRole.UserRole, self.page.last_output)
                if (self.job.workflow == 'body' and self.job.source_language == 'en' and self.job.target == 'zh-Hans'
                        and Path(self.job.source).suffix.lower() == '.pdf'):
                    self.page.review.enqueue(self.job.job_id, self.job.source, event,
                                             allow_ai=not self.job.relayout_only)
            if self.page.file_tabs.currentIndex() == 0:
                self.page.show_details(self.row_id)
        elif kind in {"error", "cancelled"}:
            self.terminal = True
            self.page.progress.setRange(0, 100)
            self.page.progress.setValue(0)
            self.page.update_row(self.row_id, "失败" if kind == "error" else "已取消", details=event.get("message", "文件处理失败"))
            self.batch_results[self.row_id] = "失败" if kind == "error" else "取消"
            self.update_batch()
            message = event.get("message") or "文件处理失败"
            self.page.status.set_task(Path(self.job.source).name, "失败" if kind == "error" else "已取消", message.splitlines()[0])
            set_state(self.page.status, "error" if kind == "error" else "paused")
            if self.page.file_tabs.currentIndex() == 0:
                self.page.show_details(self.row_id)

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.page.status.setText("文档进程无法启动，请检查运行环境或安全软件拦截。")
            self.page.update_row(self.row_id, "启动失败", details=self.page.status.text())
            self.page.report.setPlainText(Path(self.job.source).name + " · 启动阶段：" + self.page.status.text())
            self.batch_results[self.row_id] = "失败"
            self.update_batch()
            self.terminal = True
            store = JobStore(self.database)
            try:
                store.status(self.job, "failed", {"message": self.page.status.text()})
            finally:
                store.close()
            self.process_finished()

    def process_finished(self, *_, process=None):
        if self.process is None or process is not None and process is not self.process:
            return
        try:
            self.read_events()
            self._restore_terminal()
        except Exception as error:
            self._diagnostic("process_finished", error)
        finally:
            process, self.process = self.process, None
            process.deleteLater()
            self.pending = b""
        self.total_requests += self.job_requests
        self.total_hits += self.job_hits
        if not self.terminal:
            status = "cancelled" if self.cancelled else "failed"
            self.page.update_row(self.row_id, "已取消" if self.cancelled else "进程异常退出")
            self.batch_results[self.row_id] = "取消" if self.cancelled else "失败"
            self.update_batch()
            if self.cancelled:
                self.page.status.setText("任务已取消，重新开始可复用缓存")
                set_state(self.page.status, "paused")
            if not self.cancelled:
                self.page.status.setText("文档进程异常退出，可重新开始并复用已完成缓存。")
                set_state(self.page.status, "error")
            try:
                store = JobStore(self.database)
                try:
                    # A persisted terminal record remains authoritative.
                    row = store.db.execute("SELECT status FROM jobs WHERE id=?", (self.job.job_id,)).fetchone()
                    if not row or row[0] not in {"failed", "completed", "partial", "cancelled"}:
                        store.status(self.job, status, {"message": self.page.status.text()})
                finally:
                    store.close()
            except Exception as error:
                self._diagnostic("terminal_persist", error)
            self.page.update_row(self.row_id, "已取消" if self.cancelled else "进程异常退出", details=self.page.status.text())
            self.page.report.setPlainText(Path(self.job.source).name + "：" + self.page.status.text())
        try:
            self.refresh_history()
        except Exception as error:
            self._diagnostic("history_refresh", error)
        if self.terminal and self.batch_results.get(self.row_id) in {"完成", "部分完成"}:
            self.page.remove_row(self.row_id)
        if not self.paused or self.cancelled or not self.queue:
            self.next_job()

    def _restore_terminal(self):
        store = JobStore(self.database)
        try:
            row = store.db.execute("SELECT status,details FROM jobs WHERE id=?", (self.job.job_id,)).fetchone()
            if row and self.cancelled and row[0] in {"failed", "completed", "partial"}:
                store.status(self.job, "cancelled", {"message": "任务已取消，已完成缓存保留"})
                row = store.db.execute("SELECT status,details FROM jobs WHERE id=?", (self.job.job_id,)).fetchone()
        finally:
            store.close()
        if not row or row[0] not in {"failed", "completed", "partial", "cancelled"}:
            return
        info = json.loads(row[1])
        self.job_requests = info.get("requests", self.job_requests)
        self.job_hits = info.get("cache_hits", self.job_hits)
        self.page.update_billing(self.row_id, info)
        self.page.usage.setText(f"成功请求 {self.total_requests + self.job_requests} · 缓存命中 {self.total_hits + self.job_hits}")
        kind = {"failed": "error", "completed": "finished", "partial": "finished", "cancelled": "cancelled"}[row[0]]
        if self.cancelled:
            kind = "cancelled"
        self._handle_event({**info, "type": kind, "usage": info,
                            "message": "任务已取消，已完成缓存保留" if self.cancelled else info.get("message", "任务已结束")})

    def pause(self):
        if self.process is None and not self.queue:
            return
        self.paused = not self.paused
        if self.process:
            self.process.write(json.dumps({"command": "pause" if self.paused else "resume"}).encode() + b"\n")
        self.page.pause_button.setText("继续" if self.paused else "暂停")
        if self.process and not self.terminal:
            self.page.update_row(self.row_id, "暂停" if self.paused else "翻译中")
        self.page.status.set_task(Path(self.job.source).name if self.job else "",
                                  "已暂停" if self.paused else "继续处理",
                                  "正在发送的请求完成后写入缓存" if self.paused else "")
        set_state(self.page.status, "paused" if self.paused else "running")
        if not self.paused and self.process is None:
            self.next_job()

    def cancel(self):
        self.cancelled = True
        self.paused = False
        for _, _, row_id in self.queue:
            self.page.update_row(row_id, "已取消")
            self.batch_results[row_id] = "取消"
        self.queue.clear()
        self.update_batch()
        self.page.status.set_task(Path(self.job.source).name if self.job else "",
                                  "正在取消", "已完成的段落缓存可供下次复用")
        set_state(self.page.status, "paused")
        if self.process and not self.terminal:
            self.page.update_row(self.row_id, "取消中")
        if self.process:
            process = self.process
            process.write(b'{"command":"cancel"}\n')
            QTimer.singleShot(2000, lambda: process.kill() if self.process is process and process.state() != QProcess.ProcessState.NotRunning else None)
        else:
            self.page.set_busy(False)

    def shutdown(self):
        self.page.review.shutdown()
        if self.process:
            self.cancel()
            if self.process and not self.process.waitForFinished(1500):
                self.process.kill()
                self.process.waitForFinished(2000)
