from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QCursor, QFont, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

from linguafloat import __version__
from linguafloat.codex_config import default_codex_config_path
from linguafloat.config import AppSettings, ProviderProfile, TARGET_LANGUAGES
from linguafloat.game_mode import GAME_MODES
from linguafloat.assets import Background, app_icon
from linguafloat.provider_tools import address_suggestion
from linguafloat.model_picker import ModelSelector
from linguafloat.document_ui import DocumentPage
from linguafloat.ui_layout import FieldGrid
from linguafloat.resize import create_resize_edges, layout_resize_edges
from linguafloat.theme import APP_STYLESHEET, FLOAT_STYLESHEET, section_heading, separator, set_state


def apply_ui_font(application: QApplication, point_size: int) -> None:
    font = QFont()
    font.setFamilies(
        ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial"]
    )
    font.setPointSize(point_size)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    application.setStyle("Fusion")
    application.setFont(font)
    # Windows supplies a separate 9pt class font for item views and combo popups.
    application.setFont(font, "QAbstractItemView")


class WindowDragHandle(QLabel):
    moved = Signal()

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self._drag_origin: QPoint | None = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("拖动窗口")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.window().pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None:
            window = self.window()
            position = event.globalPosition().toPoint() - self._drag_origin
            screen = QGuiApplication.screenAt(event.globalPosition().toPoint()) or window.screen()
            area = screen.availableGeometry()
            position.setX(max(area.left(), min(position.x(), area.right() - window.width() + 1)))
            position.setY(max(area.top(), min(position.y(), area.bottom() - window.height() + 1)))
            window.move(position)
            self.moved.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None


