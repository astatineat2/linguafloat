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
        # Publishers sometimes outline every glyph. Such pages have neither
        # text operators nor image objects, but still need the OCR pipeline.
        return "scan_vector" if page.get_drawings() else "empty"
    if has_scanned_text_layer(page):
        return "scan_ocr"
    large = any((fitz.Rect(i["bbox"]) & page.rect).get_area() > page.rect.get_area() * .6 for i in images)
    if images and not text:
        return "scan_image"
    if large:
        # A bounded plate and its adjacent native caption are a digital page,
        # not an image of the text. Never promote page-sized OCR backgrounds.
        plates = [fitz.Rect(i['bbox']) & page.rect for i in images
                  if (fitz.Rect(i['bbox']) & page.rect).get_area() > page.rect.get_area() * .6]
        if len(plates) == 1:
            plate = plates[0]
            traces = page.get_texttrace()
            if all(s['type'] != 3 and s.get('opacity',1)>0 for s in traces):
                for block in page.get_text('blocks'):
                    if block[6] or not re.match(r'^\s*(?:Fig(?:ure)?\.?|图)\s*\d+\b', block[4], re.I):
                        continue
                    box = fitz.Rect(block[:4])
                    if (0 <= box.y0-plate.y1 <= page.rect.height*.05 and
                            min(box.x1,plate.x1)-max(box.x0,plate.x0) >= box.width*.7 and
                            any(s['chars'] and fitz.Rect(s['bbox']).intersects(box) for s in traces)):
                        return 'digital'
        return "mixed"
    return "digital"


def normalize(text):
    return re.sub(r"\W+", "", text).casefold()


def join_ocr_lines(lines):
    """Remove line-break artifacts, never guess missing letters or cross regions."""
    result = ""
    known_words = set(re.findall(r"\b[A-Za-z]{4,}\b", " ".join(l["text"] for l in lines)))
    for line in lines:
        text = line["text"].strip()
        if not result:
            result = text
            continue
        if result.endswith("\u00ad"):
            result = result[:-1] + text
        elif result.endswith("-") and re.match(r"[a-z]", text):
            left = re.search(r"([A-Za-z]+)-$", result)
            right = re.match(r"([a-z]+)", text)
            combined = (left[1] + right[1]) if left and right else ""
            # Ordinary hyphens are ambiguous; remove only with evidence from
            # another complete occurrence in this same OCR region.
            result = result[:-1] + text if combined in known_words else result + text
        else:
            result += " " + text
    return result


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
        self.table_analyzer = None
        self.form_owned_tables = {}

    def build(self,page,kind,lines,areas,digest):
        import pymupdf as f
        owners = []
        if self.table_analyzer and getattr(self.translator.job,'translate_tables',False):
            fields = ruled_field_boxes(page)
            for table in self.table_analyzer.discover_areas(page,digest):
                if table['kind'] != 'table':
                    continue
                bounds = f.Rect(table['box'])
                # A response field is inset after a distinct label column and
                # spans most of the row; ordinary adjacent grid cells do not.
                if any(b.x0-bounds.x0 >= 16 and b.width >= bounds.width*.55 and
                       not any(o is not b and abs(o.y0-b.y0)<2 and abs(o.y1-b.y1)<2
                               and (o.x1<=b.x0+1 or b.x1<=o.x0+1) for o in fields) and
                       (bounds & b).get_area() > .8*b.get_area() and any(
                        b.contains(f.Rect(l['box'])) and re.search(r'[A-Za-z]{3,}\s+[A-Za-z]{2,}',l['text'])
                        for l in lines) for b in fields):
                    self.form_owned_tables.setdefault(page.number,set()).add(table['id'])
                else:
                    owners.append(dict(label='owned_table',confidence=1,box=table['box']))
        return build_regions(page,kind,lines,[*areas,*owners])

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
            return recover_regions(page, self.build(page, kind, data['lines'], data['areas'],digest), self, digest)
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
        if store and lines:
            store.put(key, json.dumps(dict(lines=lines, areas=areas), ensure_ascii=False))
        return recover_regions(page, self.build(page, kind, lines, areas,digest), self, digest)


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
                if first.get('label') == 'form_choice' and first.get('controls') != second.get('controls'):
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
                    if first.get('label') == 'form_choice':
                        merged['source_paragraph_lines'] = [
                            *upper.get('source_paragraph_lines', [upper['lines']]),
                            *lower.get('source_paragraph_lines', [lower['lines']])]
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


