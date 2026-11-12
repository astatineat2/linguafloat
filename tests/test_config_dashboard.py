import copy
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QPushButton, QLabel, QDialog
from linguafloat.config import AppSettings, ProviderProfile, ConfigStore
from linguafloat.cc_switch import Candidate, CCSwitchError, import_category
from linguafloat.config_transfer_ui import CompleteConfigDialog
from linguafloat.ui import MainWindow
import linguafloat.app as app_module
from test_credentials_ui import controller, application
from test_config_transfer import Vault


@pytest.mark.parametrize("size", [(860,620),(1060,840)])
def test_cards_navigation_and_data_fit(application, size):
    settings = AppSettings(providers=[ProviderProfile(str(i), "长名称供应商"*8,
        "https://example.test/"+"long-prefix/"*12, "model-"*30, source_app="codex") for i in range(12)], active_provider="0")
    w = MainWindow(settings,"stored"); w.resize(*size); w.pages.setCurrentIndex(1); w.show()
    d=w.dashboard
    application.processEvents()
    assert not hasattr(d,"rail") and not hasattr(d,"nav_buttons")
    assert d.data_button.height()==d.add_button.height()
    assert d.stack.geometry().left()==0
    for b in (d.data_button,d.add_button):
        assert w.rect().contains(QRect(b.mapTo(w,QPoint()),b.size()))
    assert (w.width(),w.height()) == size
    assert len(d.cards)==12 and d.card_scroll.verticalScrollBar().maximum()>0
    assert d.card_scroll.horizontalScrollBar().maximum()==0
    cards=list(d.cards.values())
    for first,second in zip(cards,cards[1:]):
        assert not first.geometry().intersects(second.geometry())
    for card in d.cards.values():
        for b in card.findChildren(QPushButton):
            assert card.rect().contains(QRect(b.mapTo(card,QPoint()),b.size()))
            assert b.width()>=b.minimumSizeHint().width()
    d.show_data(); application.processEvents()
    for b in [d.sql_button,d.export_button]:
        assert w.rect().contains(QRect(b.mapTo(w,QPoint()),b.size()))
        assert b.width()>=b.minimumSizeHint().width()
    w.allow_close(); w.close()


def test_browsing_editing_not_activation_and_key_hidden(controller, application):
    c, secrets, store=controller; w=c.window; d=w.dashboard
    w.show(); w.pages.setCurrentIndex(1); application.processEvents()
    d._edit("second"); application.processEvents()
    assert c.settings.active_provider=="test"
    assert d.viewed_id=="second" and w.provider_editor.isVisible()
    assert d.cards["test"].property("active")
    assert d.cards["second"].property("viewed")
    assert all(secret not in " ".join(x.text() for x in d.findChildren(QLabel)) for secret in secrets.values())
    w.provider_editor.reject()
    d.use_requested.emit("second")
    assert c.settings.active_provider=="second" and store.load().active_provider=="second"


def test_sql_all_sources_import_retry_and_restart(controller, monkeypatch):
    c, secrets, store=controller
    entries=[Candidate(source,str(i),ProviderProfile("new"+str(i),"Demo", "https://example.test/"+str(i),"model",env_key=""),"synthetic")
             for i,source in enumerate(["codex","claude","gemini","opencode"])]
    entries[1].profile.model=""
    monkeypatch.setattr(app_module.QFileDialog,"getOpenFileName",lambda *a:("all.sql",""))
    monkeypatch.setattr(app_module,"read_candidates",lambda path:entries)
    snapshot=copy.deepcopy(c.settings.active_profile())
    c._import_batch("sql",True)
    assert [r[1] for r in c._transfer_report]==["成功","需要补全","成功","成功"]
    assert set(c._transfer_pending)=={1}
    assert c.settings.active_profile()==snapshot
    assert {p.source_app for p in store.load().providers}=={"manual","codex","gemini","opencode"}
    c._import_batch("sql",True)
    assert [r[1] for r in c._transfer_report]==["重复跳过","需要补全","重复跳过","重复跳过"]
    assert len(store.load().providers)==5
    assert "synthetic" not in store.path.read_text(encoding="utf-8")
    monkeypatch.setattr(app_module.CompleteConfigDialog,"exec",lambda self:0)
    c._complete_import(1)
    assert 1 in c._transfer_pending
    def complete(self):
        self.item.profile.model="completed-model"
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(app_module.CompleteConfigDialog,"exec",complete)
    c._complete_import(1)
    assert not c._transfer_pending
    assert store.load().providers[-1].source_app=="claude"


def test_sql_only_reports_specific_failure(controller, monkeypatch):
    c,_,_=controller
    monkeypatch.setattr(app_module.QFileDialog,"getOpenFileName",lambda *a:("selected.sql",""))
    def fail(_):raise CCSwitchError("SQL 事务未结束，文件可能被截断。")
    monkeypatch.setattr(app_module,"read_candidates",fail)
    c._import_batch("sql",True)
    assert "事务未结束" in c._transfer_report[0][2]
    with pytest.raises(ValueError):c._import_batch("cc")
    assert not hasattr(c.window.dashboard,"local_requested")


def test_missing_key_can_be_corrected_after_validation_failure(application):
    original=Candidate("codex","test",ProviderProfile("new","Test","","",env_key=""))
    d=CompleteConfigDialog(original)
    d.key.setText("first-synthetic-key"); d._confirm()
    assert d.result()!=QDialog.DialogCode.Accepted
    d.address.setText("https://example.test/v1"); d.model.setText("model")
    d.key.setText("corrected-synthetic-key"); d._confirm()
    assert d.result()==QDialog.DialogCode.Accepted
    assert d.item.secret=="corrected-synthetic-key" and original.secret==""


def test_old_profile_sources_are_not_guessed_and_source_survives_save(controller):
    c,_,store=controller
    c.settings.providers[0].name="Codex / Gemini / DeepSeek"
    store.save(c.settings)
    assert store.load().providers[0].source_app=="manual"
    c.settings.providers[0].source_app="claude"
    c.window.save_button.click()
    assert store.load().providers[0].source_app=="claude"
    c._duplicate_provider("test")
    assert store.load().providers[-1].source_app=="claude"