class MainWindow(QMainWindow):
    save_requested = Signal(str, str, str, str, str, str, str, str, str, int)
    delete_key_requested = Signal(str)
    pause_requested = Signal(bool)
    provider_selected = Signal(str)
    add_provider_requested = Signal()
    delete_provider_requested = Signal(str)
    import_codex_requested = Signal(str)
    export_codex_requested = Signal(str, str, bool)
    connection_test_requested = Signal(str, str, str, str, str, str, str)
    screen_select_requested = Signal()
    screen_pause_requested = Signal()
    screen_stop_requested = Signal()
    screen_intervals_changed = Signal(float, float)
    screen_move_requested = Signal()
    input_language_requested = Signal(str)
    input_hotkey_requested = Signal(str)
    self_test_requested = Signal()
    game_mode_requested = Signal(str)
    game_paths_requested = Signal(list)
    active_provider_requested = Signal(str)
    active_model_requested = Signal(str)
    duplicate_provider_requested = Signal(str)
    provider_tools_requested = Signal(str)
    clipboard_requested = Signal()
    function_save_requested = Signal()
    help_requested = Signal()

    def __init__(self, settings: AppSettings, credential_source: str) -> None:
        super().__init__()
        self._paused = False
        self._game_active = False
        self._trigger_error = ""
        self._allow_close = False
        self._updating_profiles = False
        self._profiles: dict[str, ProviderProfile] = {}
        self._editing_id = ""
        self._saved_form = None
        self._active_id = settings.active_provider
        self._model_choices = []
        self._favorite_models = []
        self.setWindowTitle(f"浮译 LinguaFloat v{__version__}")
        self.setMinimumSize(860, 620)
        self.resize(1060, 840)
        self.setWindowIcon(app_icon())
        self.setStyleSheet(APP_STYLESHEET)

        shell = Background()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(20, 12, 20, 8)
        shell_layout.setSpacing(10)
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(4, 0, 4, 4)
        header_layout.setSpacing(10)

        title = QLabel("浮译 LinguaFloat")
        title.setObjectName("title")
        title_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(QSize(32, 32)))
        logo.setFixedSize(36, 36)
        title_row.addWidget(logo)
        title_row.addWidget(title)
        self.version_label = QLabel(f"v{__version__}")
        self.version_label.setAccessibleName("软件版本")
        self.version_label.setStyleSheet("color: #68717d; font-size: 10pt;")
        title_row.addWidget(self.version_label)
        title_row.addStretch(1)
        self.game_badge = QLabel("游戏模式关闭")
        self.game_badge.setObjectName("badge")
        self.screen_badge = QLabel("区域未启动")
        self.screen_badge.setObjectName("badge")
        title_row.addWidget(self.game_badge)
        title_row.addWidget(self.screen_badge)
        self.help_button = QPushButton("帮助与更新")
        self.help_button.setToolTip("首次使用指南、许可证和软件更新")
        self.help_button.clicked.connect(self.help_requested)
        title_row.addWidget(self.help_button)
        header_layout.addLayout(title_row)
        active_row = QHBoxLayout()
        self.active_provider_combo = QComboBox()
        self.active_provider_combo.setAccessibleName("当前使用的配置")
        self.active_provider_combo.setMinimumContentsLength(10)
        self.active_provider_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.active_provider_combo.currentIndexChanged.connect(self._active_changed)
        self.active_model_combo = ModelSelector()
        self.active_model_combo.setMinimumContentsLength(10)
        self.active_model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.active_model_combo.setEditable(True)
        self.active_model_combo.use_menu_display()
        self.active_model_combo.manage_requested.connect(self._manage_active_models)
        self.active_model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.active_model_combo.setAccessibleName("当前使用的模型")
        self.active_model_combo.activated.connect(lambda: self._activate_model())
        self.active_model_combo.lineEdit().returnPressed.connect(self._activate_model)
        active_row.addWidget(QLabel("当前配置"))
        active_row.addWidget(self.active_provider_combo, 1)
        active_row.addWidget(QLabel("模型"))
        active_row.addWidget(self.active_model_combo, 1)
        self.active_connection_status = QLabel("当前配置：尚未测试")
        self.active_connection_status.setWordWrap(True)
        self.active_connection_status.setTextFormat(Qt.TextFormat.PlainText)
        self.active_connection_status.setStyleSheet("color: #68717d; font-size: 10pt;")
        # These controls belong to daily translation, not the shared header.
        while active_row.count():
            child = active_row.takeAt(0)
            if child.widget() not in (self.active_provider_combo, self.active_model_combo):
                child.widget().deleteLater()
        shell_layout.addWidget(header)
        self.pages = QTabWidget()
        self.pages.setDocumentMode(True)
        self.pages.setIconSize(QSize(18, 18))
        self.pages.setAccessibleName("设置页面")
        shell_layout.addWidget(self.pages, 1)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 12, 20, 16)
        layout.setSpacing(10)
        self.function_layout = layout
        self.function_scroll = self._scroll(root)
        function_page = QWidget()
        function_page.setObjectName("surface")
        function_page_layout = QVBoxLayout(function_page)
        function_page_layout.setContentsMargins(0, 0, 0, 0)
        function_page_layout.setSpacing(0)
        function_page_layout.addWidget(self.function_scroll, 1)
        self.pages.addTab(function_page, qta.icon("mdi6.tune-variant", color="#66717d"), "功能设置")
        layout.addWidget(section_heading("日常翻译", "mdi6.translate"))
        daily_fields = FieldGrid()
        daily_fields.add_field("当前配置", self.active_provider_combo)
        daily_fields.add_field("模型", self.active_model_combo)
        layout.addWidget(daily_fields)
        layout.addWidget(self.active_connection_status)
        layout.addWidget(separator())

        status_row = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("statusRunning")
        self.pause_button = QPushButton("暂停监听")
        self.pause_button.setIcon(qta.icon("mdi6.pause", color="#303841"))
        self.pause_button.clicked.connect(self._toggle_paused)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.pause_button)
        layout.addLayout(status_row)

        game_row = QHBoxLayout()
        game_row.addWidget(section_heading("游戏模式", "mdi6.controller-classic-outline"), 1)
        self.game_mode_combo = QComboBox()
        self.game_mode_combo.setAccessibleName("游戏模式")
        for code, label in GAME_MODES.items():
            self.game_mode_combo.addItem(label, code)
        self.game_mode_combo.setCurrentIndex(self.game_mode_combo.findData(settings.game_mode))
        self.game_mode_combo.currentIndexChanged.connect(
            lambda: self.game_mode_requested.emit(self.game_mode_combo.currentData()))
        game_row.addWidget(self.game_mode_combo)
        self.game_add_button = QPushButton("添加游戏")
        self.game_add_button.setIcon(qta.icon("mdi6.plus", color="#303841"))
        self.game_add_button.clicked.connect(self._add_game)
        game_row.addWidget(self.game_add_button)
        self.game_remove_button = QToolButton()
        self.game_remove_button.setIcon(qta.icon("mdi6.delete-outline", color="#a32929"))
        self.game_remove_button.setToolTip("移除所选游戏")
        self.game_remove_button.setAccessibleName("移除所选游戏")
        self.game_remove_button.setFixedSize(38, 38)
        self.game_remove_button.clicked.connect(self._remove_game)
        game_row.addWidget(self.game_remove_button)
        layout.addLayout(game_row)
        self.game_list = QListWidget()
        self.game_list.setAccessibleName("自动游戏模式程序列表")
        self.game_list.setMaximumHeight(108)
        self.game_list.currentRowChanged.connect(lambda row: self.game_remove_button.setEnabled(row >= 0))
        layout.addWidget(self.game_list)
        self.game_status = QLabel("游戏模式：关闭")
        self.game_status.setTextFormat(Qt.TextFormat.PlainText)
        self.game_status.setWordWrap(True)
        layout.addWidget(self.game_status)
        self.set_game_settings(settings.game_mode, settings.game_paths)

        screen_row = QHBoxLayout()
        screen_row.addWidget(section_heading("屏幕区域翻译", "mdi6.crop-free"))
        screen_row.addStretch()
        self.screen_select_button = QPushButton("选择区域")
        self.screen_select_button.setObjectName("primary")
        self.screen_select_button.setIcon(qta.icon("mdi6.crop-free", color="#ffffff"))
        self.screen_select_button.clicked.connect(self.screen_select_requested)
        screen_row.addWidget(self.screen_select_button)
        self.screen_pause_button = QToolButton()
        self.screen_pause_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.screen_pause_button.setText("暂停")
        self.screen_pause_button.setIcon(qta.icon("mdi6.pause", color="#303841"))
        self.screen_pause_button.setToolTip("暂停区域翻译")
        self.screen_pause_button.setAccessibleName("暂停区域翻译")
        self.screen_pause_button.setFixedSize(96, 38)
        self.screen_pause_button.clicked.connect(self.screen_pause_requested)
        self.screen_pause_button.setEnabled(False)
        screen_row.addWidget(self.screen_pause_button)
        self.screen_move_button = QToolButton()
        self.screen_move_button.setIcon(qta.icon("mdi6.cursor-move", color="#303841"))
        self.screen_move_button.setToolTip("移动识别区域")
        self.screen_move_button.setAccessibleName("移动识别区域")
        self.screen_move_button.setFixedSize(38, 38)
        self.screen_move_button.setEnabled(False)
        self.screen_move_button.clicked.connect(self.screen_move_requested)
        screen_row.addWidget(self.screen_move_button)
        self.screen_stop_button = QToolButton()
        self.screen_stop_button.setIcon(qta.icon("mdi6.stop", color="#a32929"))
        self.screen_stop_button.setToolTip("停止区域翻译")
        self.screen_stop_button.setAccessibleName("停止区域翻译")
        self.screen_stop_button.setFixedSize(38, 38)
        self.screen_stop_button.clicked.connect(self.screen_stop_requested)
        self.screen_stop_button.setEnabled(False)
        screen_row.addWidget(self.screen_stop_button)
        layout.addLayout(screen_row)
        intervals = QHBoxLayout()
        intervals.addWidget(QLabel("识别间隔"))
        self.ocr_interval_spin = QDoubleSpinBox()
        self.ocr_interval_spin.setRange(0.5, 5.0)
        self.ocr_interval_spin.setSingleStep(0.5)
        self.ocr_interval_spin.setDecimals(1)
        self.ocr_interval_spin.setSuffix(" 秒")
        self.ocr_interval_spin.setValue(settings.ocr_interval)
        self.ocr_interval_spin.setMaximumWidth(150)
        intervals.addWidget(self.ocr_interval_spin)
        intervals.addSpacing(12)
        intervals.addWidget(QLabel("翻译最短间隔"))
        self.translation_interval_spin = QDoubleSpinBox()
        self.translation_interval_spin.setRange(2, 30)
        self.translation_interval_spin.setDecimals(1)
        self.translation_interval_spin.setSuffix(" 秒")
        self.translation_interval_spin.setValue(settings.translation_interval)
        intervals.addWidget(self.translation_interval_spin)
        self.translation_interval_spin.setMaximumWidth(150)
        intervals.addStretch()
        layout.addLayout(intervals)
        self.ocr_interval_spin.valueChanged.connect(self._screen_intervals_changed)
        self.translation_interval_spin.valueChanged.connect(self._screen_intervals_changed)
        self.screen_status = QLabel("未选择区域")
        self.screen_status.setTextFormat(Qt.TextFormat.PlainText)
        self.screen_status.setWordWrap(True)
        self.screen_status.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.screen_status)

        input_row = QHBoxLayout()
        layout.addWidget(separator())
        layout.addWidget(section_heading("选中与右键翻译", "mdi6.cursor-text"))
        self.function_fields = FieldGrid()
        layout.addWidget(self.function_fields)
        layout.addWidget(separator())
        input_row.addWidget(section_heading("输入翻译", "mdi6.translate"))
        input_row.addStretch()
        self.input_language_combo = QComboBox()
        for code, (label, _) in TARGET_LANGUAGES.items():
            self.input_language_combo.addItem(label, code)
        self.input_language_combo.setCurrentIndex(max(0, self.input_language_combo.findData(settings.input_language)))
        self.input_language_combo.setAccessibleName("输入翻译目标语言")
        self.input_language_combo.currentIndexChanged.connect(
            lambda: self.input_language_requested.emit(self.input_language_combo.currentData())
        )
        layout.addLayout(input_row)
        input_key_row = QHBoxLayout()
        self.input_hotkey_edit = QLineEdit(settings.input_hotkey)
        self.input_hotkey_edit.setAccessibleName("输入翻译快捷键")
        input_key_row.addWidget(self.input_hotkey_edit, 1)
        self.input_hotkey_save = QToolButton()
        self.input_hotkey_save.setIcon(qta.icon("mdi6.content-save-outline", color="#303841"))
        self.input_hotkey_save.setToolTip("保存输入翻译快捷键")
        self.input_hotkey_save.setAccessibleName("保存输入翻译快捷键")
        self.input_hotkey_save.setFixedSize(38, 38)
        self.input_hotkey_save.clicked.connect(lambda: self.input_hotkey_requested.emit(self.input_hotkey_edit.text()))
        input_key_row.addWidget(self.input_hotkey_save)
        input_fields = FieldGrid()
        input_fields.add_field("输入翻译快捷键", input_key_row)
        input_fields.add_field("目标语言", self.input_language_combo)
        layout.addWidget(input_fields)

        # Keep the region controls near the top; secondary game settings follow.
        layout.removeItem(game_row)
        layout.removeWidget(self.game_list)
        layout.removeWidget(self.game_status)
        layout.addWidget(separator())
        layout.addLayout(game_row)
        layout.addWidget(self.game_list)
        layout.addWidget(self.game_status)

        layout.addWidget(separator())
        layout.addWidget(section_heading("功能测试", "mdi6.test-tube"))
        test_row = QHBoxLayout()
        self.self_test_input = QLineEdit("你好，LinguaFloat。")
        self.self_test_input.setAccessibleName("功能测试文字")
        test_row.addWidget(self.self_test_input, 1)
        self.self_test_button = QPushButton("一键测试")
        self.self_test_button.setIcon(qta.icon("mdi6.test-tube", color="#202124"))
        self.self_test_button.setToolTip("检查配置、监听、OCR 模型和真实翻译请求")
        self.self_test_button.clicked.connect(self.self_test_requested)
        test_row.addWidget(self.self_test_button)
        layout.addLayout(test_row)
        self.self_test_result = QTextBrowser()
        self.self_test_result.setPlainText("尚未测试")
        self.self_test_result.setFixedHeight(56)
        self.self_test_result.setAccessibleName("功能测试结果")
        self.self_test_result.setOpenExternalLinks(False)
        layout.addWidget(self.self_test_result)

        capture_row = QHBoxLayout()
        self.clipboard_button = QPushButton("翻译剪贴板文字")
        self.clipboard_button.setIcon(qta.icon("mdi6.clipboard-text-outline", color="#303841"))
        self.clipboard_button.clicked.connect(self.clipboard_requested)
        capture_row.addWidget(self.clipboard_button)
        capture_row.addStretch()
        layout.addLayout(capture_row)
        self.capture_diagnostics = QTextBrowser()
        self.capture_diagnostics.setAccessibleName("取词诊断")
        self.capture_diagnostics.setMaximumHeight(100)
        self.capture_diagnostics.setPlainText("取词诊断：尚未触发")
        self.diagnostics_toggle = QToolButton()
        self.diagnostics_toggle.setObjectName("disclosure")
        self.diagnostics_toggle.setText("取词诊断")
        self.diagnostics_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.diagnostics_toggle.setCheckable(True)
        self.diagnostics_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.diagnostics_toggle.toggled.connect(self._toggle_diagnostics)
        layout.addWidget(self.diagnostics_toggle)
        layout.addWidget(self.capture_diagnostics)
        self.capture_diagnostics.hide()
        self.function_save_button = QPushButton("保存功能设置")
        self.function_save_button.setIcon(qta.icon("mdi6.content-save-outline", color="#303841"))
        self.function_save_button.clicked.connect(self.function_save_requested)
        function_footer = QWidget()
        function_footer.setObjectName("footer")
        function_footer_layout = QHBoxLayout(function_footer)
        function_footer_layout.addStretch()
        function_footer_layout.addWidget(self.function_save_button)
        function_page_layout.addWidget(function_footer)
        layout.addStretch()

        config_split = QSplitter()
        self.pages.addTab(config_split, qta.icon("mdi6.server-outline", color="#66717d"), "配置设置")
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 14, 12, 12)
        side.setSpacing(8)
        self.provider_search = QLineEdit()
        self.provider_search.setPlaceholderText("搜索配置")
        self.provider_search.textChanged.connect(self._filter_providers)
        side.addWidget(self.provider_search)
        self.provider_list = QListWidget()
        self.provider_list.setAccessibleName("配置列表")
        self.provider_list.currentItemChanged.connect(self._list_selected)
        side.addWidget(self.provider_list, 1)
        duplicate = QPushButton("复制配置")
        duplicate.setIcon(qta.icon("mdi6.content-copy", color="#303841"))
        duplicate.clicked.connect(lambda: self.duplicate_provider_requested.emit(self._editing_id))
        side.addWidget(duplicate)
        config_split.addWidget(sidebar)
        detail = QWidget()
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        self.config_scroll = self._scroll(detail)
        config_right = QWidget()
        config_right.setObjectName("surface")
        config_right_layout = QVBoxLayout(config_right)
        config_right_layout.setContentsMargins(0, 0, 0, 0)
        config_right_layout.addWidget(self.config_scroll, 1)
        config_split.addWidget(config_right)
        config_split.setSizes([220, 760])
        config_split.setHandleWidth(1)
        config_split.setStretchFactor(0, 0)
        config_split.setStretchFactor(1, 1)
        config_split.setCollapsible(0, False)
        config_split.setCollapsible(1, False)
        sidebar.setMinimumWidth(170)
        self.editing_label = QLabel("编辑配置")
        self.editing_label.setStyleSheet("font-weight: 600;")
        self.editing_label.setTextFormat(Qt.TextFormat.PlainText)
        self.editing_label.setWordWrap(True)
        layout.addWidget(self.editing_label)

        layout.addWidget(separator())

        provider_row = QHBoxLayout()
        self.provider_combo = QComboBox()
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.provider_combo.hide()
        add_button = QPushButton("新增配置")
        add_button.setIcon(qta.icon("mdi6.plus", color="#303841"))
        add_button.clicked.connect(self.add_provider_requested)
        side.insertWidget(1, add_button)
        self.delete_provider_button = QToolButton()
        self.delete_provider_button.setIcon(qta.icon("mdi6.delete-outline", color="#b52e3a"))
        self.delete_provider_button.setToolTip("删除配置")
        self.delete_provider_button.setAccessibleName("删除配置")
        self.delete_provider_button.setFixedSize(34, 34)
        self.delete_provider_button.clicked.connect(self._request_provider_delete)
        layout.removeWidget(self.editing_label)
        provider_row.addWidget(self.editing_label, 1)
        provider_row.addWidget(self.delete_provider_button)
        layout.insertLayout(0, provider_row)

        form = FieldGrid()

        self.provider_name_edit = QLineEdit()
        self.provider_name_edit.setClearButtonEnabled(True)
        form.add_field("显示名称", self.provider_name_edit)
        self.favorite_check = QCheckBox("收藏此配置")
        form.add_field("", self.favorite_check)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setClearButtonEnabled(True)
        form.add_field("Base URL", self.base_url_edit, wide=True)
        self.address_hint = QLabel()
        self.address_hint.setWordWrap(True)
        self.address_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.base_url_edit.textChanged.connect(self._check_address)
        form.add_field("", self.address_hint, wide=True)
        self.fix_address_button = QPushButton("应用地址建议")
        self.fix_address_button.clicked.connect(lambda: self.base_url_edit.setText(address_suggestion(self.base_url_edit.text())))
        form.add_field("", self.fix_address_button, wide=True)

        self.api_mode_combo = QComboBox()
        self.api_mode_combo.addItem("Responses API", "responses")
        self.api_mode_combo.addItem("Chat Completions", "chat_completions")
        form.add_field("接口类型", self.api_mode_combo)

        self.model_edit = QLineEdit()
        self.model_edit.setClearButtonEnabled(True)
        self.model_search_combo = ModelSelector()
        self.model_search_combo.manage_requested.connect(self._focus_model_tools)
        self.model_search_combo.setMinimumContentsLength(10)
        self.model_search_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_search_combo.setEditable(True)
        self.model_search_combo.setLineEdit(self.model_edit)
        self.model_search_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_search_combo.setAccessibleName("搜索或选择模型")
        self.model_search_combo.activated.connect(self._remember_edited_model)
        self.model_search_combo.lineEdit().returnPressed.connect(lambda: self.model_edit.setText(self.model_search_combo.currentText()))
        form.add_field("模型", self.model_search_combo)
        model_row = QHBoxLayout()
        self.fetch_models_button = QPushButton("获取模型")
        self.fetch_models_button.clicked.connect(lambda: self.provider_tools_requested.emit("models"))
        model_row.addWidget(self.fetch_models_button)
        self.favorite_model_button = QToolButton()
        self.favorite_model_button.setIcon(qta.icon("mdi6.star-outline", color="#806200"))
        self.favorite_model_button.setToolTip("收藏 / 取消收藏当前模型")
        self.favorite_model_button.setAccessibleName("收藏当前模型")
        self.favorite_model_button.clicked.connect(self._toggle_model_favorite)
        model_row.addWidget(self.favorite_model_button)
        model_row.addStretch()

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_visibility_action = self.api_key_edit.addAction(
            qta.icon("mdi6.eye-outline", color="#68717d"),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.key_visibility_action.setCheckable(True)
        self.key_visibility_action.toggled.connect(self._set_key_visible)
        self._set_key_visible(False)
        form.add_field("API key", self.api_key_edit, wide=True)
        form.add_field("", model_row, wide=True)
        self.connection_status = QLabel()
        self.connection_status.setWordWrap(True)
        self.connection_status.setTextFormat(Qt.TextFormat.PlainText)
        self.connection_status.hide()
        form.add_field("", self.connection_status, wide=True)

        self.env_key_edit = QLineEdit()
        self.env_key_edit.setClearButtonEnabled(True)
        self.env_key_edit.setPlaceholderText("例如 OPENAI_API_KEY，可留空")

        self.hotkey_edit = QLineEdit(settings.hotkey)
        self.hotkey_edit.setClearButtonEnabled(True)
        self.function_fields.add_field("选中翻译快捷键", self.hotkey_edit)

        self.trigger_mode_combo = QComboBox()
        self.trigger_mode_combo.addItem("快捷键 + 右键", "both")
        self.trigger_mode_combo.addItem("仅快捷键", "hotkey")
        self.trigger_mode_combo.addItem("仅右键", "right_click")
        trigger_index = self.trigger_mode_combo.findData(settings.trigger_mode)
        self.trigger_mode_combo.setCurrentIndex(max(0, trigger_index))
        self.trigger_mode_combo.currentIndexChanged.connect(
            self._update_trigger_fields
        )
        self.function_fields.add_field("触发方式", self.trigger_mode_combo)

        layout.addWidget(form)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 120)
        self.timeout_spin.setSuffix(" 秒")
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 3)
        advanced = QWidget()
        advanced_form = FieldGrid()
        QVBoxLayout(advanced).addWidget(advanced_form)
        advanced_form.add_field("环境变量名", self.env_key_edit, wide=True)
        advanced_form.add_field("请求超时", self.timeout_spin)
        self.retries_spin.setToolTip("普通选中翻译与文档翻译共用此重试上限；输入/区域翻译保持现有重试策略。")
        self.output_token_limit_spin = QSpinBox()
        self.output_token_limit_spin.setRange(0, 1000000)
        self.output_token_limit_spin.setSpecialValueText("供应商默认")
        self.output_token_limit_spin.setToolTip("0 时不发送输出长度参数。设置后，Responses 使用 max_output_tokens，Chat 使用所选参数；请按供应商要求填写。")
        self.chat_token_parameter_combo = QComboBox()
        self.chat_token_parameter_combo.addItem("max_completion_tokens", "max_completion_tokens")
        self.chat_token_parameter_combo.addItem("max_tokens", "max_tokens")
        self.chat_token_parameter_combo.setToolTip("仅在 Chat Completions 且输出上限非零时发送；DeepSeek 等兼容接口可能要求 max_tokens。")
        self.instruction_role_combo = QComboBox()
        for label, value in (("System（默认）", "system"), ("Developer", "developer"), ("User", "user")):
            self.instruction_role_combo.addItem(label, value)
        self.instruction_role_combo.setToolTip("仅按供应商文档调整指令角色，不自动推测模型能力。")
        self.reasoning_effort_combo = QComboBox()
        self.reasoning_effort_combo.addItem("供应商默认（不发送）", "")
        for value in ("none", "minimal", "low", "medium", "high", "xhigh"):
            self.reasoning_effort_combo.addItem(value, value)
        self.reasoning_effort_combo.setToolTip("仅对文档明确支持的模型设置；不会自动启用推理参数。")
        advanced_form.add_field("输出 Token 上限", self.output_token_limit_spin)
        advanced_form.add_field("Chat 输出长度参数", self.chat_token_parameter_combo)
        advanced_form.add_field("指令角色", self.instruction_role_combo)
        advanced_form.add_field("推理强度", self.reasoning_effort_combo)
        self.thinking_mode_combo = QComboBox()
        for label, value in (("供应商默认（不发送）", ""), ("Qwen：关闭思考", "qwen_off"),
                             ("Qwen：开启思考", "qwen_on"), ("DeepSeek：关闭思考", "deepseek_off"),
                             ("DeepSeek：开启思考", "deepseek_on")):
            self.thinking_mode_combo.addItem(label, value)
        self.thinking_mode_combo.setToolTip("仅用于服务商明确支持的 Chat Completions 模型。Qwen 发送 enable_thinking，DeepSeek 发送 thinking.type；不根据模型名自动启用。MiMo 或中转站仅在文档确认参数相同后使用。")
        advanced_form.add_field("供应商思考开关", self.thinking_mode_combo)
        self.document_concurrency_spin = QSpinBox()
        self.document_concurrency_spin.setRange(1, 6)
        self.document_rpm_spin = QSpinBox()
        self.document_rpm_spin.setRange(1, 10000)
        self.document_tpm_spin = QSpinBox()
        self.document_tpm_spin.setRange(0, 100000000)
        self.document_tpm_spin.setSpecialValueText("未设置")
        self.document_concurrency_spin.setToolTip("文档 API 最大并发，可设置 1–6。仍遵守 RPM/TPM；遇到限流或超时暂降至 1，冷却结束后每成功 3 次逐级恢复。不改变翻译模型或质量检查。")
        self.document_rpm_spin.setToolTip("该供应商和模型的文档请求每分钟上限，默认 60；请按供应商实际限额填写。普通取词请求不计入此预算。")
        self.document_tpm_spin.setToolTip("文档请求每分钟 Token 预算；0 表示未知。按字节保守预留并根据返回用量校正，不能替代供应商的精确计量。")
        layout.addWidget(section_heading("文件翻译", "mdi6.file-document-outline"))
        document_fields = FieldGrid()
        self.document_timeout_spin=QSpinBox()
        self.document_timeout_spin.setRange(30,600)
        self.document_timeout_spin.setSuffix(" 秒")
        self.document_timeout_spin.setToolTip("仅文件翻译：每次请求的读取等待上限；不影响选中/输入/区域翻译。")
        document_fields.add_field("文档请求超时",self.document_timeout_spin)
        document_fields.add_field("失败重试次数",self.retries_spin)
        document_fields.add_field("文档并发", self.document_concurrency_spin)
        document_fields.add_field("每分钟请求数（RPM）", self.document_rpm_spin)
        document_fields.add_field("每分钟 Token（TPM）", self.document_tpm_spin)
        layout.addWidget(document_fields)
        self.advanced_toggle = QCheckBox("高级设置")
        self.advanced_toggle.toggled.connect(advanced.setVisible)
        advanced.hide()
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(advanced)
        self.detect_api_button = QPushButton("检测接口")
        self.detect_api_button.setToolTip("分别测试两种接口，最多发送两次简短翻译请求")
        self.detect_api_button.clicked.connect(lambda: self.provider_tools_requested.emit("detect"))
        advanced_form.add_field("", self.detect_api_button)

        security_note = QLabel("API key 会发送到当前 Base URL；只添加你信任的服务。")
        security_note.setObjectName("securityNote")
        security_note.setWordWrap(True)
        layout.addWidget(security_note)

        key_row = QHBoxLayout()
        self.delete_key_button = QPushButton("清除当前密钥")
        self.delete_key_button.setObjectName("danger")
        self.delete_key_button.clicked.connect(self._request_key_delete)
        self.save_button = QPushButton("保存配置")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._emit_save)
        advanced_form.add_field("", self.delete_key_button)
        self.test_connection_button = QPushButton("测试连接")
        self.test_connection_button.setIcon(
            qta.icon("mdi6.connection", color="#202124")
        )
        self.test_connection_button.setToolTip("用当前填写的配置发送一条简短翻译测试")
        self.test_connection_button.clicked.connect(self._emit_connection_test)
        key_row.addWidget(self.test_connection_button)
        key_row.addStretch()
        key_row.addWidget(self.save_button)
        self.use_provider_button = QPushButton("使用此配置")
        self.use_provider_button.clicked.connect(self._use_edited)
        key_row.addWidget(self.use_provider_button)
        footer = QWidget()
        footer.setObjectName("footer")
        footer.setLayout(key_row)
        config_right_layout.addWidget(footer)

        codex_row = QHBoxLayout()
        import_button = QPushButton("导入 Codex")
        import_button.clicked.connect(self._choose_codex_import)
        export_button = QPushButton("导出当前供应商")
        export_button.clicked.connect(self._choose_codex_export)
        self.include_key_checkbox = QCheckBox("包含 API key（明文）")
        self.include_key_checkbox.setToolTip("导出 Codex 配置时包含明文 API key")
        codex_row.addWidget(import_button)
        codex_row.addWidget(export_button)
        codex_row.addStretch()
        layout.addWidget(self.include_key_checkbox)
        layout.addLayout(codex_row)

        self.activity = QTextBrowser()
        self.activity.setObjectName("activity")
        self.activity.setFixedHeight(32)
        self.activity.setOpenExternalLinks(False)
        self.activity.setText("程序已启动。")
        layout.addStretch()
        shell_layout.addWidget(self.activity)
        self.setCentralWidget(shell)

        self.documents = DocumentPage(self)
        self.pages.addTab(self.documents, qta.icon("mdi6.file-document-outline", color="#66717d"), "文件翻译")
        self.pages.currentChanged.connect(lambda _: self.activity.setVisible(self.pages.currentWidget() is not self.documents))

        self.set_profiles(settings.providers, settings.active_provider)
        self.set_key_source(credential_source)
        self._update_trigger_fields()
        self.set_paused(False)

    @staticmethod
    def _scroll(widget):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(widget)
        return area

    def _screen_intervals_changed(self) -> None:
        self.screen_intervals_changed.emit(self.ocr_interval_spin.value(), self.translation_interval_spin.value())

    def _toggle_diagnostics(self, expanded: bool) -> None:
        self.capture_diagnostics.setVisible(expanded)
        self.diagnostics_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def set_game_settings(self, mode: str, paths: list[str]) -> None:
        self.game_mode_combo.blockSignals(True)
        self.game_mode_combo.setCurrentIndex(self.game_mode_combo.findData(mode))
        self.game_mode_combo.blockSignals(False)
        self.game_list.clear()
        for path in paths:
            item = QListWidgetItem(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.game_list.addItem(item)
        self.game_remove_button.setEnabled(False)
        self.game_list.setVisible(bool(paths))

    def _game_paths(self) -> list[str]:
        return [self.game_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.game_list.count())]

    def _add_game(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择游戏实际运行的程序", "", "Windows 程序 (*.exe)")
        if paths:
            self.game_paths_requested.emit(self._game_paths() + paths)

    def _remove_game(self) -> None:
        row = self.game_list.currentRow()
        if row >= 0:
            paths = self._game_paths()
            paths.pop(row)
            self.game_paths_requested.emit(paths)

    def set_game_state(self, active: bool, reason: str, trigger_error: str = "") -> None:
        self._game_active = active
        self._trigger_error = trigger_error
        self.game_status.setText(reason)
        self.game_badge.setText("游戏模式：仅区域翻译" if active else "游戏模式关闭")
        self.game_badge.setToolTip(reason)
        set_state(self.game_badge, "paused" if active else "idle")
        self.set_paused(self._paused)

    def set_screen_status(self, message: str, active: bool, running: bool) -> None:
        self.screen_status.setText(message)
        self.screen_badge.setText("区域翻译中" if running else "区域已暂停" if active else "区域未启动")
        self.screen_badge.setToolTip(message)
        set_state(self.screen_badge, "running" if running else "paused" if active else "idle")
        set_state(self.screen_status, "running" if running else "paused" if active else "idle")
        self.screen_select_button.setText("重新框选" if active else "选择区域")
        self.screen_pause_button.setEnabled(active)
        self.screen_stop_button.setEnabled(active)
        self.screen_move_button.setEnabled(active)
        self.screen_pause_button.setIcon(qta.icon("mdi6.pause" if running else "mdi6.play", color="#303841"))
        label = "暂停区域翻译" if running else "继续区域翻译"
        self.screen_pause_button.setToolTip(label)
        self.screen_pause_button.setAccessibleName(label)
        self.screen_pause_button.setText("暂停" if running else "继续")

    def set_self_test_result(self, message: str, *, error: bool = False) -> None:
        rows = []
        for line in message.splitlines():
            # These are presentation hints only; keep the diagnostic text intact.
            color = "#20252b"
            if any(word in line for word in ("未启用", "停用", "暂停", "跳过")):
                color = "#996000"
            elif any(word in line for word in ("失败", "未通过", "缺失", "HTTP 401", "HTTP 403")):
                color = "#b52e3a"
            elif "：通过" in line:
                color = "#16734f"
            rows.append(f'<tr><td style="padding:5px 2px; color:{color}">{_html_escape(line)}</td></tr>')
        self.self_test_result.setHtml('<table width="100%" cellspacing="0">' + "".join(rows) + '</table>')
        self.self_test_result.setFixedHeight(min(280, max(72, len(rows) * 34 + 24)))
        self.self_test_result.setToolTip("有项目未通过" if error else "功能测试结果")
        # QTextBrowser's input cursor rect makes ensureWidgetVisible scroll only
        # to the first line. Position the result block explicitly instead.
        QTimer.singleShot(0, lambda: self.function_scroll.verticalScrollBar().setValue(
            max(0, self.self_test_result.y() - 65)))

    def set_profiles(
        self, profiles: list[ProviderProfile], active_provider: str, editing_id: str | None = None
    ) -> None:
        self._updating_profiles = True
        selected = editing_id or self._editing_id or active_provider
        if not any(p.id == selected for p in profiles):
            selected = active_provider
        self._profiles = {item.id: item for item in profiles}
        self.provider_combo.clear()
        active_index = 0
        for index, profile in enumerate(profiles):
            self.provider_combo.addItem(profile.name, profile.id)
            if profile.id == selected:
                active_index = index
        self.provider_combo.setCurrentIndex(active_index)
        self._updating_profiles = False
        self._load_profile(self.provider_combo.currentData())
        self.set_active_profile(active_provider)
        self.delete_provider_button.setEnabled(len(profiles) > 1)

    def set_active_profile(self, provider_id: str) -> None:
        self._active_id = provider_id
        self.documents.set_profiles(list(self._profiles.values()), provider_id)
        self.active_provider_combo.blockSignals(True)
        self.active_provider_combo.clear()
        for profile in sorted(self._profiles.values(), key=lambda p: (not p.favorite, p.name.casefold())):
            self.active_provider_combo.addItem(("★ " if profile.favorite else "") + profile.name, profile.id)
        self.active_provider_combo.setCurrentIndex(self.active_provider_combo.findData(provider_id))
        self.active_provider_combo.blockSignals(False)
        profile = self._profiles.get(provider_id)
        if profile:
            self.active_model_combo.blockSignals(True)
            self.active_model_combo.clear()
            self.active_model_combo.favorites = list(profile.favorite_models)
            self.active_model_combo.addItems(list(dict.fromkeys([*profile.favorite_models, profile.model, *profile.models])))
            self.active_model_combo.setCurrentText(profile.model)
            self.active_model_combo.blockSignals(False)
        self._filter_providers()

    def _active_changed(self) -> None:
        value = self.active_provider_combo.currentData()
        if value and value != self._active_id:
            self.active_provider_requested.emit(value)

    def _activate_model(self) -> None:
        value = self.active_model_combo.currentText().strip()
        profile = self._profiles.get(self._active_id)
        if profile and value and value != profile.model:
            if self._editing_id == self._active_id and not self.confirm_discard():
                self.set_active_profile(self._active_id)
                return
            self.active_model_requested.emit(value)

    def _manage_active_models(self):
        self.provider_combo.setCurrentIndex(self.provider_combo.findData(self._active_id))
        if self._editing_id == self._active_id:
            self._focus_model_tools()

    def _focus_model_tools(self):
        self.pages.setCurrentIndex(1)
        self.config_scroll.ensureWidgetVisible(self.fetch_models_button)
        self.fetch_models_button.setFocus()

    def _remember_edited_model(self):
        value = self.model_edit.text().strip()
        if value and value not in self._model_choices:
            self._model_choices.append(value)

    def _filter_providers(self) -> None:
        self.provider_list.blockSignals(True)
        self.provider_list.clear()
        search = self.provider_search.text().casefold().strip()
        for profile in sorted(self._profiles.values(), key=lambda p: (not p.favorite, p.name.casefold())):
            if search and search not in (profile.name + " " + profile.base_url).casefold():
                continue
            label = ("★ " if profile.favorite else "") + profile.name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            active = profile.id == self._active_id
            item.setIcon(qta.icon("mdi6.check-circle-outline" if active else "mdi6.server-outline",
                                  color="#16845b" if active else "#66717d"))
            item.setToolTip(("正在使用\n" if active else "") + profile.name + "\n" + profile.base_url)
            self.provider_list.addItem(item)
            if profile.id == self._editing_id:
                self.provider_list.setCurrentItem(item)
        self.provider_list.blockSignals(False)

    def _list_selected(self, item, previous) -> None:
        if item:
            self.provider_combo.setCurrentIndex(self.provider_combo.findData(item.data(Qt.ItemDataRole.UserRole)))

    def _form_values(self):
        return (self.provider_name_edit.text(), self.base_url_edit.text(), self.model_edit.text(),
                self.api_mode_combo.currentData(), self.env_key_edit.text(), self.api_key_edit.text(),
                self.favorite_check.isChecked(), tuple(self._model_choices), tuple(self._favorite_models),
                self.timeout_spin.value(), self.document_timeout_spin.value(), self.retries_spin.value(), self.document_concurrency_spin.value(),
                self.document_rpm_spin.value(), self.document_tpm_spin.value(),
                tuple(self.api_options().items()))

    def api_options(self):
        return dict(output_token_limit=self.output_token_limit_spin.value(),
                    chat_token_parameter=self.chat_token_parameter_combo.currentData(),
                    instruction_role=self.instruction_role_combo.currentData(),
                    reasoning_effort=self.reasoning_effort_combo.currentData(),
                    thinking_mode=self.thinking_mode_combo.currentData())

    def mark_saved(self):
        self._saved_form = self._form_values()

    def is_dirty(self):
        return self._saved_form is not None and self._form_values() != self._saved_form

    def confirm_discard(self) -> bool:
        if not self.is_dirty():
            return True
        result = QMessageBox.question(self, "未保存的配置", "是否保存当前配置的修改？",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Save)
        if result == QMessageBox.StandardButton.Save:
            self._emit_save()
            return not self.is_dirty()
        return result == QMessageBox.StandardButton.Discard

    def _check_address(self):
        if not hasattr(self, "fix_address_button"):
            return
        raw = self.base_url_edit.text().strip()
        try:
            suggestion = address_suggestion(raw)
        except ValueError:
            suggestion = raw
        changed = bool(raw and raw != suggestion)
        self.address_hint.setText("建议地址：" + suggestion if changed else "")
        self.address_hint.setVisible(changed)
        self.fix_address_button.setVisible(changed)

    def set_models(self, values):
        current = self.model_edit.text()
        self._model_choices = list(dict.fromkeys(values))
        self.model_search_combo.blockSignals(True)
        self.model_search_combo.clear()
        self.model_search_combo.favorites = list(self._favorite_models)
        self.model_search_combo.addItems(list(dict.fromkeys([*self._favorite_models, *self._model_choices])))
        self.model_search_combo.setCurrentText(current)
        self.model_search_combo.blockSignals(False)
        self.model_search_combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.model_search_combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def _toggle_model_favorite(self):
        value = self.model_edit.text().strip()
        if not value:
            return
        if value in self._favorite_models:
            self._favorite_models.remove(value)
        else:
            self._favorite_models.append(value)
        self.set_models(self._model_choices)
        self.set_activity("模型收藏已更新，保存配置后生效。")

    def _use_edited(self):
        if self.is_dirty():
            self._emit_save()
            if self.is_dirty():
                return
        self.active_provider_requested.emit(self._editing_id)

    def set_key_source(self, source: str, api_key: str = "") -> None:
        if source == "environment":
            text = "当前密钥来自环境变量"
        elif source == "keyring":
            text = "当前密钥已保存在 Windows 凭据管理器"
        else:
            text = "请输入当前供应商的 API key"
        self._hide_key()
        self.api_key_edit.setText(api_key)
        self.api_key_edit.setModified(False)
        self.api_key_edit.setCursorPosition(0)
        self.api_key_edit.setPlaceholderText("请输入当前供应商的 API key")
        self.api_key_edit.setToolTip(text)
        if hasattr(self, "timeout_spin"):
            self.mark_saved()

    def _set_key_visible(self, visible: bool) -> None:
        self.api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        label = "隐藏 API key" if visible else "显示 API key"
        self.key_visibility_action.setText(label)
        self.key_visibility_action.setToolTip(label)
        self.key_visibility_action.setIcon(qta.icon(
            "mdi6.eye-off-outline" if visible else "mdi6.eye-outline",
            color="#68717d",
        ))

    def _hide_key(self) -> None:
        self.key_visibility_action.setChecked(False)
        self._set_key_visible(False)

    def hideEvent(self, event) -> None:
        self._hide_key()
        super().hideEvent(event)

    def _provider_changed(self) -> None:
        if self._updating_profiles:
            return
        provider_id = self.provider_combo.currentData()
        if provider_id:
            if not self.confirm_discard():
                self.provider_combo.blockSignals(True)
                self.provider_combo.setCurrentIndex(self.provider_combo.findData(self._editing_id))
                self.provider_combo.blockSignals(False)
                self._filter_providers()
                return
            self._load_profile(provider_id)
            self.provider_selected.emit(provider_id)
            self._filter_providers()

    def _load_profile(self, provider_id: str) -> None:
        self._editing_id = provider_id
        self.set_key_source("missing")
        self.connection_status.hide()
        profile = self._profiles.get(provider_id)
        if profile is None:
            return
        self.provider_name_edit.setText(profile.name)
        self.base_url_edit.setText(profile.base_url)
        self.model_edit.setText(profile.model)
        self.env_key_edit.setText(profile.env_key)
        index = self.api_mode_combo.findData(profile.api_mode)
        self.api_mode_combo.setCurrentIndex(max(0, index))
        self.editing_label.setText("编辑配置 · " + profile.name)
        self.favorite_check.setChecked(profile.favorite)
        self._favorite_models = list(profile.favorite_models)
        self.set_models(profile.models)
        self.timeout_spin.setValue(int(profile.timeout))
        self.document_timeout_spin.setValue(profile.document_timeout)
        self.retries_spin.setValue(profile.retries)
        self.document_concurrency_spin.setValue(profile.document_concurrency)
        self.document_rpm_spin.setValue(profile.document_rpm)
        self.document_tpm_spin.setValue(profile.document_tpm)
        self.output_token_limit_spin.setValue(profile.output_token_limit)
        for combo, value in ((self.chat_token_parameter_combo, profile.chat_token_parameter),
                             (self.instruction_role_combo, profile.instruction_role),
                             (self.reasoning_effort_combo, profile.reasoning_effort),
                             (self.thinking_mode_combo, profile.thinking_mode)):
            combo.setCurrentIndex(max(0, combo.findData(value)))
        self.mark_saved()

    def _emit_save(self) -> None:
        provider_id = self._editing_id
        api_mode = self.api_mode_combo.currentData() or "responses"
        self.save_requested.emit(
            self.hotkey_edit.text().strip(),
            provider_id,
            self.provider_name_edit.text().strip(),
            self.base_url_edit.text().strip(),
            self.model_edit.text().strip(),
            api_mode,
            self.env_key_edit.text().strip(),
            self.api_key_edit.text().strip() if self.api_key_edit.isModified() else "",
            str(self.trigger_mode_combo.currentData() or "both"),
            14,
        )

    def _emit_connection_test(self) -> None:
        self._hide_key()
        self.connection_test_requested.emit(
            self.provider_combo.currentData() or "",
            self.provider_name_edit.text().strip(),
            self.base_url_edit.text().strip(),
            self.model_edit.text().strip(),
            self.api_mode_combo.currentData() or "responses",
            self.env_key_edit.text().strip(),
            self.api_key_edit.text().strip(),
        )

    def set_connection_status(
        self, message: str, *, pending: bool = False, error: bool = False
    ) -> None:
        self.test_connection_button.setEnabled(not pending)
        self.test_connection_button.setText("测试中…" if pending else "测试连接")
        self.connection_status.setText(message)
        color = "#a32929" if error else ("#59636f" if pending else "#147d4f")
        self.connection_status.setStyleSheet(f"color: {color};")
        self.connection_status.show()
        self.config_scroll.ensureWidgetVisible(self.connection_status)

    def _update_trigger_fields(self) -> None:
        mode = str(self.trigger_mode_combo.currentData() or "both")
        self.hotkey_edit.setEnabled(mode != "right_click")
        self.set_paused(self._paused)

    def _request_provider_delete(self) -> None:
        provider_id = self.provider_combo.currentData() or ""
        if not provider_id:
            return
        answer = QMessageBox.question(
            self,
            "删除供应商",
            "确定删除当前供应商及其安全保存的 API key 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_provider_requested.emit(provider_id)

    def _request_key_delete(self) -> None:
        provider_id = self.provider_combo.currentData() or ""
        if not provider_id:
            return
        answer = QMessageBox.question(
            self,
            "清除 API key",
            "确定清除当前供应商安全保存的 API key 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_key_requested.emit(provider_id)

    def _choose_codex_import(self) -> None:
        if not self.confirm_discard():
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "导入 Codex 配置",
            str(default_codex_config_path()),
            "Codex 配置 (config.toml *.toml);;所有文件 (*)",
        )
        if selected:
            self.import_codex_requested.emit(selected)

    def _choose_codex_export(self) -> None:
        provider_id = self.provider_combo.currentData() or "provider"
        default_path = str(Path.home() / "Documents" / f"{provider_id}-codex.toml")
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Codex 配置",
            default_path,
            "Codex 配置 (*.toml)",
        )
        if not selected:
            return
        include_key = self.include_key_checkbox.isChecked()
        if include_key:
            answer = QMessageBox.warning(
                self,
                "密钥将以明文导出",
                "导出的 TOML 文件将包含当前 API key。请勿分享或提交该文件。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.export_codex_requested.emit(provider_id, selected, include_key)

    def _toggle_paused(self) -> None:
        self.pause_requested.emit(False if self._trigger_error else not self._paused)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.pause_button.setIcon(qta.icon("mdi6.play" if paused else "mdi6.pause", color="#303841"))
        if self._game_active:
            self.status_label.setText("游戏模式已启用，仅保留区域翻译" + ("（普通监听已暂停）" if paused else ""))
            self.status_label.setObjectName("statusPaused")
            self.pause_button.setText("开始监听" if paused else "暂停监听")
        elif self._trigger_error:
            self.status_label.setText("监听未恢复：" + self._trigger_error)
            self.status_label.setObjectName("statusPaused")
            self.pause_button.setText("重试监听")
        elif paused:
            self.status_label.setText("● 已暂停")
            self.status_label.setObjectName("statusPaused")
            self.pause_button.setText("开始监听")
        else:
            mode = str(self.trigger_mode_combo.currentData() or "both")
            self.status_label.setText(f"● 正在监听（{self._mode_label(mode)}）")
            self.status_label.setObjectName("statusRunning")
            self.pause_button.setText("暂停监听")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    @staticmethod
    def _mode_label(mode: str) -> str:
        return {
            "both": "快捷键 + 右键",
            "hotkey": "快捷键",
            "right_click": "右键",
        }.get(mode, "快捷键 + 右键")

    def set_activity(self, message: str, *, error: bool = False) -> None:
        color = "#a32929" if error else "#202124"
        self.activity.setHtml(f'<span style="color:{color}">{_html_escape(message)}</span>')

    def settings_saved(
        self,
        profiles: list[ProviderProfile],
        active_provider: str,
    ) -> None:
        self.set_profiles(profiles, active_provider)
        self.set_activity("配置已保存。")

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def allow_close(self) -> None:
        self._allow_close = True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        if not self.confirm_discard():
            event.ignore()
            return
        if self.is_dirty():
            self._load_profile(self._editing_id)
            self.provider_selected.emit(self._editing_id)
        self.hide()
        event.ignore()


class TranslateActionButton(QWidget):
    translate_requested = Signal(str)
    dismissed = Signal()

    def __init__(self) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName("translateAction")
        self._source_text = ""
        self._generation = 0
        self.setStyleSheet(
            """
            QWidget#translateAction { background: transparent; }
            QPushButton {
                color: #ffffff;
                background: #1769c2;
                border: 1px solid #0f559f;
                border-radius: 5px;
                min-width: 68px;
                min-height: 36px;
                padding: 0 12px;
                font-weight: 600;
            }
            QPushButton:hover { background: #0f559f; }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("翻译")
        button.setToolTip("翻译选中文字")
        button.clicked.connect(self._activate)
        layout.addWidget(button)

    def show_for(self, source_text: str, mouse_x: int, mouse_y: int) -> None:
        self._source_text = source_text
        self._generation += 1
        generation = self._generation
        self.adjustSize()

        point = QPoint(mouse_x, mouse_y)
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        x = mouse_x - self.width() - 12
        y = mouse_y - self.height() - 10
        if screen is not None:
            area = screen.availableGeometry()
            if x < area.left():
                x = mouse_x + 12
            if y < area.top():
                y = mouse_y + 12
            x = min(max(area.left(), x), area.right() - self.width())
            y = min(max(area.top(), y), area.bottom() - self.height())
        self.move(x, y)
        self.show()
        self.raise_()
        QTimer.singleShot(8000, lambda: self._expire(generation))

    def global_bounds(self) -> tuple[int, int, int, int]:
        geometry = self.frameGeometry()
        return geometry.left(), geometry.top(), geometry.right(), geometry.bottom()

    def dismiss(self) -> None:
        if self.isVisible():
            self.hide()
            self.dismissed.emit()

    def _activate(self) -> None:
        source_text = self._source_text
        self.dismiss()
        if source_text:
            self.translate_requested.emit(source_text)

    def _expire(self, generation: int) -> None:
        if generation == self._generation:
            self.dismiss()


class TranslationOverlay(QWidget):
    language_requested = Signal(str)

    def __init__(self) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName("overlay")
        create_resize_edges(self)
        self.origin = "selection"
        self._source_text = ""
        self.user_positioned = False
        self.setWindowTitle("翻译结果 - LinguaFloat")
        self.setMinimumSize(320, 150)
        self.setMaximumSize(800, 600)
        self.resize(420, 220)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(FLOAT_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(7)

        top = QHBoxLayout()
        self.title = WindowDragHandle("翻译结果")
        self.title.setAccessibleName("拖动输入译文窗口")
        self.title.moved.connect(self._remember_position)
        self.title.setObjectName("floatingTitle")
        top.addWidget(self.title, 1)
        self.source_preview = QLabel()
        self.source_preview.setObjectName("sourcePreview")
        self.source_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source_preview.setWordWrap(True)
        self.source_preview.setMaximumHeight(48)
        self.source_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self.target_language_combo = QComboBox()
        self.target_language_combo.addItem("自动", "auto")
        for code, (label, _) in TARGET_LANGUAGES.items():
            self.target_language_combo.addItem(label, code)
        self.target_language_combo.setAccessibleName("悬浮框目标语言")
        self.target_language_combo.setToolTip("切换当前译文目标语言")
        self.target_language_combo.currentIndexChanged.connect(
            lambda: self.language_requested.emit(str(self.target_language_combo.currentData()))
        )
        top.addWidget(self.target_language_combo)

        self.copy_button = QToolButton()
        self.copy_button.setToolTip("复制翻译")
        self.copy_button.setIcon(qta.icon("mdi6.content-copy", color="#596674"))
        self.copy_button.setFixedSize(32, 32)
        self.copy_button.setAccessibleName("复制翻译")
        self.copy_button.clicked.connect(self._copy_translation)
        top.addWidget(self.copy_button)

        close_button = QToolButton()
        close_button.setToolTip("关闭")
        close_button.setIcon(qta.icon("mdi6.close", color="#596674"))
        close_button.setFixedSize(32, 32)
        close_button.setAccessibleName("关闭译文窗口")
        close_button.clicked.connect(self.hide)
        top.addWidget(close_button)
        layout.addLayout(top)
        layout.addWidget(self.source_preview)

        self.translation = QTextBrowser()
        self.translation.setObjectName("translation")
        self.translation.setOpenExternalLinks(False)
        layout.addWidget(self.translation, 1)
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(22, 22)
        self.size_grip.setToolTip("调整译文窗口大小")
        self.size_grip.setAccessibleName("调整输入译文窗口大小")
        grip_row.addWidget(self.size_grip)
        layout.addLayout(grip_row)

    def resizeEvent(self, event) -> None:
        layout_resize_edges(self)
        super().resizeEvent(event)

    def _remember_position(self) -> None:
        self.user_positioned = True

    def _set_target_language(self, target_language: str) -> None:
        index = self.target_language_combo.findData(target_language)
        if index < 0:
            index = 0
        self.target_language_combo.blockSignals(True)
        self.target_language_combo.setCurrentIndex(index)
        self.target_language_combo.blockSignals(False)

    def target_language(self) -> str:
        return str(self.target_language_combo.currentData() or "auto")

    def show_loading(self, source_text: str, target_language: str = "auto", *, origin: str = "selection") -> None:
        self.target_language_combo.setEnabled(True)
        self.origin = origin
        self._source_text = source_text
        self._set_target_language(target_language)
        self._set_source(source_text)
        self.translation.setPlainText("翻译中…")
        self.copy_button.setEnabled(False)
        self._show_near_cursor()

    def show_translation(self, source_text: str, translated_text: str, target_language: str = "auto", *, origin: str = "selection") -> None:
        self.target_language_combo.setEnabled(True)
        self.origin = origin
        self._source_text = source_text
        self._set_target_language(target_language)
        self._set_source(source_text)
        self.translation.setPlainText(translated_text)
        self.copy_button.setEnabled(True)
        self._show_near_cursor()

    def show_error(self, message: str) -> None:
        self._source_text = ""
        self.source_preview.setToolTip("")
        self.target_language_combo.setEnabled(False)
        self.source_preview.setText("翻译未完成")
        self.translation.setHtml(f'<span style="color:#a32929">{_html_escape(message)}</span>')
        self.copy_button.setEnabled(False)
        self._show_near_cursor()

    @property
    def source_text(self) -> str:
        return self._source_text

    def _set_source(self, source_text: str) -> None:
        compact = " ".join(source_text.split())
        preview = compact if len(compact) <= 60 else compact[:57] + "..."
        self.source_preview.setText(preview)
        self.source_preview.setToolTip(source_text)

    def _copy_translation(self) -> None:
        QGuiApplication.clipboard().setText(self.translation.toPlainText())

    def _show_near_cursor(self) -> None:
        if self.user_positioned:
            self.show()
            self.raise_()
            return
        cursor = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
        if screen is None:
            self.move(cursor + QPoint(24, 20))
        else:
            area = screen.availableGeometry()
            x = cursor.x() + 24
            y = cursor.y() + 20
            if x + self.width() > area.right():
                x = cursor.x() - self.width() - 24
            if y + self.height() > area.bottom():
                y = area.bottom() - self.height()
            x = max(area.left(), x)
            y = max(area.top(), y)
            self.move(x, y)
        self.show()
        self.raise_()


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
