"""Provider cards and data management; never handles keys."""
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QToolButton, QLineEdit,
    QHBoxLayout, QVBoxLayout, QScrollArea, QStackedWidget, QSizePolicy, QMenu, QDialog, QLayout)
import qtawesome as qta
from linguafloat.config import validate_provider
import copy
from linguafloat.workbench import AppDialog

SOURCES = {
    "all": ("全部配置", "ALL"), "manual": ("手动配置", "M"),
    "codex": ("Codex", "CX"), "claude": ("Claude Code", "CL"),
    "claude-desktop": ("Claude Desktop", "CD"), "gemini": ("Gemini CLI", "GM"),
    "opencode": ("OpenCode", "OC"), "openclaw": ("OpenClaw", "CW"),
}
MODES = {"responses": "Responses API", "chat_completions": "Chat Completions",
         "anthropic": "Anthropic Messages", "gemini": "Gemini generateContent"}
STYLE = """
QWidget#configDashboard { background: #F6F6F9; }
QLabel#dashboardTitle { font-size: 24px; font-weight: 600; color: #1C2238; }
QLabel#muted { font-size: 13px; color: #626980; }
QFrame#providerCard, QFrame#dataCard { background: #FFFFFF; border: 1px solid #EDEEF3; border-radius: 10px; }
QFrame#providerCard[active="true"] { background: #FFFFFF; border: 1px solid #DCD5FA; }
QFrame#providerCard[viewed="true"] { border: 1px solid #6241D3; }
QLabel#providerName { font-size: 16px; font-weight: 600; color: #1C2238; }
QLabel#providerBadge { background: #EEECFB; border: 1px solid #DCD5FA; border-radius: 6px; color: #6241D3; font-size: 13px; font-weight: 600; }
QLabel#cardStatus { font-size: 12px; color: #147A50; }
QLabel#cardStatus[missing="true"] { color: #995B10; }
QWidget#dataActions { background: transparent; }
QWidget#configDashboard QPushButton { min-height: 36px; font-size: 13px; padding: 0 13px; }
QPushButton#dataPrimary { background: #6241D3; color: #FFFFFF; border: 0; border-radius: 6px; min-height: 40px; font-size: 14px; font-weight: 600; }
QPushButton#dataPrimary:hover { background: #5535C0; }
QWidget#configDashboard QLineEdit { font-size: 13px; }
QLabel#resultSummary { font-size: 14px; color: #626980; }
"""


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()


class ElidedLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.full_text = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip(text)
        self.setAccessibleName(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def resizeEvent(self, event):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideMiddle, self.width()))
        super().resizeEvent(event)


