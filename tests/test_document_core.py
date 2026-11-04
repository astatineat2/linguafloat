import threading
import time
import json
from types import SimpleNamespace

import pytest

from linguafloat.document_core import (Cancelled, DocumentError, DocumentJob, DocumentTranslator,
                                      JobControl, JobStore, api_error, selected_pages)


def job_for(path, **kwargs):
    return DocumentJob(str(path), str(path.parent / "out"),
        {"id": "test", "base_url": "https://example.com/v1", "model": "test", "api_mode": "responses"},
        job_id="test-job", **kwargs)


def test_formula_fragment_uses_context_cache_and_relayout_never_sends(tmp_path):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text="由于")
    job = job_for(tmp_path / "scan.pdf")
    store = JobStore(tmp_path / "fragments.sqlite")
    try:
        translator = DocumentTranslator(job, "", store, JobControl(), lambda _: None,
                                        SimpleNamespace(responses=SimpleNamespace(create=create)))
        assert translator.translate_fragment("Since", "Since B = H within material") == "由于"
        assert json.loads(calls[0]["input"][-1]["content"]) == {
            "fragment": "Since", "context": "Since B = H within material"}
        assert "Translate only fragment" in calls[0]["input"][0]["content"]
        job.relayout_only = True
        assert translator.translate_fragment("Since", "Since B = H within material") == "由于"
        with pytest.raises(DocumentError, match="缓存不完整"):
            translator.translate_fragment("Since", "Since 1995")
        assert len(calls) == 1 and translator.hits == 1
    finally:
        store.close()


def test_page_selection_deduplicates_and_trial_respects_range():
    assert selected_pages("3-5,4,8", 10) == [2, 3, 4, 7]
    assert selected_pages("3-5,8", 10, True) == [2, 3]
    assert selected_pages("", 3) == [0, 1, 2]
    for invalid in ("0", "4-2", "11", "1,", "1-", "1-a"):
        with pytest.raises(DocumentError):
            selected_pages(invalid, 10)


def test_job_rejects_docx_page_numbers_and_no_output(tmp_path):
    source = tmp_path / "a.docx"
    source.touch()
    with pytest.raises(DocumentError, match="Word"):
        job_for(source, pages="1").validate()
    with pytest.raises(DocumentError, match="输出"):
        job_for(source, mono=False, bilingual=False).validate()


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_transport_cache_persists_and_is_scoped_to_provider_model_language(tmp_path, api_mode):
    job = job_for(tmp_path / "source.pdf")
    job.provider["api_mode"] = api_mode
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text="你好", choices=[SimpleNamespace(message=SimpleNamespace(content="你好"))])

    client = SimpleNamespace(responses=SimpleNamespace(create=create),
                             chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    store = JobStore(tmp_path / "db.sqlite")
    first = DocumentTranslator(job, "dummy-secret", store, JobControl(), lambda _: None, client)
    assert first.translate("Hello") == "你好"
    assert first.translate("Hello") == "你好"
    assert len(calls) == 1
    store.close()
    store = JobStore(tmp_path / "db.sqlite")
    second = DocumentTranslator(job, "different-key", store, JobControl(), lambda _: None, client)
    assert second.translate("Hello") == "你好"
    assert second.hits == 1 and len(calls) == 1
    for field, value in (("base_url", "https://other.example/v1"), ("model", "other")):
        job.provider[field] = value
        # A configuration change applies to the next translator, not an active task.
        second = DocumentTranslator(job, "different-key", store, JobControl(), lambda _: None, client)
        second.translate("Hello")
    job.target = "ja"
    second.last_request = 0
    second.translate("Hello")
    assert len(calls) == 4
    store.status(job, "completed")
    store.close()
    assert b"dummy-secret" not in (tmp_path / "db.sqlite").read_bytes()


def test_pause_during_request_caches_but_does_not_publish_until_resume(tmp_path):
    job = job_for(tmp_path / "source.pdf")
    control = JobControl()
    started, release, completed = threading.Event(), threading.Event(), threading.Event()

    def create(**kwargs):
        started.set()
        release.wait(3)
        return SimpleNamespace(output_text="你好")

    store = JobStore(tmp_path / "db.sqlite")
    t = DocumentTranslator(job, "dummy", store, control, lambda _: None,
                           SimpleNamespace(responses=SimpleNamespace(create=create)))
    errors = []

    def run():
        try:
            t.translate("Hello")
            completed.set()
        except Cancelled:
            errors.append("cancelled")

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(2)
    control.paused.set()
    release.set()
    time.sleep(.15)
    assert not completed.is_set()
    assert store.db.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 1
    control.cancelled.set()
    thread.join(2)
    assert errors == ["cancelled"]
    store.close()


def test_error_does_not_expose_raw_provider_response():
    error = RuntimeError("Authorization: secret-key request body confidential")
    assert "secret" not in api_error(error)
    error.status_code = 401
    assert "401" in api_error(error) and "secret" not in api_error(error)


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_relayout_never_calls_api_even_when_cache_missing(tmp_path, api_mode):
    job = job_for(tmp_path / "source.pdf", relayout_only=True)
    job.provider["api_mode"] = api_mode
    def forbidden(**kwargs):
        pytest.fail("Re-layout attempted a network request")
    client = SimpleNamespace(responses=SimpleNamespace(create=forbidden),
                             chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden)))
    store = JobStore(tmp_path / "cache.sqlite")
    try:
        translator = DocumentTranslator(job, "", store, JobControl(), lambda _: None, client)
        store.put(translator.cache_key(translator.messages("Hello")), "你好")
        assert translator.translate("Hello") == "你好"
        with pytest.raises(DocumentError, match="缓存不完整"):
            translator.translate("Missing")
        assert translator.calls == 0
        assert translator.token_usage["unreported_requests"] == 0
        job.provider["model"] = "different-model"
        translator = DocumentTranslator(job, "", store, JobControl(), lambda _: None, client)
        with pytest.raises(DocumentError, match="没有调用翻译 API"):
            translator.translate("Hello")
    finally:
        store.close()


