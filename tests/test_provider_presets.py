"""Local preset projection and dialog behavior; all requests use a fake queue."""
import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QDialog, QLineEdit

from linguafloat.config import ProviderProfile
from linguafloat.provider_presets import NOW_CODING_MODELS, presets_for
from linguafloat.config_transfer_ui import ProviderPresetDialog
from test_credentials_ui import application, controller


def test_now_coding_ignores_private_profiles_and_keeps_public_defaults():
    profile = ProviderProfile("private", "No Coding", "https://example.test/v1", "chosen",
                              "chat_completions", env_key="PRIVATE_KEY", models=["other", "chosen"])
    preset, = presets_for("no_coding", [profile])
    assert preset.base_url == "https://nowcoding.ai/v1"
    assert preset.models == NOW_CODING_MODELS
    assert preset.api_mode == "responses"
    assert not hasattr(preset, "env_key") and not hasattr(preset, "id")
    assert "PRIVATE_KEY" not in repr(preset)


def test_missing_no_coding_is_not_guessed_from_unrelated_provider():
    profile = ProviderProfile("x", "Other", "https://example.test/v1", "model")
    preset, = presets_for("no_coding", [profile])
    assert (preset.name, preset.base_url, preset.api_mode) == ("Now Coding", "https://nowcoding.ai/v1", "responses")
    assert preset.models == NOW_CODING_MODELS and preset.model == NOW_CODING_MODELS[0]
    assert presets_for("no_coding", [profile], confirmed_names=["Other"])[0].name == "Now Coding"


def test_go_uses_existing_endpoint_protocol_and_models_without_switching_plan():
    go = ProviderProfile("g", "opencode", "https://opencode.ai/zen/go/v1", "local-model",
                         "chat_completions", models=["second"])
    paid = ProviderProfile("p", "OpenCode Go", "https://opencode.ai/inference/openai/v1", "paid-model")
    preset, = presets_for("go", [paid, go])
    assert (preset.base_url, preset.api_mode, preset.model) == (go.base_url, go.api_mode, go.model)
    assert preset.models[0] == "local-model" and "deepseek-v4.1-flash" in preset.models


def test_confirmed_now_coding_name_does_not_import_a_private_relay():
    profile = ProviderProfile("n", "Now Coding 全球加速", "https://local-relay.test/v1", "configured", "responses")
    preset, = presets_for("no_coding", [profile])
    assert (preset.base_url, preset.api_mode, preset.model) == ("https://nowcoding.ai/v1", "responses", NOW_CODING_MODELS[0])


def test_credential_bearing_addresses_are_not_projected():
    for address in ("https://user:secret@example.test/v1", "https://example.test/v1?key=secret",
                    "https://example.test/v1#secret"):
        p = ProviderProfile("x", "No Coding", address, "model")
        preset, = presets_for("no_coding", [p])
        assert preset.base_url == "https://nowcoding.ai/v1" and preset.model != "model"


class Queue:
    def __init__(self):
        self.tasks = []

    def start(self, task):
        self.tasks.append(task)


def test_custom_save_is_local_and_key_is_masked(application):
    d = ProviderPresetDialog("custom")
    queue = Queue(); d._thread_pool = queue
    d.name.setText("Custom")
    d.address.setText("https://example.test/v1")
    d.model.setEditText("manual-model")
    d.key.setText("synthetic")
    assert d.key.echoMode() == QLineEdit.EchoMode.Password
    assert not d.advanced_toggle.isChecked()
    d._confirm()
    assert d.result() == QDialog.DialogCode.Accepted
    assert d.candidate().profile.model == "manual-model"
    assert d.candidate().profile.env_key == ""
    assert not queue.tasks
    d.close()


def test_fetch_requires_key_and_failure_preserves_all_inputs(application):
    d = ProviderPresetDialog("custom")
    queue = Queue(); d._thread_pool = queue
    d.name.setText("Custom"); d.address.setText("https://example.test/v1")
    d.model.setEditText("manual")
    d._fetch_models()
    assert "Key" in d.status.text() and not queue.tasks
    d.key.setText("synthetic")
    d._fetch_models()
    task, = queue.tasks
    assert task.operation == "models"
    assert task.profile.model == "manual"
    task.signals.finished.emit(task.token, None, "鉴权失败，请检查 API Key。")
    assert "鉴权失败" in d.status.text()
    assert d.model.currentText() == "manual" and d.key.text() == "synthetic"
    assert d.address.text() == "https://example.test/v1" and d.name.text() == "Custom"
    assert d.fetch_models_button.isEnabled()
    d.close()


