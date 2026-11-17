from types import SimpleNamespace as NS

import pytest

from linguafloat.document_context import scientific_quantities
from linguafloat.document_scientific import _formula_fragment_problem, translate_formula_segments


@pytest.mark.parametrize('source,output', [
    ('supplemented with 0.2% Try and reduced by 96.37%', '补充0.2%的Try，降低了96.37%'),
    ('at -6.83 and 2.0 MPa', '数值为−6.83，压力为2.0 MPa'),
    ('5.0% and 1e-3', '比例5.00％，数值0.001'),
])
def test_han_adjacency_does_not_hide_scientific_values(source, output):
    assert scientific_quantities(source) == scientific_quantities(output)
    assert not _formula_fragment_problem(source, output, [])


@pytest.mark.parametrize('output', ['比例0.3%', '比例2%', '比例−0.2%', '比例0.2', '比例0.2%与0.2%'])
def test_changed_scientific_values_still_rejected(output):
    assert _formula_fragment_problem('ratio 0.2%', output, [])


def test_style_only_recovery_translates_prose_without_changing_style_or_number():
    class Translator:
        retries = 0
        store = None
        location = 'local-test'
        def messages(self, text):
            return [{'role': 'system', 'content': ''}, {'role': 'user', 'content': text}]
        def request(self, messages, **kwargs):
            import json
            packet = json.loads(messages[-1]['content'])
            return json.dumps([dict(id=r['id'], output=r['input'].replace('Activation of ', '活化：'))
                               for r in packet['fragments']])
    source = "2.2. Activation of <style id='1'>L. plantarum</style>DX7"
    ti = NS(unicode=source, placeholders=[])
    result = translate_formula_segments(ti, Translator())
    assert result != source
    assert "<style id='1'>L. plantarum</style>" in result
    assert result.startswith('2.2.') and result.endswith('DX7')


def test_decimal_prefix_cannot_move_away_from_formula():
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    from linguafloat.document_scientific import check_formula_tokens
    source = "at 54.</style>{v1}<style id='2'>mL"
    ti = NS(unicode=source, placeholders=[FormulaPlaceholder(1, None, '{v1}', '')])
    assert check_formula_tokens(ti, "浓度54.</style>{v1}<style id='2'>mL")
    assert not check_formula_tokens(ti, "浓度54</style>{v1}<style id='2'>mL")
    assert not check_formula_tokens(ti, "浓度45.</style>{v1}<style id='2'>mL")


def test_integer_recovery_does_not_change_reading_or_identifier_digits():
    assert _formula_fragment_problem('DX7 for 48 h at 517 nm', 'DX8培养48 h，在517 nm', [])
    assert not _formula_fragment_problem('DX7 for 48 h at 517 nm', 'DX7培养48 h，在517 nm', [])
