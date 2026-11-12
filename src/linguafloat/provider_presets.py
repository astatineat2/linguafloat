"""In-memory, credential-free projections of existing provider configuration."""
from dataclasses import dataclass
from urllib.parse import urlsplit

from linguafloat.config import clean_models
from linguafloat.api_compat import normalize_base_url

# Public Go eligibility and exact model IDs checked in the official Go and
# console/models documentation on 2026-11-12. Account access is not guaranteed.
GO_MODELS = ("deepseek-v4.1-flash", "deepseek-v4-pro", "glm-5.3-flash", "glm-5.3", "glm-5.2")
# Public host confirmed by the user; configured model IDs are suggestions,
# not an entitlement check. Never include group-specific routes or credentials.
NOW_CODING_MODELS = ("gpt-6.1-sol", "gpt-6-sol", "gpt-6-astra")


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    name: str
    base_url: str
    api_mode: str
    model: str
    models: tuple[str, ...] = ()
    native_auth: str = "api_key"


def _name_key(value):
    return "".join(c for c in value.casefold() if c.isalnum())


def _safe_address(value):
    try:
        url = urlsplit(value)
        return (url.scheme in {"http", "https"} and bool(url.hostname)
                and not any((url.username, url.password, url.query, url.fragment)))
    except ValueError:
        return False


def _is_go(address):
    try:
        url = urlsplit(normalize_base_url(address))
        return (url.scheme == "https" and url.hostname == "opencode.ai"
                and url.port in {None, 443}
                and url.path in {"/zen/go/v1", "/inference/go/openai/v1"})
    except ValueError:
        return False


def presets_for(kind, profiles=(), *, confirmed_names=()):
    """Select local presets without opening files, resolving keys or probing APIs.

    Names only identify local candidates, not a vendor's public identity. A caller
    can pass additional names after the user has confirmed the configuration.
    """
    if kind not in {"go", "no_coding", "custom"}:
        raise ValueError("未知供应商类型。")
    if kind == "custom":
        return []
    if kind == "no_coding":
        # Adding a public preset must not depend on saved personal relays or
        # group profiles. Existing providers remain editable in their own form.
        return [ProviderPreset("Now Coding", "https://nowcoding.ai/v1", "responses",
                               NOW_CODING_MODELS[0], NOW_CODING_MODELS)]
    names = {_name_key(name) for name in ("No Coding", "Now Coding", "Now Coding 全球加速", *confirmed_names)}
    presets = []
    for profile in profiles:
        # Explicit projection prevents credentials or unrelated settings entering
        # the preset object, including when the caller supplies a raw mapping.
        get = profile.get if isinstance(profile, dict) else lambda key, default="": getattr(profile, key, default)
        name, address, mode, model = (get(field, "") for field in ("name", "base_url", "api_mode", "model"))
        if not all(isinstance(value, str) for value in (name, address, mode, model)):
            continue
        if not _safe_address(address):
            continue
        if kind == "go":
            if not _is_go(address) or mode not in {"responses", "chat_completions"}:
                continue
        elif _name_key(name) not in names and not (urlsplit(address).hostname == "nowcoding.ai"
                                                  and urlsplit(address).path.rstrip("/") == "/v1"):
            continue
        if mode not in {"responses", "chat_completions", "anthropic", "gemini"}:
            continue
        # Do not reuse an unfiltered cached Go listing: it can mix billing plans.
        models = tuple(clean_models([model, *GO_MODELS] if kind == "go" else [model, *clean_models(get("models", []))]))
        auth = get("native_auth", "api_key")
        preset = ProviderPreset(name, address, mode, model, models,
                                auth if isinstance(auth, str) and auth in {"api_key", "bearer"} else "api_key")
        if preset not in presets:
            presets.append(preset)
    if not presets and kind == "go":
        # This public endpoint already exists in the OpenCode service adapter.
        # Published choices do not guarantee entitlement for a particular account.
        presets.append(ProviderPreset("OpenCode Go", "https://opencode.ai/inference/go/openai/v1",
                                      "chat_completions", GO_MODELS[0], GO_MODELS))
    if not presets and kind == "no_coding":
        presets.append(ProviderPreset("Now Coding", "https://nowcoding.ai/v1", "responses",
                                      NOW_CODING_MODELS[0], NOW_CODING_MODELS))
    return presets
