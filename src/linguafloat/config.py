from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import keyring
from keyring.errors import KeyringError, PasswordDeleteError


APP_NAME = "LinguaFloat"
KEYRING_SERVICE = "LinguaFloat OpenAI API"
LEGACY_KEYRING_USERNAME = "default"
TARGET_LANGUAGES = {
    "en": ("英语", "English"), "ko": ("韩语", "Korean"), "ja": ("日语", "Japanese"),
    "zh-Hans": ("简体中文", "Simplified Chinese"), "fr": ("法语", "French"),
    "de": ("德语", "German"), "es": ("西班牙语", "Spanish"),
}


@dataclass(slots=True)
class ProviderProfile:
    id: str
    name: str
    base_url: str
    model: str
    api_mode: str = "responses"
    env_key: str = "OPENAI_API_KEY"
    favorite: bool = False
    models: list[str] = field(default_factory=list)
    favorite_models: list[str] = field(default_factory=list)
    timeout: float = 30.0
    retries: int = 2
    document_concurrency: int = 2
    document_rpm: int = 60
    document_tpm: int = 0
    output_token_limit: int = 0
    chat_token_parameter: str = "max_completion_tokens"
    instruction_role: str = "system"
    reasoning_effort: str = ""
    thinking_mode: str = ""
    document_timeout: int = 180
    native_auth: str = "api_key"
    source_app: str = "manual"

    @property
    def cache_scope(self) -> str:
        from linguafloat.api_compat import request_options
        base = "|".join((self.id, self.base_url, self.api_mode, self.model))
        options = request_options(self)
        return base + ("|options:" + json.dumps(options, sort_keys=True) if options else "")


def default_provider() -> ProviderProfile:
    return ProviderProfile(
        id="openai",
        name="OpenAI 官方",
        base_url="https://api.openai.com/v1",
        model="gpt-5-mini",
        api_mode="responses",
        env_key="OPENAI_API_KEY",
    )


def default_config_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return base / APP_NAME / "config.json"


