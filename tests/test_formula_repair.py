import json
from types import SimpleNamespace as NS

import pytest
from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder

from linguafloat.document_core import Cancelled, DocumentError, JobControl, JobStore
from linguafloat.document_scientific import translate_formula_segments


def formula_input(text="Since {v1} within {v2}"):
    return NS(unicode=text, placeholders=[FormulaPlaceholder(1, None, "{v1}", ""),
                                           FormulaPlaceholder(2, None, "{v2}", "")])


@pytest.fixture
def translator(tmp_path):
    store = JobStore(tmp_path / "cache.sqlite3")
    t = NS(store=store, retries=2, control=JobControl(), job=NS(relayout_only=False), location="Page 3",
           cache_key=lambda m: json.dumps(m, ensure_ascii=False),
           messages=lambda text: [dict(content="Translate"), dict(content=text)])
    yield t
    store.close()


@pytest.mark.parametrize("invalid", ["", "由于{v1}", "由于{v999}", None])
def test_repairs_only_bad_fragment_and_keeps_original_formulas(translator, invalid):
    calls = []
    def request(messages, **kwargs):
        data = json.loads(messages[1]["content"])
        calls.append((data, kwargs))
        if "fragments" in data:
            return json.dumps([dict(id=0, output=invalid), dict(id=2, output="位于")])
        assert data["fragment"] == "Since "
        return "由于"
    translator.request = request
    assert translate_formula_segments(formula_input(), translator) == "由于{v1}位于{v2}"
    assert len(calls) == 2
    assert calls[0][1]["budget"] is calls[1][1]["budget"]
    assert calls[1][1]["budget"][0] == 1
    assert any(json.loads(value) == [dict(id=0, output="由于"), dict(id=2, output="位于")]
               for (value,) in translator.store.db.execute("SELECT value FROM cache") if value.startswith("["))


def test_persistent_bad_reply_stops_at_shared_budget(translator):
    calls = []
    def request(messages, **kwargs):
        calls.append(kwargs["budget"])
        if "fragments" in json.loads(messages[1]["content"]):
            return '[{"id":0,"output":"bad {v1}"},{"id":2,"output":"位于"}]'
        return "still bad {v1}"
    translator.request = request
    with pytest.raises(DocumentError, match="公式标记"):
        translate_formula_segments(formula_input(), translator)
    assert len(calls) == 3 and all(b is calls[0] for b in calls)
    assert calls[0][0] == 0