class ProviderEditor(AppDialog):
    """Hosts the existing editor with its existing save and request signals."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("编辑供应商")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(800, 620)
        self.setMinimumSize(620, 460)

    def reject(self):
        if not self.owner.confirm_discard():
            return
        if self.owner.is_dirty():
            self.owner._load_profile(self.owner._editing_id)
            self.owner.provider_selected.emit(self.owner._editing_id)
        self.owner._hide_key()
        super().reject()


class ConfigDashboard(QWidget):
    edit_requested = Signal(str)
    use_requested = Signal(str)
    duplicate_requested = Signal(str)
    delete_requested = Signal(str)
    add_requested = Signal()
    official_requested = Signal()
    preset_requested = Signal(str)
    sql_requested = Signal()
    export_requested = Signal()
    complete_requested = Signal(int)
    credentials_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("configDashboard")
        self.setStyleSheet(STYLE)
        self.profiles, self.cards, self.credential_status = [], {}, {}
        self.active_id = self.viewed_id = ""
        self._list_scroll = 0
        outer = QHBoxLayout(self); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        self.stack = QStackedWidget(); outer.addWidget(self.stack,1)
        self.list_page = QWidget(); page = QVBoxLayout(self.list_page); page.setContentsMargins(22,20,22,16); page.setSpacing(16)
        header = QHBoxLayout()
        self.heading = QLabel("全部配置"); self.heading.setObjectName("dashboardTitle")
        header.addWidget(self.heading); self.count = QLabel(); self.count.setObjectName("muted"); header.addWidget(self.count); header.addStretch()
        self.data_button = QPushButton("数据管理")
        self.data_button.setIcon(qta.icon("mdi6.database-outline", color="#526c8b"))
        self.data_button.clicked.connect(self.show_data)
        header.addWidget(self.data_button)
        self.add_button = QPushButton("添加供应商"); self.add_button.setObjectName("primary")
        self.add_button.setIcon(qta.icon("mdi6.plus",color="white"))
        add_menu = QMenu(self.add_button)
        for label, kind in (("OpenCode Go", "go"), ("Now Coding", "no_coding"), ("自定义", "custom")):
            add_menu.addAction(label, lambda checked=False, value=kind: self.preset_requested.emit(value))
        self.add_button.setMenu(add_menu); header.addWidget(self.add_button); page.addLayout(header)
        self.search = QLineEdit(); self.search.setPlaceholderText("搜索供应商、地址或模型")
        self.search.textChanged.connect(self.render_cards); page.addWidget(self.search)
        self.card_scroll = QScrollArea(); self.card_scroll.setWidgetResizable(True); self.card_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.card_body = QWidget(); self.card_layout = QVBoxLayout(self.card_body); self.card_layout.setContentsMargins(0,0,4,0); self.card_layout.setSpacing(12)
        self.card_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.card_scroll.setWidget(self.card_body); page.addWidget(self.card_scroll,1)
        self.stack.addWidget(self.list_page)
        self._build_data_page()
        self.show_list()

    def _build_data_page(self):
        self.data_page = QWidget(); layout = QVBoxLayout(self.data_page); layout.setContentsMargins(22,20,22,16); layout.setSpacing(16)
        header = QHBoxLayout()
        title = QLabel("数据管理"); title.setObjectName("dashboardTitle"); header.addWidget(title)
        header.addStretch()
        self.back_button = QPushButton("返回全部配置")
        self.back_button.setIcon(qta.icon("mdi6.arrow-left", color="#526c8b"))
        self.back_button.clicked.connect(self.show_list); header.addWidget(self.back_button)
        layout.addLayout(header)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(); content = QVBoxLayout(body); content.setContentsMargins(0,0,4,0); content.setSpacing(18)
        card = QFrame(); card.setObjectName("dataCard"); box = QVBoxLayout(card); box.setContentsMargins(22,20,22,22); box.setSpacing(14)
        label = QLabel("SQL 导入导出"); label.setObjectName("providerName"); box.addWidget(label)
        info = QLabel("导入 CC Switch SQL，或导出兼容备份（仅供应商配置）。")
        info.setObjectName("muted"); info.setWordWrap(True); box.addWidget(info)
        actions = QWidget(); actions.setObjectName("dataActions"); row = QHBoxLayout(actions); row.setContentsMargins(0,8,0,0); row.setSpacing(12)
        self.sql_button = QPushButton("选择 SQL 文件"); self.export_button = QPushButton("导出 SQL 备份")
        for button, icon, signal in ((self.sql_button,"mdi6.folder-open-outline",self.sql_requested),
                                     (self.export_button,"mdi6.content-save-outline",self.export_requested)):
            primary = button is self.sql_button
            button.setObjectName("dataPrimary" if primary else "")
            button.setIcon(qta.icon(icon,color="white" if primary else "#626775")); button.clicked.connect(signal.emit); row.addWidget(button,1)
        box.addWidget(actions); content.addWidget(card)
        self.result = QFrame(); self.result.setObjectName("dataCard"); result_layout = QVBoxLayout(self.result); result_layout.setContentsMargins(20,16,20,16)
        result_row = QHBoxLayout(); self.summary = QLabel(); self.summary.setObjectName("resultSummary"); self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText); result_row.addWidget(self.summary,1)
        self.details_button = QToolButton(); self.details_button.setText("展开详情"); self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._toggle_details); result_row.addWidget(self.details_button); result_layout.addLayout(result_row)
        self.result_details = QWidget(); self.result_rows = QVBoxLayout(self.result_details); self.result_rows.setContentsMargins(0,12,0,0)
        result_layout.addWidget(self.result_details); self.result_details.hide(); self.result.hide(); content.insertWidget(1,self.result)
        content.addStretch(); scroll.setWidget(body); layout.addWidget(scroll,1); self.stack.addWidget(self.data_page)

    def _toggle_details(self, checked):
        self.result_details.setVisible(checked); self.details_button.setText("收起详情" if checked else "展开详情")

    def set_report(self, report, pending=()):
        from collections import Counter
        counts = Counter(state for _,state,_ in report)
        names = {"成功":"新增", "重复跳过":"重复跳过", "需要补全":"待补全"}
        order = [k for k in ("成功","重复跳过","需要补全","不支持","失败","读取失败") if k in counts]
        order += [k for k in counts if k not in order]
        self.summary.setText("，".join(f"{names.get(k,k)} {counts[k]} 条" for k in order) or "没有可导入的配置")
        clear_layout(self.result_rows)
        pending_by_row = dict(pending)
        for index, (name,state,reason) in enumerate(report):
            row = QWidget(); line = QHBoxLayout(row); line.setContentsMargins(0,5,0,5)
            text = QLabel(f"{name} · {state}\n{reason}"); text.setTextFormat(Qt.TextFormat.PlainText); text.setWordWrap(True); text.setObjectName("muted")
            line.addWidget(text,1)
            if index in pending_by_row:
                button=QPushButton("补全" if state=="需要补全" else "重试")
                button.clicked.connect(lambda checked=False,i=index:self.complete_requested.emit(i)); line.addWidget(button)
            self.result_rows.addWidget(row)
        self.result.show(); self.show_data()

    def show_error(self, message):
        self.set_report([("导入", "失败", message)])
        self.details_button.setChecked(True)

    def show_list(self):
        self.stack.setCurrentWidget(self.list_page)
        QTimer.singleShot(0, lambda: self.card_scroll.verticalScrollBar().setValue(self._list_scroll))

    def show_data(self):
        if self.stack.currentWidget() == self.list_page:
            self._list_scroll = self.card_scroll.verticalScrollBar().value()
        self.stack.setCurrentWidget(self.data_page)

    def set_profiles(self, profiles, active_id):
        self.profiles=list(profiles); self.active_id=active_id; self.render_cards()

    def set_credentials(self, values):
        self.credential_status=dict(values); self.render_cards()

    def render_cards(self, *_):
        clear_layout(self.card_layout); self.cards={}
        query=self.search.text().strip().casefold()
        profiles=[p for p in self.profiles if not query or query in (p.name+' '+p.base_url+' '+p.model).casefold()]
        self.count.setText(f"{len(profiles)} 个")
        for p in sorted(profiles,key=lambda p:(not p.favorite,p.name.casefold())):
            card=QFrame(); card.setObjectName("providerCard"); card.setProperty("active",p.id==self.active_id)
            card.setProperty("viewed",p.id==self.viewed_id); card.setMinimumWidth(0)
            card.setMinimumHeight(128)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row=QHBoxLayout(card); row.setContentsMargins(16,16,14,16); row.setSpacing(14)
            badge=QLabel(SOURCES.get(p.source_app,SOURCES['manual'])[1]); badge.setObjectName("providerBadge"); badge.setFixedSize(44,44); badge.setAlignment(Qt.AlignmentFlag.AlignCenter); row.addWidget(badge)
            text_widget=QWidget(); text_widget.setMinimumWidth(0); texts=QVBoxLayout(text_widget); texts.setContentsMargins(0,0,0,0); texts.setSpacing(5)
            name=ElidedLabel(p.name); name.setObjectName("providerName"); texts.addWidget(name)
            address=ElidedLabel(p.base_url or "尚未填写接口地址"); address.setObjectName("muted"); texts.addWidget(address)
            model=ElidedLabel(MODES.get(p.api_mode,"协议待补全")+" · "+(p.model or "模型待补全")); model.setObjectName("muted"); texts.addWidget(model)
            row.addWidget(text_widget,1)
            missing=False
            try:validate_provider(copy.deepcopy(p))
            except ValueError:missing=True
            missing |= self.credential_status.get(p.id)=="missing"
            controls=QVBoxLayout(); controls.setSpacing(8)
            state=QLabel("当前使用 · 待补全" if missing and p.id==self.active_id else "待补全" if missing else "当前使用" if p.id==self.active_id else "未测试连接")
            state.setObjectName("cardStatus"); state.setProperty("missing",missing); state.setAlignment(Qt.AlignmentFlag.AlignRight); controls.addWidget(state)
            buttons=QHBoxLayout(); buttons.setSpacing(6)
            use=QPushButton("正在使用" if p.id==self.active_id else "使用此配置"); use.setEnabled(p.id!=self.active_id and not missing)
            use.clicked.connect(lambda checked=False,i=p.id:self.use_requested.emit(i)); buttons.addWidget(use)
            edit=QToolButton(); edit.setIcon(qta.icon("mdi6.pencil-outline",color="#65758a")); edit.setToolTip("编辑 "+p.name); edit.setAccessibleName("编辑 "+p.name)
            edit.clicked.connect(lambda checked=False,i=p.id:self._edit(i)); buttons.addWidget(edit)
            more=QToolButton(); more.setIcon(qta.icon("mdi6.dots-horizontal",color="#65758a")); more.setToolTip("更多操作"); more.setAccessibleName("更多操作 "+p.name)
            menu=QMenu(more)
            menu.addAction(qta.icon("mdi6.content-copy",color="#626775"), "复制配置",lambda i=p.id:self.duplicate_requested.emit(i))
            remove = menu.addAction(qta.icon("mdi6.delete-outline",color="#b53748"), "删除配置",lambda i=p.id:self.delete_requested.emit(i))
            remove.setEnabled(len(self.profiles) > 1)
            more.setMenu(menu); more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup); buttons.addWidget(more)
            controls.addLayout(buttons); row.addLayout(controls); self.card_layout.addWidget(card); self.cards[p.id]=card
        if not profiles:
            label=QLabel("没有匹配的配置\n可调整搜索、添加供应商或前往数据管理导入。"); label.setObjectName("muted"); label.setAlignment(Qt.AlignmentFlag.AlignCenter); label.setMinimumHeight(160); self.card_layout.addWidget(label)
        self.card_layout.addStretch()

    def _edit(self, identity):
        self.viewed_id=identity; self.render_cards(); self.edit_requested.emit(identity)

    def showEvent(self, event):
        super().showEvent(event); self.credentials_requested.emit()
