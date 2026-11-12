"""Category-level configuration transfer, separate from the provider editor."""
from pathlib import Path
import qtawesome as qta
from PySide6.QtCore import Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QToolButton, QWidget,
    QScrollArea, QFrame)
from linguafloat.cc_switch import Candidate, CCSwitchError, discover_sources, read_candidates
from linguafloat.opencode_import import discover_opencode, read_opencode
from linguafloat.config import ProviderProfile
from linguafloat.theme import APP_STYLESHEET
from linguafloat.workbench import AppDialog
from linguafloat.model_picker import ModelSelector
from linguafloat.provider_presets import presets_for
from linguafloat.provider_tools import ProviderTask
from linguafloat.api_compat import safe_text

MODE_LABELS = [("未确定", ""), ("Responses API", "responses"), ("Chat Completions", "chat_completions"),
               ("Anthropic Messages", "anthropic"), ("Gemini generateContent", "gemini")]
APP_LABELS = {"codex": "Codex", "claude": "Claude Code", "claude-desktop": "Claude Desktop",
              "gemini": "Gemini CLI", "openclaw": "OpenClaw", "opencode": "OpenCode", "cc-opencode": "OpenCode"}


class ProviderPresetDialog(AppDialog):
    """A local draft; only the explicit model-list action starts a request."""

    def __init__(self, kind="custom", profiles=(), parent=None, *, confirmed_names=()):
        from linguafloat.config import create_provider_id

        super().__init__(parent)
        self.kind = kind
        self.presets = presets_for(kind, profiles, confirmed_names=confirmed_names)
        self._provider_id = create_provider_id(kind)
        self._thread_pool = QThreadPool.globalInstance()
        self._token = 0
        self._revision = 0
        self._pending = None
        self._model_task = None
        self._candidate = None
        titles = {"go": "OpenCode Go", "no_coding": "Now Coding", "custom": "自定义供应商"}
        self.setWindowTitle("添加 " + titles[kind])
        self.resize(640, 470)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        form = QFormLayout()
        form.setSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.preset_combo = QComboBox()
        self.preset_combo.setAccessibleName("本地预设来源")
        self.preset_combo.setMinimumContentsLength(12)
        self.preset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for preset in self.presets:
            self.preset_combo.addItem(preset.name + " · " + preset.base_url)
        if len(self.presets) > 1:
            form.addRow("本地配置", self.preset_combo)
        else:
            self.preset_combo.hide()
        self.name = QLineEdit("" if kind == "custom" else titles[kind])
        self.name.setAccessibleName("服务名称")
        self.address = QLineEdit()
        self.address.setAccessibleName("接口地址")
        self.address.setPlaceholderText("https://")
        self.key = QLineEdit()
        self.key.setAccessibleName("API Key")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model = ModelSelector()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.setMinimumContentsLength(12)
        self.model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model.setAccessibleName("选择或输入模型")
        self.model.lineEdit().setPlaceholderText("选择或输入模型 ID")
        self.fetch_models_button = QPushButton("获取模型")
        self.fetch_models_button.setToolTip("使用当前地址、协议和 API Key 获取模型列表")
        self.fetch_models_button.clicked.connect(self._fetch_models)
        self.cancel_models_button = QToolButton()
        self.cancel_models_button.setIcon(qta.icon("mdi6.close", color="#626980"))
        self.cancel_models_button.setToolTip("取消获取模型")
        self.cancel_models_button.setAccessibleName("取消获取模型")
        self.cancel_models_button.setFixedSize(36, 36)
        cancel_policy = self.cancel_models_button.sizePolicy()
        cancel_policy.setRetainSizeWhenHidden(True)
        self.cancel_models_button.setSizePolicy(cancel_policy)
        self.cancel_models_button.clicked.connect(lambda: self._cancel_models(notify=True))
        self.cancel_models_button.hide()
        self.model.manage_requested.connect(self.fetch_models_button.setFocus)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.fetch_models_button)
        model_row.addWidget(self.cancel_models_button)
        form.addRow("服务名称", self.name)
        form.addRow("接口地址", self.address)
        form.addRow("API Key", self.key)
        form.addRow("模型", model_row)
        layout.addLayout(form)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("高级设置")
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.mode = QComboBox()
        for label, value in MODE_LABELS:
            if kind != "go" or value in {"responses", "chat_completions"}:
                self.mode.addItem(label, value)
        if kind == "custom":
            self.mode.setCurrentIndex(self.mode.findData("responses"))
        self.native_auth = QComboBox()
        self.native_auth.addItem("API Key", "api_key")
        self.native_auth.addItem("Bearer", "bearer")
        self.native_auth.setCurrentIndex(1 if kind == "go" else 0)
        self.native_auth.setToolTip("原生 Anthropic / Gemini 协议使用的认证请求头")
        advanced.addRow("接口协议", self.mode)
        advanced.addRow("原生协议认证", self.native_auth)
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        layout.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._confirm)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for control in (self.name, self.address, self.key):
            control.textChanged.connect(self._changed)
        self.model.currentTextChanged.connect(self._changed)
        self.mode.currentIndexChanged.connect(self._changed)
        self.native_auth.currentIndexChanged.connect(self._changed)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        if self.presets:
            self._apply_preset(0)
        elif kind == "no_coding":
            self.status.setText("未找到预设，请填写地址、模型并在高级设置中确认协议。")

    def _toggle_advanced(self, checked):
        self.advanced.setVisible(checked)
        self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def _changed(self, *_):
        self._revision += 1

    def _show_error(self, message):
        secret = self._candidate.secret if self._candidate is not None else ""
        key = self.key.text()
        self.status.setText(safe_text(message, secrets=(key, key.strip(), secret), limit=1200))

    def _validation_failed(self):
        # URL parsers may include the entire invalid netloc in ValueError.
        self.status.setText("请检查服务名称、接口地址、模型和高级协议设置。地址须为完整 HTTP/HTTPS 地址，不能包含凭据、查询参数或片段。")

    def _cancel_models(self, *, notify=False):
        task, self._model_task = self._model_task, None
        self._pending = None
        if task is not None:
            task.cancel()
        self.fetch_models_button.setEnabled(True)
        self.cancel_models_button.hide()
        if notify:
            self.status.setText("已取消获取模型，填写内容已保留。")

    def clear_sensitive(self):
        self._cancel_models()
        if self._candidate is not None:
            self._candidate.secret = ""
        self._candidate = None
        self.key.clear()

    def save_failed(self, message):
        self._show_error(message)
        if self._candidate is not None:
            self._candidate.secret = ""
        self._candidate = None
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("重试保存")

    def _apply_preset(self, index):
        if not 0 <= index < len(self.presets):
            return
        preset = self.presets[index]
        self.name.setText({"go": "OpenCode Go", "no_coding": "Now Coding"}.get(self.kind, preset.name))
        self.address.setText(preset.base_url)
        self.mode.setCurrentIndex(self.mode.findData(preset.api_mode))
        self.native_auth.setCurrentIndex(self.native_auth.findData(preset.native_auth))
        self.key.clear()
        self.model.clear()
        self.model.addItems(preset.models)
        self.model.setEditText(preset.model)
        self.status.setText("已填入预设；具体模型权限以账号为准。" if preset.model else "请填写模型，或输入 API Key 后获取模型。")

    def _profile(self, *, listing=False):
        from linguafloat.config import ProviderProfile, clean_models, validate_provider

        profile = ProviderProfile(self._provider_id, self.name.text().strip(), self.address.text().strip(),
            self.model.currentText().strip() or ("model-list" if listing else ""),
            self.mode.currentData() or "", env_key="", native_auth=self.native_auth.currentData(),
            models=clean_models([self.model.currentText(), *(self.model.itemText(i) for i in range(self.model.count()))]))
        validate_provider(profile)
        return profile

    def _fetch_models(self):
        from linguafloat.cc_switch import _key_value

        if self._pending is not None:
            return
        key = _key_value(self.key.text())
        if not key:
            self.status.setText("请先填写有效 API Key，再获取模型；也可以直接填写模型名称。")
            return
        try:
            profile = self._profile(listing=True)
        except ValueError:
            self._validation_failed()
            return
        self._token += 1
        self._pending = (self._token, self._revision)
        self.fetch_models_button.setEnabled(False)
        self.cancel_models_button.show()
        self.status.setText("正在获取模型列表…")
        task = ProviderTask(self._token, profile, key, "models")
        self._model_task = task
        task.signals.finished.connect(self._models_finished)
        try:
            self._thread_pool.start(task)
        except Exception:
            task.key = ""
            self._models_finished(self._token, None, "无法启动模型请求，请重试。")

    @Slot(int, object, str)
    def _models_finished(self, token, result, error):
        from linguafloat.config import clean_models

        if self._pending is None or token != self._pending[0]:
            return
        _, revision = self._pending
        self._pending = None
        self._model_task = None
        self.fetch_models_button.setEnabled(True)
        self.cancel_models_button.hide()
        if revision != self._revision:
            self.status.setText("配置已变更，已忽略旧模型列表。")
            return
        if error:
            self._show_error("获取模型失败：" + error)
            return
        models = clean_models(result)
        if not models:
            self.status.setText("模型列表为空或服务不支持列举模型，请手动填写模型名称。")
            return
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(clean_models([current, *models]))
        self.model.setEditText(current)
        self.status.setText(f"已获取 {len(models)} 个模型。")

    def _confirm(self):
        if self._prepare_candidate():
            self.accept()

    def _prepare_candidate(self):
        from linguafloat.cc_switch import _key_value

        if self._candidate is not None:
            self._candidate.secret = ""
        self._candidate = None
        try:
            profile = self._profile()
        except ValueError:
            self._validation_failed()
            return False
        key = _key_value(self.key.text())
        if not key:
            self.status.setText("请填写有效 API Key；占位符不能用于保存。")
            return False
        self._candidate = Candidate("opencode" if self.kind == "go" else "manual", "preset", profile, key)
        return True

    def candidate(self):
        if self._candidate is None:
            raise ValueError("请先完成表单并保存。")
        return self._candidate

    def done(self, result):
        self._cancel_models()
        if result != QDialog.DialogCode.Accepted:
            self.clear_sensitive()
        super().done(result)