def test_incomplete_response_is_not_cached(tmp_path):
    store = JobStore(tmp_path / "db.sqlite")
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: SimpleNamespace(status="incomplete", output_text="partial")))
    translator = DocumentTranslator(job_for(tmp_path / "a.pdf"), "unused", store, JobControl(), lambda _: None, client)
    with pytest.raises(DocumentError, match="未完成"):
        translator.translate("Hello")
    assert store.db.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
    store.close()


def test_document_timeout_is_independent_and_sdk_retries_disabled(tmp_path):
    job = job_for(tmp_path / "a.pdf", request_timeout=240)
    job.provider.update(timeout=30, retries=3)
    store = JobStore(tmp_path / "db.sqlite")
    translator = DocumentTranslator(job, "dummy", store, JobControl(), lambda _: None)
    try:
        assert translator.client.timeout.read == 240
        assert translator.client.timeout.connect == 15
        assert translator.client.max_retries == 0
        assert translator.retries == 3
    finally:
        translator.client.close()
        store.close()


def test_sentence_chunks_keep_all_source_characters():
    from linguafloat.document_core import split_text
    text = ("这是论文内容。\nSecond sentence with citations [12].\n\n" * 160) + "END"
    chunks = split_text(text, 2400)
    assert "".join(chunks) == text
    assert all(0 < len(part) <= 2400 for part in chunks)
    assert len(chunks) > 1


def test_chunk_boundaries_prefer_paragraphs_and_keep_math():
    from linguafloat.document_core import split_text
    text = "A" * 1300 + "\n\n" + "word " * 350
    chunks = split_text(text, 2400)
    assert chunks[0].endswith("\n\n")
    assert "".join(chunks) == text
    formula = "\\(" + "x + " * 50 + "y\\)"
    text = "a" * 2250 + formula + " remainder" * 60
    chunks = split_text(text, 2400)
    assert any(formula in chunk for chunk in chunks)
    assert "".join(chunks) == text
    chinese = "段落内容。" * 700
    assert all(chunk.endswith("。") for chunk in split_text(chinese, 2400)[:-1])


def test_partial_legacy_cache_is_not_retranslated(tmp_path):
    from linguafloat.document_core import legacy_split_text
    text = ("A scientific sentence with a citation [12]. " * 75)
    old = legacy_split_text(text, 2400)
    assert len(old) == 2
    sent = []
    def create(**kwargs):
        value = kwargs["input"][-1]["content"]
        sent.append(value)
        return SimpleNamespace(output_text=value.lower())
    store = JobStore(tmp_path / "cache.sqlite")
    try:
        translator = DocumentTranslator(job_for(tmp_path / "a.pdf"), "fake", store, JobControl(), lambda _: None,
                                       SimpleNamespace(responses=SimpleNamespace(create=create)))
        store.put(translator.cache_key(translator.messages(old[0])), old[0].lower())
        result = translator.translate(text)
        assert sent == [old[1]]
        assert " ".join(result.split()) == " ".join(text.lower().split())
    finally:
        store.close()


