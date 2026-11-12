"""Exercise real SDK serialization with a local transport; never call a provider."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import threading
import uuid

import httpx
from openai import OpenAI
import pytest

from linguafloat import __version__
from linguafloat.api_compat import ApiAdapter, opencode_go_headers
from linguafloat.config import ProviderProfile
from linguafloat.document_core import Cancelled, DocumentJob, DocumentTranslator, JobControl, JobStore
from linguafloat.provider_tools import ProviderTask
from linguafloat.translation import OpenAITranslator, TranslationTask


def profile(mode="chat_completions"):
    return ProviderProfile("go", "OpenCode Go", "https://opencode.ai/inference/go/openai/v1",
                           "deepseek-v4.1-flash", mode, retries=0, document_rpm=10000)


def reply(request):
    if request.url.path.endswith("/responses"):
        return httpx.Response(200, json={"id": "r", "object": "response", "created_at": 1,
            "model": "test", "status": "completed", "output": [{"id": "m", "type": "message",
            "role": "assistant", "status": "completed", "content": [{"type": "output_text",
            "text": "会议九点开始。", "annotations": []}]}],
            "usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9}})
    return httpx.Response(200, json={"id": "c", "object": "chat.completion", "created": 1,
        "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": "会议九点开始。"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9}})


def client(p, handler):
    return OpenAI(api_key="synthetic-do-not-log", base_url=p.base_url, max_retries=0,
                  http_client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_daily_sdk_headers_are_stable_and_configuration_is_snapshotted(mode):
    p = profile(mode)
    sent = []
    def handler(request):
        sent.append(request)
        return reply(request)
    with client(p, handler) as sdk:
        translator = OpenAITranslator("synthetic-do-not-log", p, client=sdk)
        p.base_url = "https://different.test/v1"
        p.model = "changed"
        assert translator.translate("The meeting starts at nine.") == "会议九点开始。"
        translator.translate("The meeting starts at nine.")
    assert len(sent) == 2
    session = sent[0].headers["x-opencode-session"]
    assert str(uuid.UUID(session)) == session
    assert all(r.headers["x-opencode-session"] == session for r in sent)
    assert all(r.headers["user-agent"] == "LinguaFloat/" + __version__ for r in sent)
    assert all(json.loads(r.content)["model"] == "deepseek-v4.1-flash" for r in sent)
    assert all(r.url.host == "opencode.ai" for r in sent)


@pytest.mark.parametrize("address", [
    "https://opencode.ai/inference/openai/v1", "https://api.openai.com/v1",
    "https://relay.test/inference/go/openai/v1", "https://opencode.ai.evil.test/inference/go/openai/v1",
    "http://opencode.ai/inference/go/openai/v1", "https://opencode.ai:8443/inference/go/openai/v1",
    "https://opencode.ai/inference/go/openai/v1/other", "https://opencode.ai/inference/go/openai/v1?key=bad",
])
def test_go_identity_does_not_leak_to_another_service_or_billing_endpoint(address):
    p = profile(); p.base_url = address
    assert opencode_go_headers(p) == {}


def test_concurrent_requests_share_only_their_adapter_session():
    p = profile()
    sessions = []
    lock = threading.Lock()
    def handler(request):
        with lock:
            sessions.append(request.headers["x-opencode-session"])
        return reply(request)
    with client(p, handler) as sdk:
        adapter = ApiAdapter(sdk, p)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda i: adapter.request([{"role": "user", "content": str(i)}]).text,
                                    range(6)))
        assert results == ["会议九点开始。"] * 6
        assert len(set(sessions)) == 1
        other = ApiAdapter(sdk, p)
        other.request([{"role": "user", "content": "new task"}])
        assert sessions[-1] != sessions[0]


def test_saved_legacy_go_address_gets_session_without_switching_endpoint_or_protocol():
    p = profile("responses")
    p.base_url = "https://opencode.ai/zen/go/v1/responses"
    before = asdict(p)
    headers = opencode_go_headers(p)
    assert headers["User-Agent"] == "LinguaFloat/" + __version__
    assert headers["x-opencode-session"]
    assert asdict(p) == before


def test_normalized_go_address_and_restored_scope_have_same_session():
    p = profile()
    first = opencode_go_headers(p, session_scope="document:saved")
    p.base_url += "/v1/chat/completions/"
    assert opencode_go_headers(p, session_scope="document:saved") == first
    assert opencode_go_headers(p, session_scope="document:other") != first


def test_document_session_restoration_cache_usage_and_cancellation(tmp_path):
    p = profile()
    sent = []
    def handler(request):
        sent.append(request)
        return reply(request)
    job = DocumentJob(str(tmp_path / "source.pdf"), str(tmp_path / "out"), asdict(p), job_id="saved-job")
    store = JobStore(tmp_path / "jobs.sqlite3")
    control = JobControl()
    with client(p, handler) as sdk:
        translator = DocumentTranslator(job, "synthetic-do-not-log", store, control, lambda _: None, sdk)
        original_scope = translator.cache_key(translator.messages("Hello"))
        assert translator.translate("Hello") == "会议九点开始。"
        assert translator.translate("Hello") == "会议九点开始。"
        assert len(sent) == 1 and translator.hits == 1
        assert translator.token_usage["total_tokens"] == 9
        # Recreate a worker from the persisted job ID, without persisting a header or key.
        restored = DocumentTranslator(job, "synthetic-do-not-log", store, control, lambda _: None, sdk)
        restored.translate("Another paragraph")
        assert sent[0].headers["x-opencode-session"] == sent[1].headers["x-opencode-session"]
        assert restored.cache_key(restored.messages("Hello")) == original_scope
        job.job_id = "different-document"
        separate = DocumentTranslator(job, "synthetic-do-not-log", store, control, lambda _: None, sdk)
        separate.translate("Different document paragraph")
        assert sent[2].headers["x-opencode-session"] != sent[0].headers["x-opencode-session"]
        control.cancelled.set()
        with pytest.raises(Cancelled):
            separate.translate("Do not submit")
        assert len(sent) == 3
    store.close()


def test_document_retry_uses_original_session_and_bounded_backoff(tmp_path, monkeypatch):
    import linguafloat.document_scheduler as scheduler
    monkeypatch.setattr(scheduler, "retry_delay", lambda *_: 0)
    p = profile(); p.retries = 1
    sent = []
    def handler(request):
        sent.append(request)
        if len(sent) == 1:
            return httpx.Response(429, json={"error": {"type": "rate_limit", "message": "limited"}})
        return reply(request)
    job = DocumentJob(str(tmp_path / "source.pdf"), str(tmp_path / "out"), asdict(p))
    store = JobStore(tmp_path / "jobs.sqlite3")
    with client(p, handler) as sdk:
        translator = DocumentTranslator(job, "synthetic-do-not-log", store, JobControl(), lambda _: None, sdk)
        assert translator.translate("Hello") == "会议九点开始。"
        assert len(sent) == 2
        assert sent[0].headers["x-opencode-session"] == sent[1].headers["x-opencode-session"]
        assert translator.gate.limit == 1 and translator.gate.inflight == 0
    store.close()


def test_actual_translation_task_and_model_list_use_automatic_headers(monkeypatch):
    import linguafloat.translation as translation
    import linguafloat.provider_tools as tools
    p = profile()
    sent = []
    def handler(request):
        sent.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"object": "list", "data": [{"id": p.model,
                "object": "model", "created": 1, "owned_by": "test"}]})
        return reply(request)
    def sdk_factory(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(translation, "OpenAI", sdk_factory)
    monkeypatch.setattr(tools, "OpenAI", sdk_factory)
    results = []
    task = TranslationTask("synthetic-do-not-log", p, "The meeting starts at nine.", max_retries=0)
    task.signals.succeeded.connect(lambda *args: results.append(args))
    task.run()
    assert results and results[0][2] == "会议九点开始。"
    model_task = ProviderTask(1, p, "synthetic-do-not-log", "models")
    model_task.run()
    assert len(sent) == 2 and sent[1].method == "GET"
    assert all(r.headers["user-agent"] == "LinguaFloat/" + __version__ for r in sent)
    assert all("x-opencode-session" in r.headers for r in sent)
    # Starting a task with a cached/cancelled result must never generate a request.
    cancelled = TranslationTask("synthetic-do-not-log", p, "Do not submit", allowed=lambda: False)
    cancelled.run()
    assert len(sent) == 2
