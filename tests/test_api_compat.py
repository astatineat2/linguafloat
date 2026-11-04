import copy
import json
from dataclasses import asdict
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from linguafloat.api_compat import (ApiAdapter, ApiResponseError, error_details,
                                   normalize_base_url, token_usage)
from linguafloat.config import AppSettings, ConfigStore, ProviderProfile
from linguafloat.document_core import DocumentError, DocumentJob, DocumentTranslator, JobControl, JobStore
from linguafloat.translation import OpenAITranslator, TranslationError


def profile(mode="responses", **options):
    return ProviderProfile("test", "Test supplier", "https://relay.test/custom/v1", "custom-model", mode, **options)


@pytest.mark.parametrize("raw,expected", [
    ("https://relay.test/zen/go/v1/chat/completions/", "https://relay.test/zen/go/v1"),
    ("https://relay.test/compatible-mode/v1/responses", "https://relay.test/compatible-mode/v1"),
    ("https://relay.test/v1/v1/v1", "https://relay.test/v1"),
    ("https://relay.test/custom", "https://relay.test/custom"),
    ("https://api.openai.com", "https://api.openai.com/v1"),
])
def test_normalized_address_preserves_provider_path(raw, expected):
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize("raw", ["https://relay.test?key=secret", "https://user:secret@relay.test/v1", "invalid"])
def test_addresses_do_not_accept_embedded_credentials(raw):
    with pytest.raises(ValueError):
        normalize_base_url(raw)


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("model", ["gpt-codex-custom", "deepseek-v4.1-flash", "qwen-custom", "mimo-custom"])
def test_sdk_wire_contract_for_user_supplied_models(mode, model):
    sent = []
    def handler(request):
        sent.append((str(request.url), json.loads(request.content)))
        body = {"id": "r", "object": "response", "created_at": 1, "model": model, "status": "completed",
                "output": [{"id": "msg", "type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": "译文", "annotations": []}]}]}
        if mode == "chat_completions":
            body = {"id": "c", "object": "chat.completion", "created": 1, "model": model,
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "译文"}}]}
        return httpx.Response(200, json=body, headers={"x-request-id": "supplier-001"})
    p = profile(mode); p.model = model
    p.base_url += "/responses" if mode == "responses" else "/chat/completions"
    client = openai.OpenAI(api_key="synthetic", base_url=normalize_base_url(p.base_url), max_retries=0,
                          http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        assert OpenAITranslator("synthetic", p, client=client).translate("Hello") == "译文"
        path, body = sent[0]
        assert path == "https://relay.test/custom/v1/" + ("responses" if mode == "responses" else "chat/completions")
        assert body["model"] == model
        assert not any(k in body for k in ["max_tokens", "max_completion_tokens", "max_output_tokens", "reasoning", "reasoning_effort", "temperature"])
    finally:
        client.close()


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_optional_parameters_are_explicit_and_snapshotted(mode):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return NS(output_text="译文", choices=[NS(message=NS(content="译文"))])
    p = profile(mode, output_token_limit=4096, instruction_role="developer", reasoning_effort="low", chat_token_parameter="max_tokens")
    adapter = ApiAdapter(NS(responses=NS(create=create), chat=NS(completions=NS(create=create))), p)
    p.model = "changed"; p.output_token_limit = 1
    assert adapter.request([{"role": "system", "content": "Translate"}, {"role": "user", "content": "Hello"}]).text == "译文"
    wire = calls[0]
    assert wire["model"] == "custom-model"
    assert wire["input" if mode == "responses" else "messages"][0]["role"] == "developer"
    assert wire["max_output_tokens" if mode == "responses" else "max_tokens"] == 4096
    assert wire["reasoning" if mode == "responses" else "reasoning_effort"] == ({"effort": "low"} if mode == "responses" else "low")


@pytest.mark.parametrize("mode,result", [
    ("responses", NS(output_text="partial", status="incomplete", incomplete_details={"reason": "max_output_tokens"})),
    ("responses", NS(output_text="", status="completed")),
    ("chat_completions", NS(choices=[])),
    ("chat_completions", NS(choices=[NS(finish_reason="length", message=NS(content="partial"))])),
    ("chat_completions", NS(choices=[NS(finish_reason="content_filter", message=NS(content="partial"))])),
    ("chat_completions", NS(choices=[NS(message=NS(content="", reasoning_content="private reasoning"))])),
])
def test_incomplete_or_empty_translation_is_not_accepted(mode, result):
    client = NS(responses=NS(create=lambda **_: result), chat=NS(completions=NS(create=lambda **_: result)))
    with pytest.raises(ApiResponseError):
        ApiAdapter(client, profile(mode)).request([{"role": "user", "content": "source"}])


def test_response_content_blocks_and_usage_are_normalized():
    response = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "译文"}]}]}
    adapter = ApiAdapter(NS(responses=NS(create=lambda **_: response)), profile())
    assert adapter.request([]).text == "译文"
    assert token_usage(response, "responses") is None
    assert token_usage({"usage": {"prompt_tokens": 20, "completion_tokens": 5}}, "chat_completions") == (20, 5, 25)


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_safe_supplier_errors_are_shared_and_document_events_keep_diagnostics(tmp_path, mode, status):
    secret, source = "synthetic-private-key", "private document paragraph"
    request = httpx.Request("POST", "https://relay.test/v1/responses")
    error = openai.APIStatusError("DO NOT SHOW RAW EXCEPTION", response=httpx.Response(status, request=request,
        headers={"x-request-id": "supplier-55"}), body={"error": {"type": "invalid_request_error",
        "code": "unsupported_parameter", "param": "max_tokens",
        "message": f"unsupported max_tokens. Authorization: Bearer {secret}. content: \"{source}\""}})
    def create(**kwargs):
        raise error
    client = NS(responses=NS(create=create), chat=NS(completions=NS(create=create)))
    p = profile(mode); p.retries = 0
    with pytest.raises(TranslationError) as caught:
        OpenAITranslator(secret, p, client=client).translate(source)
    text = str(caught.value)
    assert str(status) in text and "unsupported max_tokens" in text and "max_tokens" in text and mode in text
    assert secret not in text and source not in text and "DO NOT SHOW" not in text
    job = DocumentJob(str(tmp_path / "source.pdf"), str(tmp_path / "out"), asdict(p), job_id="job")
    events = []
    store = JobStore(tmp_path / "jobs.sqlite")
    try:
        translator = DocumentTranslator(job, secret, store, JobControl(), events.append, client)
        with pytest.raises(DocumentError):
            translator.translate(source)
        failed = next(e["request"] for e in events if e.get("request", {}).get("state") == "failed")
        assert failed["http_status"] == status and failed["parameter"] == "max_tokens"
        assert failed["api_mode"] == mode and failed["request_id"] == "R0001"
        assert secret not in json.dumps(failed) and source not in json.dumps(failed)
        assert translator.gate.inflight == 0
        assert not store.db.execute("select * from cache").fetchall()
    finally:
        store.close()