def test_new_segmentation_reduces_short_requests_and_bounds_bytes(tmp_path):
    requests = []
    def create(**kwargs):
        text = kwargs["input"][-1]["content"]
        requests.append(text)
        return SimpleNamespace(output_text=text)
    store = JobStore(tmp_path / "cache.sqlite")
    try:
        translator = DocumentTranslator(job_for(tmp_path / "a.pdf"), "fake", store, JobControl(), lambda _: None,
                                       SimpleNamespace(responses=SimpleNamespace(create=create)))
        text = "A sentence with context. " * 125
        assert 2400 < len(text) < 3600
        assert translator.translate(text) == text.strip()
        assert len(requests) == 1
        mixed = "english " * 450 + "汉字句子。" * 700
        translator.translate(mixed)
        assert all(len(item.encode("utf-8")) <= 7200 for item in requests)
    finally:
        store.close()


def test_timeout_splits_then_caches_full_result_and_reports_location(tmp_path):
    import httpx
    import openai
    events, submitted = [], []
    source = "A" * 2200
    def create(**kwargs):
        text = kwargs["input"][-1]["content"]
        submitted.append(text)
        if len(text) > 1200:
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://example.com"))
        return SimpleNamespace(output_text=text.lower())
    store = JobStore(tmp_path / "db.sqlite")
    t = DocumentTranslator(job_for(tmp_path / "a.pdf"), "dummy", store, JobControl(), events.append,
                           SimpleNamespace(responses=SimpleNamespace(create=create)))
    t.wait = lambda _: t.control.checkpoint()
    t.location = "PDF 第 4 页"
    assert t.translate(source).replace("\n", "") == source.lower()
    assert len(submitted) == 2  # identical halves share the successful cache
    assert t.translate(source).replace("\n", "") == source.lower()
    assert len(submitted) == 2
    assert any("第 4 页" in e.get("message", "") and "拆为" in e.get("message", "") for e in events)
    store.close()


@pytest.mark.parametrize("code,expected_calls", [(401, 1), (429, 3), (503, 3), (504, 3)])
def test_retries_are_bounded_and_structured_requests_are_not_split(tmp_path, code, expected_calls):
    import httpx
    import openai
    submitted = []
    def create(**kwargs):
        submitted.append(kwargs["input"])
        raise openai.APIStatusError("SECRET RAW BODY", response=httpx.Response(code,
            request=httpx.Request("POST", "https://example.com")), body={"key": "SECRET"})
    store = JobStore(tmp_path / "db.sqlite")
    events = []
    job = job_for(tmp_path / "a.pdf")
    job.provider["document_concurrency"] = 6
    t = DocumentTranslator(job, "dummy", store, JobControl(), events.append,
                           SimpleNamespace(responses=SimpleNamespace(create=create)))
    t.wait = lambda _: t.control.checkpoint()
    messages = [{"role": "user", "content": "<text>" + "x" * 3000 + "</text>"}]
    with pytest.raises(DocumentError) as caught:
        t.request(messages)
    assert str(code) in str(caught.value) and "SECRET" not in str(caught.value)
    assert len(submitted) == expected_calls
    assert all(item == messages for item in submitted)
    counts = [e["name"] for e in events if e.get("type") == "measurement"]
    assert counts.count("请求重试") == expected_calls - 1
    if code in {429, 504}:
        assert t.gate.limit == 1
        assert counts.count("HTTP 429 限流" if code == 429 else "请求超时") == expected_calls
    assert t.token_usage["unreported_requests"] == expected_calls
    assert t.gate.inflight == 0
    assert store.db.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
    store.close()


