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
from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices, QPainter, QColor, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea, QTextBrowser,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QAbstractItemView, QRadioButton, QButtonGroup, QToolButton, QSpinBox, QDoubleSpinBox, QTabWidget)

from linguafloat.document_core import DocumentError, DocumentJob, JobStore, LANGUAGES, SUPPORTED, billing_display
from linguafloat.theme import section_heading, set_state
from linguafloat.ui_layout import FieldGrid


class ArrangementButton(QRadioButton):
    def __init__(self, label, side_by_side):
        super().__init__(label)
        self.side_by_side = side_by_side
        self.setMinimumHeight(108)
        self.setMinimumWidth(210)
        self.setAccessibleName(label)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#1976d2" if self.isChecked() else "#dce2e8"), 2 if self.isChecked() else 1))
        painter.setBrush(QColor("#eff6ff" if self.isChecked() else "#ffffff"))
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 6, 6)
        painter.setPen(QColor("#263445" if self.isEnabled() else "#84909c"))
        painter.drawText(self.rect().adjusted(12, 8, -8, -65), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         ("● " if self.isChecked() else "○ ") + self.text())
        from PySide6.QtCore import QRect
        for index, label in enumerate(("原文", "译文")):
            rect = QRect(14 + index * 88, 46, 78, 45) if self.side_by_side else QRect(14, 40 + index * 31, 120, 28)
            painter.setBrush(QColor("#edf1f5" if index == 0 else "#dbeafe"))
            painter.setPen(QColor("#69798b"))
            painter.drawRect(rect)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def hitButton(self, point):
        return self.rect().contains(point)


