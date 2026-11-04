from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from PySide6.QtCore import QObject, QRunnable, Signal
from openai import OpenAI
from linguafloat.config import clean_models
from linguafloat.translation import OpenAITranslator, TranslationError
from linguafloat.api_compat import error_details, normalize_base_url
import copy


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

    def run(self):
        result, error = None, ""
        try:
            if self.operation == "models":
                with OpenAI(api_key=self.key, base_url=normalize_base_url(self.profile.base_url), timeout=12, max_retries=0) as client:
                    result = clean_models([item.id for item in client.models.list().data])
                if not result:
                    error = "模型列表为空，可以手动填写服务商提供的模型名称。"
            else:
                result = []
                errors = []
                for mode in ("responses", "chat_completions"):
                    from dataclasses import replace
                    profile = replace(self.profile, api_mode=mode)
                    try:
                        text = OpenAITranslator(self.key, profile, timeout=12, max_retries=0).translate("Hello.")
                        result.append((mode, text))
                    except TranslationError as exc:
                        errors.append(f"{mode}: {exc}")
                error = "\n".join(errors) if not result else ""
        except Exception as exc:
            error = error_details(exc, self.profile, key=self.key)[0]
        finally:
            self.key = ""
        self.signals.finished.emit(self.token, result, error)
