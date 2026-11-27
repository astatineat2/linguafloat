"""Local recovery records; never persist model replies or exception bodies."""
from __future__ import annotations

from linguafloat.document_core import Cancelled, DocumentError, DocumentRequestError


def require_local(error):
    # Transport/configuration failures are not evidence of a bad document block.
    if isinstance(error, (Cancelled, DocumentRequestError, OSError, MemoryError,
                          ImportError, RecursionError)):
        raise error
    if isinstance(error, DocumentError) and any(s in str(error) for s in ("模型缺失", "版本不匹配", "运行组件缺失")):
        raise error


def diagnostic(error, stage):
    require_local(error)
    from linguafloat.document_worker import failure_trace
    codes = {
        "公式旁文字补译失败": "公式旁文字补译未通过校验，有限恢复未成功",
        "公式占位符校验失败": "公式标记缺失、重复或顺序不符",
        "字体映射丢字": "字体映射缺少字符",
        "字体缺字": "字体无法生成完整字形",
        "段落缺少坐标": "段落坐标缺失，无法安全定位",
        "文字未绘制": "译文未能完整绘制",
        "字号过小": "译文字号低于可读下限",
        "空译文": "模型返回空译文",
        "翻译响应校验失败": "模型响应被截断、未完成或不符合协议",
        "格式标记": "格式标记、正文或科研数值校验失败",
        "仅重新排版": "仅重新排版缺少合格译文缓存，未发送请求",
    }
    # Only select fixed descriptions; arbitrary exception text may contain secrets.
    reason = next((v for k, v in codes.items() if k in str(error)),
                  f"{stage}发生 {type(error).__name__}，已保留原文；代码位置见问题日志")
    specifics = ("片段译文不是文字", "片段返回空译文", "片段夹带公式标记",
                 "片段格式标签丢失、增加或顺序变化", "片段格式标签丢失、增加或嵌套关系变化", "片段小数、百分比或数量级发生变化",
                 "片段正文数值发生变化", "构型前缀后误接了编号而非酶名", "跨公式重组后遗漏了晶体结构主语", "片段中的科学缩写缺失", "比较分句中显著升高的限定或方向缺失", "片段末尾括号内的科学缩写错位",
                 "补译响应不是有效 JSON", "片段数量不匹配", "片段编号缺失、重复或不匹配",
                 "单片段补译未返回纯文本", "片段数值标记缺失、重复或顺序变化")
    reason += next(("：" + value for value in specifics if value in str(error)), "")
    if isinstance(error, DocumentError) and getattr(error, 'fragment_evidence', ''):
        reason += '；' + error.fragment_evidence
    return dict(stage=stage, error_type=type(error).__name__, reason=reason,
                retry_result=("内容纠错额度已用尽；已停止补译" if "内容纠错额度已用尽" in str(error) else "当前重试额度已用尽；已停止补译" if "当前重试额度已用尽" in str(error)
                              else "保留原文未追加请求；已有尝试见任务请求记录"),
                fallback="保留原文", trace=failure_trace(error))


def mark_pdf(page, regions, page_number, control=None, *, text_boxes=()):
    """Draw in unrotated PDF coordinates, restoring the display rotation."""
    if control:
        control.checkpoint()
    rotation = page.rotation
    try:
        if rotation:
            page.set_rotation(0)
        return _mark_unrotated_pdf(page, regions, page_number, control, text_boxes)
    finally:
        if rotation:
            page.set_rotation(rotation)


def _mark_unrotated_pdf(page, regions, page_number, control=None, text_boxes=()):
    """Inspect one immutable page snapshot; track subsequent paint separately.

    Rebuilding a display list for every margin candidate is very expensive on
    vector plots. The snapshot lives only for this call, after source recovery.
    Reserved outline strips and label rectangles account for all new paint.
    """
    import pymupdf as fitz
    active = [r for r in regions if r.get("kind") != "graphic_source" and r.get("status") != "preserved"]
    if not active:
        return
    display = page.get_displaylist(annots=True)
    if control:
        control.checkpoint()
    # Interline whitespace still belongs to the translated paragraph when copied.
    occupied = [fitz.Rect(box)+(-1,-1,1,1) for box in text_boxes]
    for region in active:
        box = fitz.Rect(region["box"])
        if region.get("coordinates", "bottom-left") == "bottom-left":
            box = box * page.transformation_matrix * page.rotation_matrix
        box = (box + (-1, -1, 1, 1)) & page.rect
        occupied.extend((fitz.Rect(box.x0-1, box.y0-1, box.x1+1, box.y0+1),
                         fitz.Rect(box.x0-1, box.y1-1, box.x1+1, box.y1+1),
                         fitz.Rect(box.x0-1, box.y0, box.x0+1, box.y1),
                         fitz.Rect(box.x1-1, box.y0, box.x1+1, box.y1)))
    for index, region in enumerate(regions, 1):
        if region.get("kind") == "graphic_source" or region.get("status") == "preserved":
            continue
        identity = region.setdefault("id", f"P{page_number + 1}-U{index}")
        rect = fitz.Rect(region["box"])
        if region.get("coordinates", "bottom-left") == "bottom-left":
            rect = rect * page.transformation_matrix * page.rotation_matrix
        rect &= page.rect
        if rect.is_empty or rect.is_infinite:
            raise DocumentError("[未翻译区域坐标无效] 无法安全标记输出。")
        outline = (rect + (-1, -1, 1, 1)) & page.rect
        label = "未翻译 " + identity
        # insert_text's built-in CJK encoding uses fixed-width Latin glyphs too.
        # Font.text_length uses proportional fallback widths and underestimates
        # the actual marker, allowing it to paint into neighboring source ink.
        width = fitz.get_text_length(label, fontname="china-s", fontsize=6) + 2
        candidates = [fitz.Rect(rect.x0, rect.y0 - 11, rect.x0 + width, rect.y0 - 2),
                      fitz.Rect(rect.x0, rect.y1 + 2, rect.x0 + width, rect.y1 + 11)]
        candidates.extend(fitz.Rect(3, y, width + 3, y + 9)
                          for y in range(3, int(page.rect.height) - 10, 12))
        for candidate in candidates:
            if control:
                control.checkpoint()
            if not page.rect.contains(candidate) or any(candidate.intersects(box) for box in occupied):
                continue
            pix = display.get_pixmap(clip=candidate, colorspace=fitz.csGRAY, alpha=False)
            if pix.samples and min(pix.samples) >= 248:
                page.insert_text((candidate.x0, candidate.y0 + 7), label,
                                 fontname="china-s", fontsize=6, color=(.8, .05, .05))
                region["label_box"] = list(candidate)
                occupied.append(candidate + (-1, -1, 1, 1))
                break
        # OCR bounds can include descenders from the next label. A continuous
        # expanded frame would paint over a protected name, rule or formula.
        # Keep vector marker segments only where the immutable page is white.
        region['outline_segments'] = _safe_outline(page, display, outline, control)
        region["marked"] = True


