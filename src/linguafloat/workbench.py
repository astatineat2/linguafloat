"""Native desktop navigation and shared dialog presentation."""
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox as QtMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)
import qtawesome as qta

from linguafloat.assets import app_icon
from linguafloat.theme import APP_STYLESHEET


ICONS = {
    "settings": "mdi6.tune-variant", "providers": "mdi6.server-outline",
    "documents": "mdi6.file-document-outline", "help": "mdi6.help-circle-outline",
    "close": "mdi6.close", "database": "mdi6.database-outline",
}


def icon(name, color="#626980"):
    return qta.icon(ICONS.get(name, name), color=color)


class Workbench(QWidget):
    """Keep the existing page indexes/signals while exposing a desktop rail."""
    def __init__(self, pages, help_button, version, game, screen, activity):
        super().__init__()
        self.setObjectName("workbench")
        self.pages = pages
        self.rail = QWidget()
        self.rail.setObjectName("appRail")
        side = QVBoxLayout(self.rail)
        side.setContentsMargins(12, 22, 12, 16)
        side.setSpacing(12)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(30, 30))
        logo.setFixedSize(32, 32)
        brand.addWidget(logo)
        self.brand = QLabel("LinguaFloat")
        self.brand.setObjectName("brand")
        brand.addWidget(self.brand, 1)
        side.addLayout(brand)
        self.version = version
        side.addWidget(version)
        side.addSpacing(22)
        self.navigation = QListWidget()
        self.navigation.setObjectName("appNavigation")
        self.navigation.setAccessibleName("应用导航")
        self.navigation.setIconSize(QSize(18, 18))
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.labels = [pages.tabText(i) for i in range(pages.count())]
        for label, name in zip(self.labels, ("settings", "providers", "documents")):
            item = QListWidgetItem(icon(name), label)
            item.setToolTip(label)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, label)
            item.setSizeHint(QSize(40, 36))
            self.navigation.addItem(item)
        self.navigation.currentRowChanged.connect(pages.setCurrentIndex)
        pages.currentChanged.connect(self._changed)
        side.addWidget(self.navigation, 1)
        self.help = help_button
        help_button.setAccessibleName("帮助与更新")
        help_button.setIcon(icon("help"))
        help_button.setObjectName("railHelp")
        side.addWidget(help_button)
        main = QWidget()
        main.setMinimumWidth(0)
        body = QVBoxLayout(main)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        top = QWidget()
        top.setObjectName("topbar")
        row = QHBoxLayout(top)
        row.setContentsMargins(24, 10, 24, 10)
        row.setSpacing(10)
        self.location = QLabel()
        self.location.setObjectName("breadcrumb")
        row.addWidget(self.location, 1)
        row.addWidget(game)
        row.addWidget(screen)
        body.addWidget(top)
        pages.tabBar().hide()
        body.addWidget(pages, 1)
        activity.setContentsMargins(24, 0, 20, 0)
        body.addWidget(activity)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.rail)
        layout.addWidget(main, 1)
        self._changed(pages.currentIndex())

    def _changed(self, index):
        self.navigation.blockSignals(True)
        self.navigation.setCurrentRow(index)
        self.navigation.blockSignals(False)
        self.location.setText("工作台  /  " + self.labels[index])

    def resizeEvent(self, event):
        compact = self.width() < 1000
        self.rail.setFixedWidth(68 if compact else 192)
        self.brand.setVisible(not compact)
        self.version.setVisible(not compact)
        self.help.setText("" if compact else "帮助与更新")
        for i, label in enumerate(self.labels):
            self.navigation.item(i).setText("" if compact else label)
        super().resizeEvent(event)