def test_permanent_timeout_stops_recovery_and_cancel_stops_retries(tmp_path):
    import httpx
    import openai
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise openai.APITimeoutError(request=httpx.Request("POST", "https://example.com"))
    store = JobStore(tmp_path / "db.sqlite")
    t = DocumentTranslator(job_for(tmp_path / "a.pdf"), "dummy", store, JobControl(), lambda _: None,
                           SimpleNamespace(responses=SimpleNamespace(create=create)))
    t.wait = lambda _: t.control.checkpoint()
    t.location = "PDF 第 8 页"
    with pytest.raises(DocumentError, match="第 8 页.*超时"):
        t.translate("x" * 2400)
    assert len(calls) == 3
    t.control.cancelled.set()
    with pytest.raises(Cancelled):
        t.translate("new text")
    assert len(calls) == 3
    store.close()


def test_real_http_read_timeout_has_specific_error(tmp_path):
    import httpx
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from openai import OpenAI
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            time.sleep(.2)
            self.send_response(200)
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store = JobStore(tmp_path / "db.sqlite")
    client = OpenAI(api_key="synthetic", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    timeout=httpx.Timeout(.05), max_retries=0)
    job = job_for(tmp_path / "a.pdf")
    job.provider["retries"] = 0
    t = DocumentTranslator(job, "dummy", store, JobControl(), lambda _: None, client)
    try:
        with pytest.raises(DocumentError, match="API 等待超时"):
            t.translate("short sentence")
    finally:
        client.close()
        store.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_previous_large_page_cache_is_reused_without_network(tmp_path):
    store = JobStore(tmp_path / "db.sqlite")
    t = DocumentTranslator(job_for(tmp_path / "a.pdf"), "dummy", store, JobControl(), lambda _: None,
                           SimpleNamespace())
    text = "A" * 6000 + "B" * 1200
    store.put(t.cache_key(t.messages(text[:6000])), "已完成第一页段")
    store.put(t.cache_key(t.messages(text[6000:])), "已完成第二页段")
    assert t.translate(text) == "已完成第一页段\n已完成第二页段"
    assert t.calls == 0 and t.hits == 2
    store.close()


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_history_tokens_count_responses_and_not_local_cache(tmp_path, mode):
    import json
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"source-content")
    job = job_for(source)
    job.provider["api_mode"] = mode
    store = JobStore(tmp_path / "db.sqlite")
    store.status(job, "running")
    usage = SimpleNamespace(input_tokens=12, output_tokens=7, prompt_tokens=12, completion_tokens=7, total_tokens=19)
    response = SimpleNamespace(output_text="译文", usage=usage,
                               choices=[SimpleNamespace(message=SimpleNamespace(content="译文"))])
    create = lambda **_: response
    client = SimpleNamespace(responses=SimpleNamespace(create=create), chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    t = DocumentTranslator(job, "secret", store, JobControl(), lambda _: None, client)
    t.translate("first")
    t.translate("first")
    t.wait = lambda _: None
    t.translate("second")
    store.status(job, "completed", {"outputs": ["out.pdf"]})
    record = json.loads(store.history()[0][4])
    assert record["input_tokens"] == 24 and record["output_tokens"] == 14
    assert record["total_tokens"] == 38 and record["unreported_requests"] == 0
    assert record["file_size"] == len(b"source-content")
    assert record["elapsed_seconds"] >= 0 and record["finished_at"] >= record["started_at"]
    store.close()
    source.unlink()
    reopened = JobStore(tmp_path / "db.sqlite")
    assert json.loads(reopened.history()[0][4])["total_tokens"] == 38
    reopened.close()


def test_history_retains_usage_for_failed_and_missing_responses(tmp_path):
    import json
    job = job_for(tmp_path / "paper.pdf")
    store = JobStore(tmp_path / "db.sqlite")
    store.status(job, "running")
    response = SimpleNamespace(output_text="partial", status="incomplete",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=20, total_tokens=30))
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: response))
    t = DocumentTranslator(job, "secret", store, JobControl(), lambda _: None, client)
    with pytest.raises(DocumentError):
        t.translate("first")
    response.status = "completed"
    response.usage = None
    t.wait = lambda _: None
    t.translate("second")
    store.status(job, "failed", {"message": "synthetic failure"})
    info = json.loads(store.history()[0][4])
    assert info["total_tokens"] == 30
    assert info["reported_requests"] == 1 and info["unreported_requests"] == 1
    assert "elapsed_seconds" in info
    store.close()


