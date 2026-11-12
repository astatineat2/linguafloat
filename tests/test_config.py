import json

import pytest

from linguafloat.config import (
    AppSettings,
    ConfigStore,
    ProviderProfile,
    validate_provider,
)


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    expected = AppSettings(
        ocr_interval=1.5,
        translation_interval=6.0,
        hotkey="Ctrl+Shift+Y",
        trigger_mode="right_click",
        active_provider="proxy",
        providers=[
            ProviderProfile(
                id="proxy",
                name="Example Proxy",
                base_url="https://proxy.example.com/v1",
                model="example-model",
            )
        ],
    )

    store.save(expected)

    assert store.load() == expected
    assert "api" not in json.loads(path.read_text(encoding="utf-8"))


def test_reference_protection_defaults_on_and_migrates_legacy_option(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    settings = store.load()
    assert settings.document_skip_references
    settings.document_skip_references = False
    store.save(settings)
    assert ConfigStore(store.path).load().document_skip_references


def test_invalid_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("not json", encoding="utf-8")

    assert ConfigStore(path).load() == AppSettings()


def test_invalid_trigger_mode_falls_back_to_both(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"trigger_mode": "unknown"}), encoding="utf-8")

    assert ConfigStore(path).load().trigger_mode == "both"


@pytest.mark.parametrize("value", [-1, 0, 999, None, "fast", True, float("nan")])
def test_invalid_ocr_intervals_use_defaults(tmp_path, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ocr_interval": value, "translation_interval": value}), encoding="utf-8")
    settings = ConfigStore(path).load()
    assert settings.ocr_interval == 1.0
    assert settings.translation_interval == 3.0


def test_migrates_legacy_single_provider_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"hotkey": "Ctrl+Alt+T", "model": "legacy-model"}),
        encoding="utf-8",
    )

    settings = ConfigStore(path).load()

    assert settings.active_profile().model == "legacy-model"


def test_rejects_insecure_remote_base_url():
    profile = ProviderProfile(
        id="proxy",
        name="Proxy",
        base_url="http://proxy.example.com/v1",
        model="example-model",
    )

    with pytest.raises(ValueError, match="HTTPS"):
        validate_provider(profile)


def test_allows_local_http_base_url():
    profile = ProviderProfile(
        id="local",
        name="Local",
        base_url="http://127.0.0.1:11434/v1",
        model="local-model",
    )

    validate_provider(profile)