class FirstRunSetupDialog(ProviderPresetDialog):
    """Single-page first-run setup that reuses the existing provider form."""

    save_requested = Signal()
    skip_requested = Signal()

    def __init__(self, settings, profiles=(), parent=None):
        super().__init__("go", (), parent)
        self.setWindowTitle("配置翻译服务")
        self.setMinimumSize(420, 320)
        self.resize(640, 610)
        self._drafts = {}
        self._decorated = True
        root = self.layout()
        root.removeWidget(self.buttons)
        self.buttons.hide()
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(24, 18, 24, 18)
        body.setSpacing(12)
        while root.count():
            item = root.takeAt(0)
            if item.widget():
                body.addWidget(item.widget())
            elif item.layout():
                nested = item.layout()
                nested.setParent(None)
                body.addLayout(nested)

        header = QWidget()
        header.setObjectName("dialogHeader")
        heading = QHBoxLayout(header)
        heading.setContentsMargins(24, 18, 24, 18)
        title = QLabel("配置翻译服务")
        title.setObjectName("dialogTitle")
        heading.addWidget(title)
        service_row = QHBoxLayout()
        service_row.addWidget(QLabel("服务类型"))
        self.service_kind = QComboBox()
        self.service_kind.addItem("OpenCode Go", "go")
        self.service_kind.addItem("Now Coding", "no_coding")
        self.service_kind.addItem("自定义", "custom")
        service_row.addWidget(self.service_kind, 1)
        body.insertLayout(0, service_row)
        from linguafloat.config import AppSettings
        defaults = AppSettings()
        self.shortcut_summary = QLabel(
            f"默认快捷键\n选中文字翻译　{defaults.hotkey}\n输入翻译　　　{defaults.input_hotkey}"
        )
        self.shortcut_summary.setWordWrap(True)
        self.shortcut_summary.setObjectName("muted")
        body.addWidget(self.shortcut_summary)
        body.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(content)
        footer_widget = QWidget()
        footer_widget.setObjectName("footer")
        footer = QHBoxLayout(footer_widget)
        footer.setContentsMargins(24, 12, 24, 12)
        self.skip_button = QPushButton("跳过，稍后配置")
        self.skip_button.clicked.connect(self.skip_requested)
        self.save_button = QPushButton("完成并保存")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._confirm)
        footer.addWidget(self.skip_button)
        footer.addStretch()
        footer.addWidget(self.save_button)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(self.scroll, 1)
        root.addWidget(footer_widget)
        self.setTabOrder(self.service_kind, self.name)
        for button in (self.save_button, self.skip_button, self.fetch_models_button):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.service_kind.currentIndexChanged.connect(self._switch_kind)

    def _switch_kind(self, index):
        from linguafloat.config import create_provider_id

        self._drafts[self.kind] = (self.name.text(), self.address.text(), self.key.text(),
            self.model.currentText(), [self.model.itemText(i) for i in range(self.model.count())],
            self.mode.currentData(), self.native_auth.currentData(), self._provider_id)
        self._cancel_models()
        if self._candidate is not None:
            self._candidate.secret = ""
        self._candidate = None
        self.kind = self.service_kind.itemData(index)
        self.presets = presets_for(self.kind)
        self._provider_id = create_provider_id(self.kind)
        self.mode.clear()
        for label, value in MODE_LABELS:
            if self.kind != "go" or value in {"responses", "chat_completions"}:
                self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData("responses"))
        self.name.clear()
        self.address.clear()
        self.key.clear()
        self.model.clear()
        self.native_auth.setCurrentIndex(0)
        self.status.clear()
        self._apply_preset(0)
        draft = self._drafts.get(self.kind)
        if draft:
            name, address, key, model, models, mode, auth, self._provider_id = draft
            self.name.setText(name)
            self.address.setText(address)
            self.key.setText(key)
            self.model.clear()
            self.model.addItems(models)
            self.model.setEditText(model)
            self.mode.setCurrentIndex(self.mode.findData(mode))
            self.native_auth.setCurrentIndex(self.native_auth.findData(auth))

    def _confirm(self):
        if self._prepare_candidate():
            self.save_requested.emit()

    def save_failed(self, message):
        super().save_failed(message)
        self.save_button.setText("重试保存")

    def clear_sensitive(self):
        self._drafts.clear()
        super().clear_sensitive()

    def reject(self):
        self.skip_requested.emit()


