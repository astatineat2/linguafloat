from types import SimpleNamespace as NS

import numpy as np
import pymupdf as fitz
import pytest

from linguafloat.document_core import JobControl, JobStore
from linguafloat.document_pdf import DocumentOcr
from linguafloat.document_scan_layout import fit_translation, translate_scan_page
from linguafloat.document_scan_recovery import Recovery, recover_regions


def test_duplicate_ocr_observations_require_same_source_row_and_coverage():
    from linguafloat.document_scan_recovery import discard_duplicate_observations
    row = (10, 20, 150, 30)
    good = dict(box=list(row), text="Correct visible text", reason="", verified_row=row)
    duplicate = dict(box=list(row), text="Corrupted OCR", reason="uncertain", source_row=row)
    adjacent = dict(box=[10, 32, 150, 42], text="Next line", reason="uncertain", source_row=(10, 32, 150, 42))
    assert discard_duplicate_observations([good, duplicate, adjacent]) == [good, adjacent]
    uncovered = dict(duplicate, box=[10, 20, 170, 30])
    assert len(discard_duplicate_observations([good, uncovered])) == 2


def test_missing_ocr_row_is_detected_without_trusting_hidden_text():
    from linguafloat.document_scan_recovery import native_prose_seeds
    with fitz.open() as doc:
        p = doc.new_page(width=300, height=180)
        p.insert_text((20, 40), "First visible scientific sentence", fontsize=10, render_mode=3)
        p.insert_text((20, 70), "Missed Ampere law sentence", fontsize=10, render_mode=3)
        words = p.get_text("words")
        first = [w for w in words if w[1] < 40]
        box = list(fitz.Rect(first[0][:4]) | fitz.Rect(first[-1][:4]))
        regions = [dict(box=box, text="First visible scientific sentence",
                        lines=[dict(box=box, text="First visible scientific sentence")], reason="")]
        seeds = native_prose_seeds(p, regions)
        assert len(seeds) == 1 and seeds[0]["text"] == "Missed Ampere law sentence"
        assert seeds[0]["reason"] and seeds[0]["lines"][0]["confidence"] == 0
        analyzer = NS(translator=NS(control=JobControl()), emit=lambda _: None, ocr=NS(recognize=lambda *a, **k: []))
        recovered = Recovery(p, analyzer, "hash").region(seeds[0])
        assert all(r["reason"] for r in recovered)


def test_layout_padding_split_keeps_original_eraser_bounds():
    from linguafloat.document_scan_recovery import separate_verified_rows
    a = dict(box=[10, 20, 100, 30.4], lines=[], reason="", verified_row=(10, 20, 100, 30))
    b = dict(box=[10, 30, 100, 40], lines=[], reason="", verified_row=(10, 30, 100, 40))
    separate_verified_rows([a, b])
    assert a["box"][3] < b["box"][1]
    assert a["erase_box"] == [10, 20, 100, 30.4]
    assert b["erase_box"] == [10, 30, 100, 40]


def formula_scan(tmp_path):
    path = tmp_path / "formula.pdf"
    text = "Since B = H within the material"
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=150)
        page.insert_text((30, 50), text, fontsize=10)
        words = [dict(box=list(w[:4]), text=w[4], confidence=.999) for w in page.get_text("words")]
        box = list(page.get_text("blocks")[0][:4])
        image = page.get_pixmap(matrix=fitz.Matrix(3, 3)).tobytes("png")
        with fitz.open() as scan:
            p = scan.new_page(width=300, height=150)
            p.insert_image(p.rect, stream=image)
            p.insert_text((30, 50), text, fontsize=10, render_mode=3)
            scan.save(path)
    line = dict(box=box, text=text, confidence=.99, horizontal=True, words=words)
    region = dict(box=box, text=text, lines=[line], reason="本行包含公式符号", label="plain text")
    return path, line, region


def test_formula_prose_recovery_translates_around_unchanged_formula(tmp_path):
    path, line, region = formula_scan(tmp_path)
    control = JobControl()
    analyzer = NS(translator=NS(control=control), emit=lambda _: None,
                  ocr=NS(recognize=lambda *a, **kw: [line]))
    with fitz.open(path) as source:
        regions = recover_regions(source[0], [region], analyzer, "hash")
        prose = [r for r in regions if not r["reason"]]
        formulas = [r for r in regions if r.get("preserved")]
        assert [r["text"] for r in prose] == ["Since", "within the material"]
        assert len(formulas) == 1 and formulas[0]["text"] == "B = H"
        t = NS(control=control, job=NS(target="zh-CN"),
               translate=lambda text: {"Since": "由于", "within the material": "在材料内部"}[text])
        output, retained, placed = translate_scan_page(source, 0, "scan_ocr",
            NS(regions=lambda *a: regions), "hash", t, lambda _: None)
        with output:
            assert len(placed) == 2, retained
            with fitz.open(stream=output.tobytes(), filetype="pdf") as saved, fitz.open(path) as original:
                for formula in formulas:
                    box = fitz.Rect(formula["box"])
                    for scale in (1, 3):
                        assert saved[0].get_pixmap(matrix=fitz.Matrix(scale, scale), clip=box).samples == original[0].get_pixmap(matrix=fitz.Matrix(scale, scale), clip=box).samples


def test_local_ocr_crop_coordinates_and_word_boxes_are_absolute():
    ocr = DocumentOcr()
    def engine(image, **kwargs):
        assert kwargs == {"return_word_box": True}
        box = np.array([[8, 8], [80, 8], [80, 40], [8, 40]])
        return NS(txts=["word"], scores=[.999], boxes=[box], word_results=[[("word", .999, box)]])
    ocr.engine = engine
    with fitz.open() as doc:
        p = doc.new_page(width=300, height=150)
        lines = ocr.recognize(p, positioned=True, clip=fitz.Rect(100, 50, 200, 90), scale=4, words=True)
    assert lines[0]["box"] == [102, 52, 120, 60]
    assert lines[0]["words"][0]["box"] == lines[0]["box"]


