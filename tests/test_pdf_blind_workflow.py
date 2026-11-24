import json
import sys
from pathlib import Path

import pymupdf as pdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pdf_blind_workflow import main


def fixture(root, stem, drop=False, header='Original 1 | Translation 1/1', retained=False):
    originals = root / 'papers'
    originals.mkdir(exist_ok=True)
    outputs = root / 'outputs' / stem
    outputs.mkdir(parents=True)
    with pdf.open() as a, pdf.open() as b, pdf.open() as pair:
        for doc, text in ((a, 'Results'), (b, '结果')):
            page = doc.new_page(width=200, height=200)
            page.insert_text((20, 40), text, fontname='china-s' if doc is b else 'helv')
            if doc is a or not drop:
                page.insert_text((20, 70), 'lab@example.org')
            if doc is b and retained:
                page.insert_text((20, 90), '未翻译 P1-U1', fontname='china-s')
        a.save(originals / (stem + '.pdf'))
        b.save(outputs / (stem + '.译文.pdf'))
        sheet = pair.new_page(width=420, height=226)
        sheet.insert_text((12, 17), header, fontsize=9)
        sheet.show_pdf_page(pdf.Rect(0, 26, 200, 226), a, 0)
        sheet.show_pdf_page(pdf.Rect(220, 26, 420, 226), b, 0)
        pair.save(outputs / (stem + '.双语.pdf'))
    return outputs


def run(root, name, *extra):
    dest = root / name
    code = main([str(root/'papers'), '--outputs', str(root/'outputs'), '--out', str(dest), *extra])
    return code, json.loads((dest/'run.json').read_text('utf8'))['cases']


def test_blind_run_finds_bad_without_locations_then_retests(tmp_path):
    fixture(tmp_path, 'A', True)
    fixture(tmp_path, 'B')
    code, cases = run(tmp_path, 'first')
    assert code == 1
    assert [c['status'] for c in cases] == ['fail', 'pass']
    assert ('missing-visible-email', 1) in {(f['rule'], f['page']) for f in cases[0]['findings']}
    assert (tmp_path/'first/本轮日志.md').is_file()
    assert '无待人工核验项' in (tmp_path/'first/待人工核验/请看这里.md').read_text('utf8')
    assert run(tmp_path, 'second', '--match', 'B')[0] == 0


def test_missing_output_or_duplicate_choice_never_pass(tmp_path):
    import shutil
    pair = fixture(tmp_path, 'A')
    shutil.copytree(pair, tmp_path/'outputs/old')
    fixture(tmp_path, 'B').joinpath('B.双语.pdf').unlink()
    code, cases = run(tmp_path, 'run')
    assert code and [c['status'] for c in cases] == ['unverified', 'unverified']


def test_cumulative_report_keeps_all_papers_on_partial_recheck(tmp_path):
    from pdf_blind_ledger import TITLE, publish
    fixture(tmp_path,'A',True);fixture(tmp_path,'B');run(tmp_path,'both')
    (tmp_path/'outputs/B/B.双语.pdf').unlink();run(tmp_path,'repeat','--match','B')
    text=(tmp_path/TITLE).read_text('utf8');rows=[s for s in text.splitlines() if s.startswith(('| [A]','| B（'))]
    assert len(rows)==2 and '未通过' in rows[0] and '无本次成品' in rows[1] and '未验证' in rows[1] and '本次规则未报警' in rows[1]
    run(tmp_path,'experiment','--diagnostic');assert (tmp_path/TITLE).read_text('utf8')==text
    initial=json.loads((tmp_path/'both/run.json').read_text('utf8'));initial['cases'][0]['requests']=3
    for _ in range(2): publish(tmp_path,tmp_path/'both',initial)
    assert '**3 次**' in (tmp_path/TITLE).read_text('utf8') and '无本次成品' in (tmp_path/TITLE).read_text('utf8')


def test_missing_continuation_and_existing_run(tmp_path):
    fixture(tmp_path, 'A', header='Original 1 | Translation 1/2')
    code, cases = run(tmp_path, 'run')
    assert code and 'page-map' in {i['rule'] for i in cases[0]['findings']}
    with pytest.raises((FileExistsError, SystemExit)):
        run(tmp_path, 'run')


def test_reading_copy_without_job_log_preserves_visible_hold(tmp_path):
    fixture(tmp_path, 'A', retained=True)
    code, cases = run(tmp_path, 'run')
    assert code and cases[0]['status']=='hold'


def test_compare_same_source_never_calls_a_missing_result_fixed(tmp_path):
    folder = fixture(tmp_path, 'A', True)
    run(tmp_path, 'old')
    (folder/'A.双语.pdf').unlink()
    _, cases = run(tmp_path,'new','--previous',str(tmp_path/'old/run.json'))
    assert 'difference' not in cases[0]


def test_real_budget_is_shared_across_cases_and_rejects_wrong_endpoint():
    from types import SimpleNamespace
    from threading import Lock
    import httpx
    from pdf_blind_generate import reserve
    url = httpx.URL('https://local.invalid/v1/responses'); args=SimpleNamespace(requests=2,reserve=5000)
    case={'requests':0,'reserved':0}; previous={'requests':1,'reserved':10}
    def send(addr): reserve(httpx.Request('POST',addr,json={'max_output_tokens':64}),url,args,previous,case,Lock())
    with pytest.raises(ValueError): send('https://wrong.invalid/v1/responses')
    send(url)
    with pytest.raises(ValueError): send(url)
    args.requests=4; args.reserve=40
    with pytest.raises(ValueError): send(url)
    assert case['requests']==1


def test_unmarked_prose_is_screened_without_requiring_a_known_phrase():
    from pdf_blind_audit import narrative
    assert narrative('The samples from the second batch showed a lower value than those from the first batch after treatment.')
    assert not narrative('Smith J.; Yang Q.; Lee D. Measurements from the second batch and an overview of the values. Journal of Examples (2020), 12, 100-123. https://doi.org/10.1234/example')
