import tomllib

import pytest

from linguafloat.codex_config import (
    CodexConfigError,
    export_codex_config,
    import_codex_config,
)
from linguafloat.config import ProviderProfile


def test_imports_codex_provider_and_secret(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '''model = "example-model"
model_provider = "proxy"

[model_providers.proxy]
name = "Example Proxy"
base_url = "https://proxy.example.com/v1"
wire_api = "responses"
experimental_bearer_token = "secret-value"
''',
        encoding="utf-8",
    )

    result = import_codex_config(path)

    assert result.active_provider == "proxy"
    assert result.profiles[0].base_url == "https://proxy.example.com/v1"
    assert result.secrets == {"proxy": "secret-value"}


def test_exports_safe_codex_config_without_secret(tmp_path):
    path = tmp_path / "export.toml"
    profile = ProviderProfile(
        id="proxy",
        name="Example Proxy",
        base_url="https://proxy.example.com/v1",
        model="example-model",
        env_key="EXAMPLE_API_KEY",
    )

    export_codex_config(path, profile)
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    assert data["model_provider"] == "proxy"
    assert data["model_providers"]["proxy"]["env_key"] == "EXAMPLE_API_KEY"
    assert "experimental_bearer_token" not in data["model_providers"]["proxy"]


def test_exports_secret_only_when_requested(tmp_path):
    path = tmp_path / "export.toml"
    profile = ProviderProfile(
        id="proxy",
        name="Example Proxy",
        base_url="https://proxy.example.com/v1",
        model="example-model",
    )

    export_codex_config(path, profile, api_key="secret-value")
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    assert data["model_providers"]["proxy"]["experimental_bearer_token"] == "secret-value"


def test_rejects_chat_completions_export(tmp_path):
    profile = ProviderProfile(
        id="proxy",
        name="Example Proxy",
        base_url="https://proxy.example.com/v1",
        model="example-model",
        api_mode="chat_completions",
    )

    with pytest.raises(CodexConfigError, match="Responses"):
        export_codex_config(tmp_path / "export.toml", profile)
