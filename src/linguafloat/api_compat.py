"""Shared OpenAI-compatible transport; independent of Qt and document engines."""
from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from linguafloat import __version__

API_MODES = {"responses", "chat_completions", "anthropic", "gemini"}


def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def normalize_base_url(address: str) -> str:
    parsed = urlsplit(address.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("接口地址必须是完整的 HTTP/HTTPS 地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("接口地址不能包含用户名、密码、查询参数或片段。")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/models"):
        if path.endswith(suffix):
            path = path[:-len(suffix)].rstrip("/")
            break
    while path.endswith("/v1/v1"):
        path = path[:-3]
    if parsed.hostname == "api.openai.com" and not path:
        path = "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def opencode_go_headers(profile, *, session_scope=None):
    """Use our own identity only on the official Go OpenAI endpoint.

    One adapter represents one conversation. Document jobs supply a persistent
    job ID so concurrent requests, retries and restoration share that session.
    Session metadata does not change translation/cache compatibility.
    """
    try:
        parsed = urlsplit(normalize_base_url(field(profile, "base_url", "")))
        port = parsed.port
    except ValueError:
        return {}
    if (parsed.scheme != "https" or parsed.hostname != "opencode.ai"
            or port not in {None, 443}
            or parsed.path not in {"/inference/go/openai/v1", "/zen/go/v1"}):
        return {}
    session = (uuid.uuid5(uuid.NAMESPACE_URL, "linguafloat:opencode-go:" + session_scope)
               if session_scope else uuid.uuid4())
    return {"User-Agent": f"LinguaFloat/{__version__}", "x-opencode-session": str(session)}


OPTION_DEFAULTS = {"output_token_limit": 0, "chat_token_parameter": "max_completion_tokens",
                   "instruction_role": "system", "reasoning_effort": "", "thinking_mode": ""}


def request_options(profile):
    # Default options preserve the existing cache key for valid translations.
    return {k: field(profile, k, v) for k, v in OPTION_DEFAULTS.items() if field(profile, k, v) != v}


def validate_options(profile):
    limit = field(profile, "output_token_limit", 0)
    if type(limit) is not int or not 0 <= limit <= 1000000:
        raise ValueError("输出 Token 上限应为 0–1000000；0 表示使用供应商默认值。")
    if field(profile, "chat_token_parameter", "max_completion_tokens") not in {"max_tokens", "max_completion_tokens"}:
        raise ValueError("Chat 输出长度参数无效。")
    if field(profile, "instruction_role", "system") not in {"system", "developer", "user"}:
        raise ValueError("指令角色无效。")
    if field(profile, "reasoning_effort", "") not in {"", "none", "minimal", "low", "medium", "high", "xhigh"}:
        raise ValueError("推理强度无效。")
    if field(profile, "thinking_mode", "") not in {"", "qwen_off", "qwen_on", "deepseek_off", "deepseek_on"}:
        raise ValueError("供应商思考参数无效。")


class ApiResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApiResult:
    text: str
    raw: object
    request_id: str | None


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(field(part, "text", "") for part in content
                       if field(part, "type") in {"text", "output_text"} and isinstance(field(part, "text"), str))
    return ""


def token_usage(result, mode):
    if mode == "gemini":
        usage = field(result, "usageMetadata", {})
        incoming, outgoing = field(usage, "promptTokenCount"), field(usage, "candidatesTokenCount")
        if not all(type(v) is int and v >= 0 for v in (incoming, outgoing)):
            return None
        thoughts = field(usage, "thoughtsTokenCount", 0)
        outgoing += thoughts if type(thoughts) is int and thoughts >= 0 else 0
        total = field(usage, "totalTokenCount")
        return incoming, outgoing, total if type(total) is int and total >= 0 else incoming + outgoing
    usage = field(result, "usage")
    names = ("input_tokens", "output_tokens") if mode in {"responses", "anthropic"} else ("prompt_tokens", "completion_tokens")
    incoming, outgoing = (field(usage, name) for name in names)
    if not all(type(v) is int and v >= 0 for v in (incoming, outgoing)):
        return None
    total = field(usage, "total_tokens")
    return incoming, outgoing, total if type(total) is int and total >= 0 else incoming + outgoing


class ApiAdapter:
    def __init__(self, client, profile, *, session_scope=None):
        self.client, self.profile = client, copy.deepcopy(profile)
        validate_options(self.profile)
        self._headers = opencode_go_headers(self.profile, session_scope=session_scope)

    def validate_request(self):
        mode = field(self.profile, "api_mode")
        if mode not in API_MODES:
            raise ApiResponseError("不支持的 API 接口类型。")
        if field(self.profile, "thinking_mode", "") and mode != "chat_completions":
            raise ApiResponseError("供应商思考开关仅适用于 Chat Completions；当前 Responses 配置请使用默认或供应商明确支持的推理强度。")
        if mode in {"anthropic", "gemini"} and field(self.profile, "reasoning_effort", ""):
            raise ApiResponseError("当前原生接口未配置对应推理参数，请将推理强度设为默认。")

    def native_headers(self):
        from openai import Omit
        headers = {"Authorization": Omit()}
        if field(self.profile, "native_auth", "api_key") == "bearer":
            headers["Authorization"] = "Bearer " + self.client.api_key
        elif field(self.profile, "api_mode") == "anthropic":
            headers["x-api-key"] = self.client.api_key
        else:
            headers["x-goog-api-key"] = self.client.api_key
        if field(self.profile, "api_mode") == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        return headers

    def native_prefix(self):
        path = urlsplit(field(self.profile, "base_url")).path.rstrip("/")
        if field(self.profile, "api_mode") == "anthropic":
            return "" if path.endswith("/v1") else "v1/"
        return "" if path.endswith(("/v1beta", "/v1")) else "v1beta/"

    def _native_request(self, messages, on_response):
        mode = field(self.profile, "api_mode")
        system = "\n".join(m["content"] for m in messages if m["role"] in {"system", "developer"})
        turns = [m for m in messages if m["role"] not in {"system", "developer"}]
        limit = field(self.profile, "output_token_limit", 0)
        if mode == "anthropic":
            # Messages requires max_tokens, unlike OpenAI's optional length limit.
            body = {"model": field(self.profile, "model"), "max_tokens": limit or 4096, "messages": turns}
            if system:
                body["system"] = system
            endpoint = self.native_prefix() + "messages"
        else:
            body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user",
                                   "parts": [{"text": m["content"]}]} for m in turns]}
            if system:
                body["systemInstruction"] = {"parts": [{"text": system}]}
            if limit:
                body["generationConfig"] = {"maxOutputTokens": limit}
            model = field(self.profile, "model").removeprefix("models/")
            endpoint = self.native_prefix() + "models/" + quote(model, safe="") + ":generateContent"
        from openai._constants import RAW_RESPONSE_HEADER
        response = self.client.post(endpoint, cast_to=dict[str, Any], body=body,
            options={"headers": {**self.native_headers(), RAW_RESPONSE_HEADER: "true"}})
        raw = response.parse()
        if not isinstance(raw, dict):
            raise ApiResponseError("原生接口返回格式不正确。")
        raw["_request_id"] = response.headers.get("request-id") or response.headers.get("x-request-id") or response.headers.get("x-goog-request-id")
        if on_response:
            on_response(raw)
        if mode == "anthropic":
            if field(raw, "stop_reason") not in {"end_turn", "stop_sequence"}:
                raise ApiResponseError("Messages 输出未完整结束或返回工具调用，未保存译文。")
            text = text_content(field(raw, "content"))
        else:
            candidates = field(raw, "candidates", []) or []
            if not candidates or field(candidates[0], "finishReason") != "STOP":
                raise ApiResponseError("Gemini 输出为空、被过滤或未完整结束，未保存译文。")
            parts = field(field(candidates[0], "content"), "parts", []) or []
            if any(field(p, "functionCall") for p in parts):
                raise ApiResponseError("Gemini 返回工具调用，未保存译文。")
            text = "".join(field(p, "text", "") for p in parts if not field(p, "thought", False))
        if not isinstance(text, str) or not text.strip():
            raise ApiResponseError("模型没有返回翻译结果。")
        return ApiResult(text.strip(), raw, field(raw, "_request_id"))

    def request(self, messages, *, instructions=None, input_text=None, on_response=None):
        self.validate_request()
        profile = self.profile
        mode = field(profile, "api_mode")
        kwargs = {"model": field(profile, "model")}
        if self._headers:
            kwargs["extra_headers"] = dict(self._headers)
        messages = copy.deepcopy(messages)
        role = field(profile, "instruction_role", "system")
        for message in messages:
            if message.get("role") == "system":
                message["role"] = role
        limit = field(profile, "output_token_limit", 0)
        effort = field(profile, "reasoning_effort", "")
        thinking = field(profile, "thinking_mode", "")
        if mode in {"anthropic", "gemini"}:
            return self._native_request(messages, on_response)
        if mode == "responses":
            if instructions is not None and role == "system":
                kwargs.update(instructions=instructions, input=input_text)
            else:
                kwargs["input"] = messages
            if limit:
                kwargs["max_output_tokens"] = limit
            if effort:
                kwargs["reasoning"] = {"effort": effort}
            raw = self.client.responses.create(**kwargs)
            if on_response:
                on_response(raw)
            text = field(raw, "output_text", "")
            if not text:
                text = "".join(text_content(field(item, "content")) for item in field(raw, "output", []) or []
                               if field(item, "type") == "message")
            if field(raw, "status") in {"incomplete", "failed", "cancelled", "in_progress", "queued"}:
                reason = field(field(raw, "incomplete_details"), "reason")
                reason = reason if reason in {"max_output_tokens", "content_filter"} else "未完成或被过滤"
                raise ApiResponseError(f"模型输出被截断或未完成（{reason}），未保存不完整译文。")
        elif mode == "chat_completions":
            kwargs["messages"] = messages
            if limit:
                kwargs[field(profile, "chat_token_parameter", "max_completion_tokens")] = limit
            if effort:
                kwargs["reasoning_effort"] = effort
            if thinking.startswith("qwen_"):
                kwargs["extra_body"] = {"enable_thinking": thinking == "qwen_on"}
            elif thinking.startswith("deepseek_"):
                kwargs["extra_body"] = {"thinking": {"type": "enabled" if thinking == "deepseek_on" else "disabled"}}
            raw = self.client.chat.completions.create(**kwargs)
            if on_response:
                on_response(raw)
            choices = field(raw, "choices", [])
            if not choices:
                raise ApiResponseError("供应商未返回 choices，响应格式不符合 Chat Completions。")
            choice = choices[0]
            if field(choice, "finish_reason") in {"length", "content_filter", "tool_calls", "function_call"}:
                raise ApiResponseError("模型输出被截断、过滤或返回工具调用，未保存不完整译文。")
            message = field(choice, "message")
            if field(message, "refusal"):
                raise ApiResponseError("模型拒绝了本次翻译，未保存译文。")
            text = text_content(field(message, "content"))
        else:
            raise ApiResponseError("不支持的 API 接口类型。")
        if not isinstance(text, str) or not text.strip():
            raise ApiResponseError("模型没有返回翻译结果（空译文或仅返回推理/工具内容）。")
        return ApiResult(text.strip(), raw, field(raw, "_request_id"))


