"""Minimal geometry from the Bifidobacterium and goji v0.19.3 batch."""
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_references import (
    _digital_lines, _join_numbered_publication_lines, _reference_continues, _reference_number,
    boundary_intersects, filter_layout_paragraphs, pdf_boundary, reference_boxes,
    reference_page_only, retain_pdf_tail,
)


@pytest.fixture(autouse=True)
def local_layout(monkeypatch):
    from babeldoc.docvision.doclayout import OnnxModel
    prediction = SimpleNamespace(names={}, boxes=[])
    monkeypatch.setattr(OnnxModel, "from_pretrained",
                        lambda: SimpleNamespace(predict=lambda *a, **k: [prediction]))


def test_heading_and_contiguous_numbers_do_not_protect_body_in_final_pdf(tmp_path):
    from linguafloat.document_pdf import translate_reading_pdf
    from test_document_core import job_for
    from test_document_formats import FakeTranslator

    path = tmp_path / 'numbered-body.pdf'
    with fitz.open() as source:
        page = source.new_page(width=420, height=400)
        page.insert_text((40, 95), 'References')
        page.insert_text((40, 125), '1. Measure the sample and record the temperature.')
        page.insert_text((40, 155), '2. Repeat the measurements at each pressure.')
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
        source.save(path)
    job = job_for(path, pdf_mode='reading', skip_references=True)
    job.reference_boundary = boundary or {}
    translator = FakeTranslator(job)
    sent = []
    translate = translator.translate

    def capture(text):
        sent.append(text)
        return translate(text)

    translator.translate = capture
    outputs, _ = translate_reading_pdf(job, translator, tmp_path, lambda _: None)
    assert any('Measure the sample' in text for text in sent)
    assert any('Repeat the measurements' in text for text in sent)
    with fitz.open(outputs[1]) as dual:
        assert '\u8bd1\u6587' in dual[0].get_text()


@pytest.mark.parametrize('labels', [('', ''), ('[4] ', '[9] ')])
def test_publication_metadata_protects_citations_without_title_or_sequential_labels(labels):
    with fitz.open() as source:
        page = source.new_page(width=430, height=420)
        page.insert_text((40, 90), '12. References')
        page.insert_text((40, 120), labels[0] + 'Smith, A. Phys. Rev. B 106, 014401 (2022).')
        page.insert_text((40, 145), labels[1] + 'B. Jones, Nat. Phys. 19, 84-90 (2023).')
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
        assert boundary
        assert boundary_intersects(boundary, 0, page.search_for('014401')[0])
        assert boundary_intersects(boundary, 0, page.search_for('84-90')[0])


def test_numbered_prose_on_next_page_before_appendix_is_not_a_reference_continuation():
    with fitz.open() as source:
        page = source.new_page(width=440, height=460)
        page.insert_text((40, 90), 'References')
        page.insert_text((40, 120), '1. Smith, A. Phys. Rev. B 106, 014401 (2022).')
        page.insert_text((40, 145), '2. B. Jones, Nat. Phys. 19, 84-90 (2023).')
        page = source.new_page(width=440, height=460)
        page.insert_text((40, 90), '3. Measure the sample at steady pressure.')
        page.insert_text((40, 120), '4. Repeat the measurements.')
        page.insert_text((40, 200), 'Appendix A', fontname='hebo', fontsize=14)
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
        assert boundary and not reference_boxes(boundary, 1)


def test_numbered_prose_after_confirmed_citations_in_same_column_is_not_protected():
    with fitz.open() as source:
        page = source.new_page(width=440, height=460)
        page.insert_text((40, 90), 'References')
        page.insert_text((40, 120), '1. Smith, A. Phys. Rev. B 106, 014401 (2022).')
        page.insert_text((40, 145), '2. B. Jones, Nat. Phys. 19, 84-90 (2023).')
        page.insert_text((40, 230), '3. Repeat the measurements at each pressure.')
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
        assert boundary_intersects(boundary, 0, page.search_for('014401')[0])
        assert boundary_intersects(boundary, 0, page.search_for('84-90')[0])
        assert not boundary_intersects(boundary, 0, page.search_for('Repeat the measurements')[0])


def _footer(page, number, y=763):
    # The source's running journal line alternates sides and spans the gutter.
    page.insert_text((42.52 if number % 2 == 0 else 256.89, y),
                     "Nature Microbiology | VOL 6 | November 2021 | 1367-1382", fontsize=6)


