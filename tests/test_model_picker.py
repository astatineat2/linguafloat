import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from linguafloat.model_picker import MODEL_ROLE, SELECTED_ROLE, ModelSelector
from test_credentials_ui import application, controller


@pytest.fixture
def picker(application):
    control = ModelSelector()
    control.setEditable(True)
    control.addItems(["alpha", "beta-model", "gamma"])
    control.setCurrentIndex(1)
    control.favorites = ["gamma"]
    control.resize(300, 40)
    control.show()
    application.processEvents()
    yield control
    control.hidePopup()
    control.close()
    application.processEvents()


def values(menu):
    return [menu.models.item(i).data(MODEL_ROLE) for i in range(menu.models.count())
            if menu.models.item(i).data(MODEL_ROLE)]


def test_open_filter_and_escape_do_not_commit(picker):
    calls = []
    picker.activated.connect(calls.append)
    picker.showPopup()
    assert values(picker.menu) == ["gamma", "beta-model", "alpha"]
    picker.menu.search.setText("BETA")
    assert values(picker.menu) == ["beta-model"]
    QTest.keyClick(picker.menu.search, Qt.Key.Key_Escape)
    assert not picker.menu.isVisible()
    assert picker.currentText() == "beta-model" and calls == []


def test_current_check_and_click_commit_once(picker):
    calls = []
    picker.activated.connect(calls.append)
    picker.showPopup()
    selected = [picker.menu.models.item(i).data(MODEL_ROLE)
                for i in range(picker.menu.models.count())
                if picker.menu.models.item(i).data(SELECTED_ROLE)]
    assert selected == ["beta-model"]
    picker.menu.search.setText("gamma")
    QApplication.processEvents()
    QTest.qWait(150)
    item = picker.menu.models.currentItem()
    QTest.mouseClick(picker.menu.models.viewport(), Qt.MouseButton.LeftButton,
                     pos=picker.menu.models.visualItemRect(item).center())
    assert picker.currentText() == "gamma"
    assert len(calls) == 1 and not picker.menu.isVisible()


def test_custom_model_works_without_a_model_list(picker):
    picker.clear()
    picker.setCurrentText("existing")
    picker.showPopup()
    picker.menu.search.setText("relay/custom-model")
    assert picker.menu.custom.isVisible()
    picker.menu.custom.click()
    assert picker.currentText() == "relay/custom-model"
    picker.showPopup()
    assert "relay/custom-model" in values(picker.menu)


def test_clear_invalidates_open_menu(picker):
    picker.showPopup()
    picker.clear()
    assert not picker.menu.isVisible()
    picker.menu.commit("obsolete")
    assert picker.currentText() == ""


def test_keyboard_search_accepts_model(picker):
    picker.showPopup()
    picker.menu.search.setText("alpha")
    QTest.keyClick(picker.menu.search, Qt.Key.Key_Return)
    assert picker.currentText() == "alpha"


def test_readonly_display_opens_menu_on_click(picker):
    picker.use_menu_display()
    QTest.mouseClick(picker.lineEdit(), Qt.MouseButton.LeftButton)
    assert picker.menu.isVisible()


def test_menu_stays_inside_screen(picker):
    area = picker.screen().availableGeometry()
    picker.move(area.right() - 200, area.bottom() - 60)
    picker.showPopup()
    assert area.contains(picker.menu.geometry())


def test_profile_menu_edits_without_activating_and_saves_custom_model(controller):
    c, _, store = controller
    c.window.provider_combo.setCurrentIndex(1)
    picker = c.window.model_search_combo
    picker.showPopup()
    picker.menu.search.setText("custom-draft")
    picker.menu.custom.click()
    assert c.settings.active_profile().model == "test-model"
    assert c.window.is_dirty()
    c.window.save_button.click()
    saved = next(p for p in store.load().providers if p.id == "second")
    assert saved.model == "custom-draft" and "custom-draft" in saved.models


def test_active_menu_persists_choice_without_request(controller):
    from test_app_controller import FakeThreadPool
    c, _, store = controller
    c.thread_pool = FakeThreadPool()
    picker = c.window.active_model_combo
    picker.showPopup()
    picker.menu.search.setText("custom-active")
    picker.menu.custom.click()
    profile = store.load().active_profile()
    assert profile.model == "custom-active"
    assert "custom-active" in profile.models and "test-model" in profile.models
    assert c.thread_pool.tasks == []


def test_cancel_unsaved_draft_restores_active_model(controller, monkeypatch):
    c, _, _ = controller
    c.window.model_edit.setText("unsaved-draft")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    picker = c.window.active_model_combo
    picker.showPopup()
    picker.menu.search.setText("new-choice")
    picker.menu.custom.click()
    assert picker.currentText() == "test-model"
    assert c.window.model_edit.text() == "unsaved-draft"


def test_failed_model_save_restores_list_and_selection(controller, monkeypatch):
    c, _, store = controller
    before = list(c.settings.active_profile().models)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(store, "save", fail)
    c._select_model("new-choice")
    assert c.settings.active_profile().models == before
    assert c.window.active_model_combo.currentText() == "test-model"