class DocumentPage(QWidget):
    start_requested = Signal(bool)
    output_directory_changed = Signal(str)
    pause_requested = Signal()
    cancel_requested = Signal()
    remove_requested = Signal()
    preferences_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profiles = {}
        self.active_id = ""
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 12, 18, 12)
        actions = QHBoxLayout()
        self.add_button = QPushButton("添加文件")
        self.add_button.setIcon(qta.icon("mdi6.file-plus-outline"))
        self.add_button.clicked.connect(self.choose_files)
        self.rows = {}
        self.busy = False
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
        for button in (self.add_button, self.clear_button, self.trial_button, self.start_button, self.pause_button, self.cancel_button):
            actions.addWidget(button)
        actions.addStretch()
        outer.addLayout(actions)
        self.status = QLabel("等待添加文件")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.batch_status = QLabel("共 0 个文件")
        outer.addWidget(self.batch_status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(12)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar { border: 0; background: #e9eef4; border-radius: 3px; }"
                                    "QProgressBar::chunk { background: #1976d2; border-radius: 3px; }")
        outer.addWidget(self.progress)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 4, 8, 4)
        self.files = QTreeWidget()
        self.files.setStyleSheet("QTreeWidget { background: white; border: 1px solid #e1e6ec; border-radius: 4px; }"
            "QHeaderView::section { background: #eef2f6; color: #4c5967; border: 0; padding: 6px 8px; }"
            "QTreeWidget::item { padding: 5px 8px; } QTreeWidget::item:selected { background: #e6f1fc; color: #24374a; }")
        self.files.setHeaderLabels(["文件名", "格式", "大小", "状态 / 进度"])
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.files.setRootIsDecorated(False)
        self.files.setMinimumHeight(150)
        self._configure_columns(self.files, "files")
        self.files.itemDoubleClicked.connect(self.open_item)
        self.files.itemSelectionChanged.connect(lambda: self.show_details(self.files.selectedItems()[0].data(0,Qt.ItemDataRole.UserRole+1)) if self.files.selectedItems() else None)
        self.file_tabs = QTabWidget()
        self.file_tabs.setDocumentMode(True)
        self.file_tabs.addTab(self.files, "待翻译文件")
        self.history = QTreeWidget()
        self.history.setRootIsDecorated(False)
        self.history.setHeaderLabels(["文件名", "类型", "大小", "耗时", "输入 Token", "输出 Token", "状态"])
        self.history.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.history.setMinimumHeight(180)
        self.history.setStyleSheet(self.files.styleSheet())
        self._configure_columns(self.history, "history")
        self.history.itemSelectionChanged.connect(self.show_history_details)
        self.history.itemDoubleClicked.connect(self.open_history_result)
        self.file_tabs.addTab(self.history, "历史记录")
        self.reset_columns = QToolButton()
        self.reset_columns.setText("恢复列宽")
        self.reset_columns.setToolTip("恢复当前列表的默认列宽；拖动表头分隔线调整，双击分隔线适配内容")
        self.reset_columns.clicked.connect(self.reset_current_columns)
        self.file_tabs.setCornerWidget(self.reset_columns)
        layout.addWidget(self.file_tabs)
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
        formats = QHBoxLayout()
        self.mono = QCheckBox("纯译文")
        self.bilingual = QCheckBox("双语对照")
        self.mono.setChecked(True)
        self.bilingual.setChecked(True)
        formats.addWidget(self.mono)
        formats.addWidget(self.bilingual)
        formats.addStretch()
        form.add_field("输出文件", formats)
        self.relayout_only = QCheckBox("仅重新排版（复用译文，不调用翻译 API）")
        form.add_field("处理方式", self.relayout_only, wide=True)
        self.translate_graphics=QCheckBox("翻译图表内容（表格＋图内文字）")
        self.translate_graphics.setToolTip("PDF / 图片保留版式：翻译表格和图内自然语言，保护数值、单位和公式。")
        form.add_field("图表翻译",self.translate_graphics,wide=True)
        self.skip_references = QCheckBox("参考文献及后续内容不翻译")
        self.skip_references.setToolTip("从正文参考文献标题起保留原文，包括后续附录；无法确认边界时停止任务。需翻译完整文档。")
        form.add_field("文末内容", self.skip_references, wide=True)
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
        self.report = QTextBrowser()
        self.report.setMinimumHeight(95)
        self.report.setMaximumHeight(140)
        self.report.setOpenExternalLinks(False)
        layout.addWidget(self.report)
        details_actions=QHBoxLayout()
        self.expand_details=QCheckBox("展开详情")
        self.expand_details.toggled.connect(lambda enabled:self.report.setMaximumHeight(480 if enabled else 140))
        self.copy_details=QPushButton("复制详情")
        self.copy_details.clicked.connect(self.copy_report)
        details_actions.addWidget(self.expand_details)
        details_actions.addStretch()
        details_actions.addWidget(self.copy_details)
        layout.addLayout(details_actions)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        bottom = QHBoxLayout()
        self.usage = QLabel("成功请求 0 · 缓存命中 0")
        self.open_button = QPushButton("打开结果文件夹")
        self.open_button.setIcon(qta.icon("mdi6.folder-open-outline"))
        self.open_button.clicked.connect(self.open_output)
        bottom.addWidget(self.usage, 1)
        bottom.addWidget(self.open_button)
        outer.addLayout(bottom)
        self.last_output = ""
        self.set_busy(False)
        self._column_save_timer = QTimer(self)
        self._column_save_timer.setSingleShot(True)
        self._column_save_timer.setInterval(180)
        self._column_save_timer.timeout.connect(lambda: self.preferences_changed.emit(self.preferences()))
        for tree in (self.files, self.history):
            tree.header().sectionResized.connect(lambda *_: self._column_save_timer.start())

    def _default_columns(self, tree, name):
        samples = (["较长的论文文件名.pdf", "DOCX", "999.9 MB", "翻译中 · 正在处理图表内容"] if name == "files" else
                   ["较长的论文文件名.pdf", "DOCX", "999.9 MB", "999分59秒", "999,999,999", "999,999,999", "部分完成，需处理 · 10 处保留原文"])
        floors = [300, 80, 120, 300] if name == "files" else [300, 80, 120, 140, 150, 150, 320]
        metrics = tree.fontMetrics()
        return [max(floor, metrics.horizontalAdvance(sample) + 32,
                    metrics.horizontalAdvance(tree.headerItem().text(i)) + 32)
                for i, (floor, sample) in enumerate(zip(floors, samples))]

    def _configure_columns(self, tree, name):
        header = tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(56)
        header.setMaximumSectionSize(4096)
        header.setToolTip("拖动分隔线调整列宽；双击分隔线适配内容")
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tree.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        for column, width in enumerate(self._default_columns(tree, name)):
            tree.setColumnWidth(column, width)
        header.sectionHandleDoubleClicked.connect(lambda column: self.fit_column(tree, column))

    def fit_column(self, tree, column):
        tree.resizeColumnToContents(column)
        # Include padding and a potential sort indicator, even for an empty list.
        width = max(tree.columnWidth(column), tree.fontMetrics().horizontalAdvance(tree.headerItem().text(column)) + 32)
        if tree is self.files and column == 3:
            width = max([width] + [tree.fontMetrics().horizontalAdvance(row['bar'].format()) + 32
                                  for row in self.rows.values()])
        tree.setColumnWidth(column, width)

    def reset_current_columns(self):
        name = "history" if self.file_tabs.currentWidget() is self.history else "files"
        tree = getattr(self, name)
        for column, width in enumerate(self._default_columns(tree, name)):
            tree.setColumnWidth(column, width)

    def set_history(self, records, active_job_id=None):
        selected = self.history.currentItem()
        selected_id = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        self.history.blockSignals(True)
        self.history.clear()
        for identity, source, state, updated, raw in records:
            info = json.loads(raw)
            label = {"completed": "完成", "failed": "失败", "cancelled": "取消"}.get(state,
                     "处理中" if identity == active_job_id else "中断")
            if state == "completed" and info.get("untranslated_regions"):
                label = f"部分完成，需处理 · {info['untranslated_regions']} 处保留原文"
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
            item.setData(0, Qt.ItemDataRole.UserRole + 1, info)
            stamp = datetime.fromtimestamp(info.get("started_at", updated)).strftime("%Y-%m-%d %H:%M:%S")
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

    def show_history_details(self):
        item = self.history.currentItem()
        if item:
            self.report.setPlainText(item.data(0, Qt.ItemDataRole.UserRole + 2))

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

    def open_history_result(self, item, _=0):
        info = item.data(0, Qt.ItemDataRole.UserRole + 1)
        outputs = info.get("outputs", [])
        if outputs and Path(outputs[0]).parent.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(outputs[0]).parent)))

    def set_profiles(self, profiles, active_id):
        self.profiles = {p.id: p for p in profiles}
        self.active_id = active_id

    def preferences(self):
        return dict(document_source_language=self.source_language.currentData(),
            document_target_language=self.target.currentData(),document_pdf_mode=self.pdf_mode.currentData(),
            document_translate_graphics=self.translate_graphics.isChecked(),
            document_skip_references=self.skip_references.isChecked(),document_mono=self.mono.isChecked(),
            document_bilingual=self.bilingual.isChecked(),
            document_bilingual_layout="side_by_side" if self.side_by_side.isChecked() else "alternating",
            document_column_widths={name: [getattr(self, name).columnWidth(i) for i in range(getattr(self, name).columnCount())]
                                    for name in ("files", "history")})

    def restore_preferences(self,settings):
        for name, widths in settings.document_column_widths.items():
            tree = getattr(self, name)
            tree.header().blockSignals(True)
            for column, width in enumerate(widths):
                tree.setColumnWidth(column, width)
            tree.header().blockSignals(False)
        for widget,name in ((self.source_language,"document_source_language"),(self.target,"document_target_language"),
                            (self.pdf_mode,"document_pdf_mode")):
            widget.setCurrentIndex(max(0,widget.findData(getattr(settings,name))))
        self.translate_graphics.setChecked(settings.document_translate_graphics)
        self.skip_references.setChecked(settings.document_skip_references)
        self.mono.setChecked(settings.document_mono)
        self.bilingual.setChecked(settings.document_bilingual)
        (self.side_by_side if settings.document_bilingual_layout=="side_by_side" else self.alternating).setChecked(True)
        for widget in (self.source_language,self.target,self.pdf_mode):
            widget.currentIndexChanged.connect(lambda: self.preferences_changed.emit(self.preferences()))
        for widget in (self.translate_graphics,self.skip_references,self.mono,self.bilingual,self.side_by_side,self.alternating):
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
            item.setData(0, Qt.ItemDataRole.UserRole + 1, row_id)
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            item.setToolTip(0, path)
            item.setToolTip(1, item.text(1))
            item.setToolTip(2, size_text)
            self.files.addTopLevelItem(item)
            self.rows[row_id] = {"item": item, "state": "待处理", "value": 0}
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("待处理")
            bar.setMinimumHeight(28)
            self.files.setItemWidget(item, 3, bar)
            self.rows[row_id]["bar"] = bar
            self.update_billing(row_id, {"usage_state": "pending"})
            existing.add(path)
        self.batch_status.setText(f"共 {self.files.topLevelItemCount()} 个文件")
        if self.files.topLevelItemCount() > previous_count:
            self.file_tabs.setCurrentIndex(0)
            if previous_count==0 and getattr(self,"_has_started_task",False):
                self.pages.clear()
                self.relayout_only.setChecked(False)

    def show_details(self, row_id):
        row = self.rows.get(row_id)
        if row:
            self.report.setPlainText(row.get("details", row["state"]))
            self.scroll.ensureWidgetVisible(self.report)

    def update_row(self, row_id, state, value=None, details=None):
        row = self.rows.get(row_id)
        if not row:
            return
        row["state"] = state
        if value is not None:
            row["value"] = value
        if details is not None:
            row["details"] = details
        row["item"].setText(3, state)
        bar = row["bar"]
        bar.setRange(0, 0 if value is None and state in {"解析中", "OCR 中", "翻译中", "排版中", "字体处理中", "保存中"} else 100)
        bar.setValue(row["value"])
        bar.setFormat(state + (f" {value}%" if value is not None else ""))
        self.update_billing(row_id, {})

    def remove_row(self, row_id):
        row = self.rows.pop(row_id, None)
        if row:
            self.files.takeTopLevelItem(self.files.indexOfTopLevelItem(row["item"]))

    def dragEnterEvent(self, event):
        if self.add_button.isEnabled() and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
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
        for control in (self.add_button, self.start_button, self.trial_button,
                        self.target, self.source_language, self.pages,
                        self.pdf_mode, self.mono, self.bilingual, self.skip_references, self.output_dir, self.browse, self.relayout_only):
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
        page.pause_requested.connect(self.pause)
        page.cancel_requested.connect(self.cancel)
        page.remove_requested.connect(self.remove_selected)
        page.file_tabs.currentChanged.connect(lambda index: self.refresh_history() if index == 1 else None)
        if self.database.exists():
            store = JobStore(self.database)
            try:
                for source, status, details in reversed(store.recent()):
                    info = json.loads(details)
                    label = {"completed": "完成", "failed": "失败", "cancelled": "已取消"}.get(status, "中断，可重新添加并复用缓存")
                    if status == "completed" and info.get("untranslated_regions"):
                        label = f"部分完成，需处理 · {info['untranslated_regions']} 处保留原文"
                    if status == "completed" and info.get("outputs"):
                        page.last_output = str(Path(info["outputs"][0]).parent)
            finally:
                store.close()
        self.refresh_history()

    def refresh_history(self):
        store = JobStore(self.database)
        try:
            self.page.set_history(store.history(), self.job.job_id if self.process and self.job else None)
        finally:
            store.close()

    def start(self, trial=False):
        if getattr(self, "update_pending", False):
            self.page.status.setText("已准备安装更新，请等待当前批次完成；本次未创建新任务。")
            return
        if self.page.busy:
            return
        self.page.output_directory_changed.emit(self.page.output_dir.text())
        self.page.report.clear()
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
                graphics_enabled=(self.page.translate_graphics.isChecked() and self.page.pdf_mode.currentData()=="layout"
                    and Path(item.data(0,Qt.ItemDataRole.UserRole)).suffix.lower() not in {".docx",".pptx"})
                job = DocumentJob(source=item.data(0, Qt.ItemDataRole.UserRole), output_dir=self.page.output_dir.text().strip(),
                    provider=asdict(profile), target=self.page.target.currentData(), source_language=self.page.source_language.currentData(),
                    pages=self.page.pages.text(), trial=trial, mono=self.page.mono.isChecked(), bilingual=self.page.bilingual.isChecked(),
                    pdf_mode=self.page.pdf_mode.currentData(), job_id=uuid.uuid4().hex,
                    bilingual_layout="side_by_side" if self.page.side_by_side.isChecked() else "alternating",
                    request_timeout=profile.document_timeout,
                    relayout_only=self.page.relayout_only.isChecked(),
                    translate_tables=graphics_enabled,
                    translate_figures=graphics_enabled,
                    graphic_overrides={}, pricing={}, skip_references=self.page.skip_references.isChecked())
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
        self.page.set_busy(True)
        self.batch_ids = [row_id for _, _, row_id in self.queue]
        self.batch_results = {}
        for row_id in self.batch_ids:
            self.page.update_row(row_id, "待处理", 0, "等待处理")
        self.next_job()

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
        self.page.status.setText("正在启动：" + Path(self.job.source).name)
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
        process.readyReadStandardOutput.connect(self.read_events)
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        payload = json.dumps({"job": asdict(self.job), "api_key": key, "database": str(self.database)}, ensure_ascii=False).encode() + b"\n"
        process.started.connect(lambda: process.write(payload))
        process.finished.connect(self.process_finished)
        process.errorOccurred.connect(self.process_error)
        process.start()

    def read_events(self):
        if self.process is None:
            return
        self.pending += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            kind = event.get("type")
            if self.cancelled:
                continue
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
                self.page.status.setText(Path(self.job.source).name + " · " + message)
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
                self.page.status.setText(Path(self.job.source).name + " · " + message)
                row = self.page.rows.get(self.row_id)
                if row:
                    row["details"] = message
            elif kind == "finished":
                self.terminal = True
                retained = event.get("untranslated_regions", 0)
                completion = (f"部分完成，需处理 · {retained} 处保留原文" if retained else "已完成")
                self.page.update_billing(self.row_id, {"usage_state": "finished", **event.get("usage", {})})
                state = "部分完成，需处理" if retained else ("试译完成" if self.job.trial else "已完成")
                self.page.update_row(self.row_id, state, 100,
                                     "\n".join([*event.get("outputs", []), *event.get("warnings", []), *event.get("issues", [])]))
                self.batch_results[self.row_id] = "部分完成" if retained else "完成"
                self.update_batch()
                self.page.progress.setRange(0, 100)
                self.page.progress.setValue(100)
                self.page.status.setText(Path(self.job.source).name + "：" + completion)
                set_state(self.page.status, "paused" if retained else "running")
                self.job_requests, self.job_hits = event.get("requests", 0), event.get("cache_hits", 0)
                self.page.usage.setText(f"成功请求 {self.total_requests + self.job_requests} · 缓存命中 {self.total_hits + self.job_hits}")
                if event.get("performance", {}).get("operation_counts", {}).get("译后中间数据缓存命中"):
                    self.page.usage.setText(self.page.usage.text() + " · 已复用译后文档")
                paths = event.get("outputs", [])
                if paths:
                    self.page.last_output = str(Path(paths[0]).parent)
                    self.item.setData(1, Qt.ItemDataRole.UserRole, self.page.last_output)
                self.page.report.append(Path(self.job.source).name + "：" + completion)
                for text in [*paths, *event.get("warnings", []), *event.get("issues", [])]:
                    self.page.report.append(text.replace("<", "&lt;"))
            elif kind in {"error", "cancelled"}:
                self.terminal = True
                self.page.update_row(self.row_id, "失败" if kind == "error" else "已取消", details=event.get("message", "文件处理失败"))
                self.batch_results[self.row_id] = "失败" if kind == "error" else "取消"
                self.update_batch()
                self.page.status.setText(event.get("message", "文件处理失败").splitlines()[0])
                set_state(self.page.status, "error")
                self.page.report.append("<div style='white-space:pre-wrap'>" + html.escape(
                    Path(self.job.source).name + "：" + event.get("message", "失败")) + "</div>")
                self.page.scroll.ensureWidgetVisible(self.page.report)

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

    def process_finished(self, *_):
        if self.process is None:
            return
        self.read_events()
        process, self.process = self.process, None
        process.deleteLater()
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
            store = JobStore(self.database)
            store.status(self.job, status, {"message": self.page.status.text()})
            store.close()
            self.page.update_row(self.row_id, "已取消" if self.cancelled else "进程异常退出", details=self.page.status.text())
            self.page.report.setPlainText(Path(self.job.source).name + "：" + self.page.status.text())
        self.refresh_history()
        if self.terminal and self.batch_results.get(self.row_id) in {"完成", "部分完成"}:
            self.page.remove_row(self.row_id)
        if not self.paused or self.cancelled:
            self.next_job()

    def pause(self):
        if self.process is None and not self.queue:
            return
        self.paused = not self.paused
        if self.process:
            self.process.write(json.dumps({"command": "pause" if self.paused else "resume"}).encode() + b"\n")
        self.page.pause_button.setText("继续" if self.paused else "暂停")
        if self.process and not self.terminal:
            self.page.update_row(self.row_id, "暂停" if self.paused else "翻译中")
        self.page.status.setText("已暂停；正在发送的请求完成后写入缓存" if self.paused else "继续处理")
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
        self.page.status.setText("正在取消，已完成的段落缓存可供下次复用")
        if self.process:
            process = self.process
            process.write(b'{"command":"cancel"}\n')
            QTimer.singleShot(2000, lambda: process.kill() if self.process is process and process.state() != QProcess.ProcessState.NotRunning else None)
        else:
            self.page.set_busy(False)

    def shutdown(self):
        if self.process:
            self.cancel()
            if self.process and not self.process.waitForFinished(1500):
                self.process.kill()
                self.process.waitForFinished(2000)