class CompleteConfigDialog(AppDialog):
    """Only incomplete entries need a form; complete imports bypass this dialog."""
    def __init__(self, candidate, parent=None):
        import copy
        super().__init__(parent)
        self.item = copy.deepcopy(candidate)
        self.setWindowTitle("补全供应商配置")
        self.setStyleSheet(APP_STYLESHEET); self.resize(640, 420)
        layout = QVBoxLayout(self); layout.setContentsMargins(24,20,24,20); layout.setSpacing(14)
        title = QLabel(candidate.profile.name); title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True); title.setStyleSheet("font-size:18px;font-weight:600;"); layout.addWidget(title)
        form = QFormLayout(); form.setSpacing(12)
        self.name = QLineEdit(candidate.profile.name)
        self.address = QLineEdit(candidate.profile.base_url)
        self.model = QLineEdit(candidate.profile.model)
        self.mode = QComboBox()
        for label, value in MODE_LABELS:self.mode.addItem(label,value)
        self.mode.setCurrentIndex(max(0,self.mode.findData(candidate.profile.api_mode)))
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("密钥已读取，无需再次填写" if candidate.secret else "填写 API Key")
        self.key.setEnabled(not candidate.secret)
        for label, widget in (("名称",self.name),("接口地址",self.address),("协议",self.mode),("模型",self.model),("API Key",self.key)):form.addRow(label,widget)
        layout.addLayout(form)
        self.status = QLabel("保存后仅新增配置，不自动启用或测试连接。")
        self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("补全并导入")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._confirm); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def _confirm(self):
        from linguafloat.config import validate_provider
        from linguafloat.cc_switch import _key_value
        p = self.item.profile
        p.name, p.base_url, p.model, p.api_mode = self.name.text().strip(),self.address.text().strip(),self.model.text().strip(),self.mode.currentData()
        if self.key.isEnabled():
            self.item.secret = _key_value(self.key.text())
            p.env_key = ""
        try:validate_provider(p)
        except ValueError:
            self.status.setText("请补全有效的名称、接口地址、协议和模型。"); return
        if not self.item.secret:
            self.status.setText("请填写有效 API Key；占位符不能用于导入。"); return
        self.accept()


