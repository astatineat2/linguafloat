"""Shared presentation for the settings window and translation tools."""

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget
import qtawesome as qta
from linguafloat.assets import asset_path


APP_STYLESHEET = """
QWidget { color: #20252b; background: transparent; }
QMainWindow, QDialog { background: #f6f8fb; }
QWidget#surface, QScrollArea, QScrollArea > QWidget > QWidget { background: #ffffff; }
QWidget#sidebar { background: #f1f4f8; }
QWidget#footer { background: #ffffff; border-top: 1px solid #e4e9ef; }
QLabel#title { font-size: 22px; font-weight: 600; }
QLabel#sectionTitle { font-weight: 600; }
QLabel#fieldLabel { color: #526171; }
QProgressBar { background: #edf1f5; border: 0; border-radius: 4px; text-align: center; color: #253443; }
QProgressBar::chunk { background: #b8dcfa; border-radius: 4px; }
QLabel#subtitle, QLabel#securityNote { color: #66717d; font-size: 13px; }
QLabel#statusRunning { color: #16734f; font-weight: 600; }
QLabel#statusPaused { color: #996000; font-weight: 600; }
QLabel[state="idle"] { color: #66717d; }
QLabel[state="running"] { color: #16734f; }
QLabel[state="paused"] { color: #996000; }
QLabel[state="error"] { color: #b52e3a; }
QLabel#badge { padding: 4px 8px; border-radius: 4px; font-size: 13px; background: #eaf0f5; }
QLabel#badge[state="running"] { background: #e4f3eb; }
QLabel#badge[state="paused"] { background: #fff1d9; }
QTabWidget::pane { border: 0; background: #ffffff; top: 0; }
QTabBar::tab { padding: 10px 22px; margin-right: 8px; color: #66717d; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: #20252b; background: #edf2f7; }
QTabBar::tab:selected { color: #1969b9; border-bottom: 2px solid #1976d2; }
QTabBar::tab:focus { background: #eaf2fc; }
QLineEdit, QComboBox, QAbstractSpinBox {
    background: #ffffff; border: 1px solid #d9e0e7;
    border-radius: 6px; padding: 5px 10px; min-height: 24px;
    selection-background-color: #1976d2; selection-color: white;
}
QComboBox QLineEdit { border: 0; padding: 0; min-height: 0; }
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus { border-color: #1976d2; }
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled { background: #f3f5f7; color: #8a949f; }
QComboBox::drop-down { border: 0; width: 26px; }
QComboBox QAbstractItemView { background: white; color: #20252b; selection-background-color: #e5effb; selection-color: #155ba3; padding: 4px; border: 1px solid #d9e0e7; }
QPushButton { min-height: 34px; padding: 0 14px; background: #ffffff; border: 1px solid #d9e0e7; border-radius: 6px; icon-size: 18px; }
QPushButton:hover { background: #edf3f9; border-color: #b9cbdc; }
QPushButton:pressed { background: #dde9f5; }
QPushButton:focus, QToolButton:focus { border: 1px solid #1976d2; }
QPushButton#primary { background: #1976d2; color: white; border-color: #1976d2; font-weight: 600; }
QPushButton#primary:hover { background: #1265b8; }
QPushButton#primary:pressed { background: #10549a; }
QPushButton#danger { color: #b52e3a; }
QPushButton:disabled, QToolButton:disabled { color: #919ba6; background: #f4f6f8; border-color: #e6ebf0; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px; icon-size: 18px; }
QToolButton:hover { background: #e7eff8; }
QToolButton:pressed { background: #d9e7f5; }
QToolButton#disclosure { text-align: left; color: #596674; padding: 6px 0; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #9eafbe; border-radius: 3px; background: white; }
QCheckBox::indicator:checked { background: #1976d2; border-color: #1976d2; }
QListWidget { background: transparent; border: 0; outline: 0; padding: 4px; }
QListWidget::item { padding: 10px 8px; margin: 2px 0; border-radius: 6px; }
QListWidget::item:hover { background: #e8eef5; }
QListWidget::item:selected { background: #e0ecfa; color: #155ba3; }
QTextBrowser { background: #f6f8fb; border: 0; border-radius: 6px; padding: 8px; selection-background-color: #1976d2; selection-color: white; }
QTextBrowser#activity { background: transparent; padding: 0; color: #66717d; font-size: 13px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #c8d1dc; border-radius: 3px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #9facba; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSplitter::handle { background: #e4e9ef; width: 1px; }
QFrame#separator { background: #e4e9ef; min-height: 1px; max-height: 1px; border: 0; }
QToolTip { color: #20252b; background: #ffffff; border: 1px solid #d9e0e7; padding: 6px; }
"""

APP_STYLESHEET += """
QComboBox::down-arrow { image: url("DOWN"); width: 16px; height: 16px; }
QAbstractSpinBox { padding-right: 26px; }
QAbstractSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 24px; border: 0; }
QAbstractSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 24px; border: 0; }
QAbstractSpinBox::up-arrow { image: url("UP"); width: 14px; height: 14px; }
QAbstractSpinBox::down-arrow { image: url("DOWN"); width: 14px; height: 14px; }
QCheckBox::indicator:checked { image: url("CHECK"); }
""".replace("DOWN", asset_path("chevron-down.png").as_posix()).replace(
    "UP", asset_path("chevron-up.png").as_posix()).replace("CHECK", asset_path("check.png").as_posix())


FLOAT_STYLESHEET = APP_STYLESHEET + """
QWidget#overlay, QWidget#screenPanel { background: white; border: 1px solid #c8d3df; border-radius: 8px; }
QLabel#sourcePreview { color: #66717d; font-size: 13px; }
QLabel#floatingTitle { font-size: 15px; font-weight: 600; }
QTextBrowser#translation { background: white; border: 0; padding: 2px; }
QComboBox { font-size: 14px; padding: 3px 6px; min-height: 24px; }
"""


def set_state(widget: QWidget, state: str) -> None:
    widget.setProperty("state", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def section_heading(text: str, icon: str) -> QWidget:
    heading = QWidget()
    row = QHBoxLayout(heading)
    row.setContentsMargins(0, 8, 0, 2)
    row.setSpacing(10)
    symbol = QLabel()
    symbol.setPixmap(qta.icon(icon, color="#1976d2").pixmap(QSize(20, 20)))
    symbol.setFixedSize(24, 24)
    row.addWidget(symbol)
    title = QLabel(text)
    title.setObjectName("sectionTitle")
    row.addWidget(title, 1)
    return heading


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    return line