def safe_text(value, *, secrets=(), source_texts=(), limit=500):
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), "[已隐藏密钥]")
    for source in source_texts:
        if isinstance(source, str) and source:
            text = text.replace(source, "[已隐藏请求正文]")
            for line in source.splitlines():
                if len(line) > 12:
                    text = text.replace(line, "[已隐藏请求正文]")
    text = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [已隐藏]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[已隐藏密钥]", text)
    text = re.sub(r'''(?ix)(authorization|api[_-]?key|access[_-]?token|password|secret|input|messages|content)
                     [\s\"']*[:=]\s*(?:"(?:[^"\\]|\\.)*"|'[^']*'|[^\s,;]+)''',
                  r"\1=[已隐藏]", text)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return text[:limit] + ("…" if len(text) > limit else "")


def error_details(exc, profile=None, *, key="", source_texts=(), request_id=None):
    import openai
    code = getattr(exc, "status_code", None)
    details = {"error_type": type(exc).__name__}
    if profile is not None:
        details.update(api_mode=field(profile, "api_mode"), model=safe_text(field(profile, "model", ""), secrets=(key,)))
    if code is not None:
        details["http_status"] = code
    identity = getattr(exc, "request_id", None) or request_id
    if identity:
        details["supplier_request_id"] = safe_text(identity, secrets=(key,), source_texts=source_texts, limit=100)
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, Mapping) else None
    if isinstance(error, Mapping):
        for name, target in (("type", "supplier_error_type"), ("code", "supplier_error_code"),
                             ("param", "parameter"), ("message", "reason")):
            value = error.get(name)
            if isinstance(value, (str, int)):
                details[target] = safe_text(value, secrets=(key,), source_texts=source_texts)
    if isinstance(exc, openai.APITimeoutError):
        title = "API 等待超时：供应商未及时返回。"
    elif isinstance(exc, openai.APIConnectionError):
        title = "API 连接失败：请检查网络、代理、TLS 证书及接口地址。"
    elif code in {401, 403}:
        title = f"API 鉴权失败（HTTP {code}）：请检查密钥、账户及模型权限。"
    elif code == 429:
        title = "API 请求受限（HTTP 429）：请检查额度、RPM/TPM 或供应商限额。"
    elif code in {408, 504}:
        title = f"供应商网关超时（HTTP {code}）：仅增加本地超时不一定有效。"
    elif code:
        title = f"API 请求失败（HTTP {code}）。"
    elif isinstance(exc, ApiResponseError):
        title = safe_text(str(exc), secrets=(key,), source_texts=source_texts)
    else:
        title = f"API 响应处理异常（{type(exc).__name__}）。"
    parts = [title]
    if profile is not None:
        name = safe_text(field(profile, "name", "当前供应商"), secrets=(key,))
        parts.append(f"供应商：{name} · 接口：{details['api_mode']} · 模型：{details['model']}")
    labels = {"reason": "原因", "supplier_error_type": "供应商错误类型", "supplier_error_code": "错误代码",
              "parameter": "相关参数", "supplier_request_id": "供应商请求编号"}
    parts.extend(f"{label}：{details[k]}" for k, label in labels.items() if details.get(k))
    if code and not details.get("reason"):
        parts.append("供应商未提供可安全展示的具体原因；当前不能确定是模型、参数还是接口限制。")
    return "\n".join(parts), details
