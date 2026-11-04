from types import SimpleNamespace

import numpy as np
import pymupdf as fitz
import pytest

from linguafloat.document_core import Cancelled, DocumentError, DocumentTranslator, JobControl, JobStore
from linguafloat.document_layout import LayoutPolicy, bounds
from linguafloat.document_pdf import assemble_layout_pdf
from linguafloat.document_scan_layout import classify_page, ink_patch, translate_scan_page
from test_document_core import job_for
from test_document_layout import engine_fixture


def scan_fixture(tmp_path, hidden=True, color=(1, 1, 1)):
    source = tmp_path / "scan.pdf"
    with fitz.open() as digital:
        p = digital.new_page(width=300, height=180)
        p.draw_rect(p.rect, color=color, fill=color)
        p.insert_text((30, 55), "A simple translation example.", fontsize=12)
        p.draw_rect(fitz.Rect(30, 100, 250, 150), color=(1, 0, 0), fill=(0, 1, 0))
        line = p.search_for("A simple translation example.")[0]
        pix = p.get_pixmap(matrix=fitz.Matrix(3, 3))
        with fitz.open() as document:
            p = document.new_page(width=300, height=180)
            p.insert_image(p.rect, stream=pix.tobytes("png"))
            if hidden:
                p.insert_text((30, 55), "A simple translation example.", fontsize=12, render_mode=3)
            document.save(source)
    region = dict(box=list(line + (-1.5, -1.5, 1.5, 1.5)), text="A simple translation example.", reason="",
                  lines=[dict(box=list(line), text="A simple translation example.", confidence=1, horizontal=True)])
    return source, region


def test_strict_overflow_keeps_original_and_full_translation_without_extra_page():
    engine, page, paragraph, units = engine_fixture(text="完整译文" * 1000)
    policy = LayoutPolicy(JobControl())
    policy.prepare_page(page)
    policy.find(engine, paragraph, page, units, apply_layout=True)
    assert not policy.continuations and not policy.margin_references
    assert paragraph.unicode == "" and not paragraph.pdf_paragraph_composition
    assert policy.retained_regions[0][0]["translation"] == "完整译文" * 1000
    assert policy.retained_regions[0][0]["box"] == bounds(paragraph)


def test_restore_original_region_and_preserve_page_size(tmp_path):
    source, rendered = tmp_path / "source.pdf", tmp_path / "rendered.pdf"
    with fitz.open() as doc:
        p = doc.new_page(width=300, height=400)
        p.insert_text((40, 40), "ORIGINAL LABEL", fontsize=8)
        rect = p.search_for("ORIGINAL LABEL")[0]
        box = list(rect * ~p.transformation_matrix)
        doc.save(source)
    with fitz.open() as doc:
        doc.new_page(width=300, height=400).insert_text((40, 100), "OTHER TRANSLATION")
        doc.save(rendered)
    job = job_for(source, pdf_mode="layout")
    paths = assemble_layout_pdf(job, [0], rendered, tmp_path, {}, JobControl(),
                                retained_regions={0: [dict(box=box)]})
    with fitz.open(paths[0]) as out:
        assert len(out) == 1 and out[0].rect == fitz.Rect(0, 0, 300, 400)
        assert "ORIGINAL LABEL" in out[0].get_text()
        assert "OTHER TRANSLATION" in out[0].get_text()


