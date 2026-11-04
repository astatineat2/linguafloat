"""Responsive, unframed settings fields shared by desktop pages."""
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QWidget, QGridLayout, QVBoxLayout, QLabel, QLayout, QSizePolicy


class FieldGrid(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(24)
        self.grid.setVerticalSpacing(14)
        self.fields = []
        self.visibility = {}
        self.columns = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def add_field(self, label, control, wide=False):
        field = QWidget(self)
        field.show()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        if label:
            title = QLabel(label)
            title.setObjectName("fieldLabel")
            layout.addWidget(title)
        if isinstance(control, QLayout):
            layout.addLayout(control)
        else:
            layout.addWidget(control)
            if not label:
                self.visibility[control] = field
                control.installEventFilter(self)
                field.setVisible(not control.isHidden())
            elif not wide:
                control.setMaximumWidth(280)
        field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.fields.append((field, wide))
        self.reflow(force=True)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.ShowToParent, QEvent.Type.HideToParent) and watched in self.visibility:
            self.visibility[watched].setVisible(not watched.isHidden())
            self.reflow(force=True)
        return super().eventFilter(watched, event)

    def reflow(self, force=False):
        threshold = max(550, self.fontMetrics().horizontalAdvance("配置名称及语言设置") * 4)
        columns = 2 if self.width() >= threshold else 1
        if columns == self.columns and not force:
            return
        self.columns = columns
        for field, _ in self.fields:
            self.grid.removeWidget(field)
        row = col = 0
        for field, wide in self.fields:
            if field.isHidden():
                continue
            if wide and col:
                row, col = row + 1, 0
            self.grid.addWidget(field, row, col, 1, columns if wide else 1)
            col += columns if wide else 1
            if col >= columns:
                row, col = row + 1, 0
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1 if columns == 2 else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self.reflow(force=True)
