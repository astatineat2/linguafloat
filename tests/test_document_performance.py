import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from linguafloat.document_core import Cancelled, DocumentError, DocumentTranslator, JobControl, JobStore, billing_display
from linguafloat.document_metrics import PerformanceLog, WorkEstimate, active_clock
from linguafloat.document_scheduler import RequestGate, retry_delay
from test_document_core import job_for


def test_slow_request_trace_cache_and_relayout_are_distinct(tmp_path):
    job = job_for(tmp_path / "paper.pdf")
    store = JobStore(tmp_path / "trace.sqlite3")
    events = []
    def create(**kwargs):
        time.sleep(2.15)
        return SimpleNamespace(output_text="译文", usage=dict(input_tokens=2,output_tokens=1))
    t = DocumentTranslator(job, "fake-secret", store, JobControl(), events.append,
        SimpleNamespace(responses=SimpleNamespace(create=create)))
    t.location = "原始第 2 页 · 参考文献 3"
    try:
        assert t.translate_fragment("private prose", "context", part="参考文献题名") == "译文"
        assert t.translate_fragment("private prose", "context", part="参考文献题名") == "译文"
        job.relayout_only = True
        with pytest.raises(DocumentError, match="缓存编号"):
            t.translate_fragment("different text", "context")
        rows = [json.loads(r[0]) for r in store.db.execute("SELECT details FROM request_events ORDER BY updated")]
        assert {r["state"] for r in rows} >= {"queued","started","waiting","success","cached"}
        assert t.calls == t.hits == 1
        assert "private prose" not in json.dumps(rows) and "fake-secret" not in json.dumps(rows)
        assert all(e["stage"] == "翻译中" for e in events if e["type"] == "activity")
        assert any(r["state"] == "waiting" and r["elapsed_seconds"] >= 2 for r in rows)
    finally:
        store.close()


def test_estimates_use_work_not_percentage_and_reset_between_stages():
    estimate = WorkEstimate()
    for i in range(4):
        estimate.observe(dict(stage="翻译中", stage_key="translate", work_done=100 * i,
                              work_total=1000, phase_elapsed=2 * i, value=99))
    assert estimate.remaining == pytest.approx(14)
    assert "翻译约" in estimate.display()[0]
    estimate.observe(dict(stage="保存中", stage_key="save", value=99))
    assert estimate.display()[0] == "估算中"


def test_pause_clock_excludes_blocking_request_pause(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("linguafloat.document_metrics.time.monotonic", lambda: now[0])
    control = JobControl()
    log = PerformanceLog(control, lambda _: None)
    log(dict(type="progress", stage="翻译中"))
    now[0] += 4
    control.paused.set()
    now[0] += 90
    control.paused.set()
    assert log.summary()["active_seconds"] == 4
    control.paused.clear()
    now[0] += 3
    log(dict(type="progress", stage="保存中"))
    assert log.summary()["phases"]["翻译中"] == 7
    assert log.summary()["paused_seconds"] == 90


def test_pending_usage_is_not_a_zero_cost():
    info = dict(input_tokens=0, output_tokens=0, usage_state="waiting")
    assert billing_display(info) == ("待返回", "待返回", "未设置单价", "未设置单价")
    info.update(usage_state="finished", requests=0, cache_hits=20)
    assert billing_display(info)[2] == "$0.000000"


def test_concurrent_dedup_counts_once_and_preserves_pause(tmp_path):
    job = job_for(tmp_path / "source.pdf")
    store = JobStore(tmp_path / "cache.sqlite")
    store.status(job, "running")
    control = JobControl()
    entered, release = threading.Event(), threading.Event()
    count = []
    def create(**kwargs):
        count.append(1)
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(output_text="结果", usage=dict(input_tokens=120, output_tokens=80))
    translator = DocumentTranslator(job, "fake", store, control, lambda _: None,
                                    SimpleNamespace(responses=SimpleNamespace(create=create)))
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(translator.translate, "same input") for _ in range(4)]
            assert entered.wait(3)
            control.paused.set()
            release.set()
            deadline = time.monotonic() + 3
            while store.get(translator.cache_key(translator.messages("same input"))) is None and time.monotonic() < deadline:
                time.sleep(.01)
            assert not any(f.done() for f in futures)
            control.paused.clear()
            assert [f.result(timeout=3) for f in futures] == ["结果"] * 4
        assert len(count) == translator.calls == 1
        assert translator.hits == 3
        assert translator.token_usage["input_tokens"] == 120
        assert translator.token_usage["unreported_requests"] == 0
    finally:
        release.set()
        control.paused.clear()
        store.close()


def test_two_distinct_requests_overlap_and_third_is_bounded(tmp_path):
    job = job_for(tmp_path / "source.pdf")
    store = JobStore(tmp_path / "cache.sqlite")
    lock = threading.Lock()
    inflight = [0, 0]
    def create(**kwargs):
        with lock:
            inflight[0] += 1
            inflight[1] = max(inflight[0], inflight[1])
        time.sleep(1.3)
        with lock:
            inflight[0] -= 1
        return SimpleNamespace(output_text=kwargs["input"][-1]["content"], usage=dict(input_tokens=1, output_tokens=1))
    translator = DocumentTranslator(job, "fake", store, JobControl(), lambda _: None,
                                    SimpleNamespace(responses=SimpleNamespace(create=create)))
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            assert list(pool.map(translator.translate, ["one", "two", "three"])) == ["one", "two", "three"]
        assert inflight[1] == 2
        assert translator.calls == 3 and translator.token_usage["input_tokens"] == 3
        assert translator.gate.inflight == 0
    finally:
        store.close()