@pytest.mark.parametrize("hidden,kind", [(True, "scan_ocr"), (False, "scan_image")])
def test_scan_patch_only_changes_verified_region_and_reuses_cache(tmp_path, hidden, kind):
    source, region = scan_fixture(tmp_path, hidden)
    job = job_for(source, pdf_mode="layout")
    calls = []
    def create(**kwargs):
        calls.append(1)
        return SimpleNamespace(output_text="这是翻译示例。")
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    store = JobStore(tmp_path / "cache.sqlite")
    try:
        with fitz.open(source) as doc:
            assert classify_page(doc[0]) == kind
            analyzer = SimpleNamespace(regions=lambda *args: [region])
            for index in range(2):
                job.relayout_only = index == 1
                translator = DocumentTranslator(job, "", store, JobControl(), lambda _: None, client)
                output, retained, placed = translate_scan_page(doc, 0, kind, analyzer, "hash", translator, lambda _: None)
                with output:
                    assert not retained and len(placed) == 1
                    assert "这是翻译示例" in output[0].get_text().replace(" ", "")
                    assert "A simple" not in output[0].get_text()
                    assert any(output.extract_font(f[0])[3] for f in output[0].get_fonts(full=True)
                               if "Droid" in f[3])
                    assert output[0].rect == doc[0].rect
                    outside = fitz.Rect(0, 80, 300, 180)
                    assert output[0].get_pixmap(clip=outside).samples == doc[0].get_pixmap(clip=outside).samples
                assert translator.calls == (1 if index == 0 else 0)
            assert len(calls) == 1
    finally:
        store.close()


def test_complex_background_is_not_whited_out(tmp_path):
    source, region = scan_fixture(tmp_path, color=(.7, .8, 1))
    with fitz.open(source) as doc:
        patch, reason = ink_patch(doc[0], region)
        assert patch is None and "背景" in reason


@pytest.mark.parametrize("scale", [1.5, 3, 4])
def test_ink_erasure_has_no_dark_alpha_fringe_at_different_zooms(tmp_path, scale):
    source, region = scan_fixture(tmp_path)
    with fitz.open(source) as doc:
        patch, reason = ink_patch(doc[0], region)
        assert patch is not None, reason
        doc[0].insert_image(fitz.Rect(patch[1]), stream=patch[0], mask=patch[2])
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(region["box"]))
        pixels = np.frombuffer(pix.samples, dtype=np.uint8)
        assert pixels.min() >= 245


def test_mixed_pages_keep_order_geometry_and_untranslated_reporting(tmp_path, monkeypatch):
    import json
    import linguafloat.document_pdf as pdf
    from linguafloat.document_scan_layout import ScanAnalyzer
    source, region = scan_fixture(tmp_path)
    with fitz.open(source) as doc:
        doc.new_page(pno=0, width=420, height=500).insert_text((40, 40), "DIGITAL")
        doc.saveIncr()
    def digital(job, translator, folder, emit):
        assert job.pages == "1" and not job.bilingual
        out = folder / "digital.pdf"
        with fitz.open() as doc:
            doc.new_page(width=420, height=500).insert_text((40, 40), "TRANSLATED")
            doc.save(out)
        (folder / "layout-report.json").write_text('{}')
        return [str(out)], []
    monkeypatch.setattr(pdf, "_translate_layout_pdf", digital)
    monkeypatch.setattr(pdf, "translate_reading_pdf", lambda *a: pytest.fail("Changed to reading mode"))
    monkeypatch.setattr(ScanAnalyzer, "regions", lambda *a: [dict(region, reason="Protected region")])
    t = SimpleNamespace(control=JobControl(), job=job_for(source))
    paths, warnings = pdf.translate_layout_pdf(job_for(source, pdf_mode="layout"), t, tmp_path, lambda _: None)
    with fitz.open(paths[0]) as mono:
        assert len(mono) == 2 and mono[0].rect.width == 420 and mono[1].rect.width == 300
        assert "TRANSLATED" in mono[0].get_text() and "A simple" in mono[1].get_text()
    report = json.loads((tmp_path / "layout-report.json").read_text(encoding="utf-8"))
    assert report["untranslated_regions"]["1"][0]["reason"] == "Protected region"
    assert not report["continuations"]