def _safe_outline(page, display, rect, control=None):
    import numpy as np
    import pymupdf as fitz
    count = 0
    for horizontal, coordinate, start, end in (
            (True,rect.y0,rect.x0,rect.x1),(True,rect.y1,rect.x0,rect.x1),
            (False,rect.x0,rect.y0,rect.y1),(False,rect.x1,rect.y0,rect.y1)):
        if control:
            control.checkpoint()
        # The guard is wider than the .7pt stroke, including antialiasing.
        clip = (fitz.Rect(start-1,coordinate-1.5,end+1,coordinate+1.5) if horizontal else
                fitz.Rect(coordinate-1.5,start-1,coordinate+1.5,end+1)) & page.rect
        if clip.is_empty:
            continue
        # Thin strokes can disappear between samples at 2x. Inspect at 4x and
        # require exact white, then inset a full point from occupied samples.
        resolution = 4
        pix = display.get_pixmap(matrix=fitz.Matrix(resolution,resolution),clip=clip,colorspace=fitz.csRGB,alpha=False)
        rgb = np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
        blank = np.all(rgb == 255,axis=(0,2) if horizontal else (1,2))
        edges = np.flatnonzero(np.diff(np.r_[False,blank,False].astype(np.int8)))
        origin = pix.x if horizontal else pix.y
        for left,right in zip(edges[::2],edges[1::2]):
            low,high = max(start,(origin+int(left))/resolution+1),min(end,(origin+int(right))/resolution-1)
            if high <= low:
                continue
            a,b = ((low,coordinate),(high,coordinate)) if horizontal else ((coordinate,low),(coordinate,high))
            page.draw_line(a,b,color=(.85,.05,.05),width=.7,overlay=True)
            count += 1
    return count


def office_issue(translator, error, label, index):
    record = diagnostic(error, "翻译/校验")
    record.update(location=label, paragraph=index + 1, status="untranslated")
    if not hasattr(translator, "local_issues"):
        translator.local_issues = []
    translator.local_issues.append(record)
    return record


def mark_office(paragraph, namespace):
    from lxml import etree as ET
    if namespace.endswith("wordprocessingml/2006/main"):
        properties = paragraph.find(f"{{{namespace}}}pPr")
        if properties is None:
            properties = ET.Element(f"{{{namespace}}}pPr")
            paragraph.insert(0, properties)
        borders = properties.find(f"{{{namespace}}}pBdr")
        if borders is None:
            borders = ET.SubElement(properties, f"{{{namespace}}}pBdr")
        for side in ("top", "left", "bottom", "right"):
            border = borders.find(f"{{{namespace}}}{side}")
            if border is None:
                border = ET.SubElement(borders, f"{{{namespace}}}{side}")
            for key, value in dict(val="single", sz="6", space="2", color="CC2222").items():
                border.set(f"{{{namespace}}}{key}", value)
    else:
        # Keep slide text and its geometry unchanged; use a red wavy underline.
        for run in paragraph.findall(f"{{{namespace}}}r"):
            props = run.find(f"{{{namespace}}}rPr")
            if props is None:
                props = ET.Element(f"{{{namespace}}}rPr")
                run.insert(0, props)
            props.set("u", "wavy")
            for tag in ("uFill", "uFillTx"):
                for old in props.findall(f"{{{namespace}}}{tag}"):
                    props.remove(old)
            fill = ET.SubElement(props, f"{{{namespace}}}uFill")
            solid = ET.SubElement(fill, f"{{{namespace}}}solidFill")
            ET.SubElement(solid, f"{{{namespace}}}srgbClr", val="CC2222")
