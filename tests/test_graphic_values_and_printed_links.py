import pytest
import pymupdf as fitz

from linguafloat.document_core import DocumentError
from linguafloat.document_graphics import validate_graphic_fragment


@pytest.mark.parametrize('source,wrong', [
    ('Change −5%', '变化5%'), ('Recovery 20%', '回收率20'),
    ('Dose 5 mg', '剂量5 g'), ('p < 0.05', 'p > 0.05'),
    ('Control 1, treatment 2', '对照2，处理1'),
    ('Signal 3e-4', '信号3e+4'), ('p != 0.05', 'p = 0.05'),
    ('Control 1, treatment 2', '甲组2，乙组1'),
    ('Signal μ; dose 5 μg', '信号；剂量5微克'),
])
def test_changed_measurement_is_refused(source, wrong):
    with pytest.raises(DocumentError):
        validate_graphic_fragment(source, wrong)


@pytest.mark.parametrize('source,ok', [
    ('Change -5%', '变化−5％'), ('Dose 5 mg', '剂量5毫克'),
    ('p <= 0.05', 'p ≤ 0.05'), ('Control 1, treatment 2', '处理2，对照1'),
    ('2-aminoadipate', '2-氨基己二酸'),
    ('Signal μ; dose 5 µg', '信号μ；剂量5微克'),
])
def test_equivalent_measurement_survives(source, ok):
    validate_graphic_fragment(source, ok)


def test_printed_url_uses_new_position_and_never_guesses():
    from linguafloat.document_publisher_footer import restore_printed_urls
    with fitz.open() as source, fitz.open() as target:
        a=source.new_page(); b=target.new_page(); url='www.example.org/path'
        a.insert_text((40,40), url); b.insert_text((120,150), url+'way')
        a.insert_link(dict(kind=fitz.LINK_URI, uri='http://'+url, **{'from':a.search_for(url)[0]}))
        a=source.reload_page(a)
        assert restore_printed_urls(a,b)==0
        b.insert_text((120,190),url)
        assert restore_printed_urls(a,b)==1
        b=target.reload_page(b)
        assert restore_printed_urls(a,b)==0
        link,=b.get_links(); assert link['uri']=='http://'+url and link['from'].y0>160
        b.delete_link(link); b.insert_text((160,240),url); b=target.reload_page(b)
        assert restore_printed_urls(a,b)==0
