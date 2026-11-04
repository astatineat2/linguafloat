from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from linguafloat.config import ProviderProfile, default_provider


class CodexConfigError(RuntimeError):
    pass


@dataclass(slots=True)
class CodexImport:
    profiles: list[ProviderProfile]
    active_provider: str
    secrets: dict[str, str]


def default_codex_config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    base = Path(codex_home) if codex_home else Path.home() / ".codex"
    return base / "config.toml"


def import_codex_config(path: Path) -> CodexImport:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CodexConfigError("没有找到所选 Codex 配置文件。") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CodexConfigError(f"无法读取 Codex 配置：{exc}") from exc

    model = _nonempty_string(data.get("model")) or "gpt-5-mini"
    active_id = _nonempty_string(data.get("model_provider")) or "openai"
    provider_tables = data.get("model_providers", {})
    if not isinstance(provider_tables, dict):
        provider_tables = {}

    profiles: list[ProviderProfile] = []
    secrets: dict[str, str] = {}
    if active_id == "openai" or not provider_tables:
        profile = default_provider()
        profile.model = model
        profiles.append(profile)

    for provider_id, raw in provider_tables.items():
        if not isinstance(raw, dict):
            continue
        clean_id = str(provider_id).strip()
        if not clean_id:
            continue
        name = _nonempty_string(raw.get("name")) or clean_id
        base_url = _nonempty_string(raw.get("base_url")) or ""
        env_key = _nonempty_string(raw.get("env_key")) or ""
        profile = ProviderProfile(
            id=clean_id,
            name=name,
            base_url=base_url,
            model=model,
            api_mode="responses",
            env_key=env_key,
        )
        profiles.append(profile)
        token = _nonempty_string(raw.get("experimental_bearer_token"))
        if token:
            secrets[clean_id] = token

    if not profiles:
        raise CodexConfigError("配置中没有可导入的模型供应商。")
    if not any(item.id == active_id for item in profiles):
        active_id = profiles[0].id
    return CodexImport(profiles=profiles, active_provider=active_id, secrets=secrets)


def export_codex_config(
    path: Path,
    profile: ProviderProfile,
    *,
    api_key: str = "",
) -> str:
    if profile.api_mode != "responses":
        raise CodexConfigError("Codex 目前只支持 Responses API，不能导出此接口类型。")
    try:
        if path.resolve() == default_codex_config_path().resolve():
            raise CodexConfigError(
                "为避免覆盖 Codex 的其他设置，请导出到一个新的 TOML 文件。"
            )
    except OSError:
        pass

    provider_id = _codex_provider_id(profile, force_custom=bool(api_key))
    output: dict = {"model": profile.model, "model_provider": provider_id}
    if provider_id != "openai":
        provider: dict = {
            "name": profile.name,
            "base_url": profile.base_url,
            "wire_api": "responses",
        }
        if api_key:
            provider["experimental_bearer_token"] = api_key
        elif profile.env_key:
            provider["env_key"] = profile.env_key
        else:
            provider["env_key"] = _default_env_key(provider_id)
        output["model_providers"] = {provider_id: provider}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("wb") as handle:
            tomli_w.dump(output, handle)
        os.replace(temporary, path)
    except OSError as exc:
        raise CodexConfigError(f"无法写入 Codex 配置：{exc}") from exc
    return provider_id


def _codex_provider_id(profile: ProviderProfile, *, force_custom: bool = False) -> str:
    official = profile.base_url.rstrip("/") == "https://api.openai.com/v1"
    if profile.id == "openai" and official and not force_custom:
        return "openai"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", profile.id).strip("_")
    if not cleaned or cleaned in {"openai", "ollama", "lmstudio"}:
        cleaned = f"linguafloat_{cleaned or 'provider'}"
    return cleaned


def _default_env_key(provider_id: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", "_", provider_id.upper()).strip("_")
    return f"LINGUAFLOAT_{cleaned}_API_KEY"


def _nonempty_string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
