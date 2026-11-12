"""Masked, explicit confirmation UI for CC Switch imports."""
import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from linguafloat.cc_switch import (
    CCSwitchError, ImportChoice, discover_sources, duplicate_targets, read_candidates,
)
from linguafloat.config import validate_provider
from linguafloat.theme import APP_STYLESHEET


class CCSwitchPreview(QDialog):
    def __init__(self, existing, parent=None):
        super().__init__(parent)
        self.existing = copy.deepcopy(existing)
        self.candidates = []
        self.actions = []
        self.env_choices = []
        self.current_row = -1
        self.setWindowTitle("从 CC Switch 导入配置")
        self.resize(940, 660)
        self.setStyleSheet(APP_STYLESHEET)
        layout = QVBoxLayout(self)
        intro = QLabel("只读获取供应商；选择处理方式后确认导入。不会切换当前配置，也不会发送 API 请求。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        source_row = QHBoxLayout()
        self.source = QComboBox()
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.source.setMinimumContentsLength(25)
        for path in discover_sources():
            self.source.addItem(str(path))
        source_row.addWidget(self.source, 1)
        browse = QPushButton("选择文件…")
        browse.clicked.connect(self._browse)
        reload_button = QPushButton("读取")
        reload_button.clicked.connect(self._load)
        source_row.addWidget(browse)
        source_row.addWidget(reload_button)
        layout.addLayout(source_row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["来源", "配置名称", "接口 / 模型", "密钥状态", "兼容性", "处理方式"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStyleSheet(
            "QHeaderView::section { background: #e8edf3; color: #273444; padding: 5px; border: 1px solid #d7dfe8; }")
        for col, width in enumerate((85, 150, 195, 230, 210, 180)):
            self.table.setColumnWidth(col, width)
        # Keep the decision controls visible even on smaller displays.
        self.table.horizontalHeader().moveSection(5, 0)
        self.table.currentCellChanged.connect(self._select)
        layout.addWidget(self.table, 1)
        select_row = QHBoxLayout()
        add_selected = QPushButton("所选条目另存为新配置")
        add_selected.clicked.connect(self._add_selected)
        select_row.addWidget(add_selected)
        select_row.addStretch()
        layout.addLayout(select_row)
        form = QFormLayout()
        self.name = QLineEdit()
        self.address = QLineEdit()
        self.model = QLineEdit()
        self.mode = QComboBox()
        self.mode.addItem("未确定，请选择", "")
        self.mode.addItem("Responses API", "responses")
        self.mode.addItem("Chat Completions", "chat_completions")
        self.env = QComboBox()
        self.env.addItem("保留环境变量引用（不复制值）", True)
        self.env.addItem("将已解析值存入 Windows 凭据管理器", False)
        form.addRow("名称", self.name)
        form.addRow("接口地址", self.address)
        pair = QHBoxLayout()
        pair.addWidget(self.mode)
        pair.addWidget(self.model, 1)
        self.model.setPlaceholderText("准确模型 ID（未确认时请补全）")
        form.addRow("接口 / 模型", pair)
        form.addRow("环境变量处理", self.env)
        layout.addLayout(form)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(105)
        layout.addWidget(self.details)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认导入")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._confirm)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for widget in (self.name, self.address, self.model):
            widget.textEdited.connect(self._edit)
        self.mode.activated.connect(self._edit)
        self.env.activated.connect(self._edit)
        self._load()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 CC Switch 数据库或配置", "",
            "CC Switch 来源 (*.db *.sqlite *.sqlite3 *.json);;所有文件 (*)")
        if path:
            index = self.source.findText(path)
            if index < 0:
                self.source.addItem(path)
                index = self.source.count() - 1
            self.source.setCurrentIndex(index)
            self._load()

    def _load(self):
        self.current_row = -1
        self.candidates, self.actions, self.env_choices = [], [], []
        self.table.setRowCount(0)
        for widget in (self.name, self.address, self.model):
            widget.clear()
        self.details.clear()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        if not self.source.currentText():
            self.status.setText("未找到本机配置；请选择 CC Switch 数据库或旧版 apps/providers JSON 文件。")
            return
        try:
            self.candidates = read_candidates(Path(self.source.currentText()))
        except CCSwitchError as exc:
            self.status.setText(str(exc))
            return
        self.table.setRowCount(len(self.candidates))
        for row, candidate in enumerate(self.candidates):
            action = QComboBox()
            action.addItem("跳过（默认）", ("skip", ""))
            if not candidate.blocked:
                action.addItem("另存为新配置", ("add", ""))
                for target in duplicate_targets(candidate, self.existing):
                    action.addItem(f"更新：{target.name}", ("update", target.id))
            action.currentIndexChanged.connect(lambda _, r=row: self._action_changed(r))
            self.actions.append(action)
            self.env_choices.append(True)
            self.table.setCellWidget(row, 5, action)
            self._refresh_row(row)
        self.status.setText(f"读取 {len(self.candidates)} 条；{sum(not c.blocked for c in self.candidates)} 条可选择导入或补全。密钥不显示。")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.candidates))
        if self.candidates:
            initial = next((i for i, c in enumerate(self.candidates)
                            if not c.blocked and c.profile.base_url and c.profile.model and c.profile.api_mode), 0)
            self.table.setCurrentCell(initial, 0)

    def _refresh_row(self, row):
        c = self.candidates[row]
        missing = [label for label, value in (("地址", c.profile.base_url), ("模型", c.profile.model), ("接口", c.profile.api_mode)) if not value]
        state = c.blocked or ("需补全：" + "、".join(missing) if missing else "字段已识别；未测试连接")
        values = (c.source_app, c.profile.name, f"{c.profile.api_mode or '未确定'} / {c.profile.model or '未确定'}", c.auth_status, state)
        for col, value in enumerate(values):
            cell = QTableWidgetItem(value)
            cell.setToolTip(value)
            self.table.setItem(row, col, cell)

    def _select(self, row, _column, _old_row, _old_column):
        if not 0 <= row < len(self.candidates):
            return
        self.current_row = row
        c = self.candidates[row]
        self.name.setText(c.profile.name)
        self.address.setText(c.profile.base_url)
        self.model.setText(c.profile.model)
        self.mode.setCurrentIndex(max(0, self.mode.findData(c.profile.api_mode)))
        self.env.setCurrentIndex(0 if self.env_choices[row] else 1)
        for widget in (self.name, self.address, self.model, self.mode):
            widget.setEnabled(not c.blocked)
        self.env.setEnabled(bool(c.profile.env_key) and not c.blocked)
        self.env.model().item(1).setEnabled(bool(c.secret))
        self._details(row)

    def _details(self, row):
        c = self.candidates[row]
        message = c.blocked or c.note
        operation, target_id = self.actions[row].currentData()
        if operation == "update":
            old = next(p for p in self.existing if p.id == target_id)
            changes = [f"{label}：{getattr(old, field)} → {getattr(c.profile, field)}" for field, label in
                       (("name", "名称"), ("base_url", "地址"), ("model", "模型"), ("api_mode", "接口"))
                       if getattr(old, field) != getattr(c.profile, field)]
            changes.append("认证：有有效来源时更新；无有效密钥时保留已有密钥及引用。其他参数保留。")
            message += "\n将更新：\n" + "\n".join(changes)
        self.details.setPlainText(message)

    def _edit(self, *_):
        row = self.current_row
        if row < 0:
            return
        c = self.candidates[row]
        c.profile.name = self.name.text().strip()
        c.profile.base_url = self.address.text().strip()
        c.profile.model = self.model.text().strip()
        c.profile.api_mode = self.mode.currentData()
        self.env_choices[row] = self.env.currentData()
        self._refresh_row(row)
        self._details(row)

    def _action_changed(self, row):
        if row == self.current_row:
            self._details(row)

    def _add_selected(self):
        for index in self.table.selectionModel().selectedRows():
            if not self.candidates[index.row()].blocked:
                self.actions[index.row()].setCurrentIndex(1)

    def operations(self):
        return [ImportChoice(c, *action.currentData(), keep_env=keep) for c, action, keep in
                zip(self.candidates, self.actions, self.env_choices)]

    def _confirm(self):
        choices = [choice for choice in self.operations() if choice.action != "skip"]
        if not choices:
            self.status.setText("尚未选择导入条目；请在处理方式中选择新增或更新。")
            return
        for choice in choices:
            try:
                validate_provider(copy.deepcopy(choice.candidate.profile))
            except ValueError:
                self.status.setText("选中条目有缺失或无效的名称、地址、接口或模型，请逐条补全。")
                return
        self.accept()