def _numbered(page, number, x, y, *, fragment=False, citation=True):
    page.insert_text((x, y), f"{number}.", fontsize=7)
    page.insert_text((x + 17, y), "Peters, A. et al. Metabolites of lactic acid bacteria present in fermented" if citation
                     else "Measure the sample and record the temperature.", fontsize=7)
    page.insert_text((x + 17, y + 9), "Candida albicans chromosome" if fragment
                     else "Nature 453, 106-109 (2008)." if citation else "Repeat carefully.", fontsize=7)
    if fragment:
        page.insert_text((x + 17, y + 18), "3. PLoS Genet. 15, e1008145 (2019).", fontsize=7)


def _bifido(*, footer=True, fragment=True, footer_y=763, repeat=True):
    doc = fitz.open()
    for n in range(4):
        page = doc.new_page(width=595.276, height=790.866)
        if footer and (repeat or n == 1):
            _footer(page, n, footer_y)
        if n == 0:
            page.insert_text((42.52, 450), "Methods body beside the references.", fontsize=8)
            page.insert_text((42.52, 470), "Independent experimental details.", fontsize=8)
            page.insert_text((303.6, 405), "References", fontname="hebo", fontsize=9)
            _numbered(page, 1, 303.6, 420)
            _numbered(page, 2, 303.6, 450)
        elif n < 3:
            for offset, (x, y) in enumerate(((42.52, 60.6), (42.52, 105),
                                            (303.6, 60.6), (303.6, 105))):
                _numbered(page, 3 + (n - 1) * 4 + offset, x, y,
                          fragment=fragment and n == 1 and offset == 1)
            if n == 2:
                page.insert_text((303.64, 281.16), "Acknowledgements", fontsize=9, fontname="hebo")
                page.insert_text((303.64, 300), "We thank the participating families.", fontsize=7)
        else:
            page.insert_text((42.52, 65), "Methods", fontname="hebo", fontsize=10)
            page.insert_text((42.52, 90), "Extended data and independent methods.", fontsize=8)
    return doc


@pytest.mark.parametrize("footer,fragment", [(True, False), (False, True), (True, True)])
def test_bifido_continues_in_column_order_until_acknowledgements(footer, fragment):
    with _bifido(footer=footer, fragment=fragment) as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary and boundary["column"] == "right"
        assert set(boundary["page_boxes"]) == {"0", "1", "2"}
        assert not boundary.get("uncertain_end")
        assert boundary["end"]["page"] == 2 and boundary["end"]["column"] == "right"
        assert boundary["end"]["y"] == pytest.approx(271.53, abs=.05)
        assert reference_page_only(boundary, 1)
        assert not reference_page_only(boundary, 2)
        for p in (0, 1, 2):
            assert all(boundary_intersects(boundary, p, box) for box in doc[p].search_for("Peters"))
        if fragment:
            assert boundary_intersects(boundary, 1, doc[1].search_for("3. PLoS")[0])
        assert not boundary_intersects(boundary, 0, doc[0].search_for("Methods body")[0])
        assert not boundary_intersects(boundary, 2, doc[2].search_for("We thank")[0])
        assert reference_boxes(boundary, 3) == []


@pytest.mark.parametrize("footer_y,repeat", [(763, False), (600, True)])
def test_unconfirmed_or_body_gutter_text_remains_conservative(footer_y, repeat):
    with _bifido(fragment=False, footer_y=footer_y, repeat=repeat) as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["uncertain_end"]["page"] == 1
        assert reference_boxes(boundary, 1) == []


def test_continuing_numbers_and_small_font_are_insufficient():
    with _bifido(footer=False, fragment=False) as doc:
        doc.delete_pages(1, 3)
        page = doc.new_page(width=595.276, height=790.866)
        for n in (3, 4):
            _numbered(page, n, 42.52, 60 + 40 * (n - 3), citation=False)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert reference_boxes(boundary, 1) == []
        assert boundary["uncertain_end"]["page"] == 1


@pytest.mark.parametrize("section", ["Appendix A", "Methods"])
def test_following_independent_section_is_not_protected(section):
    with _bifido(footer=False, fragment=False) as doc:
        doc.delete_pages(1, 3)
        page = doc.new_page(width=595.276, height=790.866)
        _numbered(page, 3, 42.52, 60)
        _numbered(page, 4, 42.52, 100)
        page.insert_text((42.52, 200), section, fontsize=10, fontname="hebo")
        page.insert_text((42.52, 225), "Independent following body.", fontsize=7)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert not boundary_intersects(boundary, 1, page.search_for("Independent following")[0])