def test_config_options_round_trip_and_cache_scope_is_isolated(tmp_path):
    p = profile("chat_completions", output_token_limit=8192, chat_token_parameter="max_tokens",
                instruction_role="developer", reasoning_effort="high", thinking_mode="deepseek_off")
    settings = AppSettings(providers=[p], active_provider=p.id)
    store = ConfigStore(tmp_path / "config.json")
    store.save(settings)
    assert store.load().active_profile() == p
    assert profile().cache_scope != p.cache_scope


def test_document_and_daily_requests_preserve_original_configuration(tmp_path):
    p = profile("chat_completions")
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return NS(choices=[NS(message=NS(content="译文"))])
    client = NS(chat=NS(completions=NS(create=create)))
    daily = OpenAITranslator("dummy", p, client=client)
    job = DocumentJob(str(tmp_path / "source.pdf"), str(tmp_path / "out"), asdict(p))
    store = JobStore(tmp_path / "db.sqlite")
    try:
        document = DocumentTranslator(job, "dummy", store, JobControl(), lambda _: None, client)
        p.model = "changed-model"; job.provider["model"] = "changed-model"
        assert daily.translate("Hello") == document.translate("Hello") == "译文"
        assert all(c["model"] == "custom-model" for c in calls)
        old_key = document.cache_key(document.messages("Hello"))
        document.translate("Hello")
        assert len(calls) == 2 and store.get(old_key) == "译文"
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_shared_timeout_diagnostic_and_cancelled_requests_send_nothing(tmp_path, mode):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise openai.APITimeoutError(request=httpx.Request("POST", "https://relay.test/v1"))
    client = NS(responses=NS(create=create), chat=NS(completions=NS(create=create)))
    p = profile(mode); p.retries = 0
    with pytest.raises(TranslationError, match="API 等待超时"):
        OpenAITranslator("dummy", p, client=client).translate("short text")
    job = DocumentJob(str(tmp_path / "source.pdf"), str(tmp_path / "out"), asdict(p))
    store = JobStore(tmp_path / "db.sqlite")
    try:
        control = JobControl()
        document = DocumentTranslator(job, "dummy", store, control, lambda _: None, client)
        with pytest.raises(DocumentError, match="API 等待超时"):
            document.translate("short text")
        count = len(calls)
        control.cancelled.set()
        with pytest.raises(DocumentError, match="取消"):
            document.translate("different text")
        assert len(calls) == count and document.gate.inflight == 0
    finally:
        store.close()


@pytest.mark.parametrize("thinking,body", [
    ("qwen_off", {"enable_thinking": False}), ("qwen_on", {"enable_thinking": True}),
    ("deepseek_off", {"thinking": {"type": "disabled"}}), ("deepseek_on", {"thinking": {"type": "enabled"}}),
])
def test_supplier_thinking_switch_is_explicit_and_chat_only(thinking, body):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "c", "object": "chat.completion", "created": 1, "model": "custom-model",
           "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "译文"}}]})
    p = profile("chat_completions", thinking_mode=thinking)
    client = openai.OpenAI(api_key="synthetic", base_url=p.base_url, max_retries=0,
                          http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        assert OpenAITranslator("synthetic", p, client=client).translate("Hello") == "译文"
        assert all(calls[0][k] == v for k, v in body.items())
        p.api_mode = "responses"
        with pytest.raises(TranslationError, match="仅适用于 Chat"):
            OpenAITranslator("synthetic", p, client=client).translate("Hello")
        assert len(calls) == 1
    finally:
        client.close()


def test_local_parameter_mismatch_is_not_counted_as_an_api_submission(tmp_path):
    p = profile(thinking_mode="qwen_off")
    job = DocumentJob(str(tmp_path / "source.pdf"), str(tmp_path / "out"), asdict(p))
    store = JobStore(tmp_path / "db.sqlite")
    try:
        translator = DocumentTranslator(job, "dummy", store, JobControl(), lambda _: None, NS())
        with pytest.raises(DocumentError, match="仅适用于 Chat"):
            translator.translate("Hello")
        assert translator.token_usage["unreported_requests"] == 0
        assert translator.gate.inflight == 0
        assert not store.db.execute("select * from request_quota").fetchall()
    finally:
        store.close()
