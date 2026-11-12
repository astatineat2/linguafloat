"""Shared Privacyboard-style desktop surfaces and semantic colors."""
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget
import qtawesome as qta
from linguafloat.assets import asset_path


COLORS = dict(background="#F6F6F9", sidebar="#FBFBFD", surface="#FFFFFF",
    selected="#ECECF3", subtle="#EEECFB", accent="#6241D3", hover="#5535C0",
    pressed="#482BA8", focus="#DCD5FA", border="#EDEEF3", control="#DDE0E9",
    text="#1C2238", secondary="#626980", muted="#8B90A5",
    success="#147A50", success_bg="#E8FAEE", warning="#995B10", warning_bg="#FFF5E6",
    error="#B42338", error_bg="#FDEDEF")


def themed(styles):
    for name, value in COLORS.items():
        styles = styles.replace("@" + name + ";", value + ";")
        styles = styles.replace("@" + name + " ", value + " ")
    return styles


APP_STYLESHEET = themed("""
QWidget { color: @text; background: transparent; font-family: "Microsoft YaHei UI", "Segoe UI"; }
QMainWindow, QDialog, QWidget#workbench, QWidget#surface, QScrollArea,
QScrollArea > QWidget > QWidget { background: @background; }
QWidget#appRail { background: @sidebar; border-right: 1px solid @border; }
QWidget#topbar { background: @sidebar; border-bottom: 1px solid @border; }
QWidget#footer, QWidget#dialogHeader { background: @surface; }
QWidget#footer { border-top: 1px solid @border; }
QWidget#dialogHeader { border-bottom: 1px solid @border; }
QLabel#brand { font-size: 17px; font-weight: 600; }
QLabel#breadcrumb, QLabel#subtitle, QLabel#securityNote, QLabel#muted { color: @secondary; font-size: 13px; }
QLabel#title, QLabel#pageTitle { font-size: 24px; font-weight: 600; }
QLabel#sectionTitle, QLabel#dialogTitle { font-size: 16px; font-weight: 600; }
QLabel#fieldLabel { color: @secondary; font-size: 12px; }
QLabel#badge { background: @selected; color: @secondary; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
QLabel#badge[state="running"] { background: @success_bg; color: @success; }
QLabel#badge[state="paused"] { background: @warning_bg; color: @warning; }
QLabel[state="idle"] { color: @secondary; }
QLabel[state="running"], QLabel#statusRunning { color: @accent; }
QLabel[state="success"] { color: @success; }
QLabel[state="paused"], QLabel#statusPaused { color: @warning; }
QLabel[state="error"] { color: @error; }
QLabel#documentStatus { font-weight: 600; }
QFrame#taskSummary { background: @surface; border: 1px solid @border; border-radius: 8px; }
QWidget#taskCell { background: @surface; }
QFrame#emptyFiles { background: @surface; border: 1px dashed @control; border-radius: 8px; }
QFrame#emptyFiles[dragging="true"] { background: @subtle; border-color: @accent; }
QListWidget { background: transparent; border: 0; padding: 4px; outline: 0; }
QListWidget::item { padding: 8px; margin: 2px 0; border-radius: 6px; }
QListWidget::item:hover { background: @subtle; }
QListWidget::item:selected { background: @selected; color: @text; }
QListWidget#appNavigation { padding: 0; font-size: 13px; }
QListWidget#appNavigation::item { color: @secondary; }
QListWidget#appNavigation::item:selected { background: @selected; color: @text; font-weight: 600; }
QListWidget#appNavigation::item:focus { border: 1px solid @focus; }
QPushButton#railHelp { border: 0; text-align: left; font-size: 13px; padding: 0 6px; }
QLineEdit, QComboBox, QAbstractSpinBox {
    background: @surface; border: 1px solid @control; border-radius: 6px;
    padding: 6px 10px; min-height: 22px; selection-background-color: @accent; selection-color: white;
}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus { border-color: @accent; }
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled { background: @selected; color: @secondary; }
QLineEdit[recording="true"] { background: @warning_bg; border-color: @warning; }
QComboBox QLineEdit { border: 0; padding: 0; min-height: 0; }
QComboBox::drop-down { border: 0; width: 26px; }
QComboBox QAbstractItemView { background: @surface; color: @text; selection-background-color: @subtle; selection-color: @text; border: 1px solid @control; padding: 4px; }
QPushButton { min-height: 34px; padding: 0 12px; background: @surface; color: @text; border: 1px solid @control; border-radius: 6px; icon-size: 16px; }
QPushButton:hover { background: @subtle; border-color: @focus; }
QPushButton:pressed { background: @selected; }
QPushButton:focus, QToolButton:focus { border: 1px solid @accent; }
QPushButton#primary { background: @accent; color: white; border-color: @accent; font-weight: 600; }
QPushButton#primary:hover { background: @hover; border-color: @hover; }
QPushButton#primary:pressed { background: @pressed; border-color: @pressed; }
QPushButton#danger { color: @error; }
QPushButton#danger:hover { background: @error_bg; border-color: @error; }
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#danger:disabled, QToolButton:disabled { color: @secondary; background: @selected; border-color: @border; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 5px; icon-size: 16px; }
QToolButton:hover { background: @subtle; }
QToolButton:pressed { background: @selected; }
QToolButton#disclosure { text-align: left; color: @secondary; padding: 6px 0; }
QCheckBox, QRadioButton { spacing: 8px; }
QRadioButton#arrangement { background: @surface; border: 1px solid @control; border-radius: 6px; padding: 0 12px; }
QRadioButton#arrangement:checked { background: @subtle; border-color: @accent; }
QRadioButton#arrangement:hover { border-color: @focus; }
QRadioButton#arrangement:focus { border-color: @accent; }
QRadioButton#arrangement:disabled { background: @selected; border-color: @control; }
QCheckBox:disabled, QRadioButton:disabled { color: @secondary; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid @control; border-radius: 3px; background: @surface; }
QCheckBox::indicator:checked { background: @accent; border-color: @accent; }
QCheckBox::indicator:disabled { background-color: @selected; }
QCheckBox::indicator:focus, QRadioButton::indicator:focus { border: 1px solid @accent; }
QMenu { background: @surface; color: @text; border: 1px solid @control; border-radius: 6px; padding: 6px; }
QMenu::item { background: @surface; color: @text; border: 1px solid transparent; border-radius: 4px; padding: 8px 24px; }
QMenu::item:selected { background: @subtle; color: @text; border-color: @focus; }
QMenu::item:disabled { background: @surface; color: @secondary; }
QMenu::separator { height: 1px; background: @border; margin: 5px 8px; }
QTabWidget::pane { border: 0; background: transparent; }
QTabBar::tab { padding: 9px 14px; margin-right: 8px; color: @secondary; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { background: @subtle; color: @text; }
QTabBar::tab:selected { color: @accent; border-bottom: 2px solid @accent; }
QTabBar::tab:focus { background: @subtle; }
QProgressBar { background: @selected; border: 0; border-radius: 3px; color: @text; text-align: center; }
QProgressBar::chunk { background: @accent; border-radius: 3px; }
QProgressBar#taskProgress[state="success"]::chunk { background: @success; }
QProgressBar#taskProgress[state="paused"]::chunk { background: @warning; }
QProgressBar#taskProgress[state="error"]::chunk { background: @error; }
QTextBrowser, QTextEdit { background: @surface; color: @text; border: 1px solid @border; border-radius: 6px; padding: 10px; selection-background-color: @accent; selection-color: white; }
QTextBrowser#activity { background: transparent; border: 0; padding: 0; color: @secondary; font-size: 12px; }
QTextBrowser#taskDetails { background: @surface; border: 0; padding: 8px; }
QTreeWidget, QTableWidget { background: @surface; color: @text; border: 1px solid @border; border-radius: 8px; alternate-background-color: @sidebar; }
QHeaderView::section { background: @sidebar; color: @secondary; border: 0; border-bottom: 1px solid @border; padding: 8px; font-size: 12px; }
QTreeWidget::item, QTableWidget::item { padding: 6px 8px; }
QTreeWidget::item:selected, QTableWidget::item:selected { background: @selected; color: @text; }
QTreeWidget::item:hover, QTableWidget::item:hover { background: @subtle; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #C9CCD7; border-radius: 3px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #A5A9BA; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #C9CCD7; border-radius: 3px; min-width: 28px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QSplitter::handle, QFrame#separator { background: @border; }
QFrame#separator { min-height: 1px; max-height: 1px; border: 0; }
QToolTip { color: @text; background: @surface; border: 1px solid @control; padding: 6px; }
""")

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

FLOAT_STYLESHEET = APP_STYLESHEET + themed("""
QWidget#overlay, QWidget#screenPanel { background: @surface; border: 1px solid @control; border-radius: 8px; }
QLabel#sourcePreview { color: @secondary; font-size: 13px; }
QLabel#floatingTitle { font-size: 15px; font-weight: 600; }
QTextBrowser#translation { background: @surface; border: 0; padding: 2px; font-size: 14pt; }
QComboBox { font-size: 14px; padding: 3px 6px; min-height: 24px; }
""")


def set_state(widget: QWidget, state: str) -> None:
    widget.setProperty("state", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def section_heading(text: str, icon: str) -> QWidget:
    heading = QWidget()
    row = QHBoxLayout(heading)
    row.setContentsMargins(0, 4, 0, 2)
    row.setSpacing(8)
    symbol = QLabel()
    symbol.setPixmap(qta.icon(icon, color=COLORS["accent"]).pixmap(QSize(18, 18)))
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
