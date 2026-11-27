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
    assert calls[1][1]["budget"][0] == 2  # no transport retry used
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
    assert calls[0][0] == 2


def test_content_repair_is_independent_of_transport_budget(translator):
    calls = []
    def request(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            kwargs["budget"][0] = 0
            return '[{"id":0,"output":""},{"id":2,"output":"位于"}]'
        assert kwargs['budget'] == [0]
        return '由于'
    translator.request = request
    assert translate_formula_segments(formula_input(), translator) == '由于{v1}位于{v2}'
    assert len(calls) == 2


def test_zero_network_retries_records_numeric_difference_on_exhaustion(translator):
    from linguafloat.document_failures import diagnostic
    translator.retries = 0
    calls=[]
    def request(messages, **kw):
        calls.append(kw['budget'][0]);return '[{"id":0,"output":"误差 50%"}]' if len(calls)==1 else '误差 50%'
    translator.request=request
    with pytest.raises(DocumentError) as exc:
        translate_formula_segments(formula_input('Error is 5.0% {v1}'), translator)
    assert calls == [0,0,0]
    reason=diagnostic(exc.value,'翻译')['reason']
    assert '5%' in reason and '50%' in reason and '片段 0' in reason


def test_repeated_numeric_contamination_uses_isolated_fragment(translator):
    from linguafloat.document_context import request_source

    original = formula_input("The lower layer {v1} showed 3 times lower resistance.")
    calls = []

    def request(messages, **kwargs):
        packet = json.loads(messages[1]['content'])
        calls.append(packet)
        if 'isolated_fragment' in packet:
            assert packet == {'isolated_fragment': ' showed [[Q0]] times lower resistance.'}
            assert request_source(messages) == ''
            return '的电阻降低了 [[Q0]] 倍。'
        if 'fragments' in packet:
            return json.dumps([dict(id=0, output='下层'), dict(id=2, output='1. 电阻降低了 3 倍。')])
        return '1. 电阻降低了 3 倍。'

    translator.request = request
    assert translate_formula_segments(original, translator) == '下层{v1}的电阻降低了 3 倍。'
    assert len(calls) == 3


def test_isolated_numeric_retry_rejects_missing_reordered_and_duplicate_values():
    from linguafloat.document_scientific import _lock_repair_numbers, _restore_repair_numbers

    masked, values = _lock_repair_numbers('<style id="1">changed from -0.5 to 87%</style>')
    assert masked == '<style id="1">changed from [[Q0]] to [[Q1]]</style>'
    assert _restore_repair_numbers('<style id="1">从[[Q0]]变为[[Q1]]</style>', values) == (
        '<style id="1">从-0.5变为87%</style>')
    for invalid in ('[[Q0]]', '[[Q1]] [[Q0]]', '[[Q0]] [[Q1]] [[Q1]]', '[[Q0]] [[Q1]] [[Q2]]'):
        assert _restore_repair_numbers(invalid, values) is None


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
    translator.content_repairs = 0
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


def test_real_adapter_recovery_cache_survives_relayout(tmp_path, monkeypatch):
    import httpx
    from openai import BadRequestError
    from linguafloat.document_core import DocumentTranslator
    from test_document_core import job_for
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return NS(output_text="initial body translation")
        if len(calls) == 2:
            raise BadRequestError("The request could not be processed.",
                response=httpx.Response(400, request=httpx.Request("POST", "https://example.com/v1/responses")),
                body={"error": {"type": "invalid_request_error", "code": "invalid_request", "param": ""}})
        return NS(output_text=('[{"id":0,"output":"由于{v1}"},{"id":2,"output":"位于"}]'
                               if len(calls) == 3 else "由于"))
    job = job_for(tmp_path / "paper.pdf")
    job.provider["retries"] = 2
    store = JobStore(tmp_path / "real-adapter.sqlite")
    try:
        t = DocumentTranslator(job, "offline", store, JobControl(), lambda _: None,
                              NS(responses=NS(create=create)))
        monkeypatch.setattr(t, "wait", lambda _: None)
        assert t.request(t.messages("Earlier body paragraph")) == "initial body translation"
        assert translate_formula_segments(formula_input(), t) == "由于{v1}位于{v2}"
        assert t.calls == 3 and calls[1] == calls[2]
        job.relayout_only = True
        assert translate_formula_segments(formula_input(), t) == "由于{v1}位于{v2}"
        assert t.calls == 3 and t.hits == 1 and len(calls) == 4
        records = [json.loads(row[0]) for row in store.db.execute("SELECT details FROM request_events")]
        assert sum(e["state"] == "success" for e in records) == 3
        assert sum(e["state"] == "retry" and e.get("http_status") == 400 for e in records) == 1
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


def test_chiral_prefix_cannot_migrate_from_enzyme_to_id(translator):
    from linguafloat.document_scientific import check_formula_tokens
    t=formula_input('The structure of {v1}galactosidase of AB123 was resolved.')
    t.placeholders[0].formula=NS(pdf_character=[NS(char_unicode='β-D-')],y_offset=0)
    bad='已解析{v1}AB123 的半乳糖苷酶结构。'
    assert not check_formula_tokens(t,bad)
    calls=[]
    def request(messages,**kwargs):
        packet=json.loads(messages[1]['content']);calls.append(packet)
        return '[{"id":0,"output":"已解析"},{"id":2,"output":"AB123 的半乳糖苷酶结构。"}]' if len(calls)==1 else '半乳糖苷酶（AB123）的结构。'
    translator.retries=0;translator.request=request
    assert translate_formula_segments(t,translator)=='已解析{v1}半乳糖苷酶（AB123）的结构。'
    assert len(calls)==2 and calls[-1]['fragment'].startswith('galactosidase')


def test_head_of_split_noun_phrase_survives_chinese_reordering(translator):
    from linguafloat.document_scientific import check_formula_tokens
    ti=formula_input('The crystal structure of the{v1}galactosidase revealed density.');translator.job.target='zh-Hans'
    assert not check_formula_tokens(ti,'该{v1}半乳糖苷酶显示出电子密度。另一种酶的晶体结构不同。')
    assert check_formula_tokens(ti,'该{v1}半乳糖苷酶的晶体结构显示出电子密度。')
    calls=[]
    def request(messages,**kwargs):
        calls.append(json.loads(messages[1]['content']))
        return '[{"id":0,"output":"该"},{"id":2,"output":"半乳糖苷酶显示出电子密度。"}]' if len(calls)==1 else '半乳糖苷酶的晶体结构显示出电子密度。'
    translator.request=request
    assert '酶的晶体结构' in translate_formula_segments(ti,translator) and len(calls)==2


def test_multi_repair_groups_only_rejected_ids_under_one_allowance(translator):
    t=formula_input('Yield 12 {v1} test 24 {v2} signal 36'); calls=[]
    translator.retries=0;translator.content_repairs=1
    def request(messages,**kwargs):
        packet=json.loads(messages[1]['content']);calls.append(packet)
        if len(calls)==1:return '[{"id":0,"output":"产量 12"},{"id":2,"output":"测定 42"},{"id":4,"output":"信号 63"}]'
        assert [r['id'] for r in packet['fragments']]==[2,4]
        return '[{"id":4,"output":"信号 36"},{"id":2,"output":"测定 24"}]'
    translator.request=request
    assert translate_formula_segments(t,translator)=='产量 12{v1}测定 24{v2}信号 36'
    assert len(calls)==2


def test_dense_inline_labels_translated_in_sentences_not_isolated_words(translator):
    labels=[' A2-', ' B4-', ' +1 ']
    t=NS(unicode='The {v1}or{v2}linked labels were compared. The {v3}position was unchanged.',placeholders=[FormulaPlaceholder(i,NS(pdf_character=[NS(char_unicode=g)],y_offset=0),f'{{v{i}}}','') for i,g in enumerate(labels,1)])
    def request(messages,**kw):
        packet=json.loads(messages[1]['content']);assert 'sentences' in packet
        assert packet['sentences'][0]['input']=='The <k1>A2-</k1>or<k2>B4-</k2>linked labels were compared.'
        assert packet['sentences'][0]['slots']==['<k1>A2-</k1>', '<k2>B4-</k2>']
        return json.dumps([dict(id=0,output=['比较了由','或','连接的标识。']),dict(id=1,output=['','位置未变。'])])
    translator.job.target='zh-Hans';translator.request=request
    assert translate_formula_segments(t,translator)=='比较了由{v1}或{v2}连接的标识。{v3}位置未变。'


@pytest.mark.parametrize('gaps', [ ['比较','和'], ['比较','或','在','新增 123'],
                                   ['比较','{v2}','与','无变化。'], ['The','or','and','are stable.'] ])
def test_invalid_label_sentence_does_not_survive_in_cache(translator, gaps):
    from linguafloat.document_scientific import _translate_label_sentences
    t=NS(unicode='The {v1}or{v2}signals in {v3}are stable.',placeholders=[
        FormulaPlaceholder(i,NS(pdf_character=[NS(char_unicode=g)],y_offset=0),f'{{v{i}}}','')
        for i,g in enumerate(['A2','B1','C3'],1)])
    keys=[]
    def request(messages,**kw):
        key=translator.cache_key(messages);keys.append(key)
        bad=json.dumps([dict(id=0,output=gaps)]);translator.store.put(key,bad)
        return bad
    translator.job.target='zh-Hans';translator.request=request
    assert _translate_label_sentences(t,translator,[0]) is None
    assert len(keys)==1 and translator.store.get(keys[0]) is None


def test_validated_repair_is_not_shared_with_raw_responses_or_other_anchors(translator):
    from linguafloat.document_layout import _validated_repair_key
    from babeldoc.format.pdf.document_il.midend.il_translator import RichTextPlaceholder

    ti = formula_input()
    ti.placeholders.append(RichTextPlaceholder(3,None,"<style id='3'>",'</style>'))
    def key(doc='file-A', where='page 1 / 2'):
        return _validated_repair_key(translator, doc, where, ti)
    first = key()
    assert first != translator.cache_key(translator.messages(ti.unicode))
    assert key('file-B') != first and key(where='page 2 / 2') != first
    assert key('') is None and key(where='') is None
    ti.placeholders[0].formula = NS(pdf_character=[NS(char_unicode='=12.3')])
    assert key() != first


def test_copied_sentence_in_fragment_is_repaired_before_reassembly(translator):
    source='The samples were examined under the same light after cooling.'
    ti=formula_input('Signal {v1} '+source)
    translator.job.target='zh-Hans'
    def request(messages, **kw):
        packet=json.loads(messages[1]['content'])
        if 'fragments' in packet:
            return json.dumps([dict(id=0,output='信号'),dict(id=2,output=source)])
        assert packet['fragment'].strip()==source
        return '冷却后在相同光照下检查样品。'
    translator.request=request
    assert translate_formula_segments(ti,translator)=='信号{v1}冷却后在相同光照下检查样品。'


def test_optional_sentence_lookup_must_not_mark_paragraph_failed_on_miss(translator):
    from linguafloat.document_scientific import _translate_label_sentences
    ti=NS(unicode='The {v1}or{v2}signals in {v3}are stable.',placeholders=[
        FormulaPlaceholder(i,NS(pdf_character=[NS(char_unicode=g)]),f'{{v{i}}}','')
        for i,g in enumerate(['A2','B1','C3'],1)])
    translator.job.target='zh-Hans'; translator.job.relayout_only=True
    translator.request=lambda *a,**k: pytest.fail('guarded request would register an untranslated region')
    assert _translate_label_sentences(ti,translator,[0]) is None


def test_decimal_prefix_stays_with_native_fraction_during_repair(translator):
    ti=NS(unicode='Absorption at 1234.{v1}was recorded.',placeholders=[FormulaPlaceholder(
        1,NS(pdf_character=[NS(char_unicode='6 cm−1 (')],y_offset=0),'{v1}','')])
    def request(messages,**kw):
        packet=json.loads(messages[1]['content'])
        assert packet['context']==ti.unicode
        assert packet['fragments'][0]['input']=='Absorption at '
        return json.dumps([dict(id=0,output='在 '),dict(id=2,output='处记录吸收。')])
    translator.request=request
    assert translate_formula_segments(ti,translator)=='在 1234.{v1}处记录吸收。'


def test_isotope_labels_qualify_the_measurement_not_the_sample():
    from linguafloat.document_scientific import attach_measurement_nucleus
    ti=NS(unicode='with{v1}NMR spectrum of AB-2, the signal',placeholders=[
        FormulaPlaceholder(1,NS(pdf_character=[NS(char_unicode=' 13C ')]),'{v1}','')])
    assert attach_measurement_nucleus(ti,'与{v1}AB-2 的 NMR 谱相比')=='与AB-2 的 {v1}NMR 谱相比'
    for other in ('与AB-2 的 {v1}NMR 谱相比','与{v1}AB-3 的 NMR 谱相比','与{v1}AB-2{v2} 的 NMR 谱相比'):
        assert attach_measurement_nucleus(ti,other)==other
