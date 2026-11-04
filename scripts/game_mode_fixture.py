"""A local window for manual game-mode checks; it never calls a translation API."""

import sys

from PySide6.QtCore import QEvent
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QVBoxLayout, QWidget


class Fixture(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LinguaFloat game mode test")
        self.resize(600, 380)
        self.setStyleSheet("font: 16pt 'Segoe UI'; background: white; color: #202124;")
        self.counts = {"Right click": 0, "Ctrl+Alt+T": 0, "Ctrl+Alt+Y": 0}
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit("Hello world.\nThis is a local screen translation test.")
        self.text.setAccessibleName("Game mode test text")
        self.text.viewport().installEventFilter(self)
        layout.addWidget(self.text)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.shortcuts = []
        for chord in ("Ctrl+Alt+T", "Ctrl+Alt+Y"):
            shortcut = QShortcut(chord, self)
            shortcut.activated.connect(lambda key=chord: self.count(key))
            self.shortcuts.append(shortcut)
        self.update_status()

    def update_status(self):
        self.status.setText("\n".join(f"{key}: {value}" for key, value in self.counts.items()))

    def count(self, key):
        self.counts[key] += 1
        self.update_status()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ContextMenu:
            self.count("Right click")
        return super().eventFilter(watched, event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Fixture()
    window.show()
    sys.exit(app.exec())
