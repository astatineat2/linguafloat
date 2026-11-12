import copy
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLineEdit, QPushButton

from test_credentials_ui import application, controller
from linguafloat.config import AppSettings, ConfigStore
from linguafloat.config_transfer_ui import FirstRunSetupDialog


@pytest.fixture
def setup_dialog(application):
    dialog = FirstRunSetupDialog(AppSettings())
    yield dialog
    dialog.clear_sensitive()
    dialog.done(QDialog.DialogCode.Rejected)


def fill_custom(dialog):
    dialog.service_kind.setCurrentIndex(dialog.service_kind.findData("custom"))
    dialog.name.setText("Offline Test")
    dialog.address.setText("https://example.test/v1")
    dialog.model.setEditText("manual-model")
    dialog.key.setText("synthetic-onboarding-key")


def start_setup(app):
    app.settings.update_auto_check = False
    app.settings.first_run_complete = False
    app.help.start()
    return app.first_run_dialog


def test_single_page_actions_defaults_and_masked_key(setup_dialog):
    d = setup_dialog
    defaults = AppSettings()
    assert d.windowTitle() == "配置翻译服务"
    assert d.skip_button.text() == "跳过，稍后配置"
    assert d.save_button.text() == "完成并保存"
    assert defaults.hotkey in d.shortcut_summary.text()
    assert defaults.input_hotkey in d.shortcut_summary.text()
    assert d.key.echoMode() == QLineEdit.EchoMode.Password
    assert [d.service_kind.itemData(i) for i in range(3)] == ["go", "no_coding", "custom"]
    assert not any(b.text() == "下一步" for b in d.findChildren(QPushButton))


def test_validation_and_enter_cannot_save_or_start_requests(setup_dialog):
    d = setup_dialog
    saves = []
    d.save_requested.connect(lambda: saves.append(True))
    d.show()
    d.save_button.click()
    assert not saves and "Key" in d.status.text()
    fill_custom(d)
    d.key.setFocus()
    QTest.keyClick(d.key, Qt.Key.Key_Return)
    assert not saves
    d.save_button.click()
    assert saves == [True]
    assert d.result() != QDialog.DialogCode.Accepted


def test_switch_preserves_drafts_and_clears_sensitive_state(setup_dialog):
    d = setup_dialog
    fill_custom(d)
    d.service_kind.setCurrentIndex(d.service_kind.findData("no_coding"))
    assert d.address.text() == "https://nowcoding.ai/v1" and not d.key.text()
    d.service_kind.setCurrentIndex(d.service_kind.findData("custom"))
    assert d.name.text() == "Offline Test"
    assert d.key.text() == "synthetic-onboarding-key"
    d.clear_sensitive()
    assert not d.key.text() and not d._drafts and d._candidate is None


def test_explicit_fetch_and_stale_or_failed_reply_preserve_fields(setup_dialog):
    from test_provider_presets import Queue
    d = setup_dialog
    d._thread_pool = queue = Queue()
    fill_custom(d)
    assert not queue.tasks
    d.fetch_models_button.click()
    task, = queue.tasks
    assert task.operation == "models" and task.profile.model == "manual-model"
    task.signals.finished.emit(task.token, None, "鉴权失败 synthetic-onboarding-key")
    assert "鉴权失败" in d.status.text() and "synthetic-onboarding-key" not in d.status.text()
    assert d.key.text() == "synthetic-onboarding-key" and d.model.currentText() == "manual-model"
    d.fetch_models_button.click()
    stale = queue.tasks[-1]
    d.service_kind.setCurrentIndex(d.service_kind.findData("go"))
    assert stale.cancelled and not stale.key
    stale.signals.finished.emit(stale.token, ["stale"], "")
    assert d.model.findText("stale") == -1


@pytest.mark.parametrize("close", ["skip", "escape", "close"])
def test_skip_or_close_marks_dismissed_without_creating_provider(controller, close):
    app, secrets, store = controller
    before = copy.deepcopy(app.settings.providers)
    vault = dict(secrets)
    d = start_setup(app)
    fill_custom(d)
    if close == "skip":
        d.skip_button.click()
    elif close == "escape":
        QTest.keyClick(d, Qt.Key.Key_Escape)
    else:
        d.close()
    assert app.settings.first_run_complete and store.load().first_run_complete
    assert app.settings.providers == before and secrets == vault
    assert app.first_run_dialog is None and not d.key.text()
    app.help.start()
    assert app.first_run_dialog is None