def test_rate_budget_survives_worker_restart_and_cancel_stops_wait(tmp_path):
    store = JobStore(tmp_path / "cache.sqlite")
    provider = dict(id="a", base_url="https://example.com", model="m", document_rpm=1)
    control = JobControl()
    first = RequestGate(provider, store, control, lambda _: None)
    identity = first.acquire([dict(content="x")])
    first.release(identity, 5)
    waited = []
    def wait(seconds):
        waited.append(seconds)
        control.cancelled.set()
    second = RequestGate(provider, store, control, wait)
    try:
        with pytest.raises(Cancelled):
            second.acquire([dict(content="y")])
        assert waited and second.inflight == 0
        assert store.db.execute("SELECT COUNT(*) FROM request_quota").fetchone()[0] == 1
    finally:
        store.close()


def test_retry_after_and_tpm_guard(tmp_path, monkeypatch):
    exc = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "7"}))
    assert retry_delay(exc, 0) == 7
    store = JobStore(tmp_path / "cache.sqlite")
    gate = RequestGate(dict(document_tpm=50), store, JobControl(), lambda _: None)
    try:
        with pytest.raises(DocumentError, match="TPM"):
            gate.acquire([dict(content="x")])
        gate.backoff(7)
        assert gate.limit == 1
        for _ in range(3):
            gate.success()
        assert gate.limit == 1
        monkeypatch.setattr("linguafloat.document_scheduler.time.monotonic", lambda: gate.cooldown + 1)
        for _ in range(3):
            gate.success()
        assert gate.limit == 2
    finally:
        store.close()


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("cancel", [False, True])
def test_six_requests_share_gate_preserve_order_and_pause(tmp_path, api_mode, cancel):
    job = job_for(tmp_path / "source.pdf")
    job.provider.update(document_concurrency=6, api_mode=api_mode)
    store = JobStore(tmp_path / "cache.sqlite")
    store.status(job, "running")
    control = JobControl()
    release, six_entered = threading.Event(), threading.Event()
    lock = threading.Lock()
    started, finished = [], []
    active = peak = 0

    def create(**kwargs):
        nonlocal active, peak
        text = kwargs.get("input", kwargs.get("messages"))[-1]["content"]
        with lock:
            started.append(text)
            active += 1
            peak = max(peak, active)
            if len(started) == 6:
                six_entered.set()
        assert release.wait(12)
        time.sleep((6 - int(text)) * .015 if int(text) < 6 else 0)
        with lock:
            finished.append(text)
            active -= 1
        return SimpleNamespace(output_text=text,
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=dict(input_tokens=10, output_tokens=5, prompt_tokens=10, completion_tokens=5))

    client = SimpleNamespace(responses=SimpleNamespace(create=create),
                             chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    translator = DocumentTranslator(job, "fake", store, control, lambda _: None, client)
    inputs = [str(i) for i in range(9)] + ["0"] * 3
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(translator.translate, text) for text in inputs]
            try:
                assert six_entered.wait(9), started
                control.paused.set()
                release.set()
                time.sleep(.25)
                assert len(started) == 6
                assert not any(f.done() for f in futures)
                assert peak == 6
                if cancel:
                    control.cancelled.set()
                    for future in futures:
                        with pytest.raises(Cancelled):
                            future.result(timeout=4)
                    assert len(started) == 6
                else:
                    control.paused.clear()
                    assert [f.result(timeout=10) for f in futures] == inputs
                    assert len(started) == 9
                    assert translator.hits == 3
                assert translator.calls == len(started)
                assert translator.token_usage["input_tokens"] == len(started) * 10
                assert translator.token_usage["output_tokens"] == len(started) * 5
                assert translator.token_usage["unreported_requests"] == 0
                assert translator.gate.inflight == 0
                assert translator.inflight == {}
                assert len(finished) == len(started)
            finally:
                release.set()
                control.cancelled.set()
                control.paused.clear()
    finally:
        store.close()


def test_six_way_backoff_recovers_one_step_at_a_time(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("linguafloat.document_scheduler.time.monotonic", lambda: clock[0])
    store = JobStore(tmp_path / "quota.sqlite")
    try:
        gate = RequestGate(dict(document_concurrency=6), store, JobControl(), lambda _: None)
        gate.backoff(7)
        for _ in range(30):
            gate.success()
        assert gate.limit == 1
        clock[0] += 8
        for expected in range(2, 7):
            gate.success()
            gate.success()
            assert gate.limit == expected - 1
            gate.success()
            assert gate.limit == expected
        for _ in range(20):
            gate.success()
        assert gate.limit == 6
        gate.backoff(2)
        assert gate.limit == 1 and gate.successes == 0
    finally:
        store.close()
