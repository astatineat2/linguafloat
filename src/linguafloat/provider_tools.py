from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from PySide6.QtCore import QObject, QRunnable, Signal
from openai import OpenAI
from linguafloat.config import clean_models
from linguafloat.translation import OpenAITranslator, TranslationError
from linguafloat.api_compat import ApiAdapter, error_details, normalize_base_url, opencode_go_headers
import copy
from threading import Event, Lock
from typing import Any


def address_suggestion(address: str) -> str:
    value = address.strip().rstrip("/")
    if value and "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    path = parsed.path
    for suffix in ("/chat/completions", "/responses", "/models"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    if path.endswith("/v1/v1"):
        path = path[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def service_error(exc: Exception) -> str:
    return error_details(exc)[0]


class ProviderSignals(QObject):
    finished = Signal(int, object, str)


class ProviderTask(QRunnable):
    def __init__(self, token, profile, key, operation):
        super().__init__()
        self.token, self.profile, self.key, self.operation = token, copy.deepcopy(profile), key, operation
        self.signals = ProviderSignals()
        self._cancelled = Event()
        self._client_lock = Lock()
        self._client = None

    @property
    def cancelled(self):
        return self._cancelled.is_set()

    def cancel(self):
        self._cancelled.set()
        with self._client_lock:
            self.key = ""
            client = self._client
        if client is not None:
            # A request already received by the server cannot be recalled.
            # Closing its transport interrupts local waiting where supported.
            try:
                client.close()
            except Exception:
                pass

    def run(self):
        result, error = None, ""
        with self._client_lock:
            key = self.key
        try:
            if self.cancelled:
                return
            if self.operation == "models":
                with OpenAI(api_key=key, base_url=normalize_base_url(self.profile.base_url), timeout=12, max_retries=0,
                            default_headers=opencode_go_headers(self.profile)) as client:
                    with self._client_lock:
                        self._client = client
                    if self.cancelled:
                        return
                    if self.profile.api_mode in {"anthropic", "gemini"}:
                        adapter = ApiAdapter(client, self.profile)
                        payload = client.get(adapter.native_prefix() + "models", cast_to=dict[str, Any],
                                             options={"headers": adapter.native_headers()})
                        entries = payload.get("data", []) if self.profile.api_mode == "anthropic" else payload.get("models", [])
                        result = clean_models([item.get("id", item.get("name", "")).removeprefix("models/") for item in entries])
                    else:
                        result = clean_models([item.id for item in client.models.list().data])
                if not result:
                    error = "模型列表为空，可以手动填写服务商提供的模型名称。"
            else:
                result = []
                errors = []
                modes = (self.profile.api_mode,) if self.profile.api_mode in {"anthropic", "gemini"} else ("responses", "chat_completions")
                for mode in modes:
                    if self.cancelled:
                        return
                    from dataclasses import replace
                    profile = replace(self.profile, api_mode=mode)
                    try:
                        text = OpenAITranslator(key, profile, timeout=12, max_retries=0).translate("Hello.")
                        result.append((mode, text))
                    except TranslationError as exc:
                        errors.append(f"{mode}: {exc}")
                error = "\n".join(errors) if not result else ""
        except Exception as exc:
            error = error_details(exc, self.profile, key=key)[0]
        finally:
            with self._client_lock:
                self._client = None
                self.key = ""
            key = ""
        if not self.cancelled:
            self.signals.finished.emit(self.token, result, error)
