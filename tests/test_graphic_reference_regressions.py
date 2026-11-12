"""Narrow regressions for perspective axes and right-aligned reference numbers."""
from types import SimpleNamespace as NS

import pymupdf as f
import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_graphics import apply_graphics
from linguafloat.document_scientific import reference_entries


def perspective_fixture(page):
    page.insert_text((35, 165), "field strength", rotate=90, fontsize=10)
    page.draw_line((80, 40), (80, 185), color=(.6, .6, .6), width=.5)
    page.draw_line((80, 185), (260, 200), width=.5)
    page.draw_line((90, 160), (235, 70), color=(1, 0, 0), width=2)
    box = list(page.search_for("field strength")[0])
    return dict(id="perspective", kind="figure", box=[15, 20, 280, 210],
                regions=[dict(id="title", kind="figure", text="field strength", box=box,
                              rotation=90, confidence=1, reason="")])


def translator(**options):
    job = NS(translate_figures=True, translate_tables=True, target="zh-Hans",
             skip_references=False, reference_boundary={})
    job.__dict__.update(options)
    return NS(job=job, control=JobControl(), translate_fragment=lambda *a, **kw: "\u78c1\u573a\u5f3a\u5ea6")


def test_perspective_axis_translates_in_place_and_preserves_data():
    with f.open() as src, f.open() as dst:
        page = src.new_page(width=300, height=230)
        area = perspective_fixture(page)
        source_pdf = src.tobytes()
        dst.insert_pdf(src)
        records = apply_graphics(page, dst[0], translator(), NS(discover=lambda *a: [area], emit=lambda e: None), "fixture")
        assert records[0]["status"] == "translated", records
        assert records[0]["layout_box"] == area["regions"][0]["box"]
        assert records[0]["geometry"] == "axis-in-place"
        clip = f.Rect(70, 20, 280, 215)
        attributes = ("type", "rect", "items", "color", "fill", "width", "dashes", "lineCap",
                      "lineJoin", "closePath", "stroke_opacity", "fill_opacity", "even_odd")
        def protected_drawings(p):
            # Inclusive bounds also select zero-width vertical and horizontal paths.
            return [{key: drawing.get(key) for key in attributes} for drawing in p.get_drawings()
                    if drawing['rect'].x0 <= clip.x1 and drawing['rect'].x1 >= clip.x0
                    and drawing['rect'].y0 <= clip.y1 and drawing['rect'].y1 >= clip.y0]
        # Fresh pages avoid stale transparency state. RGBA uses the same
        # compositing path on both sides; opaque RGB has documented 1-level rounding.
        with f.open(stream=source_pdf, filetype="pdf") as original, \
                f.open(stream=dst.tobytes(), filetype="pdf") as rendered:
            before = original[0].get_pixmap(clip=clip, alpha=True)
            after = rendered[0].get_pixmap(clip=clip, alpha=True)
            assert (before.width, before.height, before.n) == (after.width, after.height, after.n)
            assert before.samples == after.samples
            original_paths = protected_drawings(original[0])
            assert len(original_paths) == 3
            assert original_paths == protected_drawings(rendered[0])


@pytest.mark.parametrize("obstacle", ["colored-rule", "crossing-curve", "inside-axis"])
def test_perspective_fallback_does_not_erase_data(obstacle):
    with f.open() as src:
        page = src.new_page(width=300, height=230)
        area = perspective_fixture(page)
        if obstacle == "colored-rule":
            page.draw_line((80, 40), (80, 185), color=(1, 0, 0), width=2)
        elif obstacle == "inside-axis":
            area["regions"][0]["box"] = [90, 100, 105, 170]
        else:
            box = f.Rect(area["regions"][0]["box"])
            page.draw_line(box.tl, box.br, color=(1, 0, 0), width=2)
        t = translator()
        t.translate_fragment = lambda *a, **kw: pytest.fail("Unsafe label reached translation")
        before = page.get_pixmap().samples
        records = apply_graphics(page, page, t, NS(discover=lambda *a: [area], emit=lambda e: None), "fixture")
        assert records[0]["status"] == "needs_review"
        assert page.get_pixmap().samples == before


