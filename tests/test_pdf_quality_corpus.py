"""Quick blind-detector checks on immutable, local bad outputs; no API or model required."""
import os
import sys
from pathlib import Path
from collections import Counter
import pytest
import pymupdf as f

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from check_pdf_sample import compare

CORPUS=Path(os.environ.get('LINGUAFLOAT_QUALITY_CORPUS',r'D:\翻译测试资料\测试结果\20261123-生物首轮'))

@pytest.mark.skipif(not CORPUS.exists() and 'LINGUAFLOAT_QUALITY_CORPUS' not in os.environ,reason='Local licensed PDFs not installed')
@pytest.mark.parametrize('sample,expected',[('01',{'unchanged-short-cell'}),('19',{'numbered-row-not-separate'}),('22',{'missing-visible-email','missing-link-target'})])
def test_frozen_defect_is_detected_without_coordinates(sample,expected):
    case=CORPUS/sample
    original,=case.glob('*.pdf');failed,=case.rglob('*.译文.pdf')
    with f.open(original) as src,f.open(failed) as out:
        issues=compare(src[0],out[0]);assert expected <= {entry['rule'] for entry in issues}
        if sample=='01':assert {'Seeds','Leaves'} <= {entry['evidence'] for entry in issues}
        if sample=='22':assert Counter(i['rule'] for i in issues)['missing-link-target']==2
