"""Conservative bibliography boundaries for document translation."""
from __future__ import annotations

import re

from linguafloat.document_core import DocumentError


HEADING = re.compile(r"^(?:(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.、)\s]+)?(?:references|bibliography|works cited|参考文献|参考资料|引用文献)\s*[:：]?$", re.I)
NUMBER = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})[.)、])\s*\S")


def is_heading(text):
    return bool(HEADING.fullmatch(re.sub(r"\s+", " ", text).strip()))


def numbered_evidence(lines):
    values = [int(m.group(1) or m.group(2)) for text in lines if (m := NUMBER.match(text))]
    return any(a == 1 and b == 2 for a, b in zip(values, values[1:]))


def _digital_lines(page):
    rows = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            row = dict(text="".join(s["text"] for s in line["spans"]), box=line["bbox"],
                       size=max((s["size"] for s in spans), default=0),
                       bold=bool(spans) and all(s.get("flags", 0) & 16 for s in spans))
            # Some publishers put raised numbers on a shared baseline and do
            # not mark them as superscripts. Require a separate, smaller run.
            if len(spans) >= 2 and line.get("dir", (1, 0)) == (1, 0):
                label, rest = spans[0], spans[1:]
                body_size = max(s["size"] for s in rest)
                if (re.fullmatch(r"[1-9]\d{0,2}", label["text"].strip())
                        and .5 * body_size <= label["size"] <= .85 * body_size
                        and 0 <= rest[0]["bbox"][0] - label["bbox"][2] <= body_size):
                    row.update(citation_number=int(label["text"].strip()),
                               label_right=label["bbox"][2],
                               citation_text="".join(s["text"] for s in rest).strip())
            rows.append(row)
    return rows


def _untitled_candidate(lines, number, page_count, page):
    """A narrow fallback for digital, full-width, raised-number bibliographies.

    Four aligned entries, independently dated and bibliographic, are required.
    The caller must also confirm a separate text region with the layout model.
    OCR text and ordinary numbered lists cannot satisfy this font evidence.
    """
    if number < (page_count - 1) * .6:
        return None
    for first in lines:
        if first.get("citation_number") != 1:
            continue
        top = first["box"][1]
        if not page.rect.height * .06 < top < page.rect.height * .9:
            continue
        anchors = [r for r in lines if r["box"][1] >= top and "citation_number" in r
                   and abs(r["label_right"] - first["label_right"]) < 3]
        if [r["citation_number"] for r in anchors[:4]] != [1, 2, 3, 4]:
            continue
        entries = []
        for index, row in enumerate(anchors[:4]):
            bottom = anchors[index + 1]["box"][1] if index + 1 < len(anchors) else page.rect.height * .94
            continuation = [r for r in lines if row["box"][1] < r["box"][1] < bottom
                            and r["box"][0] >= first["box"][0] - 3
                            and r["box"][2] <= first["box"][2] + 15]
            text = row["citation_text"] + " " + " ".join(r["text"] for r in continuation)
            author = re.match(r"(?:[A-Z]\.\s*){1,3}[A-Z][\w'-]+", text)
            year = re.search(r"\((?:18|19|20)\d{2}[a-z]?\)", text)
            publication = re.search(r"\b(?:Proceedings|Journal|doi|arXiv)\b|\b(?:Phys|Rev|Lett)\.|[\u201c\"][^\u201d\"]{8,}[\u201d\"]", text)
            if not (author and year and publication):
                break
            entries.append(row)
        if len(entries) == 4:
            return entries
    return None


def _untitled_boundary(page, number, entries, prediction):
    import pymupdf as fitz
    first = fitz.Rect(entries[0]["box"])
    centers = [(fitz.Rect(r["box"]).tl + fitz.Rect(r["box"]).br) / 2 for r in entries]
    regions = [(prediction.names[int(b.cls)], float(b.conf), fitz.Rect([float(v)/2 for v in b.xyxy]))
               for b in prediction.boxes]
    if any(name in {"figure", "table", "abandon", "isolate_formula"} and confidence >= .6
           and any(box.contains(center) for center in centers) for name, confidence, box in regions):
        return None
    for name, confidence, box in regions:
        if (name != "plain text" or confidence < .8 or box.width < page.rect.width * .5
                or abs(box.y0 - first.y0) > 6
                or not all((box + (-3, -3, 3, 3)).contains(center) for center in centers)):
            continue
        # The new route only accepts one broad text column, not an ambiguous
        # cut through another column, figure, table, or preceding paragraph.
        if any(other_name in {"plain text", "title", "figure", "table"} and score >= .6
               and other.y1 > first.y0 and (other.x1 < box.x0 or other.x0 > box.x1
                                           or other.y0 < first.y0 - 6)
               for other_name, score, other in regions):
            continue
        return dict(page=number, y=float(first.y0), title="参考文献（无标题）", scanned=False,
                    column="full", boxes=[[0, first.y0, page.rect.width, page.rect.height]],
                    evidence="untitled-superscript-citations", confirmed_entries=4)
    return None


