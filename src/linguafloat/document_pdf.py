"""PDF reading/OCR output and a version-pinned BabelDOC layout adapter."""
from __future__ import annotations

import html
import io
import hashlib
import json
import re
import threading
from importlib.metadata import version
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from linguafloat.document_core import DocumentError, selected_pages
from linguafloat.document_metrics import active_clock
from linguafloat.document_references import reference_after_end, reference_boxes, reference_page_only


def open_document(path):
    import pymupdf as fitz
    if Path(path).suffix.lower() == ".pdf":
        result = fitz.open(path)
        if result.needs_pass:
            result.close()
            raise DocumentError("PDF 已加密，请先在本地解除密码后再导入。")
        return result
    from PIL import Image, ImageOps, ImageSequence
    result = fitz.open()
    with Image.open(path) as source:
        for frame in ImageSequence.Iterator(source):
            image = ImageOps.exif_transpose(frame).convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            width, height = image.size
            scale = min(1, 1600 / max(width, height))
            page = result.new_page(width=width * scale, height=height * scale)
            page.insert_image(page.rect, stream=buffer.getvalue())
    return result


class DocumentOcr:
    def __init__(self):
        self.engine = None
        self.emit = None

    def measure(self, name, started):
        import time
        if self.emit:
            self.emit({"type": "measurement", "name": name, "seconds": time.perf_counter() - started})

    def recognize(self, page, positioned=False, *, clip=None, scale=None, words=False, single_line=False):
        import time
        import numpy as np
        import pymupdf as fitz
        import rapidocr
        if self.engine is None:
            started = time.perf_counter()
            models = Path(rapidocr.__file__).parent / "models"
            paths = {"Det.model_path": models / "PP-OCRv6_det_small.onnx",
                     "Rec.model_path": models / "PP-OCRv6_rec_small.onnx",
                     "Cls.model_path": models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"}
            if not all(path.is_file() for path in paths.values()):
                raise DocumentError("文档 OCR 模型缺失，请使用完整便携版。")
            from linguafloat.document_ocr import DocumentRapidOCR
            self.engine = DocumentRapidOCR(params={
                **{key: str(path) for key, path in paths.items()},
                "Global.log_level": "error", "Global.max_side_len": 2400,
                "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1})
            self.measure("OCR 模型初始化", started)
        started = time.perf_counter()
        area = fitz.Rect(clip) & page.rect if clip is not None else page.rect
        scale = min(scale or 2.5, 2400 / max(area.width, area.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=area, colorspace=fitz.csRGB, alpha=False)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        self.measure("OCR 页面栅格化", started)
        started = time.perf_counter()
        options = {"return_word_box": True} if words else {}
        if single_line:
            from types import SimpleNamespace
            from rapidocr.main import RapidOCRError
            self.engine.return_word_box = words
            rgb_input = image[:, :, ::-1].copy()
            try:
                recognition = self.engine.recognize_txt([rgb_input])
            except RapidOCRError as error:
                if str(error) != "The text recognize result is empty":
                    raise
                self.measure("OCR 文字识别", started)
                return [] if positioned else ""
            boxes = np.array([[[0, 0], [pix.width, 0], [pix.width, pix.height], [0, pix.height]]], dtype=np.float32)
            if recognition.txts and words:
                recognition.word_results = self.engine.calc_word_boxes([rgb_input], boxes, recognition)
            result = SimpleNamespace(txts=recognition.txts, scores=recognition.scores,
                                     boxes=boxes, word_results=recognition.word_results)
        else:
            # RapidOCR keeps per-call flags on the engine. Graphic ratio
            # probes use recognition without detection on this same instance.
            from linguafloat.document_ocr import DocumentRapidOCR
            if isinstance(self.engine, DocumentRapidOCR):
                result = self.engine(image[:, :, ::-1].copy(), use_det=True,
                                     use_cls=self.engine.cfg.Global.use_cls, use_rec=True,
                                     return_word_box=words)
            else:
                result = self.engine(image[:, :, ::-1].copy(), **options)
        self.measure("OCR 文字识别", started)
        if positioned:
            if not getattr(result, "txts", None):
                return []
            if (getattr(result, "boxes", None) is None or getattr(result, "scores", None) is None
                    or len(result.txts) != len(result.boxes) or len(result.txts) != len(result.scores)):
                raise DocumentError("[文档 OCR 数据不完整] 定位识别缺少文字框或置信度；未按无文字处理。")
            def coordinates(box):
                box = np.asarray(box)
                return [(float(box[:, 0].min()) + pix.x) / scale,
                        (float(box[:, 1].min()) + pix.y) / scale,
                        (float(box[:, 0].max()) + pix.x) / scale,
                        (float(box[:, 1].max()) + pix.y) / scale]
            lines = [dict(text=text, confidence=float(score), box=coordinates(box),
                         horizontal=bool(abs(float(box[0, 1] - box[1, 1])) <=
                                         max(2, abs(float(box[1, 0] - box[0, 0])) * .035)))
                    for text, score, box in zip(result.txts, result.scores, result.boxes) if text.strip()]
            if words:
                for line, word_line in zip(lines, getattr(result, "word_results", ()) or ()):
                    line["words"] = [dict(text=t, confidence=float(s), box=coordinates(b))
                                     for t, s, b in word_line if b is not None and t.strip()]
            return lines
        if result.txts is None:
            return ""
        return "\n".join(t for t in result.txts if t.strip())


def needs_page_ocr(page, text):
    if len(text) < 15:
        return True
    if len(text) >= 300:
        return False
    # A digital header/page number must not hide a mostly scanned page.
    import pymupdf as fitz
    area = page.rect.get_area()
    return any((fitz.Rect(item["bbox"]) & page.rect).get_area() > area * .6
               for item in page.get_image_info())


def has_scanned_text_layer(page):
    """Searchable scans have abundant invisible OCR text over a page-sized image."""
    import pymupdf as fitz
    area = page.rect.get_area()
    if not area or not any((fitz.Rect(item["bbox"]) & page.rect).get_area() > area * .6
                           for item in page.get_image_info()):
        return False
    total = hidden = 0
    for span in page.get_texttrace():
        count = len(span.get("chars", ()))
        total += count
        if span.get("type") == 3 or span.get("opacity", 1) == 0:
            hidden += count
    return total > 0 and hidden / total >= .8


def render_text_pdf(text, path, page_number):
    import pymupdf as fitz
    blocks = "".join("<p>" + html.escape(line) + "</p>" for line in text.split("\n") if line.strip())
    story = fitz.Story(html=f"<html><body><header>Page {page_number}</header>{blocks}</body></html>",
                       user_css="body { font-family: sans-serif; font-size: 11pt; line-height: 1.5; }"
                                "header { color: #65717c; font-size: 9pt; } p { margin: 8pt 0; }")
    with fitz.DocumentWriter(str(path)) as writer:
        more = True
        count = 0
        while more:
            device = writer.begin_page(fitz.Rect(0, 0, 595, 842))
            more, filled = story.place(fitz.Rect(42, 42, 553, 800))
            story.draw(device)
            writer.end_page()
            count += 1
            if count > 500:
                raise DocumentError("单页译文过长，无法排版。")


def save_composed_pdf(document, target):
    """Collect grafted resources after all page copies, overlays and links exist."""
    import os
    import tempfile
    import pymupdf as fitz

    document.save(str(target), garbage=4, deflate=True)
    # Type0 overlays can reintroduce full CJK fonts after the engine subset.
    # Reopening also resolves duplicate references left by the first GC pass.
    with fitz.open(target) as baseline:
        if (baseline.is_form_pdf or baseline.get_ocgs() or baseline.is_encrypted
                or any(page.first_annot for page in baseline)):
            return
        big_font = any(baseline.xref_get_key(x, 'Length1')[0] == 'int' and
                       int(baseline.xref_get_key(x, 'Length1')[1]) > 2_000_000
                       for x in range(1, baseline.xref_length()))
        # After grafting, identical images may still have different indirect
        # colour-space or mask references at the first save. MuPDF checks the
        # complete objects, not just compressed pixels, on the second pass.
        image_refs = {row[0] for page in baseline for row in page.get_images()}
        if not big_font and len(image_refs) < 2:
            return
    fd, candidate = tempfile.mkstemp(dir=Path(target).parent, suffix='.pdf')
    os.close(fd)
    try:
        with fitz.open(target) as compact:
            if big_font:
                compact.subset_fonts()
            compact.save(candidate, garbage=4, deflate=True)
        if os.path.getsize(candidate) >= os.path.getsize(target):
            return
        with fitz.open(target) as before, fitz.open(candidate) as after:
            if len(before) != len(after) or before.get_toc() != after.get_toc():
                return
            def links(page):
                return [{k: v for k, v in link.items() if k != 'xref'} for link in page.get_links()]
            for a, b in zip(before, after, strict=True):
                if (links(a) != links(b) or a.get_text('words') != b.get_text('words') or
                        a.get_pixmap().samples != b.get_pixmap().samples):
                    return
        os.replace(candidate, target)
    except (RuntimeError, ValueError, OSError) as exc:
        import logging
        logging.getLogger(__name__).warning('Optional PDF compaction skipped: %s', exc)
    finally:
        Path(candidate).unlink(missing_ok=True)


def append_bilingual(dual, source, number, fragment, arrangement):
    """A fragment belongs to exactly one source page, including its continuations."""
    import pymupdf as fitz
    from linguafloat.document_masthead import external_web_uri, restore_masthead_actions
    if arrangement == "alternating":
        start = len(dual)
        dual.insert_pdf(source, from_page=number, to_page=number)
        dual.insert_pdf(fragment)
        from linguafloat.document_panel_text import promote_verified_panel
        for offset in range(len(fragment)):
            promote_verified_panel(dual[start + 1 + offset])
        for index, page in enumerate((source[number], *fragment)):
            links = [dict(uri=uri, **{'from': link['from']}) for link in page.get_links()
                     if (uri := external_web_uri(page, link)) and uri.startswith('www.')]
            restore_masthead_actions(dual[start+index], links)
        return
    original = source[number]
    for index, translated_page in enumerate(fragment):
        left, right = original.rect, translated_page.rect
        height = max(left.height, right.height)
        gap, header = 20, 26
        sheet = dual.new_page(width=left.width + right.width + gap, height=height + header)
        sheet.insert_text((12, 17), f"Original {number + 1} | Translation {index + 1}/{len(fragment)}", fontsize=9)
        for document, page_no, rect in (
            (source, number, fitz.Rect(0, header, left.width, header + left.height)),
            (fragment, index, fitz.Rect(left.width + gap, header, sheet.rect.width, header + right.height)),
        ):
            # Blank pages have no content stream and cannot be shown with show_pdf_page.
            if document[page_no].get_contents():
                sheet.show_pdf_page(rect, document, page_no)
            # show_pdf_page copies drawing operators, not link annotations.
            # These panels use the native page size; external destinations stay exact.
            page = document[page_no]
            for link in page.get_links():
                if uri := external_web_uri(page, link):
                    # get_links() already returns rotated page coordinates.
                    box = fitz.Rect(link["from"])
                    box += (rect.x0, rect.y0, rect.x0, rect.y0)
                    sheet.insert_link({"kind": fitz.LINK_URI, "from": box, "uri": uri})


def restore_bilingual_source_links(dual, source, page_map, arrangement, control=None):
    """Resolve original-panel jumps only after all selected destinations exist."""
    import math
    from urllib.parse import parse_qs, urlsplit
    import pymupdf as fitz
    destinations = {item['source']: item['start'] for item in page_map}
    names = source.resolve_names()
    offset = fitz.Point(0, 0 if arrangement == 'alternating' else 26)
    restored = omitted = 0
    for item in page_map:
        if control:
            control.checkpoint()
        original = source[item['source']]
        zooms = {}
        current = original.first_link
        while current is not None:
            # PyMuPDF's dictionary reports zoom=0 even for explicit XYZ zooms;
            # its resolved destination URI retains the actual percentage.
            uri = current.uri or ''
            value = parse_qs(urlsplit(uri).fragment).get('zoom', ['0'])[0].split(',')[0] if uri.startswith('#') else '0'
            try:
                zoom = float(value) / 100
                if math.isfinite(zoom) and zoom >= 0:
                    zooms[current.xref] = zoom
            except ValueError:
                pass
            current = current.next
        copies = 1 if arrangement == 'alternating' else item['count']
        for index in range(copies):
            page = dual[item['start'] + index]
            panel = original.rect + (offset.x, offset.y, offset.x, offset.y)
            # Single-page insert_pdf can preserve self-links but drops jumps
            # elsewhere. Rebuild both together to avoid stale or duplicate IDs.
            for existing in page.get_links():
                if existing['kind'] in (fitz.LINK_GOTO, fitz.LINK_NAMED) and panel.contains(existing['from']):
                    page.delete_link(existing)
            for link in original.get_links():
                if link['kind'] not in (fitz.LINK_GOTO, fitz.LINK_NAMED):
                    continue
                target = link.get('page', -1)
                point = link.get('to')
                zoom = zooms.get(link['xref'], link.get('zoom', 0))
                viewport = None
                if link['kind'] == fitz.LINK_NAMED:
                    # Resolve in the source name tree, not the imported fragment.
                    # Reading the PDF string avoids mojibake in get_links names.
                    raw_type, name = source.xref_get_key(link['xref'], 'Dest')
                    if raw_type not in ('string', 'name'):
                        raw_type, name = source.xref_get_key(link['xref'], 'A/D')
                    entry = names.get(name) if raw_type in ('string', 'name') else None
                    if not entry or entry.get('page', -1) not in destinations:
                        omitted += 1
                        continue
                    target = entry['page']
                    transform = source[target].transformation_matrix * source[target].rotation_matrix
                    if 'to' in entry:
                        point = fitz.Point(entry['to']) * transform
                        zoom = entry.get('zoom', 0)
                    else:
                        parts = entry.get('dest', '').split()
                        try:
                            values = [float(value) for value in parts[1:]]
                        except ValueError:
                            values = []
                        if (parts[:1] != ['/FitR'] or len(values) != 4 or
                                not all(math.isfinite(value) for value in values)):
                            omitted += 1
                            continue
                        viewport = fitz.Rect(values) * transform
                        point = viewport.tl
                if target not in destinations or not isinstance(point, fitz.Point) or not all(
                        math.isfinite(value) for value in (*point, *link['from'])):
                    omitted += 1
                    continue
                box = fitz.Rect(link['from']) + (offset.x, offset.y, offset.x, offset.y)
                if box.is_empty or not panel.contains(box):
                    omitted += 1
                    continue
                destination = dual[destinations[target]]
                page.insert_link(dict(kind=fitz.LINK_GOTO, page=destinations[target], zoom=zoom,
                                      to=(point + offset) * destination.derotation_matrix,
                                      **{'from': box * page.derotation_matrix}))
                if viewport is not None:
                    target_box = (viewport + (offset.x, offset.y, offset.x, offset.y)) * (
                        destination.derotation_matrix * ~destination.transformation_matrix)
                    values = ' '.join(f'{value:.8g}' for value in target_box)
                    xref = page.annot_xrefs()[-1][0]
                    dual.xref_set_key(xref, 'A/D', f'[{destination.xref} 0 R /FitR {values}]')
                restored += 1
    return dict(restored=restored, omitted=omitted)


def check_layout_paragraph(paragraph, characters, page_number=None):
    """Reject detectable lost text or excessive shrinking in the pinned engine."""
    if not (paragraph.unicode or "").strip():
        return
    scale = paragraph.scale if paragraph.scale is not None else 1
    original_size = getattr(paragraph.pdf_style, "font_size", None)
    import re
    snippet = re.sub(r"<[^>]+>", "", paragraph.unicode)
    snippet = " ".join(snippet.split())
    snippet = snippet[:100] + ("…" if len(snippet) > 100 else "")
    location = f"原始第 {page_number} 页" if page_number is not None else "原始页码暂无法定位"
    if not characters:
        raise DocumentError(f"[PDF 文字未绘制] {location}\n"
                            f"检查结果：该段有文字，但版式引擎未生成可绘制字符。\n译文片段：{snippet}\n"
                            "可能原因：文本框、字体或复杂格式处理异常；尚不能确认具体原因。\n"
                            "处理建议：保留版式模式未改变；检查该页字体、文本框及排版报告，可复用译文仅重新排版。\n"
                            "输出状态：未发布不完整结果，原文件未修改。")
    # Original small print may remain small; it cannot be shrunk further.
    floor = min(original_size, 6) if original_size else 6
    if original_size and original_size * scale < floor - 1e-6:
        raise DocumentError(f"[PDF 字号过小] {location}\n"
                            f"检查结果：原段落字号 {original_size:.4f}pt，缩放系数 {scale:.4f}，"
                            f"排版后 {original_size * scale:.4f}pt，低于该段 {floor:g}pt 下限（正文最低 6pt；原有小字不再缩小）。\n译文片段：{snippet}\n"
                            "可能原因：文本框高度、行距、字体度量或译文长度共同影响；不代表中文一定比英文长。\n"
                            "处理建议：保留版式模式未改变；检查该页文本框及行距，可复用译文仅重新排版。\n"
                            "输出状态：未发布该次结果，原文件未修改。")


def translate_reading_pdf(job, translator, output, emit):
    import pymupdf as fitz
    from linguafloat.document_failures import diagnostic, mark_pdf
    retained = {}

    def retain(number, page, error, stage):
        retained[number] = [dict(diagnostic(error, stage), box=list(page.rect),
                                 status="untranslated", coordinates="top-left")]
    ocr = DocumentOcr()
    ocr.emit = emit
    warnings = ["阅读版重新排版，不复刻原文公式、图表及多栏布局；双语版保留原文页面。"]
    scanned = 0
    translated = 0
    store = getattr(translator, "store", None)
    with open(job.source, "rb") as handle:
        source_digest = hashlib.file_digest(handle, "sha256").hexdigest() if store else ""
    with open_document(job.source) as source, fitz.open() as mono, fitz.open() as dual:
        pages = selected_pages(job.pages, len(source), job.trial)
        boundary = job.reference_boundary if job.skip_references else {}
        texts = []
        after_texts, after_results = {}, {}
        for i, number in enumerate(pages):
            translator.location = f"PDF 第 {number + 1} 页"
            translator.control.checkpoint()
            if number in boundary.get("uncertain_pages", []):
                retain(number, source[number], DocumentError("参考文献与正文范围无法安全分离，保留本页原文"), "参考文献边界")
                texts.append("")
                continue
            if reference_page_only(boundary, number):
                texts.append("")
                continue
            page = source[number]
            cache_key = f"document-page-v1:pymupdf-{fitz.VersionBind}:rapidocr-{version('rapidocr')}:{source_digest}:{number}"
            cache_started = active_clock(translator.control)
            cached = store.get(cache_key) if store else None
            try:
                cached = json.loads(cached) if cached else None
                if not isinstance(cached, dict) or not isinstance(cached.get("text"), str) or type(cached.get("ocr")) is not bool:
                    cached = None
            except (ValueError, TypeError):
                cached = None
            emit({"type": "measurement", "name": "页面缓存查询及解码",
                  "seconds": active_clock(translator.control) - cache_started})
            emit({"type": "progress", "stage": "解析中", "stage_key": "reading-parse", "value": int(i * 10 / len(pages)), "message": f"读取第 {number + 1} 页"})
            started = active_clock(translator.control)
            text = cached["text"] if cached else page.get_text(sort=True).strip()
            used_ocr = cached["ocr"] if cached else needs_page_ocr(page, text)
            if not cached:
                emit({"type": "measurement", "name": "PDF 文本提取及页面分类",
                      "seconds": active_clock(translator.control) - started})
            if not cached and used_ocr:
                emit({"type": "progress", "stage": "OCR 中", "stage_key": f"ocr-{number}", "value": int(i * 10 / len(pages)), "message": f"本地 OCR：第 {number + 1} 页"})
                try:
                    text = ocr.recognize(page)
                except Exception as error:
                    retain(number, page, error, "OCR")
                    text = ""
            if used_ocr:
                scanned += 1
            if store and not cached and text.strip():
                store.put(cache_key, json.dumps({"text": text, "ocr": used_ocr}, ensure_ascii=False))
            elif cached:
                emit({"type": "measurement", "name": "页面解析/OCR 缓存命中", "seconds": 0})
            protected = [fitz.Rect(b) for b in reference_boxes(boundary, number)]
            if protected and number not in retained:
                from linguafloat.document_references import _digital_lines
                try:
                    rows = ocr.recognize(page, positioned=True) if used_ocr else _digital_lines(page)
                    crossing = [r for r in rows if any(fitz.Rect(r["box"]).intersects(b)
                                and not (b + (-2, -2, 2, 2)).contains(fitz.Rect(r["box"])) for b in protected)]
                    if crossing:
                        raise DocumentError("参考文献边界穿过提取行，阅读版保留该页原文")
                    if not rows:
                        raise DocumentError("参考文献页面未取得可定位文字，阅读版保留该页原文")
                except Exception as error:
                    retain(number, page, error, "参考文献边界")
                    text = ""
                else:
                    split = boundary.get("column_splits", {}).get(str(number))
                    ordered = [r for r in sorted(rows, key=lambda r: (
                               split is not None and r["box"][0] >= split, r["box"][1], r["box"][0]))
                               if not any(fitz.Rect(r["box"]).intersects(b) for b in protected)]
                    after = [r for r in ordered if reference_after_end(boundary, number, r["box"])]
                    after_texts[i] = "\n".join(r["text"] for r in after)
                    text = "\n".join(r["text"] for r in ordered if r not in after)
            translator.control.checkpoint()
            texts.append(text)
        results = [None] * len(texts)
        if hasattr(translator, "set_source_context"):
            from linguafloat.document_context import SourceBlock
            translator.set_source_context(SourceBlock(t, "page") for i, text in enumerate(texts)
                                          for t in (text, after_texts.get(i, "")) if t.strip())
        total_work = sum(len(t) for t in texts) + sum(len(t) for t in after_texts.values())
        done = 0
        def progress():
            emit({"type": "progress", "stage": "翻译中", "stage_key": "reading-translate",
                  "work_done": done, "work_total": total_work, "value": 10 + int(done * 70 / max(1, total_work)),
                  "message": f"已翻译 {done}/{total_work} 字符"})
        progress()
        def translate_page(index, after=False):
            translator.control.checkpoint()
            translator.location = f"PDF 第 {pages[index] + 1} 页"
            return translator.translate(after_texts[index] if after else texts[index])
        with ThreadPoolExecutor(max_workers=getattr(getattr(translator, "gate", None), "maximum", 1)) as pool:
            futures = {pool.submit(translate_page, i): (i, False) for i, text in enumerate(texts)
                       if text.strip()}
            futures.update({pool.submit(translate_page, i, True): (i, True) for i, text in after_texts.items() if text.strip()})
            try:
                for future in as_completed(futures):
                    translator.control.checkpoint()
                    i, after = futures[future]
                    try:
                        if after:
                            after_results[i] = future.result()
                        else:
                            results[i] = future.result()
                    except Exception as error:
                        retain(pages[i], source[pages[i]], error, "翻译")
                    done += len(after_texts[i] if after else texts[i])
                    translated += 1
                    progress()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        for i, (number, text) in enumerate(zip(pages, texts)):
            translator.control.checkpoint()
            if reference_page_only(boundary, number):
                with fitz.open() as fragment:
                    fragment.insert_pdf(source, from_page=number, to_page=number)
                    mono.insert_pdf(fragment)
                    if job.bilingual:
                        append_bilingual(dual, source, number, fragment, job.bilingual_layout)
                continue
            if reference_boxes(boundary, number) and not text.strip() and not after_texts.get(i) and number not in retained:
                with fitz.open() as fragment:
                    fragment.insert_pdf(source, from_page=number, to_page=number)
                    mono.insert_pdf(fragment)
                    if job.bilingual:
                        append_bilingual(dual, source, number, fragment, job.bilingual_layout)
                continue
            if not text.strip() and not after_texts.get(i):
                warnings.append(f"第 {number + 1} 页未识别到文字。")
                if number not in retained:
                    retain(number, source[number], DocumentError("未识别到文字"), "OCR/提取")
                result = ""
            else:
                result = results[i]
            rendered = output / "page.tmp.pdf"
            after_rendered = output / "after-references.tmp.pdf"
            emit({"type": "progress", "stage": "排版中", "stage_key": "reading-layout", "work_done": i, "work_total": len(pages), "value": 80 + int(i * 15 / len(pages)), "message": f"排版第 {number + 1} 页"})
            if number not in retained and text.strip():
                try:
                    render_text_pdf(result, rendered, number + 1)
                except Exception as error:
                    retain(number, source[number], error, "排版")
            if number not in retained and after_texts.get(i):
                try:
                    render_text_pdf(after_results[i], after_rendered, number + 1)
                except Exception as error:
                    retain(number, source[number], error, "附录排版")
            if number in retained:
                with fitz.open() as fragment:
                    fragment.insert_pdf(source, from_page=number, to_page=number)
                    mark_pdf(fragment[0], retained[number], number)
                    mono.insert_pdf(fragment)
                    if job.bilingual:
                        append_bilingual(dual, source, number, fragment, job.bilingual_layout)
                continue
            # Grafted PDF pages can retain their backing stream until dual closes.
            if text.strip():
                with fitz.open(stream=rendered.read_bytes(), filetype="pdf") as fragment:
                    mono.insert_pdf(fragment)
                    if job.bilingual:
                        append_bilingual(dual, source, number, fragment, job.bilingual_layout)
            if reference_boxes(boundary, number):
                # Reading mode has no fixed page geometry. Append the original
                # lower section as a continuation, without duplicating the body.
                with fitz.open() as original:
                    tail = original.new_page(width=source[number].rect.width, height=source[number].rect.height)
                    for box in reference_boxes(boundary, number):
                        clip = fitz.Rect(box)
                        tail.show_pdf_page(clip, source, number, clip=clip)
                    mono.insert_pdf(original)
                    if job.bilingual:
                        if job.bilingual_layout == "alternating":
                            dual.insert_pdf(original)
                        else:
                            append_bilingual(dual, source, number, original, job.bilingual_layout)
            if after_texts.get(i):
                # Keep an appendix on the same source page after its retained bibliography.
                with fitz.open(stream=after_rendered.read_bytes(), filetype="pdf") as fragment:
                    mono.insert_pdf(fragment)
                    if job.bilingual:
                        append_bilingual(dual, source, number, fragment, job.bilingual_layout)
            rendered.unlink(missing_ok=True)
            after_rendered.unlink(missing_ok=True)
            emit({"type": "progress", "stage": "排版中", "stage_key": "reading-layout", "work_done": i + 1, "work_total": len(pages), "value": 80 + int((i + 1) * 15 / len(pages)), "message": f"已完成 {i + 1}/{len(pages)} 页（原文第 {number + 1} 页）"})
        if not translated and not boundary and not retained:
            raise DocumentError("所选页面未识别到可翻译文字，请检查图片清晰度或页码。")
        outputs = []
        for enabled, label, document in ((job.mono, "译文", mono), (job.bilingual, "双语", dual)):
            if enabled:
                target = output / f"{Path(job.source).stem}.{label}.pdf"
                emit({"type": "progress", "stage": "保存中", "value": None, "message": f"保存{label}"})
                save_composed_pdf(document, target)
                outputs.append(str(target))
    if scanned:
        warnings.append(f"{scanned} 页使用了本地 OCR；复杂表格、公式和手写文字需人工核对。")
    if retained:
        (output / "layout-report.json").write_text(json.dumps(dict(mode="reading", untranslated_regions=retained),
            ensure_ascii=False, indent=2), encoding="utf-8")
    return outputs, warnings


def translate_layout_pdf(job, translator, output, emit):
    from linguafloat.document_scan_layout import classify_page, translate_mixed_layout_pdf
    with open_document(job.source) as document:
        pages = selected_pages(job.pages, len(document), job.trial)
        kinds = {p: classify_page(document[p]) for p in pages}
        scanned = [p for p in pages if kinds[p] not in {"digital", "empty"}
                   and not reference_page_only(job.reference_boundary if job.skip_references else {}, p)]
    if scanned:
        return translate_mixed_layout_pdf(job, translator, output, emit, pages, kinds)
    from babeldoc.babeldoc_exception.BabelDOCException import ScannedPDFError
    try:
        return _translate_layout_pdf(job, translator, output, emit)
    except ScannedPDFError as exc:
        raise DocumentError("[扫描类型判定不一致] 保留版式引擎检测到扫描内容，但页面预检未能安全定位扫描区域。\n"
                            f"原始页码：{', '.join(str(p + 1) for p in pages)}。\n"
                            "已停止本次输出，未切换阅读版；请按页试译以定位页面，已有译文缓存保留。") from exc


def _translate_layout_pdf(job, translator, output, emit, *, defer_graphics=False):
    translator.location = "PDF 保留版式引擎（无法可靠定位原始页码）"
    # Upstream internal adapters are isolated here and only used with tested versions.
    if version("pdf2zh-next") != "2.9.0" or version("babeldoc") != "0.6.2":
        raise DocumentError("版式引擎版本不匹配，请使用配套文档运行环境。")
    import pdf2zh_next.high_level as engine
    from babeldoc.format.pdf import high_level as babel
    from babeldoc.progress_monitor import ProgressMonitor
    from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater
    from pdf2zh_next.config.model import SettingsModel
    from pdf2zh_next.translator.base_translator import BaseTranslator
    from linguafloat.document_layout import LayoutPolicy

    class Adapter(BaseTranslator):
        name = "linguafloat"

        def __init__(self, settings):
            # Never use the engine's shared cache or its unbounded retries.
            self.lang_in, self.lang_out = settings.translation.lang_in, settings.translation.lang_out
            self.model = job.provider["model"]
            self.translate_call_count = self.translate_cache_call_count = 0

        def translate(self, text, ignore_cache=False, rate_limit_params=None):
            return self.do_translate(text, rate_limit_params)

        def llm_translate(self, text, ignore_cache=False, rate_limit_params=None):
            return self.do_llm_translate(text, rate_limit_params)

        def do_translate(self, text, rate_limit_params=None):
            return translator.request(self.prompt(text))

        def do_llm_translate(self, text, rate_limit_params=None):
            return translator.request([{"role": "user", "content": text}]) if text else text

    with open_document(job.source) as document:
        all_pages = selected_pages(job.pages, len(document), job.trial)
        pages = [p for p in all_pages if not reference_page_only(job.reference_boundary if job.skip_references else {}, p)]
    if not pages:
        from linguafloat.document_postprocess import save_body_artifact
        checkpoint = (lambda body, mapping: save_body_artifact(output, job, body, mapping, translator)) if job.workflow == "body" else None
        return (assemble_layout_pdf(job, all_pages, job.source, output, {}, translator.control,
                                   body_checkpoint=checkpoint),
                ["所选页面均在已确认的参考文献范围内，保留原页，未请求翻译。"])
    started = active_clock(translator.control)
    policy = LayoutPolicy(translator.control, job.source, set(pages), translator=translator)
    if job.skip_references and job.reference_boundary:
        policy.reference_boundary = job.reference_boundary
        import pymupdf as fitz
        with open_document(job.source) as original:
            for number in job.reference_boundary.get("uncertain_pages", []):
                if number in pages:
                    policy.retained_regions[number].append(dict(box=list(original[number].rect),
                        coordinates="top-left", status="untranslated", stage="参考文献边界",
                        reason="参考文献与正文分栏无法安全分离，保留本页原文；未宣称精确识别"))
        for page_no, (height, entries) in list(policy.scientific.references.items()):
            boxes = [fitz.Rect(box) for box in reference_boxes(job.reference_boundary, page_no)]
            if reference_page_only(job.reference_boundary, page_no):
                policy.scientific.references[page_no] = (height, [])
            else:
                policy.scientific.references[page_no] = (height, [entry for entry in entries if not any(
                    fitz.Rect(entry["box"]).intersects(box) for box in boxes)])
    from linguafloat.document_graphics import DocumentEnhancer
    enhancer = DocumentEnhancer(translator,emit)
    graphic_key=""
    if enhancer.analyzer:
        from linguafloat.document_graphics import PROTECTION_SCHEMA
        graphic_key=f"-{PROTECTION_SCHEMA}-t{int(job.translate_tables or job.workflow == 'body')}-f{int(job.translate_figures)}"
        with open_document(job.source) as document:
            for number in pages:
                areas=enhancer.analyzer.protection_regions(document[number],enhancer.digest,
                                                           translate_tables=job.translate_tables or job.workflow == "body")
                from linguafloat.document_references import boundary_intersects
                enabled=[a for a in areas if not boundary_intersects(
                    job.reference_boundary if job.skip_references else {}, number, a["box"])]
                policy.graphic_regions[number]=enabled
                height=document[number].rect.height
                for area in enabled:
                    x0,y0,x1,y1=area["box"]
                    policy.retained_regions[number].append(dict(box=[x0,height-y1,x1,height-y0],kind="graphic_source"))
    emit({"type": "measurement", "name": "原文布局保护索引", "seconds": active_clock(translator.control) - started})
    from linguafloat.document_artifacts import LayoutSnapshot
    snapshot = LayoutSnapshot(translator.store.path.parent, job, pages,
                              policy_key="source-columns-v1-richtext-v4-quantity-v4-native-structure-v3-figure-protection-v2-context-v1-"+("scientific-v7" if policy.scientific.affected else "")+graphic_key+
                              ("-bibliography-only-v4-"+hashlib.sha256(json.dumps(job.reference_boundary,sort_keys=True).encode()).hexdigest()
                               if job.skip_references else ""))
    settings = SettingsModel.model_validate({
        "translation": {"lang_in": "zh" if job.source_language == "zh-Hans" else job.source_language,
                        "lang_out": "zh" if job.target == "zh-Hans" else job.target,
                        "output": str(output), "qps": 1, "pool_max_workers": translator.gate.maximum,
                        "no_auto_extract_glossary": True, "ignore_cache": True},
        "pdf": {"pages": ",".join(str(p + 1) for p in pages), "no_mono": False,
                "no_dual": True, "watermark_output_mode": "no_watermark",
                "translate_table_text": False, "use_alternating_pages_dual": job.bilingual_layout == "alternating",
                "only_include_translated_page": False},
        "translate_engine_settings": {"translate_engine_type": "OpenAI", "openai_api_key": "local-adapter",
                                      "openai_model": job.provider["model"]}})
    settings.validate_settings()
    adapter = Adapter(settings)
    # Some engine stages recover from translation errors by retaining source text.
    # Record transport failures so such a document cannot be reported as successful.
    failures = []
    original_request = translator.request

    def guarded_request(messages, **kwargs):
        for paragraph in getattr(policy.active, "paragraphs", []):
            policy.request_keys[id(paragraph)].add(translator.cache_key(messages))
        try:
            return original_request(messages, **kwargs)
        except Exception as exc:
            from linguafloat.document_core import recoverable_transport
            if kwargs.get('recover_transport') and recoverable_transport(exc):
                # This exception belongs to the explicitly recoverable graph
                # pass after prose, not the body engine's fatal failure list.
                raise
            from linguafloat.document_failures import require_local
            try:
                require_local(exc)
            except Exception:
                failures.append(exc)
                raise
            for paragraph in ([] if getattr(policy.active, "batch", False) else getattr(policy.active, "paragraphs", [])):
                policy.retain_error(paragraph, exc, "翻译")
            raise

    translator.request = guarded_request
    original, original_term = engine.get_translator, engine.get_term_translator
    emit({"type": "progress", "stage": "解析中", "value": None, "message": "加载 PDF 版式模型和字体（首次可能需要下载）"})
    try:
        engine.get_translator = lambda settings: adapter
        engine.get_term_translator = lambda settings: adapter
        started = active_clock(translator.control)
        config = engine.create_babeldoc_config(settings, Path(job.source))
        emit({"type": "measurement", "name": "版式引擎配置及资源初始化", "seconds": active_clock(translator.control) - started})
    finally:
        engine.get_translator, engine.get_term_translator = original, original_term

    def progress(**event):
        if event["type"].startswith("progress"):
            translator.control.checkpoint()
            value = event.get("stage_progress")
            stage_name = event.get("stage", "")
            name = stage_name.lower()
            stage = ("翻译中" if "translat" in name else "字体处理中" if "font" in name else
                     "保存中" if "sav" in name else "排版中" if "typeset" in name or "drawing" in name else "解析中")
            emit({"type": "progress", "stage": stage, "stage_key": f"{event.get('part_index', 0)}:{stage_name}",
                  "work_done": event.get("stage_current"), "work_total": event.get("stage_total"),
                  "value": min(99, int(value)) if value is not None else None,
                  "progress_scope": "stage", "message": "PDF " + stage + " · " + stage_name + "（当前阶段）"})

    def run():
        # The pinned engine's async callback can lose its completion signal.
        # A synchronous worker is already off the GUI thread and returns reliably.
        with ProgressMonitor(babel.get_translation_stage(config), progress_change_callback=progress,
                             finish_callback=lambda **_: None, cancel_event=threading.Event()) as monitor:
            try:
                final = babel.do_translate(monitor, config)
            except Exception:
                if failures:
                    raise failures[0]
                raise
        if failures:
            raise failures[0]
        path = getattr(final, "no_watermark_mono_pdf_path", None) or getattr(final, "mono_pdf_path", None)
        if not path or not Path(path).is_file():
            raise DocumentError("PDF 引擎未生成完整译文。")
        emit({"type": "progress", "stage": "保存中", "stage_key": "assemble", "value": None, "message": "逐页恢复原文并生成输出"})
        return assemble_layout_pdf(job, all_pages, path, output, policy.continuation_indices,
                                   translator.control, policy.margin_references, policy.continuation_layout,
                                   policy.retained_regions,
                                   None if defer_graphics else enhancer.apply, emit=emit,
                                   body_checkpoint=body_checkpoint if job.workflow == "body" and not defer_graphics else None)
    def body_checkpoint(body, page_map):
        from linguafloat.document_postprocess import save_body_artifact
        save_body_artifact(output, job, body, page_map, translator)

    # This worker already provides process isolation and cancellation. Avoid the
    # engine spawning more Python processes for saving/subsetting on Windows.
    original_save = PDFCreater.save_pdf_with_timeout
    original_subset = PDFCreater.subset_fonts_in_subprocess
    original_render = PDFCreater.render_paragraph_to_char
    original_page_render = PDFCreater.create_render_units_for_page
    original_single, original_boxes = babel._do_translate_single, babel.fix_media_box
    media_boxes = {}
    restored = False
    paragraph_pages = {}

    def remember_boxes(document):
        result = original_boxes(document)
        media_boxes.clear()
        media_boxes.update(result or {})
        return result

    def save_snapshot(engine, docs):
        if Path(engine.translation_config.input_file).resolve() != Path(job.source).resolve():
            return
        # Older snapshots can split a chemical base and subscript. Recover exact
        # original glyphs locally, without OCR, layout detection or model requests.
        needs_source_geometry = any(part.pdf_formula
            for page in docs.page for paragraph in page.pdf_paragraph
            for part in paragraph.pdf_paragraph_composition)
        if needs_source_geometry:
            from babeldoc.format.pdf.new_parser.native_parse import parse_prepared_pdf_with_new_parser_to_legacy_ir
            started = active_clock(translator.control)
            with open_document(job.source) as original_pdf:
                original_docs = parse_prepared_pdf_with_new_parser_to_legacy_ir(
                    Path(job.source), config=engine.translation_config, doc_pdf=original_pdf)
            policy.source_characters = {page.page_number: page.pdf_character for page in original_docs.page}
            emit({"type": "measurement", "name": "原始公式与符号字形恢复", "seconds": active_clock(translator.control) - started})
        if restored:
            return
        if failures:
            raise failures[0]
        if policy.failed_paragraphs:
            # A retained source block must never become a complete translation snapshot.
            return
        started = active_clock(translator.control)
        try:
            snapshot.graphic_context = translator.graphic_context.records
            snapshot.source_context = [dict(text=b.text, role=b.role, section=b.section)
                                       for b in translator.source_context.blocks]
            snapshot.save(docs, engine.translation_config, media_boxes)
        except (OSError, ValueError, TypeError):
            policy.warnings.append("译后排版缓存未能保存；译文请求缓存仍保留。")
        finally:
            emit({"type": "measurement", "name": "保存译后中间数据", "seconds": active_clock(translator.control) - started})

    def single(monitor, translation_config):
        nonlocal restored
        if Path(translation_config.input_file).resolve() == Path(job.source).resolve():
            translator.control.checkpoint()
            started = active_clock(translator.control)
            cached = snapshot.load(translation_config, emit)
            emit({"type": "measurement", "name": "读取译后中间数据", "seconds": active_clock(translator.control) - started})
            # Caption regrouping needs original glyphs and belongs to source parsing.
            # Do not discard accepted translations when replaying an older snapshot.
            if cached:
                from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
                restored = True
                docs, prepared, boxes = cached
                translator.graphic_context.restore(snapshot.graphic_context)
                from linguafloat.document_context import SourceBlock
                translator.set_source_context(SourceBlock(**b) for b in snapshot.source_context)
                translation_config.progress_monitor = monitor
                emit({"type": "progress", "stage": "排版中", "stage_key": "snapshot",
                      "value": None, "message": "复用完整译后数据，跳过解析、版面识别和 API 翻译"})
                emit({"type": "measurement", "name": "译后中间数据缓存命中", "seconds": 0})
                Typesetting(translation_config).typesetting_document(docs)
                result = PDFCreater(prepared, docs, translation_config, boxes).write(translation_config)
                result.original_pdf_path = translation_config.input_file
                return result
        return original_single(monitor, translation_config)

    policy.before_layout = save_snapshot

    def render_page(creater, page, translation_config):
        for paragraph in page.pdf_paragraph:
            paragraph_pages[id(paragraph)] = page.page_number + 1
            policy.paragraph_pages[id(paragraph)] = page
        return original_page_render(creater, page, translation_config)

    def render(creater, paragraph):
        try:
            characters = original_render(creater, paragraph)
            check_layout_paragraph(paragraph, characters, paragraph_pages.get(id(paragraph)))
        except Exception as exc:
            policy.retain_error(paragraph, exc, "绘制/校验")
            return []
        return characters

    def save(pdf, output_path, translation_config, garbage=1, deflate=True,
             clean=True, deflate_fonts=True, linear=False, timeout=120, tag=""):
        translator.control.checkpoint()
        started = active_clock(translator.control)
        try:
            pdf.save(str(output_path), garbage=garbage, deflate=deflate, clean=clean,
                     deflate_fonts=deflate_fonts, linear=linear)
        finally:
            emit({"type": "measurement", "name": "引擎 PDF 保存", "seconds": active_clock(translator.control) - started})
        return True

    def subset(pdf, translation_config, tag):
        translator.control.checkpoint()
        policy.append_continuations(pdf, translation_config)
        started = active_clock(translator.control)
        try:
            pdf.subset_fonts()
        finally:
            emit({"type": "measurement", "name": "字体子集处理", "seconds": active_clock(translator.control) - started})
        return pdf

    PDFCreater.save_pdf_with_timeout = staticmethod(save)
    PDFCreater.subset_fonts_in_subprocess = staticmethod(subset)
    PDFCreater.render_paragraph_to_char = render
    PDFCreater.create_render_units_for_page = render_page
    babel._do_translate_single = single
    babel.fix_media_box = remember_boxes
    try:
        from linguafloat.document_fonts import BabelDOCFontPolicy
        with BabelDOCFontPolicy(job.chinese_font, job.latin_font) as fonts:
            with policy:
                paths = run()
        report = {"minimum_body_pt": 6, "paragraphs": policy.records, "warnings": policy.warnings,
             "font_policy": fonts.report(), "render_policy_key": snapshot.render_key,
             "normal_preserved_regions": {number: [r for r in regions if r.get("kind") == "graphic_source"]
                                          for number, regions in policy.retained_regions.items()},
             "reference_boundary": job.reference_boundary,
             "geometry_repairs": policy.geometry_repairs,
             "continuations": dict(policy.continuation_indices),
             "continuation_layout": dict(policy.continuation_layout),
             "mode": "strict_layout", "untranslated_regions": {
                 number:pending for number,regions in policy.retained_regions.items()
                 if (pending := [r for r in regions if r.get("kind")!="graphic_source"])},
             "margin_references": dict(policy.margin_references)}
        enhancer.report(report)
        (output / "layout-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        return paths, list(dict.fromkeys(policy.warnings))
    finally:
        PDFCreater.save_pdf_with_timeout = staticmethod(original_save)
        PDFCreater.subset_fonts_in_subprocess = staticmethod(original_subset)
        PDFCreater.render_paragraph_to_char = original_render
        PDFCreater.create_render_units_for_page = original_page_render
        babel._do_translate_single = original_single
        babel.fix_media_box = original_boxes
        translator.request = original_request


def continuation_clip(rendered, index, info):
    import pymupdf as fitz
    source = rendered[index]
    rect = fitz.Rect(info["box"]) * source.transformation_matrix * source.rotation_matrix
    return (rect + (-2, -2, 2, 2)) & source.rect


def append_reference_page(fragment, rendered, number, references, control, indices=(), layout=()):
    """Add a separate reference gutter; original page content stays at 100% scale."""
    import pymupdf as fitz
    source = rendered[number]
    entries = []
    for reference in references:
        index = reference.get("content_index")
        clip = continuation_clip(rendered, indices[index], layout[index]) if index is not None else None
        entries.append((reference, clip, max(42, (clip.height + 46) if clip else 42)))
    height = max(source.rect.height, 110, max(item[2] for item in entries) + 60)
    column_width = 180 if any(clip for _, clip, _ in entries) else 156
    column, cursor = 0, 40
    positions = []
    for reference, clip, slot in entries:
        if cursor + slot > height - 16:
            column, cursor = column + 1, 40
        positions.append((reference, clip, column, cursor))
        cursor += slot
    page = fragment.new_page(width=source.rect.width + column_width * (column + 1), height=height)
    if source.get_contents():
        page.show_pdf_page(source.rect, rendered, number)
    for column in range(column + 1):
        x = source.rect.width + column * column_width
        page.draw_line((x + 5, 12), (x + 5, page.rect.height - 12), color=(.7, .7, .7), width=.5)
        page.insert_text((x + 13, 24), "Supplementary translations", fontsize=9)
    for reference, clip, column, cursor in positions:
        control.checkpoint()
        x = source.rect.width + column * column_width + 13
        y = cursor + 6
        page.insert_text((x, y), reference["marker"], fontsize=9)
        if clip:
            content_index = indices[reference["content_index"]]
            destination = fitz.Rect(x, y + 5, x + clip.width, y + 5 + clip.height)
            page.show_pdf_page(destination, rendered, content_index, clip=clip)
            y += clip.height + 7
        # IL uses bottom-left coordinates; transform to the displayed page.
        rect = fitz.Rect(reference["box"]) * source.transformation_matrix * source.rotation_matrix
        rect = rect & source.rect
        if not rect.is_empty:
            page.draw_rect(rect, color=(.15, .4, .65), width=.5, dashes="[2 2] 0")
        page.insert_text((x, y + 12), f"Source p.{number + 1}, outlined area", fontsize=7)
        page.insert_text((x, y + 23), f"x={rect.x0:.1f}, y={rect.y0:.1f} pt (top-left)", fontsize=7)


def append_packed_continuations(fragment, rendered, number, indices, layout, control):
    """Flow deferred paragraphs into shared pages while preserving rendered glyphs."""
    import pymupdf as fitz
    page = None
    y = 0
    original = rendered[number]
    for offset, (index, info) in enumerate(zip(indices, layout)):
        control.checkpoint()
        if info["inline"]:
            continue
        clip = continuation_clip(rendered, index, info)
        required = 18 + clip.height + 14
        if page is None or y + required > page.rect.height - 32:
            page = fragment.new_page(width=max(original.rect.width, clip.width + 80),
                                     height=max(original.rect.height, required + 80))
            page.insert_text((40, 24), f"Original page {number + 1} | Additional translations", fontsize=9)
            y = 46
        page.insert_text((40, y + 10), f"[P{number + 1}-{offset + 1}]", fontsize=9)
        page.show_pdf_page(fitz.Rect(40, y + 18, 40 + clip.width, y + 18 + clip.height),
                           rendered, index, clip=clip)
        y += required


def assemble_layout_pdf(job, pages, rendered_path, output, continuation_indices, control, margin_references=None,
                        continuation_layout=None, retained_regions=None, enhance=None, emit=None, body_checkpoint=None):
    """Group each translated page and its continuations with the correct original."""
    import pymupdf as fitz
    from linguafloat.document_metrics import active_clock
    def progress(message, number=None):
        control.checkpoint()
        if emit:
            emit({"type": "progress", "stage": "保存中", "stage_key": "assemble", "value": None,
                  "message": message, "page": None if number is None else number + 1})

    def measured(name, started, number=None):
        if emit:
            emit({"type": "measurement", "name": name, "seconds": active_clock(control) - started,
                  "page": None if number is None else number + 1})
    paths = []
    page_map = []
    with open_document(job.source) as source, fitz.open(rendered_path) as rendered, fitz.open() as mono, fitz.open() as dual:
        bilingual_map = []

        def append_dual(number, fragment):
            start = len(dual)
            append_bilingual(dual, source, number, fragment, job.bilingual_layout)
            bilingual_map.append(dict(source=number, start=start, count=len(dual) - start))

        publication = {}
        masthead = None
        if 0 in pages and not reference_page_only(job.reference_boundary if job.skip_references else {}, 0):
            from linguafloat.document_masthead import repair_masthead
            started = active_clock(control)
            masthead = repair_masthead(source[0], rendered[0], control,
                                       (getattr(job,'chinese_font',''), getattr(job,'latin_font','')))
            if masthead and emit:
                emit(dict(type='measurement', name='期刊刊头重排',
                          seconds=active_clock(control) - started, **masthead))
        if job.target == 'zh-Hans' and getattr(job,'chinese_font','') and getattr(job,'latin_font',''):
            from linguafloat.document_publication import repair_dates, repair_author_identifiers
            for number in pages:
                control.checkpoint()
                details=[]
                dates=repair_dates(source[number],rendered[number],(job.chinese_font,job.latin_font))
                if dates:details.extend(dates)
                contacts=repair_author_identifiers(source[number],rendered[number],(job.chinese_font,job.latin_font))
                if contacts:details.extend(contacts)
                if details:publication[number]=dict(dates=dates,contacts=contacts)
        for ordinal, number in enumerate(pages, 1):
            control.checkpoint()
            page_started = active_clock(control)
            progress(f"恢复与合成第 {ordinal}/{len(pages)} 页（原始第 {number + 1} 页）", number)
            if reference_page_only(job.reference_boundary if job.skip_references else {}, number):
                with fitz.open() as fragment:
                    fragment.insert_pdf(source, from_page=number, to_page=number)
                    page_map.append(dict(source=number, start=len(mono), count=len(fragment), selected=False))
                    mono.insert_pdf(fragment)
                    if job.bilingual:
                        append_dual(number, fragment)
                measured("输出页面合成", page_started, number)
                continue
            with fitz.open() as fragment:
                indices = continuation_indices.get(number, [])
                layout = (continuation_layout or {}).get(number)
                if layout is not None and len(layout) != len(indices):
                    raise DocumentError(f"[PDF 补充译文索引不一致] 原始第 {number + 1} 页，已停止输出以避免漏字；译文缓存保留。")
                for index in [number, *([] if layout is not None else indices)]:
                    if index == number and (margin_references or {}).get(number):
                        append_reference_page(fragment, rendered, number, margin_references[number], control, indices, layout or [])
                    else:
                        fragment.insert_pdf(rendered, from_page=index, to_page=index)
                if layout is not None:
                    append_packed_continuations(fragment, rendered, number, indices, layout, control)
                started = active_clock(control)
                regions = (retained_regions or {}).get(number, [])
                if regions:
                    from linguafloat.document_clip import prepare_text_redaction
                    prepare_text_redaction(fragment[0])
                for region in regions:
                    control.checkpoint()
                    original = source[number]
                    if publication.get(number,{}).get('contacts'):
                        from linguafloat.document_publication import is_replaced_identifier
                        if is_replaced_identifier(original,region,publication[number]['contacts']):
                            continue
                    rect = fitz.Rect(region["box"]) * original.transformation_matrix * original.rotation_matrix
                    rect &= original.rect
                    if rect.is_empty or fragment[0].rect != original.rect:
                        raise DocumentError(f"[原文区域恢复失败] 原始第 {number + 1} 页，页面坐标或尺寸不一致。未发布不完整结果。")
                    if region.get("kind")=="graphic_source":
                        fragment[0].add_redact_annot(rect,fill=False,cross_out=False)
                        fragment[0].apply_redactions(images=0,graphics=0,text=0)
                        # Replace the whole graphic with the original vector/image content,
                        # rather than layering it over glyphs shifted by the body engine.
                        fragment[0].draw_rect(rect,color=None,fill=(1,1,1),overlay=True)
                    else:
                        # Clear partially emitted glyphs before restoring the exact source.
                        fragment[0].add_redact_annot(rect, fill=(1,1,1), cross_out=False)
                        fragment[0].apply_redactions(images=0, graphics=0, text=0)
                    from linguafloat.document_clip import show_source_region
                    show_source_region(fragment[0],rect,source,number)
                measured("局部原文恢复", started, number)
                if enhance:
                    enhance(source[number],fragment[0])
                if publication.get(number,{}).get('contacts'):
                    for link in source[number].get_links():
                        if link.get('kind') != fitz.LINK_URI or not link.get('uri'):
                            continue
                        uri=link['uri']
                        if uri.startswith('mailto:'):
                            found=fragment[0].search_for(uri[7:])
                            if len(found)==1:
                                fragment[0].insert_link({'kind':fitz.LINK_URI,'from':found[0],'uri':uri})
                        elif uri.startswith('https://') and 'orcid.org/' not in uri:
                            native=source[number].get_textbox(fitz.Rect(link['from'])).strip()
                            if native.startswith('https://'):
                                found=fragment[0].search_for(native)
                                if len(found)==1:
                                    fragment[0].insert_link({'kind':fitz.LINK_URI,'from':found[0],'uri':uri})
                started = active_clock(control)
                if job.skip_references and reference_boxes(job.reference_boundary, number):
                    from linguafloat.document_references import retain_pdf_tail
                    retain_pdf_tail(fragment[0], source[number], dict(boxes=reference_boxes(job.reference_boundary, number)))
                measured("参考文献原文恢复", started, number)
                from linguafloat.document_publisher_footer import restore_publisher_footer
                restore_publisher_footer(source[number], fragment[0])
                from linguafloat.document_failures import mark_pdf
                progress(f"定位未翻译标记 · 第 {ordinal}/{len(pages)} 页", number)
                started = active_clock(control)
                mark_pdf(fragment[0], (retained_regions or {}).get(number, []), number, control=control)
                measured("未翻译区域标记", started, number)
                started = active_clock(control)
                if number == 0 and masthead:
                    from linguafloat.document_masthead import restore_masthead_actions
                    restore_masthead_actions(fragment[0], masthead['links'])
                from linguafloat.document_publisher_footer import restore_printed_urls
                restore_printed_urls(source[number], fragment[0])
                page_map.append(dict(source=number, start=len(mono), count=len(fragment), selected=True))
                mono.insert_pdf(fragment)
                if number == 0 and masthead:
                    restore_masthead_actions(mono[page_map[-1]['start']], masthead['links'])
                if job.bilingual:
                    append_dual(number, fragment)
                contacts=publication.get(number,{}).get('contacts',[])
                if contacts:
                    from linguafloat.document_publication import draw_author_identifiers
                    fonts=(job.chinese_font,job.latin_font)
                    draw_author_identifiers(mono[page_map[-1]['start']],contacts,fonts)
                    if job.bilingual:
                        first=len(dual)-len(fragment)
                        offset=((0,0) if job.bilingual_layout=='alternating' else
                                (source[number].rect.width+20,26))
                        draw_author_identifiers(dual[first],contacts,fonts,offset=offset)
                measured("单语与双语页面合成", started, number)
                measured("输出页面合成", page_started, number)
        if job.bilingual:
            started = active_clock(control)
            progress("恢复双语原文面板的跨页跳转")
            links = restore_bilingual_source_links(dual, source, bilingual_map, job.bilingual_layout, control)
            if emit:
                emit(dict(type='measurement', name='双语原文跳转恢复',
                          seconds=active_clock(control) - started, **links))
        if body_checkpoint:
            body_checkpoint(mono, page_map)
        for enabled, label, document in ((job.mono, "译文", mono), (job.bilingual, "双语", dual)):
            if enabled:
                target = output / f"{Path(job.source).stem}.{label}.pdf"
                progress(f"整理 PDF 资源并写入{label}文件")
                started = active_clock(control)
                save_composed_pdf(document, target)
                measured(f"{label}资源整理及写入", started)
                paths.append(str(target))
    return paths