def test_scan_preflight_rejects_mismatched_hidden_text_and_reuses_ocr(tmp_path):
    from linguafloat.document_scan_layout import ScanAnalyzer
    source, region = scan_fixture(tmp_path)
    store = JobStore(tmp_path / "analysis.sqlite")
    t = SimpleNamespace(control=JobControl(), store=store)
    analyzer = ScanAnalyzer(t, lambda _: None)
    calls = []
    def recognize(*args, **kwargs):
        calls.append(1)
        return [dict(region["lines"][0], text="A completely different sentence.")]
    analyzer.ocr.recognize = recognize
    box = [value * 2 for value in region["box"]]
    analyzer.model = SimpleNamespace(predict=lambda *a, **kw: [SimpleNamespace(
        names={0: "plain text"}, boxes=[SimpleNamespace(conf=.99, cls=0, xyxy=box)])])
    try:
        with fitz.open(source) as doc:
            for _ in range(2):
                result = analyzer.regions(doc[0], "scan_ocr", "source-hash")
                assert "不一致" in result[0]["reason"]
            assert len(calls) == 2  # Full page plus one cached local recovery attempt.
            analyzer.regions(doc[0], "scan_ocr", "different-source-hash")
            assert len(calls) == 4
    finally:
        store.close()


def test_cache_only_missing_scan_translation_makes_zero_requests(tmp_path):
    source, region = scan_fixture(tmp_path)
    store = JobStore(tmp_path / "cache.sqlite")
    job = job_for(source, pdf_mode="layout", relayout_only=True)
    translator = DocumentTranslator(job, "", store, JobControl(), lambda _: None,
        SimpleNamespace(responses=SimpleNamespace(create=lambda **_: pytest.fail("API called"))))
    try:
        with fitz.open(source) as doc, pytest.raises(DocumentError, match="译文缓存不完整"):
            translate_scan_page(doc, 0, "scan_ocr", SimpleNamespace(regions=lambda *a: [region]), "hash", translator, lambda _: None)
        assert translator.calls == 0
    finally:
        store.close()


def test_scan_overflow_and_cancel_do_not_clear_source(tmp_path):
    source, region = scan_fixture(tmp_path)
    control = JobControl()
    t = SimpleNamespace(control=control, translate=lambda _: "长译文" * 1000, job=job_for(source))
    with fitz.open(source) as doc:
        result, retained, placed = translate_scan_page(doc, 0, "scan_ocr", SimpleNamespace(regions=lambda *a: [region]), "", t, lambda _: None)
        with result:
            assert retained[0]["translation"] == "长译文" * 1000 and not placed
            assert result[0].get_pixmap().samples == doc[0].get_pixmap().samples
        control.cancelled.set()
        with pytest.raises(Cancelled):
            translate_scan_page(doc, 0, "scan_ocr", SimpleNamespace(regions=lambda *a: [region]), "", t, lambda _: None)


def test_adjacent_scan_paragraphs_trim_padding_without_erasing_neighbors():
    from linguafloat.document_scan_layout import build_regions
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=200)
        lines = []
        for y, text in ((45, "First paragraph."), (59, "Second paragraph.")):
            page.insert_text((30, y), text, fontsize=10)
            lines.append(dict(box=[29, y - 10, 230, y + 2], text=text, confidence=.99, horizontal=True))
        areas = [dict(label="plain text", confidence=.99, box=l["box"]) for l in lines]
        regions = build_regions(page, "scan_image", lines, areas)
        assert len(regions) == 2 and all(not r["reason"] for r in regions)
        assert not fitz.Rect(regions[0]["box"]).intersects(fitz.Rect(regions[1]["box"]))
        for region in regions:
            patch, reason = ink_patch(page, region)
            assert patch is not None, reason


@pytest.mark.parametrize("bad", ["confidence", "formula", "tilted"])
def test_one_unreliable_line_does_not_discard_entire_paragraph(bad):
    from linguafloat.document_scan_layout import build_regions
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=200)
        lines = []
        for y, text in ((40, "Reliable first line."), (60, "E = mc2" if bad == "formula" else "Problem line."),
                        (80, "Reliable final line.")):
            page.insert_text((30, y), text, fontsize=10)
            lines.append(dict(box=[29, y - 10, 230, y + 2], text=text,
                              confidence=.6 if y == 60 and bad == "confidence" else .99,
                              horizontal=not (y == 60 and bad == "tilted")))
        regions = build_regions(page, "scan_image", lines,
                                [dict(label="plain text", confidence=.99, box=[25, 25, 240, 90])])
        assert len(regions) == 3
        assert not regions[0]["reason"] and regions[1]["reason"] and not regions[2]["reason"]
        assert regions[0]["text"] == lines[0]["text"] and regions[2]["text"] == lines[2]["text"]


