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
                 "片段格式标签丢失、增加或顺序变化", "片段小数、百分比或数量级发生变化",
                 "补译响应不是有效 JSON", "片段数量不匹配", "片段编号缺失、重复或不匹配",
                 "单片段补译未返回纯文本")
    reason += next(("：" + value for value in specifics if value in str(error)), "")
    return dict(stage=stage, error_type=type(error).__name__, reason=reason,
                retry_result=("当前重试额度已用尽；已停止补译" if "当前重试额度已用尽" in str(error)
                              else "保留原文未追加请求；已有尝试见任务请求记录"),
                fallback="保留原文", trace=failure_trace(error))


def mark_pdf(page, regions, page_number):
    """Paint persistent outlines and labels only in verified blank space."""
    import pymupdf as fitz
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
        width = 24 + len(identity) * 3.4
        candidates = [fitz.Rect(rect.x0, rect.y0 - 11, rect.x0 + width, rect.y0 - 2),
                      fitz.Rect(rect.x0, rect.y1 + 2, rect.x0 + width, rect.y1 + 11)]
        candidates.extend(fitz.Rect(3, y, width + 3, y + 9)
                          for y in range(3, int(page.rect.height) - 10, 12))
        for candidate in candidates:
            if not page.rect.contains(candidate):
                continue
            pix = page.get_pixmap(clip=candidate, colorspace=fitz.csGRAY, alpha=False)
            if pix.samples and min(pix.samples) >= 248:
                page.insert_text((candidate.x0, candidate.y0 + 7), label,
                                 fontname="china-s", fontsize=6, color=(.8, .05, .05))
                region["label_box"] = list(candidate)
                break
        page.draw_rect(outline, color=(.85, .05, .05), width=.7, overlay=True)
        region["marked"] = True


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