def ruled_field_boxes(page):
    """Find wide closed fields from rectangular paths or four matching strokes."""
    import pymupdf as fitz
    horizontal, vertical, corners, boxes = [], [], [], []
    for path in page.get_drawings():
        if path['type'] != 's':
            continue
        for item in path['items']:
            if item[0] == 're':
                boxes.append(fitz.Rect(item[1]))
            elif item[0] == 'c':
                a,b = item[1],item[4]
                if 0.5 <= abs(a.x-b.x) <= 8 and abs(abs(a.x-b.x)-abs(a.y-b.y)) < .5:
                    corners.append((a,b))
            elif item[0] == 'l':
                a, b = item[1:3]
                if abs(a.y-b.y) <= .5 and abs(a.x-b.x) >= 60:
                    horizontal.append((min(a.x,b.x), max(a.x,b.x), (a.y+b.y)/2))
                elif abs(a.x-b.x) <= .5:
                    vertical.append(((a.x+b.x)/2, min(a.y,b.y), max(a.y,b.y)))
    def connected(a,b):
        return abs(a-b) < 1 or any(min(abs(a-i)+abs(b-j),abs(b-i)+abs(a-j)) < 1 for i,j in corners)
    for x0, x1, top in horizontal:
        bottom = min((y for l,r,y in horizontal if abs(x0-l)<1 and abs(x1-r)<1 and y>top+2), default=None)
        if bottom is not None:
            edges = [[x for x,y0,y1 in vertical if abs(x-edge)<=8 and
                      connected(fitz.Point(edge,top),fitz.Point(x,y0)) and
                      connected(fitz.Point(edge,bottom),fitz.Point(x,y1))] for edge in (x0,x1)]
            if all(len(set(side)) == 1 for side in edges):
                boxes.append(fitz.Rect(edges[0][0],top,edges[1][0],bottom))
    return sorted((b for b in boxes if b.width >= 60 and 9 <= b.height <= b.width*.7), key=lambda b:(b.y0,b.x0))


def checkbox_label_rows(page, lines):
    """Attach labels to repeated square controls, not to detector paragraphs."""
    import pymupdf as fitz
    squares = [fitz.Rect(item[1]) for path in page.get_drawings()
               if path['type'] == 's' for item in path['items'] if item[0] == 're'
               and 4 <= fitz.Rect(item[1]).width <= 16
               and .8 <= fitz.Rect(item[1]).height / fitz.Rect(item[1]).width <= 1.25]
    repeated = [box for box in squares if any(
        abs(box.x0-other.x0) < 1 and abs(box.width-other.width) < 1
        and box.height < abs(box.y0-other.y0) < box.height*4
        for other in squares)]
    result = {}
    for i, line in enumerate(lines):
        box = fitz.Rect(line['box'])
        controls = [c for c in repeated if 0 <= box.x0-c.x1 <= max(box.height,c.height)*2
                    and abs((box.y0+box.y1-c.y0-c.y1)/2) < max(box.height,c.height)*.6]
        if controls and not any(box.intersects(c) for c in squares):
            result[i] = controls
    return result