@dataclass(slots=True)
class AppSettings:
    update_auto_check: bool = True
    first_run_complete: bool = False
    document_timeout: int = 180
    document_output_dir: str = ""
    document_prices: dict = field(default_factory=dict)
    document_translate_tables: bool = False
    document_translate_figures: bool = False
    hotkey: str = "Ctrl+Alt+T"
    trigger_mode: str = "both"
    font_size: int = 14
    ocr_interval: float = 1.0
    translation_interval: float = 3.0
    input_hotkey: str = "Ctrl+Alt+Y"
    input_language: str = "en"
    game_mode: str = "off"
    game_paths: list[str] = field(default_factory=list)
    active_provider: str = "openai"
    providers: list[ProviderProfile] = field(default_factory=lambda: [default_provider()])
    document_translate_graphics: bool = False
    document_skip_references: bool = True
    document_source_language: str = "en"
    document_target_language: str = "zh-Hans"
    document_pdf_mode: str = "layout"
    document_mono: bool = True
    document_bilingual: bool = True
    document_bilingual_layout: str = "side_by_side"
    document_column_widths: dict = field(default_factory=dict)

    def active_profile(self) -> ProviderProfile:
        for profile in self.providers:
            if profile.id == self.active_provider:
                return profile
        self.active_provider = self.providers[0].id
        return self.providers[0]


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_path()

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return AppSettings()
        if not isinstance(raw, dict):
            return AppSettings()
        from linguafloat.hotkeys import HotkeyError, parse_hotkey

        defaults = AppSettings()
        from linguafloat.game_mode import GAME_MODES, normalize_game_paths
        from linguafloat.document_core import clean_pricing
        prices = raw.get("document_prices", {})
        game_mode = raw.get("game_mode", "off")
        if not isinstance(game_mode, str) or game_mode not in GAME_MODES:
            game_mode = "off"
        hotkey = raw.get("hotkey", defaults.hotkey)
        trigger_mode = raw.get("trigger_mode", defaults.trigger_mode)
        if trigger_mode not in {"both", "hotkey", "right_click"}:
            trigger_mode = defaults.trigger_mode
        providers = self._load_providers(raw)
        from linguafloat.document_core import LANGUAGES
        def option(name, choices, default):
            value=raw.get(name)
            return value if isinstance(value,str) and value in choices else default
        try:
            input_hotkey = parse_hotkey(str(raw.get("input_hotkey", defaults.input_hotkey))).display
        except HotkeyError:
            input_hotkey = defaults.input_hotkey
        input_language = raw.get("input_language", "en")
        if not isinstance(input_language, str) or input_language not in TARGET_LANGUAGES:
            input_language = "en"
        active_provider = raw.get("active_provider", providers[0].id)
        if not isinstance(active_provider, str) or not any(
            item.id == active_provider for item in providers
        ):
            active_provider = providers[0].id
        return AppSettings(
            update_auto_check=raw.get("update_auto_check", True) is True,
            # Existing users keep their settings and are not treated as a clean install.
            first_run_complete=raw.get("first_run_complete", True) is True,
            document_timeout=int(self._interval(raw.get("document_timeout"), 180, 30, 600)),
            document_output_dir=raw.get("document_output_dir", "").strip() if isinstance(raw.get("document_output_dir"), str) else "",
            document_prices={k: clean_pricing(v) for k, v in prices.items()} if isinstance(prices, dict) else {},
            document_translate_tables=raw.get("document_translate_tables") is True,
            document_translate_figures=raw.get("document_translate_figures") is True,
            hotkey=hotkey if isinstance(hotkey, str) and hotkey.strip() else defaults.hotkey,
            trigger_mode=trigger_mode,
            font_size=14,
            ocr_interval=self._interval(raw.get("ocr_interval"), 1.0, 0.5, 5.0),
            translation_interval=self._interval(raw.get("translation_interval"), 3.0, 2.0, 30.0),
            input_hotkey=input_hotkey,
            input_language=input_language,
            game_mode=game_mode,
            game_paths=normalize_game_paths(raw.get("game_paths", [])),
            active_provider=active_provider,
            providers=providers,
            document_translate_graphics=(raw.get("document_translate_graphics") is True if "document_translate_graphics" in raw
                else raw.get("document_translate_tables") is True or raw.get("document_translate_figures") is True),
            document_skip_references=True,
            document_source_language=option("document_source_language",LANGUAGES,"en"),
            document_target_language=option("document_target_language",LANGUAGES,"zh-Hans"),
            document_pdf_mode=option("document_pdf_mode",{"reading","layout"},"layout"),
            document_mono=raw.get("document_mono",True) is True,
            document_bilingual=raw.get("document_bilingual",True) is True,
            document_bilingual_layout=option("document_bilingual_layout",{"side_by_side","alternating"},"side_by_side"),
            document_column_widths=self._column_widths(raw.get("document_column_widths")),
        )

    @staticmethod
    def _column_widths(raw):
        if not isinstance(raw, dict):
            return {}
        return {name: widths for name, count in (("files", 4), ("history", 7))
                if isinstance(widths := raw.get(name), list) and len(widths) == count
                and all(type(width) is int and 56 <= width <= 4096 for width in widths)}

    @staticmethod
    def _interval(value: object, default: float, lower: float, upper: float) -> float:
        if type(value) in (int, float) and lower <= value <= upper:
            return float(value)
        return default

    @staticmethod
    def _load_providers(raw: dict) -> list[ProviderProfile]:
        providers: list[ProviderProfile] = []
        for item in raw.get("providers", []):
            if not isinstance(item, dict):
                continue
            try:
                profile = ProviderProfile(
                    id=str(item["id"]).strip(),
                    name=str(item["name"]).strip(),
                    base_url=str(item["base_url"]).strip(),
                    model=str(item["model"]).strip(),
                    api_mode=str(item.get("api_mode", "responses")).strip(),
                    env_key=str(item.get("env_key", "")).strip(),
                    favorite=item.get("favorite") is True,
                    models=clean_models(item.get("models")),
                    favorite_models=clean_models(item.get("favorite_models")),
                    timeout=ConfigStore._interval(item.get("timeout"), 30, 5, 120),
                    retries=item.get("retries") if type(item.get("retries")) is int and 0 <= item["retries"] <= 3 else 2,
                    document_concurrency=int(ConfigStore._interval(item.get("document_concurrency"), 2, 1, 6)),
                    document_rpm=int(ConfigStore._interval(item.get("document_rpm"), 60, 1, 10000)),
                    document_tpm=int(ConfigStore._interval(item.get("document_tpm"), 0, 0, 100000000)),
                    output_token_limit=int(ConfigStore._interval(item.get("output_token_limit"), 0, 0, 1000000)),
                    chat_token_parameter=item.get("chat_token_parameter") if item.get("chat_token_parameter") in {"max_tokens", "max_completion_tokens"} else "max_completion_tokens",
                    instruction_role=item.get("instruction_role") if item.get("instruction_role") in {"system", "developer", "user"} else "system",
                    reasoning_effort=item.get("reasoning_effort") if item.get("reasoning_effort") in {"none", "minimal", "low", "medium", "high", "xhigh"} else "",
                    thinking_mode=item.get("thinking_mode") if item.get("thinking_mode") in {"qwen_off", "qwen_on", "deepseek_off", "deepseek_on"} else "",
                    native_auth=item.get("native_auth") if item.get("native_auth") in {"api_key", "bearer"} else "api_key",
                    source_app=item.get("source_app") if item.get("source_app") in {"codex", "claude", "claude-desktop", "gemini", "opencode", "openclaw"} else "manual",
                    document_timeout=int(ConfigStore._interval(item.get("document_timeout"),
                        ConfigStore._interval(raw.get("document_timeout"),180,30,600),30,600)),
                )
            except KeyError:
                continue
            if (
                profile.id
                and profile.name
                and profile.model
                and profile.api_mode in {"responses", "chat_completions", "anthropic", "gemini"}
            ):
                providers.append(profile)

        if providers:
            return providers

        # Migrate the single-provider configuration written by version 0.1.
        profile = default_provider()
        profile.document_timeout=int(ConfigStore._interval(raw.get("document_timeout"),180,30,600))
        legacy_model = raw.get("model")
        if isinstance(legacy_model, str) and legacy_model.strip():
            profile.model = legacy_model.strip()
        return [profile]

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class CredentialError(RuntimeError):
    pass