def _author_year(page, author, x, y, *, valid=True):
    page.insert_text((x, y), f"{author}, A., & Brown, B.", fontsize=6.376)
    page.insert_text((x + 11.96, y + 8), "(2019). Fermented food and microbial metabolites.", fontsize=6.376)
    page.insert_text((x + 11.96, y + 16), "Journal of Functional Foods, 11, 192-202. https://doi.org/" if valid
                     else "Measure samples and compare the results.", fontsize=6.376)
    if valid:
        page.insert_text((x + 11.96, y + 24), "10.1016/example.2019.001", fontsize=6.376)


def _goji(*, valid=True, heading=True, borrow_left=False):
    doc = fitz.open()
    for n in range(3):
        page = doc.new_page(width=595.276, height=793.701)
        page.insert_text((37.59, 40), "H. Xie et al.                                      Food Bioscience 54 (2023)", fontsize=6.376)
        if n == 0:
            page.insert_text((37.59, 630), "Conclusion body remains translatable.", fontsize=7.97)
            page.insert_text((37.59, 650), "Further conclusions and author statement.", fontsize=7.97)
            page.insert_text((306.59, 558), "Appendix A. Supplementary data", fontname="hebo", fontsize=7.97)
            if heading:
                page.insert_text((306.59, 610.53), "References", fontname="hebo", fontsize=7.97)
            _author_year(page, "Bergamo", 306.59, 629.09, valid=valid)
            _author_year(page, "Biagioli", 306.59, 669.09, valid=valid)
            if borrow_left:
                _author_year(page, "Smith", 37.59, 695)
                _author_year(page, "Jones", 37.59, 735)
        else:
            for x, author in ((37.59, "Chan"), (306.59, "Tsuji")):
                if n == 1 and x < 100:
                    page.insert_text((x + 11.96, 59), "Wrapped end of the previous citation.", fontsize=6.376)
                _author_year(page, author, x, 85, valid=valid)
                _author_year(page, "Wang", x, 125, valid=valid)
    return doc


def test_goji_unnumbered_hanging_author_year_bibliography_continues():
    with _goji() as doc:
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary and boundary["page"] == 0 and boundary["column"] == "right"
        assert not boundary.get("uncertain_pages") and not boundary.get("uncertain_end")
        assert boundary["whole_pages"] == [1, 2]
        assert not boundary_intersects(boundary, 0, doc[0].search_for("Conclusion body")[0])
        assert not boundary_intersects(boundary, 0, doc[0].search_for("Appendix A")[0])
        for p, text in ((0, "Bergamo"), (1, "Wrapped end"), (1, "Tsuji"), (2, "Wang")):
            assert all(boundary_intersects(boundary, p, box) for box in doc[p].search_for(text))


@pytest.mark.parametrize("valid,heading,borrow_left", [(False, True, False), (False, True, True), (True, False, False)])
def test_author_dates_or_neighboring_citations_do_not_establish_bibliography(valid, heading, borrow_left):
    with _goji(valid=valid, heading=heading, borrow_left=borrow_left) as doc:
        assert pdf_boundary(doc, JobControl(), lambda _: None) is None


def test_author_year_continuation_requires_complete_citations():
    with _goji() as doc:
        doc.delete_pages(1, 2)
        page = doc.new_page(width=595.276, height=793.701)
        _author_year(page, "Smith", 37.59, 80, valid=False)
        _author_year(page, "Jones", 37.59, 120, valid=False)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary and boundary["uncertain_end"]["page"] == 1
        assert reference_boxes(boundary, 1) == []


def test_author_year_route_requires_digital_font_geometry():
    with _goji() as doc:
        rows = [{k: v for k, v in r.items() if k != "size"} for r in _digital_lines(doc[1])]
        assert not _reference_continues(rows, None, 298, author_year=True)