def build_regions(page, kind, lines, areas):
    from linguafloat.document_scan_recovery import starts_numbered_item
    import pymupdf as fitz
    allowed = {"plain text", "title", "figure_caption", "table_caption"}
    protected = [fitz.Rect(a["box"]) for a in areas if a["label"] not in allowed]
    visible = [fitz.Rect(c[3]) for s in page.get_texttrace()
               if s.get("type") != 3 and s.get("opacity", 1) > 0 for c in s.get("chars", ())]
    used, regions = set(), []
    # Fence native seeds and pixels too, even when OCR recognized no labels.
    for area in areas:
        if area['label'] in ('figure','owned_table') and area['confidence'] >= .9:
            fence = fitz.Rect(area['box']) & page.rect
            used.update(i for i, l in enumerate(lines) if fence.contains(fitz.Rect(l['box'])))
            regions.append(dict(box=list(fence), text='', lines=[], label=area['label'], preserved=True,
                                reason='独立表格由单元格流程处理' if area['label']=='owned_table' else
                                '图内文字由图片翻译处理，正文保留原图'))

    def add(group, label, reason):
        rect = fitz.Rect(group[0]["box"])
        for line in group[1:]:
            rect |= fitz.Rect(line["box"])
        regions.append(dict(box=list((rect + (-1.5, -1.5, 1.5, 1.5)) & page.rect),
                            text=join_ocr_lines(group), lines=group, label=label, reason=reason,
                            outline_prose=kind == 'scan_vector' or (kind=='scan_image' and label=='title')))

    fields = ruled_field_boxes(page) if kind in ('scan_image','scan_vector') else []
    for i, controls in checkbox_label_rows(page, lines).items():
        line = lines[i]
        if i in used or any(fitz.Rect(line['box']).intersects(p) for p in protected):
            continue
        used.add(i)
        safe = line['confidence'] >= .95 and line['horizontal'] and not risky_math(line['text'])
        add([line], 'form_choice', '' if safe else '复选项文字尚未核实，保留该项及勾选状态')
        region = regions[-1]
        box = (fitz.Rect(line['box'])+(-.5,-.5,.5,.5)) & page.rect
        box.x0 = max(box.x0, max(c.x1 for c in controls)+.6)
        region.update(atomic=True, controls=[list(c) for c in controls], box=list(box))
    for area in areas:
        if area["label"] not in allowed:
            continue
        rect = fitz.Rect(area["box"]) & page.rect
        members = [(i, line) for i, line in enumerate(lines) if i not in used
                   and rect.contains((fitz.Rect(line["box"]).tl + fitz.Rect(line["box"]).br) / 2)
                   and not any(field.contains(fitz.Rect(line['box'])) for field in fields)]
        used.update(i for i, _ in members)
        members.sort(key=lambda pair: (round(pair[1]["box"][1] / 3), pair[1]["box"][0]))
        headings = [l for l in lines if normalize(l["text"]) in {"references", "参考文献"}
                    and 0 <= rect.y0 - l["box"][1] <= 120
                    and rect.x0 - 10 <= (l["box"][0] + l["box"][2]) / 2 <= rect.x1 + 10]
        if headings and sum(starts_numbered_item(l["text"]) for _, l in members) >= 2:
            add([l for _, l in members], area["label"], "参考文献保留原有编号、元数据及原始字形")
            regions[-1]["preserved"] = True
            continue
        if area['label'] in ('figure_caption', 'table_caption') and members:
            group = [line for _, line in members]
            add(group, area['label'], '')
            region = regions[-1]
            region['atomic'] = True
            if (area['confidence'] < .8 or any(l['confidence'] < .95 or not l['horizontal']
                    or risky_math(l['text']) for l in group)):
                region['reason'] = '图表说明含未核实的文字或公式，整组保留原文'
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
            separated = False
            if group:
                last = fitz.Rect(group[-1]["box"])
                # A detector can put two columns/paragraphs inside one region.
                # Keep separate ownership rather than joining by proximity alone.
                horizontal_overlap = min(box.x1, last.x1) - max(box.x0, last.x0)
                separated = (box.y0 - last.y1 > max(last.height, box.height) * .9 or
                             horizontal_overlap <= 0)
            if group and (reason != previous or separated):
                add(group, area["label"], previous)
                group = []
            group.append(line)
            previous = reason
        if group:
            add(group, area["label"], previous)
    if fields:
        for field in fields:
            group = [(i,l) for i,l in enumerate(lines) if i not in used and field.contains(fitz.Rect(l['box']))]
            if not group or not any(re.search(r'[A-Za-z]{3,}\s+[A-Za-z]{2,}', l['text']) for _,l in group):
                continue
            if any((i in used or not field.contains(fitz.Rect(l['box']))) and field.intersects(fitz.Rect(l['box']))
                   for i,l in enumerate(lines)):
                continue
            if any(r.get('preserved') and field.intersects(fitz.Rect(r['box'])) for r in regions):
                continue
            used.update(i for i,_ in group)
            members = sorted((l for _,l in group), key=lambda l:(l['box'][1],l['box'][0]))
            safe = all(l['confidence'] >= .95 and l['horizontal'] and not risky_math(l['text']) for l in members)
            add(members, 'form_field', '' if safe else '字段含不确定文字或科学符号，整项保留')
            regions[-1].update(atomic=True, field_box=list(field), box=list(fitz.Rect(regions[-1]['box']) & field))
    for i, line in enumerate(lines):
        if i not in used:
            regions.append(dict(box=list(line["box"]), text=line["text"], lines=[line],
                                reason="图形、公式或未确认的文字区域，保持原样"))
            if (re.match(r'^Downloaded (?:from|via)\b', line['text'], re.I) and any(
                    a['label'] == 'abandon' and a['confidence'] >= .9 and
                    fitz.Rect(a['box']).contains(fitz.Rect(line['box'])) for a in areas)):
                regions[-1].update(preserved=True, reason='下载水印，保留原文')
    if not regions:
        regions.append(dict(box=list(page.rect), text="", reason="本地 OCR 未识别到可验证文字，整页保持原样"))
    separate_regions(page, regions)
    for region in regions:
        if region.get("reason"):
            continue
        box = fitz.Rect(region["box"])
        if region.get('label') == 'form_field':
            if any(a['label'] not in allowed | {'table'} and box.intersects(fitz.Rect(a['box'])) for a in areas):
                region['reason'] = '表单字段与其他受保护区域相交，整项保留'
        elif any(box.intersects(p) for p in protected):
            region["reason"] = "排版框与公式、图形、表格或页眉保护区域相交"
        elif kind == "mixed" and any(box.intersects(p) for p in visible) and not (
                region.get('atomic') and verified_native_text(page, region)):
            region["reason"] = "排版框与可见数字文字层相交，无法安全分离"
        elif kind == "scan_ocr":
            hidden = page.get_text("text", clip=box)
            if SequenceMatcher(None, normalize(hidden), normalize(region["text"]), autojunk=False).ratio() < .88:
                region["reason"] = "隐藏 OCR 文字层与本地可见文字识别不一致，需核对原文"
    return regions


