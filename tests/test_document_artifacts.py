import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from linguafloat.document_artifacts import LayoutSnapshot
from linguafloat.document_core import DocumentJob


def fixture(tmp_path):
    from babeldoc.format.pdf.document_il import Document, Page, PdfCurve, GraphicState
    source = tmp_path / "source.pdf"
    source.write_bytes(b"unchanged-original")
    job = DocumentJob(str(source), str(tmp_path), dict(id="one", model="test", base_url="https://example.invalid", api_mode="responses"))
    work = tmp_path / "work"
    work.mkdir()
    (work / "input.pdf").write_bytes(b"normalized-pdf-with-fonts")
    config = SimpleNamespace(get_working_file_path=lambda name: work / name)
    curve = PdfCurve(ctm=[1.0, 0.0, 0.0, 1.0, 22.5, 30.0],
                     graphic_state=GraphicState(passthrough_per_char_instruction="q 0 G"))
    docs = Document(page=[Page(page_number=0, pdf_curve=[curve])])
    return job, config, docs


def test_snapshot_preserves_pdf_numbers_resources_and_scope(tmp_path):
    job, config, docs = fixture(tmp_path)
    cache = LayoutSnapshot(tmp_path, job, [0])
    assert cache.save(docs, config, {0: [0, 0, 600, 800]})
    restored, pdf, boxes = cache.load(config)
    assert restored == docs
    assert type(restored.page[0].pdf_curve[0].ctm[0]) is float
    assert pdf.read_bytes() == b"normalized-pdf-with-fonts"
    assert boxes == {0: [0, 0, 600, 800]}
    assert LayoutSnapshot(tmp_path, job, [1]).load(config) is None
    job.provider["output_token_limit"] = 4096
    assert LayoutSnapshot(tmp_path, job, [0]).load(config) is None
    job.provider.pop("output_token_limit")
    assert LayoutSnapshot(tmp_path, job, [0]).key == cache.key
    job.provider = {**job.provider, "model": "another-model"}
    assert LayoutSnapshot(tmp_path, job, [0]).load(config) is None
    Path(job.source).write_bytes(b"edited-original")
    assert LayoutSnapshot(tmp_path, job, [0]).load(config) is None


def test_snapshot_corruption_falls_back_without_overwriting_input(tmp_path):
    job, config, docs = fixture(tmp_path)
    cache = LayoutSnapshot(tmp_path, job, [0])
    cache.save(docs, config, {})
    with ZipFile(cache.path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries["translated.json"] = b"corrupt"
    with ZipFile(cache.path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    assert cache.load(config) is None
    assert Path(job.source).read_bytes() == b"unchanged-original"
    cache.path.write_bytes(b"not-a-zip")
    assert cache.load(config) is None


def test_snapshot_restores_both_source_and_completed_graphic_context(tmp_path):
    job, config, docs = fixture(tmp_path)
    cache = LayoutSnapshot(tmp_path, job, [0])
    cache.source_context = [dict(text="magnetic resonance (MR)", role="body", section="Methods")]
    cache.graphic_context = [dict(page=0, source="magnetic resonance (MR)", translation="磁共振（MR）", role="body")]
    assert cache.save(docs, config, {})
    fresh = LayoutSnapshot(tmp_path, job, [0])
    assert fresh.load(config) is not None
    assert fresh.source_context == cache.source_context
    assert fresh.graphic_context == cache.graphic_context


def test_snapshot_decoder_preserves_legacy_boolean_and_untyped_numbers():
    from babeldoc.format.pdf.document_il.il_version_1 import PdfFont, PdfCurve
    from linguafloat.document_artifacts import _decoder
    font = _decoder(PdfFont)({"bold": 0, "italic": 1, "serif": "true"})
    assert font.bold is False and font.italic is True and font.serif is True
    curve = _decoder(PdfCurve)({"ctm": [1, 0.0, 22.5], "debug_info": "a"})
    assert curve.ctm == [1, 0.0, 22.5]
    assert type(curve.ctm[0]) is int
    assert curve.debug_info == "a"
