from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from linguafloat.theme import APP_STYLESHEET


class ImportPreview(QDialog):
    def __init__(self, profiles, existing, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入配置预览")
        self.resize(760, 400)
        self.setStyleSheet(APP_STYLESHEET)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(QLabel("确认导入项目；当前使用的配置不会自动切换。"))
        table = QTableWidget(len(profiles), 4)
        table.setHorizontalHeaderLabels(["名称", "接口地址", "模型", "处理方式"])
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(44)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.choices = []
        for row, profile in enumerate(profiles):
            for col, text in enumerate((profile.name, profile.base_url, profile.model)):
                table.setItem(row, col, QTableWidgetItem(text))
            choice = QComboBox()
            choice.addItem("新增副本" if profile.id in existing else "新增", "add")
            if profile.id in existing:
                choice.addItem("更新同 ID 配置", "update")
            choice.addItem("跳过", "skip")
            table.setCellWidget(row, 3, choice)
            self.choices.append((profile, choice))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def operations(self):
        return [(profile, choice.currentData()) for profile, choice in self.choices]