def verified_native_text(page, region):
    """Authorize colored text by visible glyph identity, not its color or box alone."""
    import pymupdf as fitz
    lines = region.get('erase_lines', region.get('lines', []))
    if not lines or any(l.get('confidence', 0) < .95 for l in lines):
        return False
    bounds = fitz.Rect(region.get('erase_box', region['box']))
    if any(kind not in ('fill-text', 'stroke-text', 'ignore-text') and bounds.intersects(fitz.Rect(box))
           for kind, box, *_ in page.get_bboxlog()):
        return False
    text = ''.join(chr(c[0]) for span in page.get_texttrace() if span['type'] != 3 and span.get('opacity', 1) > 0
                   for c in span['chars'] if bounds.intersects(fitz.Rect(c[3])))
    return bool(text.strip()) and ''.join(text.split()) == ''.join(''.join(l['text'].split()) for l in lines)


def verified_outline_text(page, region, bounds):
    """For flattened text, require glyph-sized fills and corroborating OCR rows."""
    import pymupdf as fitz
    if not region.get('outline_prose'):
        return False
    lines = region.get('erase_lines', region.get('lines', []))
    if not lines or any(l.get('confidence', 0) < .97 or not l.get('horizontal') or
                        not re.search(r'[A-Za-z]{3,}', l['text']) or risky_math(l['text']) for l in lines):
        return False
    if region.get('recovered') and not all(l.get('outline_whole_row') for l in lines):
        return False
    if any(kind in ('fill-image', 'fill-shade') and bounds.intersects(fitz.Rect(box))
           for kind, box, *_ in page.get_bboxlog()):
        return False
    own = [(fitz.Rect(line['box']), len(re.sub(r'\s+', '', line['text'])), []) for line in lines]
    for path in page.get_cdrawings():
        rect = fitz.Rect(path['rect'])
        if path['type'] != 'f':
            pad = max(.15, (path.get('width') or 0)/2)
            if bounds.intersects(rect+(-pad,-pad,pad,pad)):
                return False
            continue
        if not bounds.intersects(rect):
            # A tight OCR crop can end after a valid word but before the end
            # of the field. Inspect the original baseline beyond both edges.
            for row, _, _ in own:
                gap = max(rect.x0 - row.x1, row.x0 - rect.x1)
                overlap = min(rect.y1, row.y1) - max(rect.y0, row.y0)
                if (.0 <= gap <= row.height and .05 <= rect.width <= row.height*1.65
                        and row.height*.35 <= rect.height <= row.height+.9
                        and overlap >= rect.height*.75):
                    return False
            continue
        if not bounds.contains(rect) or any(item[0] in ('re','qu') for item in path['items']):
            return False
        matches = [(row, paths) for row, _, paths in own if (row+(-.6,-.6,.6,.6)).contains(rect)]
        if len(matches) != 1:
            return False
        row, paths = matches[0]
        if not .05 <= rect.width <= row.height*1.65 or not .05 <= rect.height <= row.height+.9:
            return False
        paths.append((rect, any(item[0] == 'c' for item in path['items'])))
    for row, expected, paths in own:
        if not .55*expected <= len(paths) <= 1.6*expected or not any(curved for _, curved in paths):
            return False
        covered = fitz.Rect(paths[0][0])
        for rect, _ in paths[1:]:
            covered |= rect
        if covered.width < row.width*.75 or covered.height < row.height*.45:
            return False
    return True


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
    if (np.any(np.max(image, axis=2).astype(int) - np.min(image, axis=2).astype(int) > 24)
            and not verified_native_text(page, region) and not verified_outline_text(page, region, box)):
        # Layout padding can cross a rule below a heading. Try the complete
        # OCR row with identical ink safeguards; never use a partial word crop.
        if (region.get('outline_prose') and region.get('label')=='title' and
                len(region.get('lines', []))==1 and not region.get('erase_box') and not region.get('recovered')):
            tight=fitz.Rect(region['lines'][0]['box'])
            if box.contains(tight) and tight!=box and verified_outline_text(page,region,tight):
                patch,reason=ink_patch(page,dict(region,erase_box=list(tight)))
                if patch is not None and not reason:
                    region['erase_box']=list(tight)
                    return patch,''
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
    from linguafloat.document_graphics import graphic_font_resolver
    from linguafloat.document_graphic_layout import GraphicFontMetrics
    region.pop('_fit_failure', None)
    resolver = region.get('_font_resolver') or graphic_font_resolver(
        region.get('chinese_font', ''), region.get('latin_font', ''))
    if resolver is not None:
        # OCR supplies no reliable style evidence. Use the chosen regular faces;
        # missing glyphs raise an explicit diagnostic instead of a fallback.
        from linguafloat.document_fonts import FontPolicyError
        try:
            font = GraphicFontMetrics(resolver, text)
        except FontPolicyError as error:
            region['_fit_failure'] = str(error)
            return None
    else:
        fontname = {"ja": "japan", "ko": "korea"}.get(target, "china-s")
        font = fitz.Font(fontname)
        if any(not font.has_glyph(ord(c)) for c in text if not c.isspace()):
            return None
    lines = region["lines"]
    size = max(6, min(24, median(l["box"][3] - l["box"][1] for l in lines) * 1.25))
    # A searchable scan's invisible OCR font can have arbitrary metrics. Only
    # visible native glyphs contained in this region may override raster height.
    box = fitz.Rect(region["box"])
    spans = [s for s in page.get_texttrace() if s.get("type") != 3 and s.get("opacity", 1) > 0
             and s.get("size", 0) > 0 and box.contains(fitz.Rect(s["bbox"]))]
    if spans:
        size = median(s["size"] for s in spans)
    for scale in font_scales(size):
        control.checkpoint()
        if (region.get("recovered") or region.get('label') == 'form_choice') and len(lines) == 1 and "\n" not in text:
            fitted = fit_single_line(page, region, text, font, size * scale)
            if fitted:
                metadata = dict(size=size * scale, spacing=1, method="glyph_bounds")
                if resolver is not None:
                    metadata['font_policy'] = resolver.report()
                return fitted, metadata
        for spacing in (1.3, 1.2):
            control.checkpoint()
            if resolver is not None:
                fitted = fit_policy_paragraph(page, region, text, font, size * scale, spacing)
                if fitted:
                    return fitted, dict(size=size * scale, spacing=spacing,
                                        font_policy=resolver.report())
                continue
            with fitz.open() as probe:
                trial = probe.new_page(width=page.rect.width, height=page.rect.height)
                trial.insert_font(fontname="LFScan", fontbuffer=font.buffer)
                remaining = trial.insert_textbox(fitz.Rect(region["box"]), text, fontname="LFScan",
                                                fontsize=size * scale, lineheight=spacing, color=(.12, .12, .12))
                if remaining >= 0 and normalize(trial.get_text()) == normalize(text):
                    probe.subset_fonts()
                    return probe.tobytes(), dict(size=size * scale, spacing=spacing)
    return None