def _is_repeated_margin_line(source, number, row):
    """Recognize digital running furniture, not arbitrary right-aligned text.

    Require matching text and geometry on an adjacent page, within the outer
    six percent of both pages. This only affects column evidence; source text
    and the retained bibliography are never removed.
    """
    page = source[number]
    x0, y0, x1, y1 = row["box"]
    height, width = page.rect.height, page.rect.width
    if not (y1 <= height * .06 or y0 >= height * .94):
        return False
    text = re.sub(r"\s+", " ", row["text"]).strip()
    if not text or is_heading(text) or NUMBER.match(text):
        return False
    for adjacent in (number - 1, number + 1):
        if not 0 <= adjacent < len(source):
            continue
        other_page = source[adjacent]
        if other_page.rotation or other_page.cropbox.x0 or other_page.cropbox.y0:
            continue
        other_height, other_width = other_page.rect.height, other_page.rect.width
        if abs(other_height - height) > 3 or abs(other_width - width) > 3:
            continue
        for block in other_page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                other_text = re.sub(r"\s+", " ", "".join(span["text"] for span in line["spans"])).strip()
                box = line["bbox"]
                if (other_text == text
                        and (box[3] <= other_height * .06 or box[1] >= other_height * .94)
                        and all(abs(a - b) <= 3 for a, b in zip((x0, y0, x1, y1), box))):
                    return True
    return False


def boundary_intersects(boundary, number, box):
    if boundary and "page_boxes" in boundary:
        import pymupdf as fitz
        return any(fitz.Rect(box).intersects(fitz.Rect(area)) for area in reference_boxes(boundary, number))
    if not boundary or number < boundary["page"]:
        return False
    if number > boundary["page"]:
        return True
    import pymupdf as fitz
    return any(fitz.Rect(box).intersects(fitz.Rect(area)) for area in boundary["boxes"])


def filter_layout_paragraphs(page, boundary, retain=None):
    """Exclude source tail before any engine or bibliography requests."""
    if not boundary or page.page_number < boundary["page"]:
        return
    if page.page_number > boundary["page"] and "page_boxes" not in boundary:
        page.pdf_paragraph[:] = []
        return
    height = page.cropbox.box.y2
    boxes = [(x0, height-y1, x1, height-y0) for x0,y0,x1,y1 in reference_boxes(boundary, page.page_number)]
    keep = []
    for paragraph in page.pdf_paragraph:
        box = paragraph.box
        if box is None:
            raise DocumentError(f"第 {page.page_number + 1} 页段落缺少坐标，无法安全判断参考文献边界。")
        overlaps = [a for a in boxes if box.x < a[2] and box.x2 > a[0] and box.y < a[3] and box.y2 > a[1]]
        if overlaps:
            if is_heading(paragraph.unicode or ""):
                continue
            if not any(box.x >= a[0]-2 and box.x2 <= a[2]+2 and box.y >= a[1]-2 and box.y2 <= a[3]+2 for a in overlaps):
                error = DocumentError(f"第 {page.page_number + 1} 页参考文献边界穿过正文段落；该段保留原文，需核对。")
                if retain is None:
                    raise error
                retain(paragraph, error, "参考文献边界")
        else:
            keep.append(paragraph)
    page.pdf_paragraph[:] = keep