def test_save_commits_credential_activation_and_marker_together(controller):
    app, secrets, store = controller
    shared = app.settings
    old_ids = {p.id for p in shared.providers}
    d = start_setup(app)
    fill_custom(d)
    provider_id = d._provider_id
    d.save_button.click()
    saved = store.load()
    assert app.settings is shared
    assert saved.active_provider == provider_id and saved.first_run_complete
    assert len(saved.providers) == len(old_ids) + 1
    assert old_ids <= {p.id for p in saved.providers}
    assert secrets[provider_id] == "synthetic-onboarding-key"
    assert "synthetic-onboarding-key" not in store.path.read_text(encoding="utf-8")
    assert app.window.active_provider_combo.currentData() == provider_id
    assert app.first_run_dialog is None and not d.key.text()
    assert app.documents.settings is shared


@pytest.mark.parametrize("skip", [False, True])
def test_failed_save_keeps_dialog_input_and_original_configuration(controller, monkeypatch, skip):
    app, secrets, store = controller
    d = start_setup(app)
    fill_custom(d)
    original_settings, vault, raw = copy.deepcopy(app.settings), dict(secrets), store.path.read_bytes()
    save = store.save
    monkeypatch.setattr(store, "save", lambda _: (_ for _ in ()).throw(OSError("synthetic")))
    (d.skip_button if skip else d.save_button).click()
    assert app.settings == original_settings and secrets == vault and store.path.read_bytes() == raw
    assert d.isVisible() and "保存未完成" in d.status.text()
    assert d.key.text() == "synthetic-onboarding-key"
    assert d._candidate is None
    monkeypatch.setattr(store, "save", save)
    d.skip_button.click()
    assert store.load().first_run_complete


def test_credential_failure_does_not_mark_first_run_complete(controller, monkeypatch):
    from linguafloat.config import CredentialError
    app, _, _ = controller
    d = start_setup(app)
    fill_custom(d)
    monkeypatch.setattr(app.credentials, "save_api_key", lambda *args: (_ for _ in ()).throw(CredentialError("blocked")))
    d.save_button.click()
    assert not app.settings.first_run_complete
    assert d.isVisible() and "保存未完成" in d.status.text()
    assert not d._candidate
    d.skip_button.click()


def test_existing_user_never_forced_into_first_run(controller, tmp_path):
    app, _, _ = controller
    app.settings.update_auto_check = False
    app.settings.first_run_complete = True
    app.help.start()
    assert app.first_run_dialog is None
    legacy = ConfigStore(tmp_path / "legacy.json")
    legacy.path.write_text('{"update_auto_check":false}', encoding="utf-8")
    assert legacy.load().first_run_complete


@pytest.mark.parametrize("size", [(640, 610), (520, 430), (420, 320)])
def test_footer_stays_visible_and_form_scrolls(setup_dialog, application, size):
    d = setup_dialog
    d.resize(*size)
    d.show()
    application.processEvents()
    for control in (d.skip_button, d.save_button):
        assert d.rect().contains(QRect(control.mapTo(d, QPoint()), control.size()))
        assert control.width() >= control.minimumSizeHint().width()
    if size == (420, 320):
        assert d.scroll.verticalScrollBar().maximum() > 0
    d.advanced_toggle.click()
    application.processEvents()
    assert d.scroll.horizontalScrollBar().maximum() == 0


def test_installer_sequence_folder_creation_and_single_finish_message():
    source = Path("scripts/installer/LinguaFloat.iss").read_text(encoding="utf-8")
    assets = Path("scripts/build_distribution.py").read_text(encoding="utf-8")
    assert "DisableReadyPage=yes" in source
    assert "FinishedLabel=LinguaFloat v{#AppVersion} 已安装。\n" in source
    assert source.count("ClickFinish=点击“完成”退出安装程序。") == 1
    assert "BrowseForFolder('选择安装位置，可新建文件夹并修改名称', Directory, True)" in source
    assert "WizardForm.DirBrowseButton.OnClick := @ChooseInstallDirectory" in source
    assert "WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall)" in source
    assert "draw.rectangle((0, 0, 7, 628)" not in assets