def draw_scan_line(page, origin, text, font, size):
    """Use the same face runs as measurement; leave legacy drawing unchanged."""
    if hasattr(font, 'resolver'):
        font.resolver.draw_line(page, origin, text, fontsize=size, color=(.12, .12, .12))
    else:
        page.insert_font(fontname="LFScan", fontbuffer=font.buffer)
        page.insert_text(origin, text, fontname="LFScan", fontsize=size, color=(.12, .12, .12))


def fit_policy_paragraph(page, region, text, font, size, spacing):
    """Selected-face paragraph fit with the existing scan spacing choices."""
    import pymupdf as fitz
    from linguafloat.document_graphic_layout import wrapped_lines
    box = fitz.Rect(region['box'])
    if box.is_empty or not page.rect.contains(box):
        return None
    paragraphs = text.splitlines()
    groups = region.get('source_paragraph_lines', [])
    # Merging tight notes adds semantic paragraph separators to the request,
    # not evidence of a full blank printed row. Keep the two text groups and
    # their break, using compact spacing only for the same verified controls.
    if (region.get('label') == 'form_choice' and len(groups) > 1 and
            len(re.split(r'\n\s*\n', text)) == len(groups) and all(groups)):
        heights = [line['box'][3]-line['box'][1] for group in groups for line in group]
        gaps = [min(line['box'][1] for line in after)-max(line['box'][3] for line in before)
                for before, after in zip(groups, groups[1:])]
        if min(heights) > 0 and all(0 <= gap <= median(heights)*.65 for gap in gaps):
            paragraphs = [line for line in paragraphs if line.strip()]
    # All other explicit paragraph spacing remains intact.
    lines = [line for paragraph in paragraphs for line in
             (wrapped_lines(paragraph, font, size, box.width) or [''])]
    left, top, right, bottom = font.padding(size)
    height = (font.ascender-font.descender)*size + top + bottom
    step = max(size*spacing, height)
    if (not lines or height+(len(lines)-1)*step > box.height or
            any(font.text_length(line, fontsize=size) > box.width for line in lines)):
        return None
    with fitz.open() as probe:
        trial = probe.new_page(width=page.rect.width, height=page.rect.height)
        for index, line in enumerate(lines):
            draw_scan_line(trial, (box.x0+left, box.y0+top+font.ascender*size+index*step), line, font, size)
        if normalize(trial.get_text()) != normalize(text):
            return None
        probe.subset_fonts()
        return probe.tobytes()