def test_interrupted_request_remains_unknown_in_history(tmp_path):
    import json
    job = job_for(tmp_path / "paper.pdf")
    job.provider["retries"] = 0
    store = JobStore(tmp_path / "db.sqlite")
    store.status(job, "running")
    def fail(**kwargs):
        raise RuntimeError("do not persist this secret")
    t = DocumentTranslator(job, "secret", store, JobControl(), lambda _: None,
                           SimpleNamespace(responses=SimpleNamespace(create=fail)))
    with pytest.raises(DocumentError):
        t.translate("first")
    store.status(job, "cancelled")
    info = json.loads(store.history()[0][4])
    assert info["total_tokens"] == 0 and info["unreported_requests"] == 1
    assert "secret" not in json.dumps(info)
    store.close()


def test_billing_estimate_and_missing_usage():
    from linguafloat.document_core import billing_display, clean_pricing
    info = {"input_tokens": 1000000, "output_tokens": 500000, "pricing": {"input": 2, "output": 8}}
    assert billing_display(info) == ("1,000,000", "500,000", "$6.000000", "$6.000000")
    info["pricing"]["multiplier"] = .1
    assert billing_display(info)[3] == "$0.600000"
    info["pricing"]["multiplier"] = 0
    assert billing_display(info)[3] == "$0.000000"
    info.update(unreported_requests=1, reported_requests=1)
    assert billing_display(info)[2] == "$6.000000 部分"
    assert billing_display(info, compact=True) == ("1,000,000", "500,000", "$6.000000", "$0.000000")
    info["reported_requests"] = 0
    assert billing_display(info) == ("待返回/未知",) * 4
    assert billing_display(info, compact=True) == ("待返回/未知",) * 4
    assert billing_display({"input_tokens": 0, "output_tokens": 0})[2] == "$0.000000"
    assert billing_display({"input_tokens": 10, "output_tokens": 5})[2] == "待填官方单价"
    assert billing_display({}) == ("未记录",) * 4
    assert clean_pricing({"input": float("nan"), "output": -1}) == {}


def test_history_preserves_price_snapshot(tmp_path):
    import json
    job = job_for(tmp_path / "test.pdf")
    job.pricing = {"input": 2, "output": 8}
    store = JobStore(tmp_path / "history.sqlite")
    try:
        store.status(job, "running")
        job.pricing["input"] = 99
        store.record_usage(job, {"input_tokens": 100, "output_tokens": 10})
        store.status(job, "completed")
        assert json.loads(store.history()[0][4])["pricing"] == {"input": 2, "output": 8}
    finally:
        store.close()


def test_failure_details_are_actionable_and_do_not_expose_raw_errors(tmp_path):
    from zipfile import BadZipFile
    from linguafloat.document_worker import describe_failure
    job = job_for(tmp_path / "paper.pdf")
    for error, expected in ((PermissionError("secret-key"), "文件访问被拒绝"),
                            (BadZipFile("secret-key"), "Office 文件格式损坏"),
                            (RuntimeError("secret-key"), "未能确定具体原因"),
                            (FileNotFoundError("secret-key"), "所需文件不存在"),
                            (ImportError("secret-key"), "运行组件缺失"),
                            (OSError(28, "secret-key"), "磁盘空间不足")):
        detail = describe_failure(error, job, "排版中")
        assert expected in detail and "secret-key" not in detail
        assert "paper.pdf" in detail and "发生阶段：排版中" in detail
        assert "缓存保留" in detail
    detail = describe_failure(DocumentError("原始第 2 页：API 等待超时"), job, "翻译中")
    assert "原始第 2 页：API 等待超时" in detail


def test_attribute_failure_keeps_location_without_payload_or_secrets(tmp_path):
    from linguafloat.document_worker import describe_failure, execute
    job = job_for(tmp_path / "paper.pdf")
    secret_payload = "sk-private-document-body"
    try:
        execute(None, secret_payload, None, JobControl(), lambda _: None)
    except AttributeError as exc:
        detail = describe_failure(exc, job, "排版中")
    assert "文档数据处理错误" in detail
    assert "document_worker.py:" in detail and "execute" in detail
    assert "无法读取字段：validate" in detail and "字段所属对象为空" in detail
    assert secret_payload not in detail and str(tmp_path) not in detail
    assert "缓存保留" in detail
    detail = describe_failure(AttributeError(secret_payload), job, "排版中")
    assert secret_payload not in detail
