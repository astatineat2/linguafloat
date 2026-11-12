"""Navigation and modal safety for the native workbench."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QPushButton, QDialogButtonBox
from linguafloat.config import AppSettings
from linguafloat.ui import MainWindow
from linguafloat.workbench import MessageBox, AppDialog
from linguafloat.config_transfer_ui import OpenCodeServiceDialog
from test_credentials_ui import application


def test_navigation_keeps_page_indexes_and_compacts(application):
    w = MainWindow(AppSettings(), "missing")
    w.show()
    for width, rail in ((1060, 192), (860, 68)):
        w.resize(width, 620)
        application.processEvents()
        assert w.workbench.rail.width() == rail
        for i in range(3):
            w.workbench.navigation.setCurrentRow(i)
            assert w.pages.currentIndex() == i
        w.pages.setCurrentIndex(1)
        assert w.workbench.navigation.currentRow() == 1
    w.allow_close()
    w.close()


def test_confirmation_enter_and_escape_cannot_delete(application):
    checked = []
    def inspect():
        dialog = application.activeModalWidget()
        buttons = dialog.findChildren(QPushButton)
        assert {b.text() for b in buttons} == {"取消", "删除配置"}
        assert not any(b.isDefault() or b.autoDefault() for b in buttons)
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        assert dialog.isVisible()
        checked.append(True)
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    QTimer.singleShot(20, inspect)
    answer = MessageBox.question(None, "删除配置", "测试配置，不涉及真实数据")
    assert checked and answer != MessageBox.StandardButton.Yes


def test_save_discard_cancel_have_distinct_results(application):
    for caption, expected in (("保存配置", MessageBox.StandardButton.Save),
                              ("放弃修改", MessageBox.StandardButton.Discard),
                              ("取消", MessageBox.StandardButton.Cancel)):
        def choose(text=caption):
            dialog = application.activeModalWidget()
            next(b for b in dialog.findChildren(QPushButton) if b.text() == text).click()
        QTimer.singleShot(20, choose)
        result = MessageBox.question(None, "未保存的配置", "是否保存？",
            MessageBox.StandardButton.Save | MessageBox.StandardButton.Discard | MessageBox.StandardButton.Cancel)
        assert result == expected


def test_dialog_does_not_leave_blocking_shade(application):
    w = MainWindow(AppSettings(), "missing")
    w.show()
    dialog = OpenCodeServiceDialog(w)
    for _ in range(3):
        dialog.show()
        application.processEvents()
        assert dialog._shade is not None and dialog._shade.isVisible()
        names = {b.text() for b in dialog.findChildren(QPushButton)}
        assert {"保存配置", "取消"} <= names
        assert dialog.plan.isVisibleTo(dialog)
        assert dialog.key.isVisibleTo(dialog)
        dialog.reject()
        application.processEvents()
        assert dialog._shade is None
    assert len(dialog.findChildren(QPushButton)) == 2
    menu = next(iter(w.dashboard.cards.values())).findChild(QMenu)
    assert not menu.actions()[1].isEnabled()
    w.allow_close()
    w.close()