class BatchImportDialog(QDialog):
    def __init__(self, source="cc", parent=None):
        super().__init__(parent)
        self.kind = source
        self.candidates = []
        self.visible = []
        self.setWindowTitle("从 CC Switch 导入" if source == "cc" else "从 OpenCode 导入")
        self.resize(900, 660)
        self.setStyleSheet(APP_STYLESHEET)
        layout = QVBoxLayout(self)
        info = QLabel("选择来源类别即可导入该类别全部兼容配置，无需逐条勾选。重复跳过，当前使用配置不变。")
        info.setWordWrap(True)
        layout.addWidget(info)
        row = QHBoxLayout()
        self.path = QComboBox()
        self.path.setMinimumContentsLength(20)
        self.path.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for p in (discover_sources() if source == "cc" else discover_opencode()):
            self.path.addItem(str(p))
        row.addWidget(self.path, 1)
        browse = QPushButton("选择文件…")
        browse.clicked.connect(self._browse)
        reload = QPushButton("读取")
        reload.clicked.connect(self._load)
        row.addWidget(browse)
        row.addWidget(reload)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("来源应用"))
        self.category = QComboBox()
        self.category.currentIndexChanged.connect(self._category_changed)
        row.addWidget(self.category, 1)
        self.group_label = QLabel("供应商")
        row.addWidget(self.group_label)
        self.group = QComboBox()
        self.group.currentIndexChanged.connect(self._filter)
        row.addWidget(self.group, 1)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["配置名称", "协议", "模型", "导入状态"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStyleSheet("QHeaderView::section {background:#e8edf3;color:#273444;padding:5px;}")
        for i, width in enumerate((220, 160, 200, 280)):
            self.table.setColumnWidth(i, width)
        self.table.verticalHeader().hide()
        self.table.currentCellChanged.connect(self._select)
        layout.addWidget(self.table, 1)
        label = QLabel("仅缺失条目需要补全；完整条目直接导入。选中一行查看地址和原因。")
        label.setWordWrap(True)
        layout.addWidget(label)
        form = QFormLayout()
        self.address = QLineEdit()
        self.model = QLineEdit()
        self.mode = QComboBox()
        for label, mode in MODE_LABELS:
            self.mode.addItem(label, mode)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("已有密钥不显示；缺少时可在此补全")
        form.addRow("接口地址", self.address)
        pair = QHBoxLayout()
        pair.addWidget(self.mode)
        pair.addWidget(self.model, 1)
        form.addRow("接口 / 模型", pair)
        form.addRow("补充 API Key", self.key)
        layout.addLayout(form)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(90)
        layout.addWidget(self.details)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入此类别全部配置")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.address.textEdited.connect(self._edit)
        self.model.textEdited.connect(self._edit)
        self.key.textEdited.connect(self._edit)
        self.mode.activated.connect(self._edit)
        self._load()

    def _browse(self):
        filter = "CC Switch 配置 (*.db *.sqlite *.sqlite3 *.json *.sql)" if self.kind == "cc" else "OpenCode 配置 (*.json *.jsonc)"
        path, _ = QFileDialog.getOpenFileName(self, "选择来源", "", filter)
        if path:
            index = self.path.findText(path)
            if index < 0:
                self.path.addItem(path)
                index = self.path.count() - 1
            self.path.setCurrentIndex(index)
            self._load()

    def _load(self):
        self.candidates = []
        try:
            if self.path.currentText():
                loader = read_candidates if self.kind == "cc" else read_opencode
                self.candidates = loader(Path(self.path.currentText()))
                self.status.setText(f"已读取 {len(self.candidates)} 条；请选择来源类别。")
            else:
                self.status.setText("未发现本机配置，请选择来源文件。没有安装 OpenCode 时可使用配置页顶部的服务配置入口。")
        except CCSwitchError as exc:
            self.status.setText(str(exc))
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("请选择来源应用", "")
        for app in dict.fromkeys(c.source_app for c in self.candidates):
            self.category.addItem(APP_LABELS.get(app, app), app)
        if self.kind == "opencode" and self.category.count() == 2:
            self.category.setCurrentIndex(1)
        self.category.blockSignals(False)
        self._category_changed()

    def _category_changed(self):
        app = self.category.currentData()
        groups = list(dict.fromkeys(c.group for c in self.candidates if c.source_app == app and c.group))
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem("该类别全部供应商", "")
        for value in groups:
            self.group.addItem(value, value)
        self.group.blockSignals(False)
        self.group.setVisible(bool(groups))
        self.group_label.setVisible(bool(groups))
        self._filter()

    def _filter(self):
        app, group = self.category.currentData(), self.group.currentData()
        self.visible = [c for c in self.candidates if c.source_app == app and (not group or c.group == group)]
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.visible))
        for row, c in enumerate(self.visible):
            state = c.blocked or ("需要补全" if not all((c.profile.base_url, c.profile.model, c.profile.api_mode, c.secret)) else "可导入（尚未连接测试）")
            for col, value in enumerate((c.profile.name, c.profile.api_mode, c.profile.model, state)):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, col, cell)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.visible))
        if self.visible:
            self.table.setCurrentCell(0, 0)
        else:
            for widget in (self.address, self.model, self.key):
                widget.clear()
            self.details.clear()

    def _select(self, row, *_):
        if not 0 <= row < len(self.visible):
            return
        c = self.visible[row]
        self.address.setText(c.profile.base_url)
        self.model.setText(c.profile.model)
        self.mode.setCurrentIndex(max(0, self.mode.findData(c.profile.api_mode)))
        self.key.clear()
        for widget in (self.address, self.model, self.mode, self.key):
            widget.setEnabled(not c.blocked)
        self.key.setEnabled(not c.blocked and not c.secret)
        self.details.setPlainText(c.blocked or c.auth_status + "\n" + c.note)

    def _edit(self, *_):
        row = self.table.currentRow()
        if not 0 <= row < len(self.visible):
            return
        c = self.visible[row]
        c.profile.base_url, c.profile.model, c.profile.api_mode = self.address.text().strip(), self.model.text().strip(), self.mode.currentData()
        if self.key.isModified() and self.key.text().strip():
            from linguafloat.cc_switch import _key_value
            c.secret = _key_value(self.key.text())
            c.profile.env_key = ""
        for col, value in ((1, c.profile.api_mode), (2, c.profile.model)):
            self.table.item(row, col).setText(value)
        complete = all((c.profile.base_url, c.profile.model, c.profile.api_mode, c.secret))
        self.table.item(row, 3).setText("可导入（尚未连接测试）" if complete else "需要补全")

    def selected_candidates(self):
        return list(self.visible)