def test_bifido_filter_and_source_pixels_preserve_only_bibliography():
    from babeldoc.format.pdf.document_il import Box, Cropbox, Page, PdfParagraph
    with _bifido() as source, fitz.open() as rendered:
        boundary = pdf_boundary(source, JobControl(), lambda _: None)
        for p in (1, 2):
            page = source[p]
            paragraphs = []
            for text in ("Peters", "3. PLoS", "Acknowledgements", "We thank"):
                for box in page.search_for(text):
                    paragraphs.append(PdfParagraph(box=Box(box.x0, page.rect.height-box.y1,
                        box.x1, page.rect.height-box.y0), unicode=text))
            il_page = Page(page_number=p, cropbox=Cropbox(Box(0, 0, page.rect.width, page.rect.height)),
                           pdf_paragraph=paragraphs)
            filter_layout_paragraphs(il_page, boundary)
            assert [r.unicode for r in il_page.pdf_paragraph] == ([] if p == 1 else ["Acknowledgements", "We thank"])
            target = rendered.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_text((303.64, 300), "Translated acknowledgements.", fontsize=7)
            retain_pdf_tail(target, page, dict(boxes=reference_boxes(boundary, p)))
            for box in reference_boxes(boundary, p):
                clip = fitz.Rect(box) + (1, 1, -1, -1)
                assert target.get_pixmap(clip=clip).samples == page.get_pixmap(clip=clip).samples
            if p == 2:
                assert "Translated acknowledgements" in target.get_text()


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("raised", [False, True])
def test_hanging_numbered_author_is_not_swallowed_as_publication_tail(wrapped, raised):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((40, 60), "3. Smith, A. A scientific monograph.", fontsize=7)
        page.insert_text((55, 69), "Cambridge University Press, 2018.", fontsize=7)
        citation = "Jones, B. A new study." + ("" if wrapped else " Nature 8, 10-20 (2020).")
        if raised:
            page.insert_text((55, 78), "4", fontsize=5)
            page.insert_text((58, 78), citation, fontsize=7)
        else:
            page.insert_text((55, 78), "4. " + citation, fontsize=7)
        if wrapped:
            page.insert_text((55, 87), "Nature 8, 10-20 (2020).", fontsize=7)
        page.insert_text((40, 110), "5. Smith, A. Last study. Nature 9, 11-21 (2022).", fontsize=7)
        rows = _join_numbered_publication_lines(_digital_lines(page), None)
        assert [_reference_number(r) for r in rows if _reference_number(r) is not None] == [3, 4, 5]
        assert any("Jones" in r["text"] and _reference_number(r) == 4 for r in rows)


def _ocr_rows(monkeypatch):
    from linguafloat import document_pdf

    def recognize(self, page, *, positioned):
        assert positioned
        return [dict(text=r["text"], box=r["box"], horizontal=r["horizontal"], confidence=.99)
                for r in _digital_lines(page)]

    monkeypatch.setattr(document_pdf, "needs_page_ocr", lambda page, text: True)
    monkeypatch.setattr(document_pdf.DocumentOcr, "recognize", recognize)


@pytest.mark.parametrize("scanned,label", [(s, label) for label in ("{n}.", "[{n}]", "({n})")
                                          for s in (False, True)] + [(False, "raised")])
def test_ieee_bare_year_continues_with_original_citation_labels(monkeypatch, scanned, label):
    if scanned:
        _ocr_rows(monkeypatch)
    with _bifido(footer=False, fragment=False) as doc:
        doc.delete_pages(1, 3)
        page = doc.new_page(width=595.276, height=790.866)
        for n, y in ((3, 60), (4, 100)):
            citation = "J. Smith, A study, IEEE Trans. Control, vol. 8, pp. 10-20, 2020."
            if label == "raised":
                page.insert_text((40, y), str(n), fontsize=5)
                page.insert_text((44, y), citation, fontsize=8)
            else:
                page.insert_text((40, y), label.format(n=n) + " " + citation, fontsize=7)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary and not boundary.get("uncertain_end")
        assert reference_page_only(boundary, 1)
        assert all(boundary_intersects(boundary, 1, box) for box in page.search_for("J. Smith"))


@pytest.mark.parametrize("scanned", [False, True])
@pytest.mark.parametrize("dated", [True, False])
def test_journal_with_volume_issue_and_article_number_continues(monkeypatch, scanned, dated):
    if scanned:
        _ocr_rows(monkeypatch)
    with _bifido(footer=False, fragment=False) as doc:
        doc.delete_pages(1, 3)
        page = doc.new_page(width=595.276, height=790.866)
        for n, y in ((3, 60), (4, 100)):
            year = " (2023)." if dated else "."
            page.insert_text((40, y), f"{n}. Smith, A., & Lee, B.{year} A study.", fontsize=8)
            page.insert_text((54, y + 12), "Rapid Research Letters, 17(6), 2200493.", fontsize=8)
        following = doc.new_page()
        following.insert_text((40, 65), "Appendix", fontname="hebo", fontsize=12)
        following.insert_text((40, 89), "Independent material must remain translatable.", fontsize=10)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert reference_page_only(boundary, 1) is dated
        assert not reference_page_only(boundary, 2)
        for box in following.search_for("Independent material"):
            assert not boundary_intersects(boundary, 2, box)