def test_real_overlap_and_protected_formula_are_not_merged():
    from linguafloat.document_scan_layout import separate_regions
    import copy
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=200)
        page.insert_text((30, 50), "OVERLAPPING", fontsize=16)
        region = dict(box=[25, 30, 230, 60], lines=[dict(box=[30, 32, 230, 60])],
                      text="First", label="plain text", reason="")
        regions = [copy.deepcopy(region), dict(copy.deepcopy(region), text="Second")]
        separate_regions(page, regions)
        assert len(regions) == 2 and all(r["reason"] for r in regions)
        regions = [copy.deepcopy(region), dict(copy.deepcopy(region), reason="Protected formula")]
        separate_regions(page, regions)
        assert len(regions) == 2 and regions[1]["reason"] == "Protected formula" and regions[0]["reason"]


def test_cached_raw_scan_is_rechecked_against_current_policy(tmp_path, monkeypatch):
    import linguafloat.document_scan_layout as scan
    source, region = scan_fixture(tmp_path)
    store = JobStore(tmp_path / "raw.sqlite")
    analyzer = scan.ScanAnalyzer(SimpleNamespace(control=JobControl(), store=store), lambda _: None)
    analyzer.ocr.recognize = lambda *a, **k: region["lines"]
    analyzer.model = SimpleNamespace(predict=lambda *a, **kw: [SimpleNamespace(
        names={0: "plain text"}, boxes=[SimpleNamespace(conf=.99, cls=0, xyxy=[v * 2 for v in region["box"]])])])
    try:
        with fitz.open(source) as doc:
            assert not analyzer.regions(doc[0], "scan_ocr", "hash")[0]["reason"]
            analyzer.ocr.recognize = lambda *a, **k: pytest.fail("Repeated OCR")
            monkeypatch.setattr(scan, "risky_math", lambda text: True)
            from linguafloat.document_scan_recovery import Recovery
            monkeypatch.setattr(Recovery, "recognize", lambda *a: [])
            assert "公式" in analyzer.regions(doc[0], "scan_ocr", "hash")[0]["reason"]
    finally:
        store.close()


def test_scan_horizontal_detection_uses_angle_not_fixed_pixel_difference():
    from linguafloat.document_pdf import DocumentOcr
    ocr = DocumentOcr()
    ocr.engine = lambda image: SimpleNamespace(
        txts=["Slightly skewed long line", "Vertical"], scores=[.99, .99],
        boxes=np.array([[[10, 10], [510, 16], [510, 36], [10, 30]],
                        [[20, 20], [30, 40], [20, 50], [10, 30]]]))
    with fitz.open() as doc:
        lines = ocr.recognize(doc.new_page(width=300, height=200), positioned=True)
    assert lines[0]["horizontal"] and not lines[1]["horizontal"]


def test_tightly_set_prose_can_merge_without_losing_paragraph_boundary():
    from linguafloat.document_scan_layout import build_regions
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=200)
        lines = []
        for y, text in ((45, "Typography example."), (54, "Another paragraph.")):
            page.insert_text((30, y), text, fontsize=10)
            lines.append(dict(box=[29, y - 10, 230, y + 2], text=text, confidence=.99, horizontal=True))
        regions = build_regions(page, "scan_image", lines,
                                [dict(label="plain text", confidence=.99, box=line["box"]) for line in lines])
        assert len(regions) == 1 and not regions[0]["reason"]
        assert regions[0]["text"] == "Typography example.\n\nAnother paragraph."
        assert len(regions[0]["lines"]) == 2
