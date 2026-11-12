"""Read declarative OpenCode provider settings without running plugins/commands."""
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from linguafloat.cc_switch import Candidate, CCSwitchError, _key_value, _text
from linguafloat.config import ProviderProfile

PACKAGES = {
    "@ai-sdk/openai-compatible": "chat_completions", "@ai-sdk/deepseek": "chat_completions",
    "@ai-sdk/openai": "responses", "@ai-sdk/anthropic": "anthropic", "@ai-sdk/google": "gemini",
    "@opencode/ai/providers/openai/responses": "responses", "@opencode/ai/providers/openai/chat": "chat_completions",
    "@opencode/ai/providers/anthropic": "anthropic", "@opencode/ai/providers/google": "gemini",
    "@opencode/ai/providers/deepseek": "chat_completions",
}
# Only documented built-in provider IDs, never inferred from a model name.
BUILTINS = {
    "openai": ("responses", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "google": ("gemini", "https://generativelanguage.googleapis.com/v1beta", "GOOGLE_GENERATIVE_AI_API_KEY"),
    "deepseek": ("chat_completions", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
}


def load_jsonc(path):
    if path.stat().st_size > 8 * 1024 * 1024:
        raise CCSwitchError("OpenCode 配置过大，未读取。")
    text = path.read_text(encoding="utf-8-sig")
    # Tokenize strings first so URLs, escaped quotes and comment-like key data survive.
    pattern = r'"(?:[^"\\]|\\.)*"|//[^\r\n]*|/\*[\s\S]*?\*/'
    clean = re.sub(pattern, lambda m: m[0] if m[0].startswith('"') else " ", text)
    clean = re.sub(r'"(?:[^"\\]|\\.)*"|,\s*(?=[}\]])', lambda m: m[0] if m[0].startswith('"') else "", clean)
    try:
        value = json.loads(clean)
    except ValueError:
        raise CCSwitchError("OpenCode JSON/JSONC 无法解析；未回显文件正文。") from None
    if not isinstance(value, dict):
        raise CCSwitchError("OpenCode 配置必须是 JSON 对象。")
    return value


def discover_opencode(home=None):
    home = home or Path.home()
    root = Path(os.environ.get("OPENCODE_CONFIG_DIR") or (Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "opencode"))
    paths = [root / "opencode.json", root / "opencode.jsonc"]
    if os.environ.get("OPENCODE_CONFIG"):
        paths.append(Path(os.environ["OPENCODE_CONFIG"]).expanduser())
    return list(dict.fromkeys(p.resolve() for p in paths if p.is_file()))


def auth_path():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "opencode/auth.json"


def _resolve(value):
    value = _text(value)
    match = re.fullmatch(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", value)
    if match:
        return _key_value(os.environ.get(match[1])), match[1]
    # File references are deliberately not followed to unrelated private paths.
    if value.startswith("{file:"):
        return "", ""
    return _key_value(value), ""


def parse_provider(provider_id, definition, auth=None, default_model="", source="opencode", name=""):
    if not isinstance(definition, dict):
        raise CCSwitchError("OpenCode 供应商字段不完整。")
    auth = auth or {}
    options = definition.get("options", definition.get("settings", {})) or {}
    if not isinstance(options, dict):
        raise CCSwitchError("供应商 options/settings 字段无效。")
    models = dict(definition.get("models") or {})
    if default_model and default_model not in models:
        models[default_model] = {}
    if not models:
        models = {"": {}}
    if len(models) > 2000:
        raise CCSwitchError("模型列表过大。")
    result = []
    for model_key, model in models.items():
        if not isinstance(model, dict):
            model = {}
        model_provider = model.get("provider") or {}
        npm = _text(model.get("package")) or _text(model_provider.get("npm")) or _text(definition.get("npm", definition.get("package")))
        builtin = BUILTINS.get(provider_id)
        mode = PACKAGES.get(npm, "") if npm else (builtin[0] if builtin else "")
        merged = {**options, **(model.get("options", model.get("settings", {})) or {})}
        address = _text(model_provider.get("api")) or _text(merged.get("baseURL")) or _text(definition.get("api"))
        if not address and builtin and not npm:
            address = builtin[1]
        if address.startswith("{env:"):
            address = _resolve(address)[0]
        identity = hashlib.sha256(f"{source}:{provider_id}:{model_key}".encode()).hexdigest()[:20]
        label = _text(name) or _text(definition.get("name")) or provider_id
        p = ProviderProfile("oc-" + identity, label + (" / " + model_key if model_key else ""), address,
                            _text(model.get("modelID", model.get("id"))) or model_key, api_mode=mode, env_key="")
        c = Candidate(source, provider_id, p)
        c.group = provider_id
        if not mode:
            c.blocked = "来源未明确支持的协议包；不能按模型名猜测接口。"
        if (definition.get("headers") or model.get("headers") or merged.get("headers")
                or definition.get("body") or model.get("body")):
            c.blocked = "存在额外请求头/请求体，无法完整安全映射，请手动配置。"
        if definition.get("compatibility") or model.get("compatibility"):
            c.blocked = "包含协议兼容性覆盖；当前需人工确认实际协议。"
        if model.get("disabled"):
            c.blocked = "来源模型已禁用。"
        key, env_name = _resolve(merged.get("apiKey"))
        raw_key = _text(merged.get("apiKey"))
        if not raw_key and auth:
            if auth.get("type") == "api":
                key = _key_value(auth.get("key"))
            else:
                c.blocked = "来源使用 OAuth/订阅或动态凭据，不能作为 API Key 导入。"
        if not raw_key and not key and not auth:
            envs = definition.get("env") or ([builtin[2]] if builtin and not npm else [])
            if isinstance(envs, list) and len(envs) == 1 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(envs[0])):
                env_name = envs[0]
                key = _key_value(os.environ.get(env_name))
        p.env_key = env_name
        c.secret = key if not c.blocked else ""
        parsed = urlsplit(address)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            p.base_url = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
            c.blocked = "接口地址含敏感参数，未导入；需独立配置认证。"
            c.secret = ""
        if parsed.hostname == "opencode.ai" and parsed.path.startswith("/inference/"):
            p.native_auth = "bearer"
        for secret in (raw_key, key):
            if secret:
                p.name, p.base_url, p.model = (v.replace(secret, "[已隐藏]") for v in (p.name, p.base_url, p.model))
        c.note = "仅使用声明式连接字段；不执行插件、命令或文件引用。未测试连接。"
        result.append(c)
    return result


def read_opencode(path, auth_file=None):
    try:
        config = load_jsonc(path)
        legacy, native = config.get("provider", {}), config.get("providers", {})
        if not isinstance(legacy, dict) or not isinstance(native, dict) or not (legacy or native):
            raise CCSwitchError("未发现声明式 provider/providers 配置；登录凭据或插件不等同于连接配置。")
        # Official normalization prefers the complete native provider entry on conflict.
        providers = {**legacy, **native}
        auth_file = auth_file or auth_path()
        auth = load_jsonc(auth_file) if auth_file.is_file() else {}
        default = config.get("model", "")
        if isinstance(default, dict):
            selected_provider, selected_model = default.get("providerID", ""), default.get("modelID", "")
        else:
            selected_provider, _, selected_model = str(default).partition("/")
        result = []
        for pid, definition in providers.items():
            try:
                entries = parse_provider(pid, definition, auth.get(pid), selected_model if pid == selected_provider else "")
                if pid in config.get("disabled_providers", []) or (config.get("enabled_providers") and pid not in config["enabled_providers"]):
                    for c in entries:
                        c.blocked = "来源供应商未启用。"
                result.extend(entries)
            except (ValueError, TypeError, AttributeError):
                p = ProviderProfile("oc-invalid-" + hashlib.sha256(pid.encode()).hexdigest()[:12], pid, "", "", env_key="")
                result.append(Candidate("opencode", pid, p, blocked="供应商字段异常；其他条目可继续导入。", group=pid))
        return result
    except CCSwitchError:
        raise
    except (OSError, ValueError, TypeError):
        raise CCSwitchError("OpenCode 文件无法读取或结构不支持。") from None
