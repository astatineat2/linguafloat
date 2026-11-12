import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPalette, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu, QCheckBox, QPushButton, QDialog, QToolButton
from linguafloat.ui import MainWindow
from linguafloat.config import AppSettings, ProviderProfile
from linguafloat.help_ui import HelpDialog
from test_credentials_ui import application, controller
import linguafloat.app as module


def test_data_roundtrip_preserves_search_and_scroll(application):
    settings=AppSettings(providers=[ProviderProfile(str(i),f"Demo {i}","https://example.test","model") for i in range(20)])
    w=MainWindow(settings,"missing");w.pages.setCurrentIndex(1);w.resize(860,620);w.show()
    d=w.dashboard;d.search.setText("Demo");QTest.qWait(30)
    bar=d.card_scroll.verticalScrollBar();bar.setValue(220)
    before=bar.value();d.data_button.click();QTest.qWait(20)
    assert d.stack.currentWidget()==d.data_page
    d.back_button.click();QTest.qWait(30)
    assert d.stack.currentWidget()==d.list_page
    assert d.search.text()=="Demo" and bar.value()==before
    d.search.setText("no-match");QTest.qWait(20)
    assert len(d.cards)==0
    w.allow_close();w.close()


@pytest.mark.parametrize("dark",[False,True])
def test_popup_pixels_and_keyboard_selection(application,dark):
    old=application.palette()
    palette=QPalette(old)
    for role in (QPalette.ColorRole.Window,QPalette.ColorRole.Base,QPalette.ColorRole.Button):
        palette.setColor(role,QColor('#202020' if dark else '#ffffff'))
    palette.setColor(QPalette.ColorRole.Text,QColor('#eeeeee' if dark else '#202020'))
    application.setPalette(palette)
    settings=AppSettings(providers=[ProviderProfile(str(i),f"Demo {i}","https://example.test","model") for i in range(2)],active_provider="0")
    w=MainWindow(settings,"missing");w.show();w.pages.setCurrentIndex(1)
    menus=[w.dashboard.add_button.menu(),next(m for m in w.dashboard.cards.values()).findChild(QMenu)]
    try:
        for menu in menus:
            disabled=menu.addAction('Disabled test');disabled.setEnabled(False)
            menu.popup(w.mapToGlobal(QPoint(200,200)));QTest.qWait(30)
            img=menu.grab().toImage();dpr=img.devicePixelRatio()
            assert img.pixelColor(int(8*dpr),int(8*dpr)).lightness()>230
            action=menu.actions()[0];QTest.keyClick(menu,Qt.Key.Key_Down);QTest.qWait(20)
            assert menu.activeAction()==action
            rect=menu.actionGeometry(action)
            img=menu.grab().toImage()
            bg=img.pixelColor(int((rect.left()+4)*dpr),int(rect.center().y()*dpr))
            assert bg.lightness()>180 and bg.blue()>bg.red()
            # Offscreen does not reliably dispatch global cursor moves. Hover is
            # exercised separately with the native Windows verification harness.
            QTest.keyClick(menu,Qt.Key.Key_Down);QTest.qWait(20)
            assert menu.activeAction()==menu.actions()[1]
            QTest.keyClick(menu,Qt.Key.Key_Down);QTest.qWait(20)
            assert menu.activeAction()!=disabled
            menu.hide();menu.removeAction(disabled)
    finally:
        w.allow_close();w.close();application.setPalette(old)


def test_copy_delete_preserve_active_and_credentials(controller,monkeypatch):
    c,secrets,store=controller
    original=c.settings.active_provider
    menu=c.window.dashboard.cards['second'].findChild(QMenu)
    menu.actions()[0].trigger()
    duplicate=c.settings.providers[-1]
    assert duplicate.id not in {'test','second'} and secrets[duplicate.id]==secrets['second']
    assert c.settings.active_provider==original
    monkeypatch.setattr(module.QMessageBox,'question',lambda *a:module.QMessageBox.StandardButton.Yes)
    c.window.dashboard.cards[duplicate.id].findChild(QMenu).actions()[1].trigger()
    assert duplicate.id not in {p.id for p in store.load().providers}
    assert c.settings.active_provider==original and secrets['second']=='dummy-second-key'


def test_editor_cleanup_and_sql_key_choice_still_default_off(controller,monkeypatch):
    c,_,_=controller;w=c.window
    assert not hasattr(w,'include_key_checkbox')
    assert not any(b.text() in {'导入 Codex','导出当前供应商'} for b in w.findChildren(QPushButton))
    assert w.api_key_edit and w.delete_key_button and w.key_visibility_action
    inspected=[]
    def inspect(dialog):
        boxes=dialog.findChildren(QCheckBox)
        assert len(boxes)==1 and '包含 API Key' in boxes[0].text() and not boxes[0].isChecked()
        inspected.append(True)
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(module.QDialog,'exec',inspect)
    c._export_cc_sql();assert inspected


def test_help_only_two_tabs_with_accessible_notices(application):
    h=HelpDialog(AppSettings())
    assert [h.tabs.tabText(i) for i in range(h.tabs.count())]==['软件更新','首次使用指南']
    assert 'THIRD_PARTY_NOTICES.md' in h.guide.toHtml()
    assert 'AGPL-3.0-only' in h.guide.toPlainText()
    h.close()