class AppDialog(QDialog):
    """Common heading, Chinese standard actions and safe keyboard defaults."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(APP_STYLESHEET)
        self.setWindowIcon(app_icon())
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._decorated = False
        self._shade = None
        self._previous_focus = None

    def eventFilter(self, watched, event):
        if self._shade is not None and event.type() == QEvent.Type.Resize:
            self._shade.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        if self._shade is not None:
            self._shade.parentWidget().removeEventFilter(self)
            self._shade.hide()
            self._shade.deleteLater()
            self._shade = None
        super().hideEvent(event)
        if self._previous_focus is not None:
            try:
                if self._previous_focus.isVisible():
                    self._previous_focus.setFocus()
            except RuntimeError:
                pass
            self._previous_focus = None

    def showEvent(self, event):
        if self.parentWidget() and self._shade is None:
            owner = self.parentWidget().window()
            self._previous_focus = owner.focusWidget()
            self._shade = QFrame(owner)
            self._shade.setStyleSheet("background-color: rgba(28, 34, 56, 122);")
            self._shade.setGeometry(owner.rect())
            self._shade.show()
            self._shade.raise_()
            owner.installEventFilter(self)
        if not self._decorated and isinstance(self.layout(), QVBoxLayout):
            self._decorated = True
            layout = self.layout()
            last = layout.itemAt(layout.count() - 1) if layout.count() else None
            if last and isinstance(last.widget(), (QDialogButtonBox, QPushButton)):
                action = layout.takeAt(layout.count() - 1).widget()
                content = QWidget()
                content_layout = QVBoxLayout(content)
                content_layout.setContentsMargins(24, 18, 24, 20)
                content_layout.setSpacing(14)
                while layout.count():
                    item = layout.takeAt(0)
                    if item.widget():
                        content_layout.addWidget(item.widget())
                    elif item.layout():
                        nested = item.layout()
                        nested.setParent(None)
                        content_layout.addLayout(nested)
                    else:
                        content_layout.addItem(item)
                # Tabbed help already contains scrollable readers. Simple forms
                # need their own viewport so long messages cannot hide actions.
                from PySide6.QtWidgets import QTabWidget
                if content.findChild(QTabWidget):
                    layout.addWidget(content, 1)
                else:
                    body = QScrollArea()
                    body.setWidgetResizable(True)
                    body.setFrameShape(QFrame.Shape.NoFrame)
                    body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                    body.setWidget(content)
                    body.setMinimumHeight(140)
                    layout.addWidget(body, 1)
                footer = QWidget()
                footer.setObjectName("footer")
                actions = QHBoxLayout(footer)
                actions.setContentsMargins(20, 12, 20, 12)
                actions.addStretch()
                actions.addWidget(action)
                layout.addWidget(footer)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            header = QWidget()
            header.setObjectName("dialogHeader")
            row = QHBoxLayout(header)
            row.setContentsMargins(20, 14, 16, 14)
            self.dialog_heading = QLabel(self.windowTitle())
            self.dialog_heading.setObjectName("dialogTitle")
            self.dialog_heading.setWordWrap(True)
            self.dialog_heading.setTextFormat(Qt.TextFormat.PlainText)
            self.windowTitleChanged.connect(self.dialog_heading.setText)
            row.addWidget(self.dialog_heading, 1)
            close = QToolButton()
            close.setIcon(icon("close"))
            close.setToolTip("关闭")
            close.setAccessibleName("关闭弹窗")
            close.setFixedSize(32, 32)
            close.clicked.connect(self.reject)
            row.addWidget(close)
            layout.insertWidget(0, header)
            for button in self.findChildren(QPushButton):
                button.setAutoDefault(False)
                button.setDefault(False)
            for box in self.findChildren(QDialogButtonBox):
                for standard, label in ((box.StandardButton.Save, "保存配置"),
                        (box.StandardButton.Cancel, "取消"), (box.StandardButton.Ok, "确定"),
                        (box.StandardButton.Close, "关闭")):
                    button = box.button(standard)
                    if button and button.text().replace("&", "") in {"Save", "Cancel", "OK", "Close"}:
                        button.setText(label)
                    if button and standard in (box.StandardButton.Save, box.StandardButton.Ok):
                        button.setObjectName("primary")
        area = self.screen().availableGeometry()
        self.resize(min(self.width(), area.width() - 24), min(self.height(), area.height() - 48))
        if self.parentWidget():
            center = self.parentWidget().window().frameGeometry().center()
            self.move(max(area.left(), min(center.x() - self.width() // 2, area.right() - self.width())),
                      max(area.top() + 30, min(center.y() - self.height() // 2, area.bottom() - self.height())))
        super().showEvent(event)


class MessageBox(QtMessageBox):
    """QMessageBox-compatible return values with explicit Chinese actions."""
    @classmethod
    def question(cls, parent, title, text, buttons=None, defaultButton=None):
        buttons = buttons or (cls.StandardButton.Yes | cls.StandardButton.No)
        return cls._show(parent, title, text, buttons, defaultButton, False)

    @classmethod
    def warning(cls, parent, title, text, buttons=None, defaultButton=None):
        return cls._show(parent, title, text, buttons or cls.StandardButton.Ok, defaultButton, True)

    @classmethod
    def _show(cls, parent, title, text, buttons, default, warning):
        dialog = AppDialog(parent)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(400)
        dialog.resize(500, 230)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setContentsMargins(24, 14, 24, 22)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll.setWidget(label)
        layout.addWidget(scroll, 1)
        footer = QWidget()
        footer.setObjectName("footer")
        row = QHBoxLayout(footer)
        row.setContentsMargins(20, 14, 20, 14)
        row.addStretch()
        result = [cls.StandardButton.Cancel]
        labels = {cls.StandardButton.No: "取消", cls.StandardButton.Cancel: "取消",
                  cls.StandardButton.Discard: "放弃修改", cls.StandardButton.Save: "保存配置",
                  cls.StandardButton.Yes: {"删除供应商": "删除配置", "未保存的配置": "保存配置",
                    "接口检测完成": "应用接口", "安装更新": "等待并安装"}.get(title, title),
                  cls.StandardButton.Ok: "知道了" if warning else "确定"}
        def finish(value):
            result[0] = value
            dialog.accept()
        for standard, caption in labels.items():
            if not buttons & standard:
                continue
            button = QPushButton(caption)
            if standard in (cls.StandardButton.Yes, cls.StandardButton.Save, cls.StandardButton.Ok):
                button.setObjectName("danger" if any(t in title for t in ("删除", "清除")) else "primary")
            button.clicked.connect(lambda checked=False, value=standard: finish(value))
            row.addWidget(button)
            if standard in (cls.StandardButton.No, cls.StandardButton.Cancel):
                button.setFocus()
        layout.addWidget(footer)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
        return result[0]