def clean_models(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(x.strip() for x in value if isinstance(x, str) and 0 < len(x.strip()) <= 200))[:2000]


def create_provider_id(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")
    return f"{cleaned or 'provider'}-{uuid.uuid4().hex[:6]}"


def validate_provider(profile: ProviderProfile) -> None:
    from linguafloat.api_compat import normalize_base_url, validate_options
    profile.base_url = normalize_base_url(profile.base_url)
    validate_options(profile)
    if not profile.id:
        raise ValueError("供应商标识不能为空。")
    if not profile.name:
        raise ValueError("供应商显示名称不能为空。")
    if not profile.model:
        raise ValueError("模型名称不能为空。")
    if profile.api_mode not in {"responses", "chat_completions", "anthropic", "gemini"}:
        raise ValueError("接口类型无效。")
    if profile.native_auth not in {"api_key", "bearer"}:
        raise ValueError("认证请求头类型无效。")
    if profile.env_key and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", profile.env_key):
        raise ValueError("环境变量名只能包含字母、数字和下划线，且不能以数字开头。")

    parsed = urlparse(profile.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Base URL 必须是完整的 http:// 或 https:// 地址。")
    if parsed.username or parsed.password:
        raise ValueError("Base URL 不能包含用户名或密码。")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme == "http" and parsed.hostname not in local_hosts:
        raise ValueError("远程第三方接口必须使用 HTTPS；HTTP 仅允许本机地址。")


class CredentialStore:
    def get_api_key(self, provider_id: str, env_key: str = "") -> str:
        environment_key = os.environ.get(env_key, "").strip() if env_key else ""
        if environment_key:
            return environment_key

        try:
            value = keyring.get_password(KEYRING_SERVICE, provider_id)
            if not value and provider_id == "openai":
                value = keyring.get_password(KEYRING_SERVICE, LEGACY_KEYRING_USERNAME)
            return (value or "").strip()
        except KeyringError as exc:
            raise CredentialError("无法读取 Windows 凭据管理器。") from exc

    def source(self, provider_id: str, env_key: str = "") -> str:
        if env_key and os.environ.get(env_key, "").strip():
            return "environment"
        return "keyring" if self.get_api_key(provider_id, "") else "missing"

    def save_api_key(self, provider_id: str, api_key: str) -> None:
        api_key = api_key.strip()
        if not api_key:
            raise CredentialError("API key 不能为空。")
        try:
            keyring.set_password(KEYRING_SERVICE, provider_id, api_key)
        except KeyringError as exc:
            raise CredentialError("无法写入 Windows 凭据管理器。") from exc

    def delete_api_key(self, provider_id: str) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, provider_id)
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise CredentialError("无法从 Windows 凭据管理器删除 API key。") from exc