def test_finite_references_do_not_skip_later_appendix_figure():
    with f.open() as src, f.open() as dst:
        src.new_page(width=300, height=230)
        page = src.new_page(width=300, height=230)
        area = perspective_fixture(page)
        dst.insert_pdf(src, from_page=1, to_page=1)
        boundary = dict(page=0, boxes=[[0, 100, 300, 230]],
                        page_boxes={"0": [[0, 100, 300, 230]]})
        records = apply_graphics(page, dst[0], translator(skip_references=True, reference_boundary=boundary),
                                 NS(discover=lambda *a: [area], emit=lambda e: None), "fixture")
        assert len(records) == 1 and records[0]["status"] == "translated"


def test_right_aligned_reference_numbers_do_not_split_column():
    with f.open() as doc:
        page = doc.new_page(width=360, height=300)
        page.insert_text((20, 25), "References")
        for index, number in enumerate((8, 9, 10, 11, 12)):
            label = f"[{number}]"
            y = 60 + index * 30
            page.insert_text((50 - f.get_text_length(label, fontsize=9), y), label, fontsize=9)
            page.insert_text((58, y), f"Author, A. Unique entry token{number}. Journal (2020).", fontsize=9)
        entries = reference_entries(page)
        assert [e["number"] for e in entries] == [f"[{n}]" for n in (8, 9, 10, 11, 12)]
        for entry in entries:
            number = int(entry["number"][1:-1])
            assert f"token{number}." in entry["text"]
            assert all(f"token{n}." not in entry["text"] for n in (8, 9, 10, 11, 12) if n != number)
        assert all(not f.Rect(a["box"]).intersects(f.Rect(b["box"]))
                   for i, a in enumerate(entries) for b in entries[i + 1:])


def test_reference_prepare_rebuilds_each_entry_only_once(tmp_path):
    from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfParagraph, PdfParagraphComposition, PdfStyle
    from linguafloat.document_scientific import ScientificLayout, composition_chars
    source = tmp_path / "number-transition.pdf"
    style = PdfStyle(font_id="F1", font_size=9)
    chars = []
    with f.open() as pdf:
        page = pdf.new_page(width=360, height=300)
        page.insert_text((20, 25), "References")
        for index, number in enumerate((8, 9, 10, 11, 12)):
            marker = f"[{number}]"
            y = 60 + index * 30
            page.insert_text((50 - f.get_text_length(marker, fontsize=9), y), marker, fontsize=9)
            page.insert_text((58, y), f"Author, A. Unique token{number}. Nature 12 (2020).", fontsize=9)
        pdf.save(source)
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    for c in span["chars"]:
                        x0, y0, x1, y1 = c["bbox"]
                        chars.append(PdfCharacter(box=Box(x0, 300-y1, x1, 300-y0), char_unicode=c["c"],
                                                  pdf_style=style, pdf_character_id=len(chars)))
    paragraph = PdfParagraph(box=Box(20, 100, 340, 290), pdf_style=style, unicode="",
                             pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=c) for c in chars])
    page = NS(page_number=0, pdf_font=[], pdf_xobject=[], pdf_character=[], pdf_paragraph=[paragraph])
    calls = []
    def translate(text, *args, **kwargs):
        calls.append(text)
        return text
    policy = ScientificLayout(source, NS(translate_fragment=translate), [])
    docs = NS(page=[page])
    policy.prepare(docs)
    refs = [p for p in page.pdf_paragraph if p.layout_label == "reference"]
    assert len(refs) == 5 and len(calls) == 5
    assert "".join(c.char_unicode for c in page.pdf_character) == "[8][9][10][11][12]"
    for number in (8, 9, 10, 11, 12):
        assert sum(p.unicode.count(f"token{number}.") for p in refs) == 1
    surviving = [c.char_unicode for p in page.pdf_paragraph for part in p.pdf_paragraph_composition
                 for c in composition_chars(part)]
    assert "Author" not in "".join(surviving)
    policy.prepare(docs)
    assert len(calls) == 5


def test_stationary_label_overflow_preserves_entire_group():
    with f.open() as src:
        page = src.new_page(width=300, height=230)
        area = perspective_fixture(page)
        t = translator()
        t.translate_fragment = lambda *a, **kw: "\u8fc7\u957f\u6807\u9898" * 100
        before = page.get_pixmap().samples
        records = apply_graphics(page, page, t, NS(discover=lambda *a: [area], emit=lambda e: None), "fixture")
        assert records[0]["status"] == "needs_review"
        assert page.get_pixmap().samples == before