def retain_pdf_tail(target, source, boundary):
    """Clip the translated prefix and original tail without destructive redaction."""
    import pymupdf as fitz
    if source.rotation or source.cropbox.x0 or source.cropbox.y0:
        raise DocumentError("参考文献回填遇到未验证的旋转/裁剪坐标，已停止保存。")
    with fitz.open(stream=target.parent.tobytes(), filetype="pdf") as rendered:
        prefix = [target.rect]
        for area in boundary["boxes"]:
            box = fitz.Rect(area)
            remaining = []
            for rectangle in prefix:
                overlap = rectangle & box
                if overlap.is_empty:
                    remaining.append(rectangle)
                else:
                    remaining.extend(r for r in (
                        fitz.Rect(rectangle.x0, rectangle.y0, rectangle.x1, overlap.y0),
                        fitz.Rect(rectangle.x0, overlap.y1, rectangle.x1, rectangle.y1),
                        fitz.Rect(rectangle.x0, overlap.y0, overlap.x0, overlap.y1),
                        fitz.Rect(overlap.x1, overlap.y0, rectangle.x1, overlap.y1)) if not r.is_empty)
            prefix = remaining
        # Replace content streams, preserving page geometry and annotations.
        for xref in target.get_contents():
            target.parent.update_stream(xref, b"")
        for clip in prefix:
            if rendered[target.number].get_contents():
                target.show_pdf_page(clip, rendered, target.number, clip=clip)
        for area in boundary["boxes"]:
            clip = fitz.Rect(area)
            if source.get_contents():
                target.show_pdf_page(clip, source.parent, source.number, clip=clip)


def require_boundary(boundary, kind):
    if boundary is None:
        raise DocumentError(f"已启用‘参考文献及后续内容不翻译’，但无法在{kind}中可靠确认正文参考文献标题和连续条目。未发送翻译请求；请检查章节结构或关闭此选项。")
    return boundary