def test_stale_model_response_does_not_overwrite_edit(application):
    d = ProviderPresetDialog("custom")
    queue = Queue(); d._thread_pool = queue
    d.name.setText("Custom"); d.address.setText("https://example.test/v1")
    d.key.setText("synthetic"); d.model.setEditText("original")
    d._fetch_models()
    task, = queue.tasks
    d.model.setEditText("new-manual")
    task.signals.finished.emit(task.token, ["remote-model"], "")
    assert d.model.currentText() == "new-manual"
    assert d.model.findText("remote-model") == -1
    assert "忽略" in d.status.text()
    d.close()


def test_successful_fetch_keeps_manual_model_and_does_not_save(application):
    d = ProviderPresetDialog("custom")
    queue = Queue(); d._thread_pool = queue
    d.name.setText("Custom"); d.address.setText("https://example.test/v1")
    d.key.setText("synthetic"); d.model.setEditText("manual")
    d.mode.setCurrentIndex(d.mode.findData("anthropic"))
    d._fetch_models()
    task, = queue.tasks
    assert task.profile.api_mode == "anthropic"
    task.signals.finished.emit(task.token, ["remote", "remote"], "")
    assert d.model.currentText() == "manual"
    assert [d.model.itemText(i) for i in range(d.model.count())] == ["manual", "remote"]
    assert d.result() != QDialog.DialogCode.Accepted and d._candidate is None
    d.close()


def test_close_ignores_inflight_reply_and_clears_key(application):
    d = ProviderPresetDialog("custom")
    queue = Queue(); d._thread_pool = queue
    d.name.setText("Custom"); d.address.setText("https://example.test/v1")
    d.key.setText("synthetic"); d.model.setEditText("manual")
    d._fetch_models()
    task, = queue.tasks
    d.reject()
    assert task.cancelled and not task.key
    task.signals.finished.emit(task.token, ["remote"], "")
    assert not d.key.text() and d.model.findText("remote") == -1
    assert d._candidate is None
    d.close()


def test_preset_selection_never_copies_keys_or_mixes_provider_models(application):
    profiles = [ProviderProfile("a", "No Coding", "https://first.test/v1", "first"),
                ProviderProfile("b", "No-Coding", "https://second.test/v1", "second", "anthropic")]
    d = ProviderPresetDialog("no_coding", profiles)
    assert d.key.text() == "" and d.model.currentText() == NOW_CODING_MODELS[0]
    assert d.preset_combo.count() == 1 and d.preset_combo.isHidden()
    assert d.address.text() == "https://nowcoding.ai/v1"
    assert d.mode.currentData() == "responses"
    assert d.model.findText("first") == -1
    assert [p.base_url for p in profiles] == ["https://first.test/v1", "https://second.test/v1"]
    d.close()


def test_custom_native_auth_can_still_be_saved(application):
    d = ProviderPresetDialog("custom")
    d.name.setText("Custom native")
    d.address.setText("https://custom.test/v1")
    d.model.setEditText("manual")
    d.mode.setCurrentIndex(d.mode.findData("anthropic"))
    d.native_auth.setCurrentIndex(d.native_auth.findData("bearer"))
    d.key.setText("synthetic"); d._confirm()
    assert d.candidate().profile.native_auth == "bearer"
    d.clear_sensitive(); d.close()


def test_local_url_validation_never_echoes_malformed_credentials(application):
    d = ProviderPresetDialog("custom")
    queue = Queue(); d._thread_pool = queue
    d.name.setText("Custom"); d.model.setEditText("model"); d.key.setText("synthetic-key")
    d.address.setText("https://user:synthetic-key@example.test\uff0fwrong")
    for action in (d._fetch_models, d._confirm):
        action()
        assert "synthetic-key" not in d.status.text()
        assert "example.test" not in d.status.text()
        assert "地址" in d.status.text()
    assert not queue.tasks and d.key.text() == "synthetic-key"
    d.close()