class OpenCodeServiceDialog(AppDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OpenCode 官方服务快捷配置")
        self.setStyleSheet(APP_STYLESHEET)
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        info = QLabel("客户端配置导入与官方服务是两个入口。请选择自己的计费方案；保存不调用 API，也不自动启用。")
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.plan = QComboBox()
        self.plan.addItem("请选择计费方案", "")
        self.plan.addItem("官方按量接口", "paid")
        self.plan.addItem("Go 订阅（沿用本地会话适配）", "go")
        self.mode = QComboBox()
        for label, mode in MODE_LABELS[1:]:
            self.mode.addItem(label, mode)
        self.model = QLineEdit()
        self.model.setPlaceholderText("填写有权限且支持该协议的准确模型 ID")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        form.addRow("计费方案", self.plan)
        form.addRow("协议", self.mode)
        form.addRow("接口地址", self.address)
        form.addRow("模型", self.model)
        form.addRow("API Key", self.key)
        layout.addLayout(form)
        self.status = QLabel("官方依据：https://opencode.ai/v2/docs/console/api/inference/")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.plan.currentIndexChanged.connect(self._address)
        self.mode.currentIndexChanged.connect(self._address)

    def _address(self):
        plan, mode = self.plan.currentData(), self.mode.currentData()
        family = {"responses": "openai/v1", "chat_completions": "openai/v1", "anthropic": "anthropic/v1", "gemini": "google/v1beta"}[mode]
        self.address.setText("https://opencode.ai/inference/" + ("go/" if plan == "go" else "") + family if plan else "")

    def _confirm(self):
        if self.plan.currentData() == "go" and self.mode.currentData() not in {"responses", "chat_completions"}:
            self.status.setText("本轮仅保留 Go 的 OpenAI 协议适配，尚未验证其他 Go 协议，不自动切换方案。")
            return
        if not all((self.plan.currentData(), self.model.text().strip(), self.key.text().strip())):
            self.status.setText("请填写计费方案、准确模型和 API Key。")
            return
        self.accept()

    def candidate(self):
        from linguafloat.cc_switch import _key_value
        from linguafloat.config import create_provider_id
        p = ProviderProfile(create_provider_id("opencode"), "OpenCode " + ("Go" if self.plan.currentData() == "go" else "按量"),
            self.address.text(), self.model.text().strip(), self.mode.currentData(), env_key="", native_auth="bearer")
        return Candidate("opencode", "service", p, _key_value(self.key.text()))