def fit_single_line(page, region, text, font, size):
    """Fit real glyph ink instead of imposing a full font ascent on a scan row."""
    import numpy as np
    import pymupdf as fitz
    box = fitz.Rect(region["box"])
    if font.text_length(text, fontsize=size) > box.width:
        return None
    with fitz.open() as probe:
        trial = probe.new_page(width=page.rect.width, height=page.rect.height)
        start = fitz.Point(box.x0, box.y0 + size)
        draw_scan_line(trial, start, text, font, size)
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
        draw_scan_line(final, start, text, font, size)
        if normalize(final.get_text()) != normalize(text):
            return None
        probe.delete_page(0)
        probe.subset_fonts()
        return probe.tobytes()


def validate_scan_values(source, translation):
    from linguafloat.document_scientific import prose_numbers
    from linguafloat.document_context import scientific_quantities
    if prose_numbers(source) != prose_numbers(translation) or scientific_quantities(source) != scientific_quantities(translation):
        raise DocumentError('图表说明或字段的数值校验失败，整组保留原文')


def translate_scan_page(source, number, kind, analyzer, digest, translator, emit):
    import pymupdf as fitz
    from linguafloat.document_failures import diagnostic
    from linguafloat.document_graphics import graphic_font_resolver
    font_pair = (getattr(translator.job, 'chinese_font', ''), getattr(translator.job, 'latin_font', ''))
    cached_font = getattr(translator, '_scan_font_policy', None)
    if cached_font is None or cached_font[:2] != font_pair:
        cached_font = (*font_pair, graphic_font_resolver(*font_pair))
        translator._scan_font_policy = cached_font
    page = source[number]
    try:
        regions = analyzer.regions(page, kind, digest)
    except Exception as error:
        issue = diagnostic(error, "OCR/版面识别")
        result = fitz.open()
        result.insert_pdf(source, from_page=number, to_page=number)
        return result, [dict(issue, box=list(page.rect), coordinates="top-left", status="untranslated")], []
    if not regions:
        result = fitz.open()
        result.insert_pdf(source, from_page=number, to_page=number)
        return result, [dict(box=list(page.rect), coordinates="top-left", status="untranslated",
                            stage="OCR", reason="扫描页未取得可定位文字，保留整页原图；无法判断全部漏识别位置")], []
    boundary = getattr(translator.job, "reference_boundary", {}) if getattr(translator.job, "skip_references", False) else {}
    from linguafloat.document_references import reference_boxes
    if reference_boxes(boundary, number):
        boxes = [fitz.Rect(box) for box in reference_boxes(boundary, number)]
        for region in regions:
            if any(fitz.Rect(region["box"]).intersects(box) and not box.contains(fitz.Rect(region["box"])) for box in boxes):
                region["reason"] = "扫描区域跨越参考文献边界；保留该区域原图，其他区域继续翻译"
        regions = [region for region in regions if not any(box.contains(fitz.Rect(region["box"])) for box in boxes)]
    for region in regions:
        if region.get('reason') or 'erase_box' not in region:
            continue
        erase = fitz.Rect(region['erase_box'])
        for other in regions:
            if other is not region and (erase.intersects(fitz.Rect(other['box'])) or
                    erase.intersects(fitz.Rect(other.get('erase_box',other['box'])))):
                region['reason'] = '擦除范围与另一处文字相交，保留原文'
                if not other.get('reason'):
                    other['reason'] = region['reason']
    if hasattr(translator, "set_source_context"):
        from linguafloat.document_context import SourceBlock
        translator.set_source_context((*translator.source_context.blocks, *(SourceBlock(
            r["text"], r.get("label", "body")) for r in regions if not r.get("reason"))))
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
            details = {}
            if not reason:
                try:
                    patch, reason = ink_patch(page, region)
                except Exception as error:
                    details = diagnostic(error, "原图区域分析")
                    reason = details.pop("reason")
            translation = None
            if not reason:
                translator.location = f"扫描 PDF 第 {number + 1} 页，区域 {i + 1}"
                stage = "翻译"
                try:
                    if region.get('atomic') and hasattr(translator, 'translate_fragment'):
                        translation = translator.translate_fragment(region['text'], region.get('context', ''),
                            part='扫描图注或字段', validate=lambda value: validate_scan_values(region['text'], value))
                    elif region.get("context") and hasattr(translator, "translate_fragment"):
                        translation = translator.translate_fragment(region["text"], region["context"])
                    else:
                        translation = translator.translate(region["text"])
                    translator.control.checkpoint()
                    if region.get('atomic'):
                        validate_scan_values(region['text'], translation)
                    if hasattr(translator, "graphic_context"):
                        translator.graphic_context.remember(number, region["text"], translation,
                                                            region.get("label", "body"))
                    stage = "排版"
                    fitting_region = dict(region, _font_resolver=cached_font[2]) if cached_font[2] is not None else region
                    fitted = fit_translation(page, fitting_region, translation, translator.job.target, translator.control)
                    if fitted is None:
                        reason = fitting_region.get('_fit_failure') or "译文在原框及字号下限内无法完整排入，未扩大页面或新增续页"
                except Exception as error:
                    details = diagnostic(error, stage)
                    reason = details.pop("reason")
            if reason:
                retained.append(dict(box=region["box"], **details,
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
    from linguafloat.document_pdf import (
        _translate_layout_pdf, append_bilingual, open_document, restore_bilingual_source_links)
    output = Path(output)
    with open(job.source, "rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    boundary = job.reference_boundary if job.skip_references else {}
    from linguafloat.document_references import reference_page_only,reference_boxes
    from linguafloat.document_reviewed import reviewed_pages
    reviewed={p:file for p,file in reviewed_pages(getattr(translator,'store',None),digest,job).items()
              if p in pages and not reference_page_only(boundary,p) and not reference_boxes(boundary,p)}
    digital = [p for p in pages if p not in reviewed and kinds[p] == "digital" and not reference_page_only(boundary, p)]
    digital_path = None
    report = dict(mode="strict_layout", minimum_body_pt=6, pages={str(p + 1): kinds[p] for p in pages},
                  untranslated_regions={}, preserved_regions={}, paragraphs=[], warnings=[], continuations={}, margin_references={})
    if digital:
        folder = output / "digital"
        folder.mkdir()
        subjob = replace(job, pages=",".join(str(p + 1) for p in digital), trial=False, mono=True, bilingual=False)
        paths, warnings = _translate_layout_pdf(subjob, translator, folder, emit, defer_graphics=True)
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
    if hasattr(analyzer,'form_owned_tables') and hasattr(enhancer,'analyzer'):
        analyzer.table_analyzer = enhancer.analyzer
        if enhancer.analyzer:
            enhancer.analyzer.form_owned_tables = analyzer.form_owned_tables
    with open_document(job.source) as source, fitz.open() as mono, fitz.open() as dual:
        bilingual_map = []
        for number in pages:
            translator.control.checkpoint()
            if reference_page_only(boundary, number):
                with fitz.open() as fragment:
                    fragment.insert_pdf(source, from_page=number, to_page=number)
                    mono.insert_pdf(fragment)
                continue
            emit({"type": "progress", "stage": "OCR 中", "value": None,
                  "message": f"保留版式预检：第 {number + 1} 页（{kinds[number]}）"})
            if number in reviewed:
                fragment=fitz.open(reviewed[number]);report.setdefault('reviewed_pages',[]).append(number+1)
                emit(dict(type='progress',stage='排版中',value=None,message=f'第 {number+1} 页采用已核对的本机校对页'))
            elif number in digital:
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
                    from linguafloat.document_failures import mark_pdf
                    mark_pdf(fragment[0], report["untranslated_regions"].get(str(number), []), number)
                mono.insert_pdf(fragment)
        # Every selected page's body is now available as bounded label context.
        for index, number in enumerate(pages):
            translator.control.checkpoint()
            if number not in reviewed and not reference_page_only(boundary, number):
                enhancer.apply(source[number], mono[index])
                from linguafloat.document_publisher_footer import restore_printed_urls
                restore_printed_urls(source[number], mono[index])
            if job.bilingual:
                with fitz.open() as fragment:
                    fragment.insert_pdf(mono, from_page=index, to_page=index)
                    start = len(dual)
                    append_bilingual(dual, source, number, fragment, job.bilingual_layout)
                    bilingual_map.append(dict(source=number, start=start, count=len(dual)-start))
        if job.bilingual:
            report['source_link_restoration'] = restore_bilingual_source_links(
                dual, source, bilingual_map, job.bilingual_layout, translator.control)
        paths = []
        if job.workflow == "body":
            from linguafloat.document_postprocess import save_body_artifact
            save_body_artifact(output, job, mono,
                [dict(source=p, start=i, count=1, selected=not reference_page_only(boundary, p))
                 for i, p in enumerate(pages)], translator)
        for enabled, label, document in ((job.mono, "译文", mono), (job.bilingual, "双语", dual)):
            if enabled:
                translator.control.checkpoint()
                path = output / f"{Path(job.source).stem}.{label}.pdf"
                document.save(path, garbage=4, deflate=True)
                paths.append(str(path))
    enhancer.report(report)
    (output / "layout-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths, report["warnings"]
