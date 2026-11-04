"""Conservative, coordinate-preserving scan translation using installed models.

Only verified plain text on a near-white background is replaced. Image text
removal uses a transparent ink mask, never a paragraph-sized white rectangle.
All ambiguous regions remain original and are included in the output report.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
from collections import Counter

from linguafloat.document_core import DocumentError
from linguafloat.document_metrics import active_clock


def classify_page(page):
    import pymupdf as fitz
    from linguafloat.document_pdf import has_scanned_text_layer
    text = page.get_text().strip()
    images = page.get_image_info()
    if not text and not images:
        return "empty"
    if has_scanned_text_layer(page):
        return "scan_ocr"
    large = any((fitz.Rect(i["bbox"]) & page.rect).get_area() > page.rect.get_area() * .6 for i in images)
    if images and not text:
        return "scan_image"
    if large:
        return "mixed"
    return "digital"


def normalize(text):
    return re.sub(r"\W+", "", text).casefold()


def risky_math(text):
    # Inline equations are deliberately retained until their glyph positions can
    # be preserved independently from translated prose.
    return bool(re.search(r"[=∑∫√±×÷≤≥≡∂∇µα-ωΑ-Ω]|\b[A-Za-z]\s*[_^]|[₀-₉⁰-⁹]", text))


class ScanAnalyzer:
    def __init__(self, translator, emit):
        from linguafloat.document_pdf import DocumentOcr
        self.translator, self.emit = translator, emit
        self.ocr = DocumentOcr()
        self.ocr.emit = emit
        self.model = None

    def regions(self, page, kind, digest):
        from linguafloat.document_scan_recovery import recover_regions
        import numpy as np
        import pymupdf as fitz
        from importlib.metadata import version
        store = getattr(self.translator, "store", None)
        key = (f"strict-scan-input-v2:{digest}:{page.number}:"
               f"{fitz.VersionBind}:{version('rapidocr')}:{version('babeldoc')}")
        cached = store.get(key) if store else None
        data = None
        if cached:
            try:
                data = json.loads(cached)
            except (ValueError, TypeError):
                data = None
        if page.rotation or page.cropbox.x0 or page.cropbox.y0:
            return [dict(box=list(page.rect), text="", reason="旋转或偏移裁剪页尚未验证 OCR 坐标映射")]
        self.translator.control.checkpoint()
        if isinstance(data, dict) and isinstance(data.get("lines"), list) and isinstance(data.get("areas"), list):
            self.emit({"type": "measurement", "name": "扫描区域/OCR 缓存命中", "seconds": 0})
            return recover_regions(page, build_regions(page, kind, data["lines"], data["areas"]), self, digest)
        started = active_clock(self.translator.control)
        lines = self.ocr.recognize(page, positioned=True)
        if self.model is None:
            from babeldoc.docvision.doclayout import OnnxModel
            self.model = OnnxModel.from_pretrained()
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csRGB, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        prediction = self.model.predict(rgb[:, :, ::-1].copy(), imgsz=1024)[0]
        areas = []
        for item in prediction.boxes:
            if float(item.conf) < .35:
                continue
            areas.append(dict(label=prediction.names[int(item.cls)], confidence=float(item.conf),
                              box=[float(v) / 2 for v in item.xyxy]))
        self.emit({"type": "measurement", "name": "扫描页版面识别", "seconds": active_clock(self.translator.control) - started})
        if store:
            store.put(key, json.dumps(dict(lines=lines, areas=areas), ensure_ascii=False))
        return recover_regions(page, build_regions(page, kind, lines, areas), self, digest)


def separate_regions(page, regions):
    """Trim overlapping OCR padding only where the raster proves a blank gap."""
    import cv2
    import numpy as np
    import pymupdf as fitz
    from linguafloat.document_scan_recovery import starts_numbered_item
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), colorspace=fitz.csGRAY, alpha=False)
    pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    _, labels, stats, _ = cv2.connectedComponentsWithStats((pixels < 200).astype(np.uint8), 8)
    # Ignore only isolated scan speckles, smaller than a printed dot at 216 dpi.
    occupied = (stats[:, cv2.CC_STAT_AREA] > 3)[labels]
    occupied[labels == 0] = False
    for index, first in enumerate(regions):
        for second in regions[index + 1:]:
            a, b = fitz.Rect(first["box"]), fitz.Rect(second["box"])
            if not a.intersects(b):
                continue
            upper, lower = (first, second) if a.y0 < b.y0 else (second, first)
            a, b = fitz.Rect(upper["box"]), fitz.Rect(lower["box"])
            heights = [l["box"][3] - l["box"][1] for r in (upper, lower) for l in r.get("lines", [])]
            if (not heights or a.y1 - b.y0 > median(heights) * .65 or
                    (a & b).width < min(a.width, b.width) * .7):
                continue
            x0, x1 = max(0, int(min(a.x0, b.x0) * 3)), min(pix.width, int(max(a.x1, b.x1) * 3) + 1)
            y0, y1 = max(3, int(b.y0 * 3)), min(pix.height - 3, int(a.y1 * 3) + 1)
            candidates = [y for y in range(y0, y1) if not np.any(occupied[y - 3:y + 4, x0:x1])]
            if candidates:
                cut = min(candidates, key=lambda y: abs(y / 3 - (a.y1 + b.y0) / 2)) / 3
                upper["box"][3], lower["box"][1] = cut - 1 / 3, cut + 1 / 3
    # Closely set adjacent paragraphs can have no full-width whitespace stripe.
    # Join only verified prose with distinct line centers, never a protected run.
    changed = True
    while changed:
        changed = False
        for index, first in enumerate(regions):
            if first.get("reason") or not first.get("lines"):
                continue
            for second in regions[index + 1:]:
                if second.get("reason") or second.get("label") != first.get("label") or not second.get("lines"):
                    continue
                upper, lower = sorted((first, second), key=lambda r: r["box"][1])
                a, b = fitz.Rect(upper["box"]), fitz.Rect(lower["box"])
                last = max(upper["lines"], key=lambda l: (l["box"][1] + l["box"][3]) / 2)["box"]
                start = min(lower["lines"], key=lambda l: (l["box"][1] + l["box"][3]) / 2)["box"]
                height = min(last[3] - last[1], start[3] - start[1])
                if (a.intersects(b) and (a & b).width >= min(a.width, b.width) * .9
                        and not starts_numbered_item(lower["text"])
                        and a.y1 - b.y0 < height * .65
                        and (start[1] + start[3] - last[1] - last[3]) / 2 >= height * .75):
                    merged = dict(first, box=list(a | b), text=upper["text"] + "\n\n" + lower["text"],
                                  lines=upper["lines"] + lower["lines"])
                    regions[index] = merged
                    regions.remove(second)
                    changed = True
                    break
            if changed:
                break
    for region in regions:
        if not region.get("reason") and any(
                other is not region and fitz.Rect(region["box"]).intersects(fitz.Rect(other["box"]))
                for other in regions):
            region["reason"] = "OCR 区域重叠且未找到安全空白分界，保留该局部原文"


def build_regions(page, kind, lines, areas):
    from linguafloat.document_scan_recovery import starts_numbered_item
    import pymupdf as fitz
    allowed = {"plain text", "title", "figure_caption", "table_caption"}
    protected = [fitz.Rect(a["box"]) for a in areas if a["label"] not in allowed]
    visible = [fitz.Rect(c[3]) for s in page.get_texttrace()
               if s.get("type") != 3 and s.get("opacity", 1) > 0 for c in s.get("chars", ())]
    used, regions = set(), []

    def add(group, label, reason):
        rect = fitz.Rect(group[0]["box"])
        for line in group[1:]:
            rect |= fitz.Rect(line["box"])
        regions.append(dict(box=list((rect + (-1.5, -1.5, 1.5, 1.5)) & page.rect),
                            text=" ".join(l["text"] for l in group), lines=group, label=label, reason=reason))

    for area in areas:
        if area["label"] not in allowed:
            continue
        rect = fitz.Rect(area["box"]) & page.rect
        members = [(i, line) for i, line in enumerate(lines) if i not in used
                   and rect.contains((fitz.Rect(line["box"]).tl + fitz.Rect(line["box"]).br) / 2)]
        used.update(i for i, _ in members)
        members.sort(key=lambda pair: (round(pair[1]["box"][1] / 3), pair[1]["box"][0]))
        headings = [l for l in lines if normalize(l["text"]) in {"references", "参考文献"}
                    and 0 <= rect.y0 - l["box"][1] <= 120
                    and rect.x0 - 10 <= (l["box"][0] + l["box"][2]) / 2 <= rect.x1 + 10]
        if headings and sum(starts_numbered_item(l["text"]) for _, l in members) >= 2:
            add([l for _, l in members], area["label"], "参考文献保留原有编号、元数据及原始字形")
            regions[-1]["preserved"] = True
            continue
        group, previous = [], None
        for _, line in members:
            box, reason = fitz.Rect(line["box"]), ""
            if area["confidence"] < .8:
                reason = "版面归类置信度不足，需核对该局部区域"
            elif line["confidence"] < .95 or not line["horizontal"]:
                reason = "本行 OCR 置信度不足或文字倾斜，保留本行原文"
            elif any(box.intersects(p) for p in protected):
                reason = "本行与公式、图形、表格或页眉保护区域相交"
            elif kind == "mixed" and any(box.intersects(p) for p in visible):
                reason = "本行与可见数字文字层相交，无法安全分离"
            elif risky_math(line["text"]):
                reason = "本行包含公式符号，保留本行以保护公式及上下标"
            if group and reason != previous:
                add(group, area["label"], previous)
                group = []
            group.append(line)
            previous = reason
        if group:
            add(group, area["label"], previous)
    for i, line in enumerate(lines):
        if i not in used:
            regions.append(dict(box=list(line["box"]), text=line["text"], lines=[line],
                                reason="图形、公式或未确认的文字区域，保持原样"))
    if not regions:
        regions.append(dict(box=list(page.rect), text="", reason="本地 OCR 未识别到可验证文字，整页保持原样"))
    separate_regions(page, regions)
    for region in regions:
        if region.get("reason"):
            continue
        box = fitz.Rect(region["box"])
        if any(box.intersects(p) for p in protected):
            region["reason"] = "排版框与公式、图形、表格或页眉保护区域相交"
        elif kind == "mixed" and any(box.intersects(p) for p in visible):
            region["reason"] = "排版框与可见数字文字层相交，无法安全分离"
        elif kind == "scan_ocr":
            hidden = page.get_text("text", clip=box)
            if SequenceMatcher(None, normalize(hidden), normalize(region["text"]), autojunk=False).ratio() < .88:
                region["reason"] = "隐藏 OCR 文字层与本地可见文字识别不一致，需核对原文"
    return regions


def ink_patch(page, region):
    """Return a tight RGBA erasure patch or a reason to preserve the region."""
    import cv2
    import numpy as np
    import pymupdf as fitz
    from PIL import Image
    import io
    box = fitz.Rect(region.get("erase_box", region["box"]))
    if not page.rect.contains(box):
        return None, "文字区域越过页面边界"
    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=box, colorspace=fitz.csRGB, alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    light = (np.min(image, axis=2) > 210).astype(np.uint8)
    background = image[cv2.erode(light, np.ones((3, 3), np.uint8)).astype(bool)]
    if len(background) < image.shape[0] * image.shape[1] * .25:
        return None, "背景不是可验证的浅色底，未使用白色矩形遮盖"
    color = np.median(background, axis=0)
    if np.max(color) - np.min(color) > 8 or np.min(color) < 240 or np.max(np.std(background, axis=0)) > 8:
        return None, "背景存在颜色或纹理变化，局部清除可能破坏底图"
    ink = (np.min(image, axis=2) < 200).astype(np.uint8)
    if np.any(np.max(image, axis=2).astype(int) - np.min(image, axis=2).astype(int) > 24):
        return None, "区域含彩色墨迹或图线，未对其进行清除"
    allowed = np.zeros(ink.shape, np.uint8)
    for line in region.get("erase_lines", region["lines"]):
        rect = fitz.Rect(line["box"]) + (-.7, -.7, .7, .7)
        x0, y0 = max(0, int(rect.x0 * 3 - pix.x)), max(0, int(rect.y0 * 3 - pix.y))
        x1, y1 = min(pix.width, int(rect.x1 * 3 - pix.x) + 1), min(pix.height, int(rect.y1 * 3 - pix.y) + 1)
        allowed[y0:y1, x0:x1] = 1
    if np.count_nonzero(ink & (1 - allowed)) > max(6, np.count_nonzero(ink) * .015):
        return None, "OCR 行框外仍有墨迹，可能包含漏识别字符或图线"
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    mask = np.zeros(ink.shape, np.uint8)
    for index in range(1, count):
        x, y, width, height, area = stats[index]
        local = labels[y:y + height, x:x + width] == index
        if not np.any(allowed[y:y + height, x:x + width][local]):
            continue
        if x == 0 or y == 0 or x + width >= pix.width or y + height >= pix.height:
            if area <= 3:
                continue
            return None, "墨迹接触区域边界，清除可能留下半个字符或截断图线"
        if width > max(42, height * 7) or height > max(65, width * 9):
            return None, "检测到疑似图线或坐标轴，保持原样"
        mask[y:y + height, x:x + width][local] = 255
    if not np.any(mask):
        return None, "OCR 坐标内没有对应可见墨迹"
    # Cover resampling fringes of the original high-resolution scan as well as
    # the 216-dpi probe. A one-pixel mask leaves English outlines at other zooms.
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8))
    # Keep color and alpha separate. Loading a transparent PNG premultiplies
    # zero-alpha pixels to black, producing dark contour fringes in PDF viewers.
    color_buffer, mask_buffer = io.BytesIO(), io.BytesIO()
    Image.new("RGB", (pix.width, pix.height), tuple(int(v) for v in np.round(color))).save(color_buffer, format="PNG")
    Image.fromarray(mask).save(mask_buffer, format="PNG")
    return (color_buffer.getvalue(), [pix.x / 3, pix.y / 3, (pix.x + pix.width) / 3, (pix.y + pix.height) / 3],
            mask_buffer.getvalue()), ""


def fit_translation(page, region, text, target, control):
    """Probe on disposable pages; no failed fit can leak partial glyphs."""
    import pymupdf as fitz
    from linguafloat.document_layout import font_scales
    fontname = {"ja": "japan", "ko": "korea"}.get(target, "china-s")
    font = fitz.Font(fontname)
    if any(not font.has_glyph(ord(c)) for c in text if not c.isspace()):
        return None
    lines = region["lines"]
    size = max(6, min(24, median(l["box"][3] - l["box"][1] for l in lines) * 1.25))
    spans = [s for b in page.get_text("dict", clip=fitz.Rect(region["box"]))["blocks"]
             for line in b.get("lines", []) for s in line["spans"] if s.get("size", 0) > 0]
    if spans:
        size = median(s["size"] for s in spans)
    for scale in font_scales(size):
        control.checkpoint()
        if region.get("recovered") and len(lines) == 1 and "\n" not in text:
            fitted = fit_single_line(page, region, text, font, size * scale)
            if fitted:
                return fitted, dict(size=size * scale, spacing=1, method="glyph_bounds")
        for spacing in (1.3, 1.2):
            control.checkpoint()
            with fitz.open() as probe:
                trial = probe.new_page(width=page.rect.width, height=page.rect.height)
                trial.insert_font(fontname="LFScan", fontbuffer=font.buffer)
                remaining = trial.insert_textbox(fitz.Rect(region["box"]), text, fontname="LFScan",
                                                fontsize=size * scale, lineheight=spacing, color=(.12, .12, .12))
                if remaining >= 0 and normalize(trial.get_text()) == normalize(text):
                    probe.subset_fonts()
                    return probe.tobytes(), dict(size=size * scale, spacing=spacing)
    return None


def fit_single_line(page, region, text, font, size):
    """Fit real glyph ink instead of imposing a full font ascent on a scan row."""
    import numpy as np
    import pymupdf as fitz
    box = fitz.Rect(region["box"])
    if font.text_length(text, fontsize=size) > box.width:
        return None
    with fitz.open() as probe:
        trial = probe.new_page(width=page.rect.width, height=page.rect.height)
        trial.insert_font(fontname="LFScan", fontbuffer=font.buffer)
        start = fitz.Point(box.x0, box.y0 + size)
        trial.insert_text(start, text, fontname="LFScan", fontsize=size, color=(.12, .12, .12))
        pix = trial.get_pixmap(matrix=fitz.Matrix(4, 4), clip=(box + (-size, -size, size, size)) & page.rect,
                              colorspace=fitz.csGRAY, alpha=False)
        rows, cols = np.nonzero(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) < 240)
        if not len(rows):
            return None
        ink = fitz.Rect((pix.x + cols.min()) / 4, (pix.y + rows.min()) / 4,
                        (pix.x + cols.max() + 1) / 4, (pix.y + rows.max() + 1) / 4)
        if ink.width > box.width - .25 or ink.height > box.height - .25:
            return None
        start += fitz.Point(box.x0 + .125 - ink.x0, (box.y0 + box.y1 - ink.y0 - ink.y1) / 2)
        final = probe.new_page(width=page.rect.width, height=page.rect.height)
        final.insert_font(fontname="LFScan", fontbuffer=font.buffer)
        final.insert_text(start, text, fontname="LFScan", fontsize=size, color=(.12, .12, .12))
        if normalize(final.get_text()) != normalize(text):
            return None
        probe.delete_page(0)
        probe.subset_fonts()
        return probe.tobytes()


def translate_scan_page(source, number, kind, analyzer, digest, translator, emit):
    import pymupdf as fitz
    page = source[number]
    regions = analyzer.regions(page, kind, digest)
    boundary = getattr(translator.job, "reference_boundary", {}) if getattr(translator.job, "skip_references", False) else {}
    if boundary and number == boundary["page"]:
        boxes = [fitz.Rect(box) for box in boundary["boxes"]]
        if any(any(fitz.Rect(region["box"]).intersects(box) and not box.contains(fitz.Rect(region["box"]))
                   for box in boxes) for region in regions if region["text"].strip()):
            raise DocumentError(f"第 {number + 1} 页扫描识别区域跨越参考文献边界，无法安全跳过；未翻译该页。")
        regions = [region for region in regions if not any(box.contains(fitz.Rect(region["box"])) for box in boxes)]
    retained, placed = [], []
    result = fitz.open()
    result.insert_pdf(source, from_page=number, to_page=number)
    try:
        for i, region in enumerate(regions):
            translator.control.checkpoint()
            reason = region.get("reason", "")
            emit({"type": "progress", "stage": "排版中" if reason else "翻译中", "stage_key": f"scan-{number}",
                  "work_done": i, "work_total": len(regions), "value": None,
                  "message": f"保留版式：第 {number + 1} 页，区域 {i + 1}/{len(regions)}"})
            patch = None
            if not reason:
                patch, reason = ink_patch(page, region)
            translation = None
            if not reason:
                translator.location = f"扫描 PDF 第 {number + 1} 页，区域 {i + 1}"
                if region.get("context") and hasattr(translator, "translate_fragment"):
                    translation = translator.translate_fragment(region["text"], region["context"])
                else:
                    translation = translator.translate(region["text"])
                translator.control.checkpoint()
                fitted = fit_translation(page, region, translation, translator.job.target, translator.control)
                if fitted is None:
                    reason = "译文在原框及字号下限内无法完整排入，未扩大页面或新增续页"
            if reason:
                retained.append(dict(box=region["box"], source=region["text"], translation=translation,
                                     reason=reason, status="preserved" if region.get("preserved") else "untranslated",
                                     coordinates="top-left"))
                continue
            translated = result[0]
            # Remove only the hidden text beneath this patch. The image and all
            # vector artwork survive; glyph erasure is supplied by the ink mask.
            translated.add_redact_annot(fitz.Rect(region.get("erase_box", region["box"])), fill=False, cross_out=False)
            translated.apply_redactions(images=0, graphics=0, text=0)
            translated.insert_image(fitz.Rect(patch[1]), stream=patch[0], mask=patch[2])
            with fitz.open(stream=fitted[0], filetype="pdf") as overlay:
                translated.show_pdf_page(translated.rect, overlay, 0)
            placed.append(dict(page=number + 1, box=region["box"], text=translation, **fitted[1]))
        return result, retained, placed
    except BaseException:
        result.close()
        raise


def translate_mixed_layout_pdf(job, translator, output, emit, pages, kinds):
    import pymupdf as fitz
    from linguafloat.document_pdf import _translate_layout_pdf, append_bilingual, open_document
    output = Path(output)
    with open(job.source, "rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    boundary = job.reference_boundary if job.skip_references else {}
    digital = [p for p in pages if kinds[p] == "digital" and (not boundary or p <= boundary["page"])]
    digital_path = None
    report = dict(mode="strict_layout", minimum_body_pt=6, pages={str(p + 1): kinds[p] for p in pages},
                  untranslated_regions={}, preserved_regions={}, paragraphs=[], warnings=[], continuations={}, margin_references={})
    if digital:
        folder = output / "digital"
        folder.mkdir()
        subjob = replace(job, pages=",".join(str(p + 1) for p in digital), trial=False, mono=True, bilingual=False)
        paths, warnings = _translate_layout_pdf(subjob, translator, folder, emit)
        digital_path = paths[0]
        digital_report = json.loads((folder / "layout-report.json").read_text(encoding="utf-8"))
        report["untranslated_regions"].update(digital_report.get("untranslated_regions", {}))
        report["paragraphs"].extend(digital_report.get("paragraphs", []))
        report["warnings"].extend(warnings)
        report.setdefault("graphics_regions",[]).extend(digital_report.get("graphics_regions",[]))
        report.setdefault("frontmatter",[]).extend(digital_report.get("frontmatter",[]))
    analyzer = ScanAnalyzer(translator, emit)
    from linguafloat.document_graphics import DocumentEnhancer
    enhancer=DocumentEnhancer(translator,emit)
    with open_document(job.source) as source, fitz.open() as mono, fitz.open() as dual:
        for number in pages:
            translator.control.checkpoint()
            if boundary and number > boundary["page"]:
                with fitz.open() as fragment:
                    fragment.insert_pdf(source, from_page=number, to_page=number)
                    mono.insert_pdf(fragment)
                    if job.bilingual:
                        append_bilingual(dual, source, number, fragment, job.bilingual_layout)
                continue
            emit({"type": "progress", "stage": "OCR 中", "value": None,
                  "message": f"保留版式预检：第 {number + 1} 页（{kinds[number]}）"})
            if number in digital:
                with fitz.open(digital_path) as rendered:
                    fragment = fitz.open()
                    index = digital.index(number)
                    fragment.insert_pdf(rendered, from_page=index, to_page=index)
            elif kinds[number] == "empty":
                fragment = fitz.open()
                fragment.insert_pdf(source, from_page=number, to_page=number)
            else:
                fragment, retained, placed = translate_scan_page(source, number, kinds[number], analyzer,
                                                                  digest, translator, emit)
                report["paragraphs"].extend(placed)
                preserved = [r for r in retained if r.get("status") == "preserved"]
                if preserved:
                    report["preserved_regions"][str(number)] = preserved
                retained = [r for r in retained if r.get("status") != "preserved"]
                if retained:
                    report["untranslated_regions"][str(number)] = retained
                    reasons = "；".join(f"{reason}（{count} 处）" for reason, count in Counter(
                        item["reason"] for item in retained).items())
                    report["warnings"].append(f"原始第 {number + 1} 页：{len(placed)} 个区域已替换，"
                                              f"{len(retained)} 个区域保留原文。{reasons}。坐标及译文见“排版说明.txt”。")
            with fragment:
                if len(fragment) != 1 or fragment[0].rect != source[number].rect:
                    raise DocumentError(f"[保留版式尺寸校验失败] 原始第 {number + 1} 页，已停止发布。")
                if number not in digital:
                    enhancer.apply(source[number],fragment[0])
                mono.insert_pdf(fragment)
                if job.bilingual:
                    append_bilingual(dual, source, number, fragment, job.bilingual_layout)
        paths = []
        for enabled, label, document in ((job.mono, "译文", mono), (job.bilingual, "双语", dual)):
            if enabled:
                translator.control.checkpoint()
                path = output / f"{Path(job.source).stem}.{label}.pdf"
                document.save(path, garbage=4, deflate=True)
                paths.append(str(path))
    enhancer.report(report)
    (output / "layout-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths, report["warnings"]
