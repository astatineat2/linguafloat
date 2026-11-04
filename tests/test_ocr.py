import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
import pytest

from linguafloat.ocr import LocalOcr, assemble_ocr_lines


def box(x, y, width=100, height=24):
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]


def test_ocr_groups_separate_same_row_blocks_and_keeps_other_rows():
    texts = ["world", "Second line", "Hello", "right"]
    boxes = [box(160, 11), box(10, 52), box(10, 10), box(170, 51)]
    assert assemble_ocr_lines(texts, boxes) == "Hello world\nSecond line right"


def test_ocr_handles_same_baseline_with_different_font_sizes():
    assert assemble_ocr_lines(["Small", "Heading"], [box(180, 16, height=18), box(10, 0, height=34)]) == "Heading Small"


def test_ocr_does_not_merge_adjacent_lines_or_split_cjk_words():
    texts = ["世界", "你好", "下一行"]
    boxes = [box(112, 10), box(10, 10), box(10, 38)]
    assert assemble_ocr_lines(texts, boxes) == "你好世界\n下一行"


@pytest.fixture(scope="module")
def engine():
    return LocalOcr()


@pytest.mark.parametrize("text,font_name", [
    ("Hello world. Screen translation is ready.", "segoeui.ttf"),
    ("欢迎使用屏幕翻译", "msyh.ttc"),
    ("こんにちは世界", "YuGothM.ttc"),
])
def test_real_local_ocr_reads_english_chinese_and_japanese_without_network(engine, monkeypatch, text, font_name):
    from pathlib import Path
    from PIL import Image, ImageDraw, ImageFont
    from rapidocr.utils.download_file import DownloadFile

    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font_name
    if not font_path.exists():
        pytest.skip(f"Windows test font unavailable: {font_name}")
    def reject_download(*args, **kwargs):
        raise AssertionError("OCR must work without a runtime model download")
    monkeypatch.setattr(DownloadFile, "run", reject_download)
    canvas = Image.new("RGB", (1000, 110), "white")
    ImageDraw.Draw(canvas).text((24, 22), text, font=ImageFont.truetype(str(font_path), 36), fill="black")
    pixels = canvas.tobytes()
    image = QImage(pixels, canvas.width, canvas.height, canvas.width * 3, QImage.Format.Format_RGB888)
    assert "".join(engine.recognize(image).split()) == "".join(text.split())


def test_blank_image_has_no_text(engine):
    image = QImage(300, 100, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.white)
    assert engine.recognize(image) == ""
