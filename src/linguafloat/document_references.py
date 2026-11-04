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


def boundary_intersects(boundary, number, box):
    if not boundary or number < boundary["page"]:
        return False
    if number > boundary["page"]:
        return True
    import pymupdf as fitz
    return any(fitz.Rect(box).intersects(fitz.Rect(area)) for area in boundary["boxes"])


def filter_layout_paragraphs(page, boundary):
    """Exclude source tail before any engine or bibliography requests."""
    if not boundary or page.page_number < boundary["page"]:
        return
    if page.page_number > boundary["page"]:
        page.pdf_paragraph[:] = []
        return
    height = page.cropbox.box.y2
    boxes = [(x0, height-y1, x1, height-y0) for x0,y0,x1,y1 in boundary["boxes"]]
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
                raise DocumentError(f"第 {page.page_number + 1} 页参考文献边界穿过正文段落；已停止以避免误跳过。")
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


def pdf_boundary(source, control, emit):
    """Return the first credible heading's page and top coordinate."""
    from linguafloat.document_pdf import DocumentOcr, has_scanned_text_layer, needs_page_ocr

    ocr = DocumentOcr()
    ocr.emit = emit
    layout_model = None
    for number, page in enumerate(source):
        control.checkpoint()
        if page.rotation or page.cropbox.x0 or page.cropbox.y0:
            continue
        text = page.get_text("text")
        scanned = has_scanned_text_layer(page) or needs_page_ocr(page, text)
        if scanned:
            lines = ocr.recognize(page, positioned=True)
        else:
            lines = [dict(text="".join(span["text"] for span in line["spans"]), box=line["bbox"])
                     for block in page.get_text("dict")["blocks"] if "lines" in block
                     for line in block["lines"]]
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
            prediction = layout_model.predict(rgb[:, :, ::-1].copy(), imgsz=1024)[0]
            center = (fitz.Rect(line["box"]).tl + fitz.Rect(line["box"]).br) / 2
            if any(prediction.names[int(item.cls)] in {"figure", "table", "abandon"}
                   and float(item.conf) >= .6 and fitz.Rect([float(v)/2 for v in item.xyxy]).contains(center)
                   for item in prediction.boxes):
                continue
            # A single horizontal cut cannot preserve reading order when a
            # column contains pre-heading body text below the cut.
            left, right = line["box"][0], line["box"][2]
            anchors = [row for row in lines if row["box"][1] > top + 2 and NUMBER.match(row["text"])]
            right_anchors = [row for row in anchors if row["box"][0] >= page.rect.width * .48]
            left_body = [row for row in lines if row["box"][1] > top + 2 and
                         row["box"][2] < page.rect.width * .48 and len(row["text"].strip()) > 15]
            if (left + right) / 2 > page.rect.width * .6 and len(right_anchors) >= 2 and left_body:
                column = "right"
                left_edge = max(row["box"][2] for row in left_body)
                right_edge = min(row["box"][0] for row in right_anchors)
                if right_edge <= left_edge:
                    raise DocumentError(f"第 {number + 1} 页分栏之间没有安全空白，无法确定参考文献边界。")
                boxes = [[(left_edge+right_edge)/2, top, page.rect.width, page.rect.height]]
            elif any(row["box"][0] > page.rect.width * .54 and row["box"][1] < top - 2 for row in lines):
                raise DocumentError(f"第 {number + 1} 页参考文献与同页另一分栏交错，无法安全区分前文与文末；未发送翻译请求。")
            else:
                column = "full"
                boxes = [[0, top, page.rect.width, page.rect.height]]
            return dict(page=number, y=float(top), title=line["text"].strip(), scanned=scanned,
                        column=column, boxes=boxes)
    return None


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
