"""Actual Walnut evidence: dates, contacts and body font size across assembly."""
from pathlib import Path
import glob
import json
from zipfile import ZipFile

import pymupdf as f
import pytest

from linguafloat.document_publication import (
    date_rows,repair_dates,repair_author_identifiers,draw_author_identifiers,
    suppress_date_paragraphs,prune_split_identifier_suffixes,split_identifier_links)


ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path.home()/'Downloads'/'Walnut Pellicle Polyphenols Trigger Lactiplantibacillus plantarum DX7 to Produce Indole-3-Lactic Acid.pdf'


@pytest.mark.skipif(not SOURCE.is_file(),reason='Walnut source PDF unavailable')
def test_walnut_dates_and_identifiers_on_direct_text_page(tmp_path):
    prepared=glob.glob(str(ROOT/'.scratch'/'quality-real'/'run'/'85fa*-prepared.pdf'))
    if not prepared:pytest.skip('cached direct-text source unavailable')
    with f.open(SOURCE) as source,f.open(prepared[0]) as output:
        dates=date_rows(source[0]);assert [d['name'] for d in dates]==['Received','Revised','Accepted','Published']
        before=output[9].get_text()
        assert repair_dates(source[0],output[0],('simsun','times'))
        contacts=repair_author_identifiers(source[9],output[9],('simsun','times'))
        assert len(contacts)==4
        draw_author_identifiers(output[9],contacts,('simsun','times'))
        for row in ('收稿日期：2025年10月28日','修订日期：2026年2月20日',
                    '接受日期：2026年2月20日','发表日期：2026年3月2日'):
            assert row in output[0].get_text()
        for entry in contacts:
            assert output[9].get_text().count(entry['uri'].removeprefix('https://'))==1
        for author in ('Qing Wang','Xinyu Yuan','Qiqi Feng','Zhu Qiao','Tao Wang','Hongyu Pan','Yongliang Zhuang'):
            assert author in before and author in output[9].get_text()
        output.save(tmp_path/'repaired.pdf',garbage=4,deflate=True)
    with f.open(tmp_path/'repaired.pdf') as reopened:
        assert len(reopened)==11
        for entry in contacts:
            assert reopened[9].get_text().count(entry['uri'].removeprefix('https://'))==1


@pytest.mark.skipif(not SOURCE.is_file(),reason='Walnut source PDF unavailable')
def test_cached_date_and_orcid_tails_removed_before_pdf_creation():
    snapshots=list((ROOT/'.scratch'/'quality-real'/'run'/'layout-cache').glob('*.zip'))
    if not snapshots:pytest.skip('cached translated IL unavailable')
    from babeldoc.format.pdf.document_il import il_version_1
    from linguafloat.document_artifacts import _decoder
    with ZipFile(snapshots[0]) as archive:
        document=_decoder(il_version_1.Document)(json.loads(archive.read('translated.json')))
    with f.open(SOURCE) as source:
        first=next(p for p in document.page if p.page_number==0)
        authors=next(p for p in document.page if p.page_number==9)
        assert suppress_date_paragraphs(first,date_rows(source[0]),
                                        (source[0].rect.width,source[0].rect.height))==1
        assert all('收稿日期：' not in (p.unicode or '') for p in first.pdf_paragraph)
        assert prune_split_identifier_suffixes(authors,
                     split_identifier_links(source[9]))==3
        text=''.join(p.unicode or '' for p in authors.pdf_paragraph)
        assert all(s not in text for s in ('6481-3916','6608</style>','0344-5545'))
        assert 'zyj0048@163.com' in text


def test_side_legend_keeps_original_graphic_ink():
    from linguafloat.document_graphic_layout import add_side_legend
    with f.open() as original,f.open() as output:
        source=original.new_page(width=607,height=800)
        target=output.new_page(width=607,height=800)
        for page in (source,target):
            page.draw_line((50,70),(500,600),color=(.1,.1,.1),width=1)
            page.insert_text((200,250),'Cellular community',fontsize=5)
        before=target.get_pixmap(clip=f.Rect(0,0,607,800)).samples
        records=[dict(status='needs_review',kind='figure',translation='细胞群落',
                      source_line='Cellular community',
                      reason='本组未通过统一排版检查。可用区域放不下完整译文。',
                      box=[200,245,260,255])]
        assert len(add_side_legend(target,source,records,('simsun','times')))==1
        assert target.rect.width>source.rect.width
        assert target.get_pixmap(clip=f.Rect(0,0,607,800)).samples==before
        assert records[0]['status']=='legend'
        assert '#1  Cellular community：细胞群落' in target.get_text()


def test_publication_pattern_rejects_incomplete_dates():
    with f.open() as doc:
        p=doc.new_page()
        p.insert_text((50,700),'Received: October 28, 2025',fontsize=9)
        p.insert_text((50,711),'Revised: unknown',fontsize=9)
        assert date_rows(p)==[]
