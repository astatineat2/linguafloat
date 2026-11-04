import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit

import linguafloat.app as app_module
import linguafloat.config as config_module
from linguafloat.config import AppSettings, ConfigStore, ProviderProfile
from linguafloat.ui import MainWindow


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(application, monkeypatch, tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    store.save(AppSettings(active_provider="test", providers=[
        ProviderProfile("test", "Test", "https://example.com/v1", "test-model", env_key=""),
        ProviderProfile("second", "Second", "https://example.org/v1", "second-model", env_key=""),
    ]))
    secrets = {"test": "dummy-stored-key", "second": "dummy-second-key"}
    monkeypatch.setattr(config_module.keyring, "get_password", lambda service, name: secrets.get(name))
    monkeypatch.setattr(config_module.keyring, "set_password", lambda service, name, value: secrets.__setitem__(name, value))
    monkeypatch.setattr(config_module.keyring, "delete_password", lambda service, name: secrets.pop(name, None))
    monkeypatch.setattr(app_module, "ConfigStore", lambda: store)
    monkeypatch.setattr(app_module.AppController, "_configure_triggers", lambda *args: None)
    instance = app_module.AppController(application)
    yield instance, secrets, store
    instance.window.allow_close()
    instance.window.close()
    instance.overlay.close()
    instance.action_button.close()
    instance.game_monitor.stop()


def test_saved_key_is_masked_and_eye_toggles_without_editing(controller):
    c, _, _ = controller
    field = c.window.api_key_edit
    assert field.text() == "dummy-stored-key"
    assert field.displayText() != field.text()
    assert field.displayText()
    assert not c.window.key_visibility_action.icon().isNull()
    c.window.key_visibility_action.trigger()
    assert field.echoMode() == QLineEdit.EchoMode.Normal
    assert field.displayText() == "dummy-stored-key"
    assert not field.isModified()
    c.window.key_visibility_action.trigger()
    assert field.echoMode() == QLineEdit.EchoMode.Password


def test_save_retains_new_key_as_dots_and_configuration_has_no_secret(controller):
    c, secrets, store = controller
    c.window.api_key_edit.selectAll()
    c.window.api_key_edit.insert("dummy-replacement-key")
    c.window.save_button.click()
    assert secrets["test"] == "dummy-replacement-key"
    assert c.window.api_key_edit.text() == "dummy-replacement-key"
    assert c.window.api_key_edit.displayText() != "dummy-replacement-key"
    assert not c.window.api_key_edit.isModified()
    assert "dummy-replacement-key" not in store.path.read_text(encoding="utf-8")


def test_switching_provider_reloads_key_and_hides_plaintext(controller):
    c, _, _ = controller
    c.window.key_visibility_action.trigger()
    c.window.provider_combo.setCurrentIndex(1)
    assert c.window.api_key_edit.text() == "dummy-second-key"
    assert c.window.api_key_edit.echoMode() == QLineEdit.EchoMode.Password
    c.window.provider_combo.setCurrentIndex(0)
    assert c.window.api_key_edit.text() == "dummy-stored-key"


def test_hiding_window_masks_revealed_key(controller, application):
    c, _, _ = controller
    c.window.show()
    application.processEvents()
    c.window.key_visibility_action.trigger()
    c.window.hide()
    assert c.window.api_key_edit.echoMode() == QLineEdit.EchoMode.Password


def test_unmodified_environment_key_is_not_copied_to_local_storage(controller, monkeypatch):
    c, secrets, store = controller
    monkeypatch.setenv("LINGUAFLOAT_TEST_KEY", "dummy-environment-key")
    c.settings.active_profile().env_key = "LINGUAFLOAT_TEST_KEY"
    c.window.env_key_edit.setText("LINGUAFLOAT_TEST_KEY")
    c._refresh_key(c.settings.active_profile())
    assert c.window.api_key_edit.text() == "dummy-environment-key"
    c.window.save_button.click()
    assert secrets["test"] == "dummy-stored-key"
    assert store.load().active_profile().env_key == "LINGUAFLOAT_TEST_KEY"


def test_explicit_key_replacement_overrides_old_environment_for_this_provider(controller, monkeypatch):
    c, secrets, store = controller
    monkeypatch.setenv("LINGUAFLOAT_TEST_KEY", "dummy-environment-key")
    c.settings.active_profile().env_key = "LINGUAFLOAT_TEST_KEY"
    c.window.env_key_edit.setText("LINGUAFLOAT_TEST_KEY")
    c._refresh_key(c.settings.active_profile())
    c.window.api_key_edit.selectAll()
    c.window.api_key_edit.insert("dummy-new-key")
    c.window.save_button.click()
    p = store.load().active_profile()
    assert p.env_key == ""
    assert c.credentials.get_api_key(p.id, p.env_key) == "dummy-new-key"
    assert os.environ["LINGUAFLOAT_TEST_KEY"] == "dummy-environment-key"


def test_connection_test_uses_unsaved_form_and_does_not_hit_translation_cache(controller):
    c, secrets, store = controller
    class Pool:
        def __init__(self):
            self.tasks = []

        def start(self, task):
            self.tasks.append(task)

    c.thread_pool = Pool()
    original = store.path.read_bytes()
    c.window.api_key_edit.selectAll()
    c.window.api_key_edit.insert("dummy-unsaved-key")
    c.window.model_edit.setText("unsaved-model")
    c.window.output_token_limit_spin.setValue(4096)
    c.window.chat_token_parameter_combo.setCurrentIndex(c.window.chat_token_parameter_combo.findData("max_tokens"))
    c.window.instruction_role_combo.setCurrentIndex(c.window.instruction_role_combo.findData("developer"))
    c.window.test_connection_button.click()
    c.window.test_connection_button.click()
    assert c.thread_pool.tasks[0].profile.output_token_limit == 4096
    assert c.thread_pool.tasks[0].profile.chat_token_parameter == "max_tokens"
    assert c.thread_pool.tasks[0].profile.instruction_role == "developer"
    assert len(c.thread_pool.tasks) == 1
    task = c.thread_pool.tasks[0]
    assert task.api_key == "dummy-unsaved-key"
    assert task.profile.model == "unsaved-model"
    assert task.timeout == 12.0 and task.max_retries == 0
    assert secrets["test"] == "dummy-stored-key"
    assert store.path.read_bytes() == original
    task.signals.succeeded.emit(task.profile.cache_scope, "Hello.", "Hi")
    assert c.window.test_connection_button.isEnabled()
    assert c.cache.get(task.profile.cache_scope, "Hello.") is None
    assert not c.overlay.isVisible()


def test_connection_test_failure_allows_retry_and_empty_key_sends_nothing(controller):
    c, _, _ = controller
    tasks = []
    class Pool:
        def start(self, task):
            tasks.append(task)
    c.thread_pool = Pool()
    c.window.api_key_edit.clear()
    c.window.test_connection_button.click()
    assert tasks == []
    c.window.api_key_edit.insert("dummy-test-key")
    c.window.test_connection_button.click()
    task = tasks[0]
    task.signals.failed.emit(task.profile.cache_scope, "Hello.", "HTTP 401")
    assert c.window.test_connection_button.isEnabled()
    assert "401" in c.window.connection_status.text()
    c.window.test_connection_button.click()
    assert len(tasks) == 2