def _pdf_start(source, control, emit):
    """Return the first credible heading's page and top coordinate."""
    from linguafloat.document_pdf import DocumentOcr, has_scanned_text_layer, needs_page_ocr

    ocr = DocumentOcr()
    ocr.emit = emit
    layout_model = None
    def predict(page, number):
        nonlocal layout_model
        import numpy as np
        import pymupdf as fitz
        if layout_model is None:
            from babeldoc.docvision.doclayout import OnnxModel
            layout_model = OnnxModel.from_pretrained()
        control.checkpoint()
        emit(dict(type="progress", stage="解析中", value=None,
                  message=f"核对第 {number + 1} 页参考文献章节边界"))
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csRGB, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        return layout_model.predict(rgb[:, :, ::-1].copy(), imgsz=1024)[0]

    for number, page in enumerate(source):
        control.checkpoint()
        if page.rotation or page.cropbox.x0 or page.cropbox.y0:
            continue
        text = page.get_text("text")
        scanned = has_scanned_text_layer(page) or needs_page_ocr(page, text)
        if scanned:
            lines = ocr.recognize(page, positioned=True)
        else:
            lines = _digital_lines(page)
        lines.sort(key=lambda line: (line["box"][1], line["box"][0]))
        if any(re.fullmatch(r"(?:table of )?contents|目录|目\s+录", row["text"].strip(), re.I)
               and row["box"][1] < page.rect.height * .25 for row in lines):
            continue
        if sum(bool(re.search(r"(?:\.{3,}|…{2,}|·{3,})\s*\d+\s*$", row["text"])) for row in lines) >= 2:
            continue  # Contents pages can contain a References entry.
        for line in lines:
            if not is_heading(line["text"]):
                continue
            if scanned and (line.get("confidence", 0) < .95 or not line.get("horizontal", False)):
                continue
            if not scanned:
                import pymupdf as fitz
                if any(fitz.Rect(image["bbox"]).contains(fitz.Rect(line["box"])) for image in page.get_image_info()):
                    continue  # Image labels are not document chapter headings.
            top = line["box"][1]
            if top < page.rect.height * .025 or top > page.rect.height * .94:
                continue  # Running headers and footers are not chapter headings.
            if not scanned and top < page.rect.height * .12:
                repeated_header = False
                for adjacent in (number - 1, number + 1):
                    if not 0 <= adjacent < len(source):
                        continue
                    repeated_header |= any(
                        is_heading("".join(span["text"] for span in other["spans"]))
                        and abs(other["bbox"][1] - top) < 3
                        for block in source[adjacent].get_text("dict")["blocks"] if "lines" in block
                        for other in block["lines"])
                if repeated_header:
                    continue
            subsequent = [row["text"] for row in lines if top + 2 < row["box"][1] < top + 350]
            if number + 1 < len(source) and top > page.rect.height * .7:
                next_text = source[number + 1].get_text("text")
                if next_text:
                    subsequent.extend(next_text.splitlines()[:70])
            if not numbered_evidence(subsequent):
                continue
            # A title and numbered labels can occur inside a scientific figure.
            # Validate candidates against the installed local layout detector.
            import pymupdf as fitz
            prediction = predict(page, number)
            center = (fitz.Rect(line["box"]).tl + fitz.Rect(line["box"]).br) / 2
            if any(prediction.names[int(item.cls)] in {"figure", "table", "abandon"}
                   and float(item.conf) >= .6 and fitz.Rect([float(v)/2 for v in item.xyxy]).contains(center)
                   for item in prediction.boxes):
                continue
            # A single horizontal cut cannot preserve reading order when a
            # column contains pre-heading body text below the cut.
            # A repeated author/journal header is not another body column.
            # Keep unconfirmed margin text and all scanned text conservative.
            column_lines = lines if scanned else [
                row for row in lines if not _is_repeated_margin_line(source, number, row)]
            left, right = line["box"][0], line["box"][2]
            anchors = [row for row in column_lines if row["box"][1] > top + 2 and NUMBER.match(row["text"])]
            right_anchors = [row for row in anchors if row["box"][0] >= page.rect.width * .48]
            left_body = [row for row in column_lines if row["box"][1] > top + 2 and
                         row["box"][2] < page.rect.width * .48 and len(row["text"].strip()) > 15]
            uncertain = False
            if (left + right) / 2 > page.rect.width * .6 and len(right_anchors) >= 2 and left_body:
                column = "right"
                left_edge = max(row["box"][2] for row in left_body)
                right_edge = min(row["box"][0] for row in right_anchors)
                if right_edge <= left_edge:
                    uncertain = True
                    boxes = [[0, 0, page.rect.width, page.rect.height]]
                else:
                    boxes = [[(left_edge+right_edge)/2, top, page.rect.width, page.rect.height]]
            elif any(row["box"][0] > page.rect.width * .54 and row["box"][1] < top - 2
                     and len(re.findall(r"[A-Za-z]{2,}|[\u4e00-\u9fff]", row["text"])) >= 3
                     and row["box"][2] - row["box"][0] > page.rect.width * .12 for row in column_lines):
                # A genuine second column requires geometric and citation order
                # evidence; otherwise keep this page visibly marked for review.
                left_refs = [r for r in anchors if r["box"][2] < page.rect.width * .5]
                right_refs = [r for r in column_lines if r["box"][0] > page.rect.width * .5 and NUMBER.match(r["text"])]
                values = lambda rs: [int((m := NUMBER.match(r["text"])).group(1) or m.group(2)) for r in rs]
                if (left_refs and right_refs and min(values(left_refs)) == 1
                        and min(values(right_refs)) == max(values(left_refs)) + 1):
                    split = (max(r["box"][2] for r in left_refs) + min(r["box"][0] for r in right_refs)) / 2
                    boxes = [[0, top, split, page.rect.height], [split, min(r["box"][1] for r in right_refs), page.rect.width, page.rect.height]]
                    column = "both"
                else:
                    column, uncertain = "full", True
                    boxes = [[0, 0, page.rect.width, page.rect.height]]
            else:
                column = "full"
                boxes = [[0, top, page.rect.width, page.rect.height]]
            return dict(page=number, y=float(top), title=line["text"].strip(), scanned=scanned,
                        column=column, boxes=boxes, uncertain_pages=[number] if uncertain else [])
        if not scanned:
            entries = _untitled_candidate(lines, number, len(source), page)
            if entries:
                boundary = _untitled_boundary(page, number, entries, predict(page, number))
                if boundary:
                    return boundary
    return None


def reference_boxes(boundary, number):
    """Page-local bibliography rectangles; legacy snapshots keep their old meaning."""
    if not boundary:
        return []
    if "page_boxes" in boundary:
        return boundary["page_boxes"].get(str(number), [])
    return boundary["boxes"] if number == boundary["page"] else []


def reference_page_only(boundary, number):
    if not boundary:
        return False
    if "page_boxes" in boundary:
        return number in boundary.get("whole_pages", [])
    return number > boundary["page"]


def reference_after_end(boundary, number, box):
    end = boundary.get("end", {}) if boundary else {}
    if end.get("page") != number:
        return False
    split = end.get("split_x")
    if split is None or end.get("column") == "full":
        return box[1] >= end["y"]
    if end.get("column") == "right":
        return box[0] >= split and box[1] >= end["y"]
    return box[0] >= split or box[1] >= end["y"]


