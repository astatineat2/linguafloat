"""Read-only CC Switch provider import. No network calls or source mutations."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from linguafloat.config import ProviderProfile, validate_provider


class CCSwitchError(ValueError):
    """Messages must never contain source document fragments or credentials."""


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _key_value(value):
    value = _text(value)
    if (not value or value.startswith(("${", "$", "{env:", "{file:", "%"))
            or value.lower() in {"your_api_key", "your-api-key", "your_api_key_here", "<api_key>", "sk-xxx", "sk-..."}
            or set(value) <= {"*", "●"}):
        return ""
    return value


def resolve_key_reference(value):
    value = _text(value)
    for pattern in (r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", r"\$([A-Za-z_][A-Za-z0-9_]*)",
                    r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", r"%([A-Za-z_][A-Za-z0-9_]*)%"):
        match = re.fullmatch(pattern, value)
        if match:
            return _key_value(os.environ.get(match[1])), match[1]
    return _key_value(value), ""


@dataclass
class Candidate:
    source_app: str
    source_id: str
    profile: ProviderProfile
    secret: str = field(default="", repr=False)
    blocked: str = ""
    note: str = ""
    group: str = ""

    @property
    def auth_status(self):
        if self.blocked:
            return "不可直接使用；认证不导入"
        if self.profile.env_key:
            return "环境变量：" + self.profile.env_key + ("（已解析）" if self.secret else "（未解析，需补全）")
        return "API Key 已读取（隐藏）" if self.secret else "未发现 API Key；请先补全后导入"


@dataclass
class ImportChoice:
    candidate: Candidate
    action: str = "skip"
    target_id: str = ""
    keep_env: bool = True


def discover_sources(home: Path | None = None, appdata: Path | None = None) -> list[Path]:
    """Official default plus Tauri app_paths override; never scan unrelated files."""
    home = home or Path.home()
    appdata = appdata or Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
    roots = []
    store = appdata / "com.ccswitch.desktop" / "app_paths.json"
    if store.is_file():
        try:
            value = json.loads(store.read_text(encoding="utf-8-sig")).get("app_config_dir_override")
            if isinstance(value, str) and value.strip():
                root = Path(value.strip()).expanduser()
                if root.is_absolute() and root.is_dir():
                    roots.append(root)
        except (OSError, ValueError, AttributeError):
            pass
    roots.append(home / ".cc-switch")
    # Official Windows fallback for older installations using HOME.
    legacy = os.environ.get("HOME", "")
    if legacy and home == Path.home():
        roots.append(Path(legacy) / ".cc-switch")
    found = []
    for root in roots:
        db = root / "cc-switch.db"
        path = db if db.is_file() else root / "config.json"
        if path.is_file() and path.resolve() not in found:
            found.append(path.resolve())
    return found


def _rows(path: Path):
    if path.suffix.lower() == ".sql":
        from linguafloat.cc_sql import sql_rows
        return sql_rows(path)
    if path.suffix.lower() == ".json":
        if path.stat().st_size > 20 * 1024 * 1024:
            raise CCSwitchError("配置文件过大；请选择供应商数据库。")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        apps = data.get("apps") if isinstance(data, dict) else None
        if not isinstance(apps, dict):
            raise CCSwitchError("不支持此 JSON；需要 CC Switch MultiAppConfig（apps/providers）格式。")
        rows = []
        for app, manager in apps.items():
            if not isinstance(manager, dict) or not isinstance(manager.get("providers"), dict):
                raise CCSwitchError("供应商列表结构不完整。")
            for provider_id, provider in manager["providers"].items():
                if not isinstance(provider, dict):
                    raise CCSwitchError("供应商条目格式不正确。")
                rows.append((provider_id, app, provider.get("name", ""),
                             provider.get("settingsConfig"), provider.get("meta", {}), ""))
        return rows
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise CCSwitchError("不是受支持的 SQLite 数据库；请选择官方数据库、JSON 配置或带 .sql 扩展名的导出文件。")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(providers)")}
        if not {"id", "app_type", "name", "settings_config"} <= columns:
            raise CCSwitchError("数据库供应商结构不受支持；未修改来源。")
        meta = "meta" if "meta" in columns else "'{}'"
        kind = "provider_type" if "provider_type" in columns else "''"
        records = connection.execute(
            f"SELECT id,app_type,name,settings_config,{meta},{kind} FROM providers ORDER BY app_type,id LIMIT 2001"
        ).fetchall()
        return records
    finally:
        connection.close()


def read_candidates(path: Path) -> list[Candidate]:
    try:
        rows = _rows(path)
        if len(rows) > 2000:
            raise CCSwitchError("供应商超过 2000 条，请选择精简的来源文件。")
        result = []
        for row in rows:
            if row[1] == "opencode":
                from linguafloat.opencode_import import parse_provider
                try:
                    settings = json.loads(row[3]) if isinstance(row[3], str) else row[3]
                    result.extend(parse_provider(str(row[0]), settings, source="cc-opencode", name=row[2]))
                except (ValueError, TypeError, AttributeError):
                    result.append(_parse(*row))
            else:
                candidate = _parse(*row)
                if candidate.source_app == "openclaw" and len(candidate.profile.models) > 1 and not candidate.blocked:
                    for model in candidate.profile.models:
                        variant = copy.deepcopy(candidate)
                        variant.profile.model = model
                        variant.profile.id += "-" + hashlib.sha256(model.encode()).hexdigest()[:8]
                        variant.profile.name += " / " + model
                        result.append(variant)
                else:
                    result.append(candidate)
        return result
    except CCSwitchError:
        raise
    except sqlite3.OperationalError:
        raise CCSwitchError("数据库正在被独占使用或无法读取；请稍后重试或选择 CC Switch 的一致性备份。") from None
    except (OSError, sqlite3.Error, ValueError, TypeError):
        raise CCSwitchError("无法读取来源文件，或配置格式已变化；未修改任何配置。") from None


def _parse(provider_id, app, name, settings, metadata, provider_type):
    identity = hashlib.sha256(f"{app}:{provider_id}".encode()).hexdigest()[:20]
    profile = ProviderProfile("cc-" + identity, _text(name) or "未命名配置", "", "", api_mode="", env_key="")
    item = Candidate(_text(app), str(provider_id), profile)
    try:
        settings = json.loads(settings) if isinstance(settings, str) else settings
        meta = json.loads(metadata) if isinstance(metadata, str) else metadata
        if not isinstance(settings, dict) or not isinstance(meta, dict):
            raise ValueError()
        kind = _text(provider_type) or _text(meta.get("providerType"))
        binding = meta.get("authBinding") or {}
        if not isinstance(binding, dict):
            raise ValueError()
        if ("oauth" in kind.lower() or kind == "github_copilot" or meta.get("requiresOAuth")
                or binding.get("source") == "managed_account" or meta.get("githubAccountId")):
            item.blocked = "网页登录/OAuth/订阅凭据不能作为普通 API Key 导入。"
            return item
        if app != "codex":
            return _parse_other(item, settings, meta)
        raw = settings.get("config", "")
        config = tomllib.loads(raw) if isinstance(raw, str) else None
        if not isinstance(config, dict):
            raise ValueError()
        selected = config
        if config.get("profile"):
            override = config.get("profiles", {}).get(config["profile"])
            if not isinstance(override, dict):
                item.blocked = "所选 Codex profile 不存在，无法确定有效配置。"
                return item
            selected = {**config, **override}
        provider = config.get("model_providers", {}).get(selected.get("model_provider"), {})
        if not isinstance(provider, dict):
            raise ValueError()
        auth = settings.get("auth") or {}
        if not isinstance(auth, dict):
            raise ValueError()
        literal = _text(provider.get("experimental_bearer_token")) or _text(auth.get("OPENAI_API_KEY"))
        if auth.get("auth_mode") in {"chatgpt", "chatgptAuthTokens"} or (auth.get("tokens") and not literal):
            item.blocked = "此配置使用 ChatGPT 登录；请另提供可用于翻译 API 的密钥。"
            return item
        if provider.get("http_headers") or provider.get("env_http_headers") or provider.get("query_params"):
            item.blocked = "包含额外请求头或查询参数，当前无法安全完整映射；请手动配置。"
            return item
        profile.model = _text(selected.get("model"))
        wire = _text(provider.get("wire_api"))
        profile.api_mode = {"responses": "responses", "chat": "chat_completions"}.get(wire, "")
        if wire and not profile.api_mode:
            item.blocked = "来源明确指定了暂不支持的 wire_api 协议。"
        address = _text(provider.get("base_url"))
        parsed = urlsplit(address)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            profile.base_url = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
            item.blocked = "来源地址含凭据或查询参数，已隐藏；请在 CC Switch 中改用独立 API Key。"
        else:
            profile.base_url = address
        env_name = _text(provider.get("env_key"))
        # env_key is an official Codex field, not an expression to evaluate.
        if env_name:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
                item.blocked = "环境变量名称不合法。"
            else:
                profile.env_key = env_name
                item.secret = _key_value(os.environ.get(env_name))
        elif literal:
            if not _key_value(literal):
                item.note = "密钥为占位符或未确认的引用；未导入，请在导入后补全。"
            else:
                item.secret = literal
        # No request/retry/session values are copied; their semantics differ.
        item.note = (item.note + " 仅导入地址、模型、协议及 API Key/环境引用；未测试连接。未迁移超时、重试、代理或会话。").strip()
        for value in (literal, item.secret):
            if value:
                for field_name in ("name", "model", "base_url"):
                    setattr(profile, field_name, getattr(profile, field_name).replace(value, "[已隐藏]"))
    except (ValueError, TypeError, AttributeError, KeyError):
        item.blocked = "供应商字段或 TOML 无法解析；详情未回显，以保护密钥。"
        item.secret = ""
    return item


def _parse_other(item, settings, meta):
    """Map explicit source protocols, never infer them from a model name."""
    p = item.profile
    env = settings.get("env") or {}
    if not isinstance(env, dict):
        raise ValueError()
    if item.source_app in {"claude", "claude-desktop"}:
        protocol = meta.get("apiFormat", "anthropic")
        p.api_mode = {"anthropic": "anthropic", "openai_chat": "chat_completions", "openai_responses": "responses"}.get(protocol, "")
        p.base_url = _text(env.get("ANTHROPIC_BASE_URL")) or "https://api.anthropic.com/v1"
        p.model = _text(env.get("ANTHROPIC_MODEL"))
        token, key = _text(env.get("ANTHROPIC_AUTH_TOKEN")), _text(env.get("ANTHROPIC_API_KEY"))
        # Official Claude env has a token field (Bearer) distinct from API-key headers.
        literal = token or key
        p.native_auth = "bearer" if token or ("ANTHROPIC_AUTH_TOKEN" in env and not key) else "api_key"
        if meta.get("authBinding") or env.get("CLAUDE_CODE_USE_BEDROCK") or env.get("CLAUDE_CODE_USE_VERTEX"):
            item.blocked = "托管账号、Bedrock 或 Vertex 认证不能按普通 Messages API 导入。"
    elif item.source_app == "gemini":
        p.api_mode = "gemini"
        p.base_url = _text(env.get("GOOGLE_GEMINI_BASE_URL")) or "https://generativelanguage.googleapis.com/v1beta"
        p.model = _text(env.get("GEMINI_MODEL"))
        literal = _text(env.get("GEMINI_API_KEY")) or _text(env.get("GOOGLE_API_KEY"))
        if env.get("GOOGLE_GENAI_USE_VERTEXAI") or env.get("GOOGLE_APPLICATION_CREDENTIALS"):
            item.blocked = "Vertex/服务账号认证暂不支持；不会替换成普通 API Key。"
    elif item.source_app == "openclaw":
        p.api_mode = {"openai-responses": "responses", "openai-completions": "chat_completions",
                      "anthropic-messages": "anthropic", "google-generative-ai": "gemini"}.get(settings.get("api"), "")
        p.base_url = _text(settings.get("baseUrl"))
        models = settings.get("models", [])
        ids = [_text(m.get("id")) for m in models if isinstance(m, dict) and _text(m.get("id"))]
        p.models = ids
        p.model = ids[0] if len(ids) == 1 else ""
        literal = _text(settings.get("apiKey"))
        if settings.get("headers") or settings.get("auth") not in {None, "api-key"}:
            item.blocked = "额外请求头或非 API Key 认证暂不支持。"
        if len(ids) > 1:
            item.note = "来源包含多个模型，需选择使用模型。"
    else:
        item.blocked = "暂不支持此来源协议。"
        return item
    if not p.api_mode:
        item.blocked = "来源协议未能可靠映射。"
    parsed = urlsplit(p.base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        p.base_url = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
        item.blocked = "接口地址含敏感参数，已隐藏；需单独配置认证。"
    if not item.blocked:
        item.secret, p.env_key = resolve_key_reference(literal)
    for secret in (literal, item.secret):
        if secret:
            p.name, p.model, p.base_url = (value.replace(secret, "[已隐藏]") for value in (p.name, p.model, p.base_url))
    item.note += " 连接字段已按来源协议映射；未测试连接，不迁移脚本、代理或会话。"
    return item


def duplicate_targets(candidate, existing):
    from linguafloat.api_compat import normalize_base_url
    def address(value):
        try:
            return normalize_base_url(value)
        except ValueError:
            return value.rstrip("/")
    p = candidate.profile
    return [old for old in existing if old.id == p.id or old.name.casefold() == p.name.casefold()
            or (p.base_url and p.model and address(old.base_url) == address(p.base_url)
                and old.model == p.model and old.api_mode == p.api_mode)]


def commit_import(choices, settings, config_store, credentials):
    """Prepare everything first; compensate vault writes if saving fails.

    Existing profile objects and active selection are never mutated, so running
    tasks keep their snapshot. ConfigStore atomically replaces the config file.
    """
    proposed = copy.deepcopy(settings)
    profiles = {p.id: p for p in proposed.providers}
    writes = {}
    updated = set()
    report = []
    for choice in choices:
        item = choice.candidate
        if choice.action == "skip":
            report.append((item.profile.name, "跳过", item.blocked or "未选择导入"))
            continue
        if item.blocked or choice.action not in {"add", "update"}:
            raise CCSwitchError("选择中包含不支持的条目；未保存。")
        incoming = copy.deepcopy(item.profile)
        incoming.source_app = "opencode" if item.source_app == "cc-opencode" else item.source_app if item.source_app in {"codex", "claude", "claude-desktop", "gemini", "openclaw", "opencode"} else "manual"
        if incoming.env_key and not choice.keep_env and not item.secret:
            raise CCSwitchError("环境变量未解析，无法转存为密钥；请保留引用或先补全环境变量。")
        old = None
        if choice.action == "update":
            if choice.target_id not in profiles or choice.target_id in updated:
                raise CCSwitchError("更新目标不存在或被多条来源重复选中；请每次只更新一次。")
            updated.add(choice.target_id)
            old = profiles[choice.target_id]
            profile = copy.deepcopy(old)
            for name in ("name", "base_url", "model", "api_mode", "native_auth", "source_app"):
                setattr(profile, name, getattr(incoming, name))
        else:
            profile = incoming
            if profile.id in profiles:
                profile.id = "cc-" + uuid.uuid4().hex[:20]
            names = {p.name.casefold() for p in profiles.values()}
            name, index = profile.name, 2
            while profile.name.casefold() in names:
                profile.name = f"{name}（导入 {index}）"
                index += 1
        if incoming.env_key and choice.keep_env:
            # An unresolved source must not replace an existing usable credential.
            if item.secret or old is None:
                profile.env_key = incoming.env_key
        elif item.secret:
            profile.env_key = ""
            writes[profile.id] = item.secret
        elif old is None:
            profile.env_key = ""
        try:
            validate_provider(profile)
        except ValueError:
            raise CCSwitchError("选中条目的名称、地址、模型或接口不完整/无效，请在预览中补全。") from None
        profiles[profile.id] = profile
        report.append((profile.name, "成功", "已保存，未启用；连接尚未测试"))
    if not any(result == "成功" for _, result, _ in report):
        return report
    proposed.providers = list(profiles.values())
    previous = {}
    attempted = []
    try:
        # Read all previous secrets before making the first write.
        previous = {pid: credentials.get_api_key(pid, "") for pid in writes}
        for pid, key in writes.items():
            attempted.append(pid)
            credentials.save_api_key(pid, key)
        config_store.save(proposed)
    except Exception:
        failed_restore = False
        for pid in reversed(attempted):
            try:
                if previous[pid]:
                    credentials.save_api_key(pid, previous[pid])
                else:
                    credentials.delete_api_key(pid)
            except Exception:
                failed_restore = True
        if failed_restore:
            raise CCSwitchError("导入失败，配置未提交；部分凭据回退失败，请在配置页检查受影响条目的密钥后重试。") from None
        raise CCSwitchError("导入失败，未提交配置；已恢复本次涉及的凭据。请检查凭据管理器及配置目录写入权限。") from None
    settings.providers = proposed.providers
    return report


def import_category(candidates, settings, config_store, credentials):
    """Independent per-entry transactions; no overwrites or automatic activation."""
    report = []
    for original in candidates:
        item = copy.deepcopy(original)
        p = item.profile
        if item.blocked:
            report.append((p.name, "不支持", item.blocked))
            continue
        try:
            validate_provider(p)
        except ValueError:
            report.append((p.name, "需要补全", "名称、地址、协议或模型缺失/无效；点击结果中的“补全”后导入"))
            continue
        if not item.secret:
            report.append((p.name, "需要补全", "缺少有效 API Key 或环境变量尚未解析；请补全认证后重试"))
            continue
        try:
            duplicate = False
            for old in settings.providers:
                other = copy.deepcopy(old)
                try:
                    validate_provider(other)
                except ValueError:
                    continue
                fields = ("base_url", "model", "api_mode", "native_auth", "env_key")
                if all(getattr(p, name) == getattr(other, name) for name in fields):
                    if credentials.get_api_key(old.id, old.env_key) == item.secret:
                        duplicate = True
                        break
            if duplicate:
                report.append((p.name, "重复跳过", "连接字段和认证相同；未新增副本"))
                continue
            if any(old.name.casefold() == p.name.casefold() for old in settings.providers):
                p.name += "（" + ("OpenCode" if item.source_app == "opencode" else "CC Switch") + "）"
            report.extend(commit_import([ImportChoice(item, "add")], settings, config_store, credentials))
        except Exception as exc:
            reason = str(exc) if isinstance(exc, CCSwitchError) else "凭据读取或保存失败；此项未完成，其他项继续"
            report.append((p.name, "失败", reason))
    return report