def test_local_recovery_ocr_is_cached_and_does_not_call_translation(tmp_path):
    path, line, region = formula_scan(tmp_path)
    calls = []
    def recognize(*args, **kwargs):
        calls.append(kwargs)
        return [line]
    store = JobStore(tmp_path / "recovery.sqlite")
    analyzer = NS(translator=NS(control=JobControl(), store=store), emit=lambda _: None,
                  ocr=NS(recognize=recognize))
    try:
        with fitz.open(path) as source:
            for _ in range(2):
                assert any(not r["reason"] for r in recover_regions(source[0], [region], analyzer, "same-source"))
        assert len(calls) == 1 and calls[0]["scale"] == 4
    finally:
        store.close()


def test_single_line_glyph_fit_respects_box_and_minimum_size(tmp_path):
    path, line, region = formula_scan(tmp_path)
    with fitz.open(path) as source:
        box = [30, 42, 65, 50]
        recovered = dict(region, box=box, recovered=True)
        fitted = fit_translation(source[0], recovered, "中文测试", "zh-CN", JobControl())
        assert fitted is not None and fitted[1]["size"] >= 6
        with fitz.open(stream=fitted[0], filetype="pdf") as overlay:
            pix = overlay[0].get_pixmap(matrix=fitz.Matrix(4, 4), colorspace=fitz.csGRAY)
            rows, cols = np.nonzero(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) < 200)
            assert cols.min() / 4 >= box[0] and (cols.max() + 1) / 4 <= box[2]
            assert rows.min() / 4 >= box[1] and (rows.max() + 1) / 4 <= box[3]


def test_single_line_empty_ocr_does_not_disable_future_detection():
    from rapidocr.main import RapidOCRError
    class Engine:
        use_det = True
        def recognize_txt(self, images):
            raise RapidOCRError("The text recognize result is empty")
        def __call__(self, image, **kwargs):
            assert self.use_det
            return NS(txts=["Visible"], scores=[.999], boxes=[np.array([[5, 5], [50, 5], [50, 20], [5, 20]])])
    ocr = DocumentOcr()
    ocr.engine = Engine()
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=150)
        assert ocr.recognize(page, positioned=True, single_line=True) == []
        assert ocr.recognize(page) == "Visible"
        ocr.engine.recognize_txt = lambda images: (_ for _ in ()).throw(RapidOCRError("Model failure"))
        with pytest.raises(RapidOCRError, match="Model failure"):
            ocr.recognize(page, positioned=True, single_line=True)


def test_local_row_recovery_keeps_good_rows_when_another_row_is_bad():
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=180)
        lines = []
        for y, text in ((40, "Reliable opening words"), (70, "Damaged middle words"), (100, "Reliable final words")):
            page.insert_text((30, y), text, fontsize=10)
            native = page.get_text("words", clip=fitz.Rect(20, y - 13, 250, y + 3))
            box = list(fitz.Rect(native[0][:4]) | fitz.Rect(native[-1][:4]))
            lines.append(dict(text=text, box=box, confidence=.999, horizontal=True,
                              words=[dict(text=w[4], box=list(w[:4]), confidence=.999) for w in native]))
        original = dict(text=" ".join(l["text"] for l in lines), lines=lines,
                        box=[25, 25, 250, 105], reason="OCR confidence", label="plain text")
        local = [lines[0], dict(lines[1], text="Unreliable unrelated recognition", confidence=.5), lines[2]]
        analyzer = NS(translator=NS(control=JobControl()), emit=lambda _: None, ocr=NS(recognize=lambda *a, **k: local))
        regions = Recovery(page, analyzer, "hash").region(original)
        assert [r["text"] for r in regions if not r["reason"]] == [lines[0]["text"], lines[2]["text"]]
        assert any(r["reason"] for r in regions)


def test_neighboring_rows_do_not_enter_formula_prose_eraser():
    from linguafloat.document_scan_layout import ink_patch
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=180)
        lines = []
        for y, text in ((50, "Since B = H within material"), (60, "Another complete text line")):
            page.insert_text((30, y), text, fontsize=9)
            # Deliberately tall OCR boxes extend into the next line.
            words = page.get_text("words", clip=fitz.Rect(25, y - 10, 270, y + 1))
            selected = [w for w in words if abs(w[3] - (y + 2.691)) < 1]
            if not selected:
                selected = [w for w in page.get_text("words") if abs(w[3] - (y + 2.691)) < 1]
            box = [29, y - 12, 165, y + 4]
            lines.append(dict(text=text, box=box, confidence=.999, horizontal=True,
                              words=[dict(text=w[4], box=list(w[:4]), confidence=.999) for w in selected]))
        region = dict(text=" ".join(l["text"] for l in lines), box=[25, 35, 270, 65], lines=lines,
                      reason="formula", label="plain text")
        analyzer = NS(translator=NS(control=JobControl()), emit=lambda _: None, ocr=NS(recognize=lambda *a, **k: lines))
        recovered = Recovery(page, analyzer, "hash").region(region)
        prose = [r for r in recovered if not r["reason"]]
        assert len(prose) == 3, recovered
        for item in prose:
            assert ink_patch(page, item)[0] is not None
        assert max(r["box"][3] for r in prose if r["text"] != lines[1]["text"]) < min(r["box"][1] for r in prose if r["text"] == lines[1]["text"])