AFTER_REFERENCES = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.\s]+)?"
    r"(?:appendi(?:x|ces)\b|supplement(?:ary|al)?\b|supporting information\b|"
    r"acknowledg(?:e)?ments?\b|author contributions\b|data availability\b|"
    r"conflicts? of interest\b|附录|补充材料|致谢|作者贡献|数据可用性)", re.I)


def _reference_column_split(lines, page, boundary, number):
    if number == boundary["page"]:
        if boundary.get("column") == "right":
            return boundary["boxes"][0][0] or None
        if boundary.get("column") == "both":
            return boundary["boxes"][0][2]
    middle = page.rect.width / 2
    left = [r for r in lines if r["box"][2] < middle and len(r["text"].strip()) > 15]
    right = [r for r in lines if r["box"][0] > middle and len(r["text"].strip()) > 15]
    if len(left) < 2 or len(right) < 2:
        return None
    if max(min(r["box"][1] for r in left), min(r["box"][1] for r in right)) >= min(
            max(r["box"][3] for r in left), max(r["box"][3] for r in right)):
        return None
    split = (max(r["box"][2] for r in left) + min(r["box"][0] for r in right)) / 2
    top = min(r["box"][1] for r in left + right)
    if any(r["box"][0] < split < r["box"][2] and r["box"][1] >= top for r in lines):
        return None
    return split


def _reference_section_end(row, lines, page, split, scanned):
    text = row["text"].strip()
    if (not AFTER_REFERENCES.match(text) or len(text) >= 130
            or row.get("citation_number") is not None or re.match(r"\s*\[\d+\]", text)):
        return False
    if scanned and (row.get("confidence", 0) < .95 or not row.get("horizontal", False)):
        return False
    x0, y0, x1, y1 = row["box"]
    previous = [r for r in lines if r is not row and r["box"][1] < y0 - 1
                and r["box"][3] > page.rect.height * .06
                and (split is None or (r["box"][0] >= split) == (x0 >= split))]
    # A wrapped cited title may start with "Supplementary" or "Appendix".
    # Require an independent heading: page/column start, emphasis, or blank space.
    if not previous:
        return True
    closest = max(previous, key=lambda r: r["box"][3])
    return bool(row.get("bold") and not closest.get("bold")
                or row.get("size", 0) > closest.get("size", float("inf")) * 1.1
                or y0 - closest["box"][3] > max(8, (y1 - y0) * .9))


def _reference_number(row):
    if row.get("citation_number") is not None:
        return row["citation_number"]
    match = NUMBER.match(row["text"])
    return int(match.group(1) or match.group(2)) if match else None


def _reference_continues(lines, last_number):
    numbers = [n for row in lines if (n := _reference_number(row)) is not None]
    if numbers:
        return (last_number is not None and numbers[0] == last_number + 1
                and all(b == a + 1 for a, b in zip(numbers, numbers[1:])))
    return sum(bool(re.search(r"\b(?:19|20)\d{2}\b", row["text"])
                    and re.search(r"\bdoi\b|\bJournal\b|\b[A-Z]\.\s*[A-Z][a-z]+", row["text"]))
               for row in lines) >= 2