def test_transport_and_content_failures_share_budget(translator):
    calls = []
    def request(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            kwargs["budget"][0] = 0
            return '[{"id":0,"output":""},{"id":2,"output":"位于"}]'
        pytest.fail("No content retry may follow exhausted transport budget")
    translator.request = request
    with pytest.raises(DocumentError, match="空译文"):
        translate_formula_segments(formula_input(), translator)
    assert len(calls) == 1


def test_cancellation_before_repair_stops_new_requests(translator):
    calls = []
    def request(messages, **kwargs):
        calls.append(messages)
        translator.control.cancelled.set()
        return '[{"id":0,"output":""},{"id":2,"output":"位于"}]'
    translator.request = request
    with pytest.raises(Cancelled):
        translate_formula_segments(formula_input(), translator)
    assert len(calls) == 1


def test_relayout_never_attempts_content_retry(translator):
    translator.job.relayout_only = True
    calls = []
    def request(messages, **kwargs):
        calls.append(messages)
        return '[{"id":0,"output":""},{"id":2,"output":"位于"}]'
    translator.request = request
    with pytest.raises(DocumentError, match="仅重新排版"):
        translate_formula_segments(formula_input(), translator)
    assert len(calls) == 1


def test_tag_only_pieces_do_not_become_translation_targets(translator):
    translator.request = lambda *a, **k: pytest.fail("Only style tags and formulas, no prose")
    original = "<b0>{v1}</b0> {v2}"
    assert translate_formula_segments(formula_input(original), translator) == original


def test_lost_style_tags_are_repaired_without_reordering(translator):
    calls = []
    def request(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return '[{"id":0,"output":"由于"}]'
        return "<b0>由于</b0>"
    translator.request = request
    assert translate_formula_segments(formula_input("<b0>Since</b0>{v1}"), translator) == "<b0>由于</b0>{v1}"
    assert len(calls) == 2


@pytest.mark.parametrize("response,reason", [
    ('[]', "数量"), ('[{"id":0,"output":"a"},{"id":0,"output":"b"}]', "编号"),
    ('[{"id":0,"output":"a"},{"id":8,"output":"b"}]', "编号"),
    ('[{"id":0,"output":"a"},{"id":2,"output":""}]', "空译文"),
    ('[{"id":0,"output":"a"},{"id":2,"output":"{v1}"}]', "公式标记"),
    ('not JSON', "JSON"),
])
def test_specific_errors_and_unrelated_cache_preserved(translator, response, reason):
    translator.retries = 0
    translator.store.put("unrelated", "保留")
    translator.request = lambda *a, **kw: response
    with pytest.raises(DocumentError, match=reason):
        translate_formula_segments(formula_input(), translator)
    assert translator.store.get("unrelated") == "保留"


def test_bad_identity_retries_batch_without_guessing_map(translator):
    calls = []
    def request(messages, **kwargs):
        calls.append(json.loads(messages[1]["content"]))
        if len(calls) == 1:
            return '[{"id":1,"output":"位于"},{"id":3,"output":"由于"}]'
        return '[{"id":2,"output":"位于"},{"id":0,"output":"由于"}]'
    translator.request = request
    assert translate_formula_segments(formula_input(), translator) == "由于{v1}位于{v2}"
    assert len(calls) == 2 and all("fragments" in c for c in calls)


def test_real_adapter_recovery_cache_survives_relayout(tmp_path):
    from linguafloat.document_core import DocumentTranslator
    from test_document_core import job_for
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return NS(output_text=('[{"id":0,"output":"由于{v1}"},{"id":2,"output":"位于"}]'
                               if len(calls) == 1 else "由于"))
    job = job_for(tmp_path / "paper.pdf")
    job.provider["retries"] = 2
    store = JobStore(tmp_path / "real-adapter.sqlite")
    try:
        t = DocumentTranslator(job, "offline", store, JobControl(), lambda _: None,
                              NS(responses=NS(create=create)))
        assert translate_formula_segments(formula_input(), t) == "由于{v1}位于{v2}"
        assert t.calls == 2
        job.relayout_only = True
        assert translate_formula_segments(formula_input(), t) == "由于{v1}位于{v2}"
        assert t.calls == 2 and t.hits == 1 and len(calls) == 2
        records = [json.loads(row[0]) for row in store.db.execute("SELECT details FROM request_events")]
        assert sum(e["state"] == "success" for e in records) == 2
    finally:
        store.close()


def test_pause_holds_recovery_until_resume(translator):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    received = threading.Event()
    second = threading.Event()
    def request(messages, **kwargs):
        if "fragments" in json.loads(messages[1]["content"]):
            translator.control.paused.set()
            received.set()
            return '[{"id":0,"output":""},{"id":2,"output":"位于"}]'
        second.set()
        return "由于"
    translator.request = request
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(translate_formula_segments, formula_input(), translator)
        try:
            assert received.wait(2)
            assert not second.wait(.15)
            assert not future.done()
        finally:
            translator.control.paused.clear()
        assert future.result(timeout=2) == "由于{v1}位于{v2}"


def test_changed_scientific_quantity_requires_correction(translator):
    replies = iter(['[{"id":0,"output":"误差为 50%"}]', "误差为 5.0%"])
    translator.request = lambda *a, **kw: next(replies)
    assert translate_formula_segments(formula_input("Error is 5.0% {v1}"), translator) == "误差为 5.0%{v1}"
