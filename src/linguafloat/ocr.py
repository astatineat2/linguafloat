from __future__ import annotations

from pathlib import Path
from statistics import median

from PySide6.QtCore import QObject, QRunnable, Signal, Slot, Qt
from PySide6.QtGui import QImage


def assemble_ocr_lines(texts, boxes, *, regions=None) -> str:
    """Rebuild horizontal reading order from the detector's text quadrilaterals."""
    if texts is None:
        return ""
    if boxes is None or len(boxes) != len(texts):
        return "\n".join(text.strip() for text in texts if text.strip())
    blocks = []
    for text, box in zip(texts, boxes):
        if not text.strip():
            continue
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        blocks.append((min(xs), min(ys), max(xs), max(ys), text.strip()))
    rows = []
    for block in sorted(blocks, key=lambda b: ((b[1] + b[3]) / 2, b[0])):
        height = max(1, block[3] - block[1])
        center = (block[1] + block[3]) / 2
        matches = []
        for row in rows:
            top, bottom = median(b[1] for b in row), median(b[3] for b in row)
            shared_height = max(1, min(height, bottom - top))
            overlap = max(0, min(bottom, block[3]) - max(top, block[1]))
            distance = abs(center - (top + bottom) / 2)
            if overlap >= shared_height * 0.55 and (
                distance <= shared_height * 0.5 or abs(bottom - block[3]) <= shared_height * 0.25
            ):
                matches.append((distance, row))
        if matches:
            min(matches, key=lambda match: match[0])[1].append(block)
        else:
            rows.append([block])
    lines = []
    for row in sorted(rows, key=lambda r: median(b[1] for b in r)):
        ordered = sorted(row, key=lambda b: b[0])
        line = ordered[0][4]
        for left, right in zip(ordered, ordered[1:]):
            cjk = lambda char: "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
            tight = right[0] - left[2] < min(left[3] - left[1], right[3] - right[1])
            separator = "" if tight and cjk(left[4][-1]) and cjk(right[4][0]) else " "
            line += separator + right[4]
        lines.append(line)
        if regions is not None:
            regions.append({"text": line, "box": (min(b[0] for b in row), min(b[1] for b in row),
                                                   max(b[2] for b in row), max(b[3] for b in row))})
    return "\n".join(lines)


class LocalOcr:
    """One lazy CPU engine, used only by the serial screen OCR worker."""

    def __init__(self) -> None:
        self.engine = None
        self.last_regions = []

    def recognize(self, image: QImage, *, progress=None) -> str:
        if progress:
            progress("正在加载本地 OCR 模型" if self.engine is None else "正在识别文字")
        import cv2
        # OpenCV otherwise creates a machine-sized worker pool in addition to ORT.
        cv2.setNumThreads(2)
        import numpy as np
        import rapidocr
        from rapidocr import RapidOCR

        if self.engine is None:
            model_root = Path(rapidocr.__file__).parent / "models"
            paths = {
                "Det.model_path": model_root / "PP-OCRv6_det_small.onnx",
                "Rec.model_path": model_root / "PP-OCRv6_rec_small.onnx",
                "Cls.model_path": model_root / "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            }
            if not all(path.is_file() for path in paths.values()):
                raise FileNotFoundError("本地 OCR 模型不完整，请使用完整的便携版文件夹。")
            self.engine = RapidOCR(params={
                **{key: str(path) for key, path in paths.items()},
                "Global.log_level": "error",
                "Global.text_score": 0.65,
                "Global.max_side_len": 2000,
                "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            })
            # RapidOCR passes OmegaConf ListConfig to numpy.array for every
            # detection. NumPy's optional-attribute probes create caught
            # ConfigAttributeError tracebacks retaining the entire OCR stack
            # (including large detector tensors) until cyclic GC runs. Plain
            # sequences preserve every normalization value without that cycle.
            detector = self.engine.text_det
            detector.mean = list(detector.mean) if detector.mean is not None else None
            detector.std = list(detector.std) if detector.std is not None else None
        if progress:
            progress("正在识别文字")
        original_width = image.width()
        self.last_regions = []
        if max(image.width(), image.height()) > 2000:
            image = image.scaled(2000, 2000, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        rgb = image.convertToFormat(QImage.Format.Format_RGB888)
        pixels = np.frombuffer(rgb.constBits(), dtype=np.uint8).reshape(rgb.height(), rgb.bytesPerLine())
        pixels = pixels[:, :rgb.width() * 3].reshape(rgb.height(), rgb.width(), 3)
        output = self.engine(pixels[:, :, ::-1].copy())
        text = assemble_ocr_lines(output.txts, output.boxes, regions=self.last_regions)
        scale = original_width / image.width()
        for region in self.last_regions:
            region["box"] = tuple(value * scale for value in region["box"])
        return text


class ReleaseOcrTask(QRunnable):
    """Release idle models on the same serial worker, never on the GUI thread."""
    def __init__(self, engine, allowed):
        super().__init__()
        self.engine, self.allowed = engine, allowed

    def run(self):
        if self.allowed():
            self.engine.engine = None
            self.engine.last_regions = []


class OcrSignals(QObject):
    phase = Signal(int, str)
    regions = Signal(int, object)
    succeeded = Signal(int, bytes, str)
    failed = Signal(int, str)


class OcrTask(QRunnable):
    def __init__(self, engine: LocalOcr, image: QImage, job: int, digest: bytes, *, allowed=None) -> None:
        super().__init__()
        self.engine, self.image, self.job, self.digest = engine, image, job, digest
        self.signals = OcrSignals()
        self.allowed = allowed or (lambda: True)

    @Slot()
    def run(self) -> None:
        if not self.allowed():
            self.signals.succeeded.emit(self.job, self.digest, "")
            return
        try:
            text = self.engine.recognize(self.image, progress=lambda message: self.signals.phase.emit(self.job, message))
        except FileNotFoundError:
            self.signals.failed.emit(self.job, "本地 OCR 模型不完整，请使用完整的便携版文件夹。")
        except Exception:
            self.signals.failed.emit(self.job, "文字识别失败，区域翻译已暂停。请重新框选后重试。")
        else:
            self.signals.regions.emit(self.job, getattr(self.engine, "last_regions", []))
            self.signals.succeeded.emit(self.job, self.digest, text)
        finally:
            self.image = QImage()
