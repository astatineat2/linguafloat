"""The OCR engine persists per-call options across graphic and page work."""
from types import SimpleNamespace as NS

import numpy as np
import pymupdf as fitz
import pytest

from linguafloat.document_core import DocumentError, JobControl
from linguafloat.document_graphics import classify_content
from linguafloat.document_pdf import DocumentOcr


class StickyOcrEngine:
    def __init__(self):
        self.use_det = True
        self.use_cls = True
        self.use_rec = True
        self.return_word_box = False
        self.cfg = NS(Global=NS(use_cls=True))

    def __call__(self, image, *, use_det=None, use_cls=None, use_rec=None,
                 return_word_box=None):
        for name, value in (("use_det", use_det), ("use_cls", use_cls),
                            ("use_rec", use_rec), ("return_word_box", return_word_box)):
            if value is not None:
                setattr(self, name, value)
        if not self.use_det:
            # RapidOCR returns TextRecOutput here; it has no boxes member.
            return NS(txts=("B/H",), scores=(.99,), word_results=())
        box = np.array([[[0, 0], [90, 0], [90, 20], [0, 20]]], dtype=np.float32)
        return NS(txts=("Crystal structure",), scores=(.99,), boxes=box,
                  word_results=((('Crystal', .99, box[0]),),) if self.return_word_box else ())


def test_graphic_ratio_probe_restores_detection_before_next_positioned_ocr():
    ocr = DocumentOcr()
    ocr.engine = StickyOcrEngine()
    analyzer = NS(ocr=ocr, control=JobControl(), store=None)
    with fitz.open() as pdf:
        page = pdf.new_page(width=300, height=180)
        page.insert_text((40, 60), "Crystal structure", fontsize=10)
        area = dict(kind="figure", box=[20, 20, 120, 100], lines=[])
        region = dict(id="vertical-ratio", text="B/H", box=[30, 20, 45, 75], confidence=.7)
        assert classify_content(page, area, region, analyzer)[0] == "protected"
        assert ocr.engine.use_det and ocr.engine.use_cls
        lines = ocr.recognize(page, positioned=True, clip=(20, 20, 130, 100), words=True)
        assert len(lines) == 1 and lines[0]["text"] == "Crystal structure"
        assert lines[0]["words"][0]["text"] == "Crystal"
        assert ocr.engine.use_det and ocr.engine.use_cls and ocr.engine.return_word_box


def test_text_without_position_boxes_reports_incomplete_ocr_data():
    ocr = DocumentOcr()
    engine = StickyOcrEngine()
    engine.use_det = False
    ocr.engine = engine
    with fitz.open() as pdf:
        page = pdf.new_page(width=300, height=180)
        with pytest.raises(DocumentError, match="定位识别缺少文字框"):
            ocr.recognize(page, positioned=True)