@pytest.mark.parametrize("key", ["synthetic-key", " synthetic-key "])
def test_fetch_error_redacts_key_without_erasing_inputs(application, key):
    d = ProviderPresetDialog("custom")
    queue = Queue(); d._thread_pool = queue
    d.name.setText("Custom"); d.address.setText("https://example.test/v1")
    d.model.setEditText("manual"); d.key.setText(key)
    d._fetch_models(); task, = queue.tasks
    task.signals.finished.emit(task.token, None, "invalid credential synthetic-key")
    assert "synthetic-key" not in d.status.text()
    assert d.key.text() == key and d.model.currentText() == "manual"
    d.close()


def test_go_matching_uses_the_same_normalization_as_request_adapter():
    for address in ("https://opencode.ai:443/zen/go/v1/responses/",
                    "https://opencode.ai/inference/go/openai/v1/v1/chat/completions/"):
        p = ProviderProfile("g", "Local Go", address, "configured", "responses")
        preset, = presets_for("go", [p])
        assert (preset.base_url, preset.api_mode, preset.model) == (address, "responses", "configured")
    p = ProviderProfile("g", "Local Go", "https://opencode.ai:8443/zen/go/v1", "not-go")
    assert presets_for("go", [p])[0].model != "not-go"


def test_explicit_cancel_preserves_draft_and_allows_another_fetch(application):
    d = ProviderPresetDialog("custom")
    queue = Queue(); d._thread_pool = queue
    d.name.setText("Custom"); d.address.setText("https://example.test/v1")
    d.model.setEditText("manual"); d.key.setText("synthetic")
    d._fetch_models(); old = queue.tasks[0]
    d.cancel_models_button.click()
    assert old.cancelled and d.key.text() == "synthetic"
    d._fetch_models(); current = queue.tasks[1]
    old.signals.finished.emit(old.token, ["stale"], "")
    current.signals.finished.emit(current.token, ["fresh"], "")
    assert d.model.findText("stale") == -1 and d.model.findText("fresh") >= 0
    d.close()


@pytest.mark.parametrize("retry", [False, True])
def test_controller_preserves_failed_save_for_retry_and_clears_finished_draft(controller, monkeypatch, retry):
    c, secrets, store = controller
    dialogs, candidates, saves = [], [], []
    original_save = store.save
    original_count = len(c.settings.providers)
    original_active = c.settings.active_provider
    original_secrets = dict(secrets)

    def save(settings):
        saves.append(True)
        if len(saves) == 1:
            raise OSError("Synthetic write failure")
        original_save(settings)

    def interact(d):
        dialogs.append(d)
        if len(dialogs) == 1:
            d.name.setText("Retry Provider")
            d.address.setText("https://retry.example.test/v1")
            d.model.setEditText("manual-model")
            d.key.setText("synthetic-retry")
        else:
            assert d is dialogs[0]
            assert d.name.text() == "Retry Provider"
            assert d.address.text() == "https://retry.example.test/v1"
            assert d.model.currentText() == "manual-model"
            assert d.key.text() == "synthetic-retry"
            assert "保存未完成" in d.status.text()
            assert "synthetic-retry" not in d.status.text()
            assert candidates[0].secret == ""
            assert secrets == original_secrets
            if not retry:
                d.reject()
                return QDialog.DialogCode.Rejected
        d._confirm()
        candidates.append(d.candidate())
        return d.result()

    monkeypatch.setattr(store, "save", save)
    monkeypatch.setattr(ProviderPresetDialog, "exec", interact)
    c._configure_provider("custom")
    assert len(dialogs) == 2
    assert len(c.settings.providers) == original_count + int(retry)
    assert c.settings.active_provider == original_active
    assert dialogs[0].key.text() == "" and dialogs[0]._candidate is None
    assert all(candidate.secret == "" for candidate in candidates)
    if retry:
        assert store.load().providers[-1].name == "Retry Provider"
    else:
        assert secrets == original_secrets