def pdf_boundary(source, control, emit):
    """Locate a bounded bibliography, rather than treating the document tail as one."""
    boundary = _pdf_start(source, control, emit)
    if not boundary:
        return None
    from linguafloat.document_pdf import DocumentOcr, has_scanned_text_layer, needs_page_ocr
    areas, whole, splits = {}, [], {}
    last_number = None
    ocr = None
    for number in range(boundary["page"], len(source)):
        control.checkpoint()
        page = source[number]
        if page.rotation or page.cropbox.x0 or page.cropbox.y0:
            raise DocumentError(f"第 {number + 1} 页参考文献范围使用未验证的旋转或裁剪坐标。")
        scanned = has_scanned_text_layer(page) or needs_page_ocr(page, page.get_text("text"))
        lines = _digital_lines(page)
        if scanned:
            if ocr is None:
                ocr = DocumentOcr()
                ocr.emit = emit
            lines = ocr.recognize(page, positioned=True)
        split = _reference_column_split(lines, page, boundary, number)
        if split is not None:
            splits[str(number)] = split
        lines.sort(key=lambda r: (split is not None and r["box"][0] >= split, r["box"][1], r["box"][0]))
        boxes = boundary["boxes"] if number == boundary["page"] else [[0, 0, page.rect.width, page.rect.height]]
        # Only explicit independent section headings terminate the bibliography.
        # A cited title mentioning an appendix does not qualify as a heading.
        endings = [r for r in lines if _reference_section_end(r, lines, page, split, scanned)
                   and any(b[0] <= r["box"][0] < b[2] and b[1] <= r["box"][1] < b[3] for b in boxes)]
        end = endings[0] if endings else None
        if number > boundary["page"] and not end and not _reference_continues(lines, last_number):
            boundary["uncertain_end"] = dict(page=number, reason="unconfirmed-reference-continuation")
            emit({"type": "progress", "stage": "解析中", "value": None,
                  "message": f"第 {number + 1} 页未确认参考文献续页，不将后续正文自动纳入保护范围。"})
            break
        end_column = ("full" if split is None or (end and end["box"][0] < split < end["box"][2])
                      else "right" if end and end["box"][0] >= split else "left")
        if end and number not in boundary.get("uncertain_pages", []):
            clipped = []
            for x0, y0, x1, y1 in boxes:
                if end_column == "right":
                    if x0 < split:
                        clipped.append([x0, y0, min(x1, split), y1])
                    if x1 > split and end["box"][1] > y0:
                        clipped.append([max(x0, split), y0, x1, end["box"][1]])
                elif end_column == "left":
                    if x0 < split and end["box"][1] > y0:
                        clipped.append([x0, y0, min(x1, split), end["box"][1]])
                elif end["box"][1] > y0:
                    clipped.append([x0, y0, x1, end["box"][1]])
            boxes = clipped
        if boxes:
            areas[str(number)] = boxes
            if number > boundary["page"] and not end:
                whole.append(number)
        if end:
            boundary["end"] = dict(page=number, y=end["box"][1], column=end_column,
                                   split_x=split, reason="explicit-following-section")
            break
        numbers = [n for row in lines if any(b[0] <= row["box"][0] < b[2] and b[1] <= row["box"][1] < b[3]
                                             for b in boxes) and (n := _reference_number(row)) is not None]
        if numbers:
            last_number = numbers[-1]
    boundary.update(page_boxes=areas, whole_pages=whole, column_splits=splits, policy="bibliography-only-v3")
    return boundary


def office_boundary(paragraphs, namespace, *, structured):
    """Find a heading in document order, excluding TOC and ordinary mentions."""
    for index, paragraph in enumerate(paragraphs):
        text = "".join(node.text or "" for node in paragraph.iter(f"{{{namespace}}}t")
                       if next(node.iterancestors(f"{{{namespace}}}p"), None) is paragraph).strip()
        if not is_heading(text):
            continue
        if structured:
            # Word headings have a heading style or outline level. TOC entries
            # have TOC styles and often a hyperlink/page number instead.
            style = paragraph.find(f"{{{namespace}}}pPr/{{{namespace}}}pStyle")
            outline = paragraph.find(f"{{{namespace}}}pPr/{{{namespace}}}outlineLvl")
            style_name = style.get(f"{{{namespace}}}val", "") if style is not None else ""
            if (any(link.get(f"{{{namespace}}}anchor", "").startswith("_Toc")
                    for link in paragraph.iter(f"{{{namespace}}}hyperlink"))
                    or any("Table of Contents" in str(node.attrib.values())
                           for ancestor in paragraph.iterancestors(f"{{{namespace}}}sdt")
                           for node in ancestor.iter(f"{{{namespace}}}docPartGallery"))):
                continue
            if not (re.match(r"^(?:Heading|标题)\d*", style_name, re.I) or outline is not None):
                continue
        following = paragraphs[index + 1:index + 30]
        following_text = ["".join(node.text or "" for node in candidate.iter(f"{{{namespace}}}t")
                                   if next(node.iterancestors(f"{{{namespace}}}p"), None) is candidate)
                          for candidate in following]
        numbered = numbered_evidence(following_text)
        automatic_numbering = False
        if structured:
            tagged = [p for p in following if p.find(f"{{{namespace}}}pPr/{{{namespace}}}numPr") is not None]
            automatic_numbering = len(tagged) >= 2
        dated_entries = sum(bool(re.search(r"\b(?:19|20)\d{2}\b", text)) for text in following_text)
        citation_entries = dated_entries >= 2 and sum(bool(text.strip()) for text in following_text) >= 2
        if numbered or automatic_numbering or (structured and citation_entries):
            return index
    return None
