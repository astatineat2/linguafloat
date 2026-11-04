"""Searchable model menu shared by the active selector and profile editor."""

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout,
)
import qtawesome as qta

from linguafloat.theme import APP_STYLESHEET


MODEL_ROLE = Qt.ItemDataRole.UserRole
SELECTED_ROLE = Qt.ItemDataRole.UserRole + 1


class ModelDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self.check = qta.icon("mdi6.check", color="#303841")

    def paint(self, painter, option, index):
        text_option = QStyleOptionViewItem(option)
        text_option.rect.adjust(0, 0, -28, 0)
        super().paint(painter, text_option, index)
        if index.data(SELECTED_ROLE):
            rect = QRect(option.rect.right() - 24, option.rect.center().y() - 9, 18, 18)
            self.check.paint(painter, rect)


class ModelMenu(QFrame):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.owner = owner
        self.setObjectName("modelMenu")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(APP_STYLESHEET + """
            QFrame#modelMenu { background: white; border: 1px solid #d9e0e7; border-radius: 8px; }
            QLabel#menuTitle { color: #66717d; font-size: 13px; }
            QLineEdit#modelSearch { border: 1px solid #d9e0e7; border-radius: 6px; padding: 5px 8px; min-height: 24px; }
            QListWidget#modelChoices { padding: 0; border: 0; }
            QListWidget::item { padding: 6px 10px; margin: 0; }
            QListWidget::item:hover, QListWidget::item:selected { background: #f0f2f5; color: #20252b; }
            QPushButton { text-align: left; border: 0; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QLabel("选择模型")
        title.setObjectName("menuTitle")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setObjectName("modelSearch")
        self.search.setPlaceholderText("搜索或输入模型名称")
        self.search.setAccessibleName("搜索或输入模型名称")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self.accept_search)
        self.search.installEventFilter(self)
        layout.addWidget(self.search)
        self.models = QListWidget()
        self.models.setObjectName("modelChoices")
        self.models.setAccessibleName("可选模型")
        self.models.setMouseTracking(True)
        self.models.setItemDelegate(ModelDelegate(self.models))
        self.models.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.models.itemClicked.connect(self.choose)
        self.models.itemActivated.connect(self.choose)
        layout.addWidget(self.models, 1)
        self.empty = QLabel("暂无匹配模型")
        self.empty.setObjectName("menuTitle")
        layout.addWidget(self.empty)
        self.custom = QPushButton("使用输入的模型")
        self.custom.setIcon(qta.icon("mdi6.plus", color="#303841"))
        self.custom.clicked.connect(lambda: self.commit(self.search.text().strip()))
        layout.addWidget(self.custom)
        manage = QPushButton("管理模型")
        manage.setIcon(qta.icon("mdi6.tune-variant", color="#66717d"))
        manage.clicked.connect(self.manage)
        layout.addWidget(manage)

    def refresh(self):
        query = self.search.text().strip()
        current = self.owner.currentText().strip()
        values = list(dict.fromkeys([current, *(self.owner.itemText(i) for i in range(self.owner.count()))]))
        favorites = set(self.owner.favorites)
        self.models.clear()
        selected = None
        for heading, group in (("收藏", [v for v in values if v in favorites]),
                               ("模型", [v for v in values if v not in favorites])):
            matches = [v for v in group if v and query.casefold() in v.casefold()]
            if not matches:
                continue
            if favorites:
                label = QListWidgetItem(heading)
                label.setFlags(Qt.ItemFlag.NoItemFlags)
                label.setForeground(QColor("#66717d"))
                font = QFont(self.font())
                font.setPixelSize(13)
                label.setFont(font)
                self.models.addItem(label)
            for value in matches:
                item = QListWidgetItem(value)
                item.setData(MODEL_ROLE, value)
                item.setData(SELECTED_ROLE, value == current)
                item.setToolTip(value + ("（当前选择）" if value == current else ""))
                item.setSizeHint(QSize(0, max(36, self.fontMetrics().height() + 14)))
                self.models.addItem(item)
                if selected is None or value == current:
                    selected = item
        if selected is not None:
            self.models.setCurrentItem(selected)
        self.empty.setVisible(selected is None)
        self.models.setVisible(selected is not None)
        self.models.setFixedHeight(min(8, max(1, self.models.count())) * max(36, self.fontMetrics().height() + 14) + 4)
        self.custom.setVisible(bool(query) and query not in values)
        if self.isVisible():
            self.resize(self.width(), self.sizeHint().height())

    def eventFilter(self, watched, event):
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Down:
                self.models.setFocus()
                return True
        return super().eventFilter(watched, event)

    def accept_search(self):
        item = self.models.currentItem()
        if item and item.data(MODEL_ROLE):
            self.choose(item)
        else:
            self.commit(self.search.text().strip())

    def choose(self, item):
        value = item.data(MODEL_ROLE)
        if value:
            self.commit(value)

    def commit(self, value):
        if not value or not self.isVisible():
            return
        self.owner.hidePopup()
        if self.owner.findText(value) < 0:
            self.owner.addItem(value)
        self.owner.setCurrentIndex(self.owner.findText(value))
        self.owner.activated.emit(self.owner.currentIndex())

    def manage(self):
        self.owner.hidePopup()
        self.owner.manage_requested.emit()


class ModelSelector(QComboBox):
    manage_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.favorites = []
        self.menu = None

    def use_menu_display(self):
        self.lineEdit().setReadOnly(True)
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and self.lineEdit().isReadOnly():
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.showPopup()
                return True
            if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Down):
                self.showPopup()
                return True
        return super().eventFilter(watched, event)

    def clear(self):
        self.hidePopup()
        super().clear()

    def showPopup(self):
        if self.menu is None:
            self.menu = ModelMenu(self)
        self.menu.search.clear()
        self.menu.refresh()
        anchor = self.mapToGlobal(QPoint(0, self.height()))
        screen = QGuiApplication.screenAt(anchor) or self.screen()
        area = screen.availableGeometry()
        width = min(max(320, self.width()), 440, area.width())
        height = min(self.menu.sizeHint().height(), area.height())
        self.menu.resize(width, height)
        x = max(area.left(), min(anchor.x(), area.right() - width + 1))
        y = anchor.y() + 4
        if y + height > area.bottom() + 1:
            y = self.mapToGlobal(QPoint()).y() - height - 4
        y = max(area.top(), min(y, area.bottom() - height + 1))
        self.menu.move(x, y)
        self.menu.show()
        self.menu.search.setFocus()

    def hidePopup(self):
        if self.menu is not None:
            self.menu.hide()
        super().hidePopup()