@pytest.mark.parametrize("scanned", [False, True])
def test_author_and_terminal_year_without_publication_do_not_confirm_continuation(monkeypatch, scanned):
    if scanned:
        _ocr_rows(monkeypatch)
    with _bifido(footer=False, fragment=False) as doc:
        doc.delete_pages(1, 3)
        page = doc.new_page(width=595.276, height=790.866)
        for n, y in ((3, 60), (4, 100)):
            page.insert_text((40, y), f"{n}. J. Smith, Measure the sample and record the year, 2020.", fontsize=7)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["uncertain_end"]["page"] == 1
        assert not reference_boxes(boundary, 1)


@pytest.mark.parametrize("scanned", [False, True])
@pytest.mark.parametrize("title", ["Methods in Enzymology and microbial pathways.",
                                  "Appendix to the study of microbial pathways."])
def test_page_start_hanging_cited_title_does_not_end_bibliography(monkeypatch, scanned, title):
    if scanned:
        _ocr_rows(monkeypatch)
    with _bifido(footer=False, fragment=False) as doc:
        doc.delete_pages(1, 3)
        page = doc.new_page(width=595.276, height=790.866)
        page.insert_text((55, 60), title, fontsize=7)
        page.insert_text((55, 69), "Nature 8, 10-20 (2020).", fontsize=7)
        _numbered(page, 3, 40, 95)
        _numbered(page, 4, 40, 135)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary and not boundary.get("end") and not boundary.get("uncertain_end")
        assert boundary_intersects(boundary, 1, page.search_for(title)[0])
        assert all(boundary_intersects(boundary, 1, box) for box in page.search_for("Peters"))


@pytest.mark.parametrize("scanned", [False, True])
@pytest.mark.parametrize("section", ["Methods", "Appendix A"])
def test_actual_page_start_section_remains_translatable(monkeypatch, scanned, section):
    if scanned:
        _ocr_rows(monkeypatch)
    with _bifido(footer=False, fragment=False) as doc:
        doc.delete_pages(1, 3)
        page = doc.new_page(width=595.276, height=790.866)
        page.insert_text((55, 60), section, fontsize=10, fontname="hebo")
        page.insert_text((55, 84), "Independent experimental details.", fontsize=7)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        assert boundary["end"]["page"] == 1
        assert not boundary_intersects(boundary, 1, page.search_for(section)[0])
        assert not boundary_intersects(boundary, 1, page.search_for("Independent experimental")[0])


def test_ambiguous_author_year_start_does_not_protect_neighboring_body():
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((330, 150), "Independent right column body remains translatable.", fontsize=7)
        page.insert_text((40, 300), "References", fontsize=10, fontname="hebo")
        _author_year(page, "Smith", 40, 325)
        _author_year(page, "Jones", 40, 375)
        boundary = pdf_boundary(doc, JobControl(), lambda _: None)
        body = page.search_for("Independent right column")[0]
        assert not boundary_intersects(boundary, 0, body)


@pytest.mark.parametrize("suffix", ["(22), No. 4789, DOI: 10.1234/example.",
                                   "(4), 120-130."])
def test_wrapped_publication_issue_is_not_a_citation_label(suffix):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((55, 60), "(11) Smith, A.; Brown, B. A scientific study.", fontsize=8)
        page.insert_text((51, 70), "Journal of Chemistry 2022, 14", fontsize=8)
        page.insert_text((51, 80), suffix, fontsize=8)
        page.insert_text((55, 100), "(12) Jones, C. Another study.", fontsize=8)
        page.insert_text((51, 110), "Journal of Chemistry 2023, 15 (1), 90-99.", fontsize=8)
        rows = _join_numbered_publication_lines(_digital_lines(page), None)
        assert [_reference_number(r) for r in rows if _reference_number(r) is not None] == [11, 12]
        assert _reference_continues(rows, 10)


@pytest.mark.parametrize("suffix", ["(12) Jones, B. Independent article.",
                                   "(12) Measure the temperature carefully.",
                                   "(12), unrelated instructions."])
def test_left_aligned_numbered_text_is_not_merged_without_publication_evidence(suffix):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((55, 60), "(11) Smith, A. An unfinished title.", fontsize=8)
        page.insert_text((51, 70), "Journal of Chemistry 2022, 14", fontsize=8)
        page.insert_text((51, 80), suffix, fontsize=8)
        rows = _join_numbered_publication_lines(_digital_lines(page), None)
        assert [_reference_number(r) for r in rows if _reference_number(r) is not None] == [11, 12]
