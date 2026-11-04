"""Conservative paragraph layout policy for BabelDOC 0.6.2.

Only this worker's adapter is patched; installed engine files are never edited.
Coordinates in this module use PDF's bottom-left origin.
"""
from __future__ import annotations

import copy
import re
from collections import defaultdict

from linguafloat.document_core import DocumentError


def bounds(value):
    box = getattr(value, "box", value)
    if box is None or any(getattr(box, k, None) is None for k in ("x", "y", "x2", "y2")):
        return None
    return (box.x, box.y, box.x2, box.y2)


def intersection(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def expanded_boxes(box, obstacles, page_box):
    """Small, bounded steps; reject new intersections, including thin rules."""
    from babeldoc.format.pdf.document_il import Box
    original = bounds(box)
    yield copy.copy(box)
    for down, right in ((2, 0), (4, 0), (6, 0), (10, 0), (16, 0),
                        (0, 2), (0, 4), (0, 8), (2, 4), (4, 8)):
        candidate = (original[0], original[1] - down, original[2] + right, original[3])
        if candidate[1] < page_box[1] + 12 or candidate[2] > page_box[2] - 12:
            continue
        if any(intersection(candidate, other) > intersection(original, other) + .01 for other in obstacles):
            continue
        yield Box(*candidate)


def font_scales(size):
    # Original small print may stay small, but must not be shrunk further below 6pt.
    floor = min(1.0, 6 / size) if size and size > 0 else 1.0
    yield 1.0
    value = .95
    while value > floor + 1e-6:
        yield value
        value = round(value - .05, 6)
    if floor < 1:
        yield floor


class LayoutPolicy:
    def __init__(self, control, source=None, pages=None, translator=None):
        self.control = control
        self.continuations = defaultdict(list)
        self.continuation_indices = defaultdict(list)
        self.margin_references = defaultdict(list)
        self.continuation_layout = defaultdict(list)
        self.warnings = []
        self.records = []
        self.geometry_repairs = []
        self.retained_regions = defaultdict(list)
        self.original_boxes = {}
        self.prepared = set()
        self.creator = None
        self.font_bindings = {}
        self.font_counter = 0
        self._saved = {}
        self.source_words = {}
        self.source_characters = {}
        self.source_subscripts = {}
        self.frontmatter = {}
        self.graphic_regions = {}
        self.before_layout = None
        self.scientific = None
        self.translation_failures = []
        self.reference_boundary = {}
        if source and translator:
            from linguafloat.document_scientific import ScientificLayout
            self.scientific = ScientificLayout(source, translator, self.warnings)
        if source:
            import pymupdf
            with pymupdf.open(source) as document:
                for number, page in enumerate(document):
                    if pages is not None and number not in pages:
                        continue
                    # Ignore ambiguous coordinate systems instead of removing text
                    # based on an unverified match in a rotated/cropped page.
                    if page.rotation or page.cropbox.x0 or page.cropbox.y0:
                        continue
                    height = page.rect.height
                    from linguafloat.document_frontmatter import plan_frontmatter
                    self.frontmatter[number] = plan_frontmatter(page)
                    self.source_words[number] = [(w[4], (w[0], height - w[3], w[2], height - w[1]))
                                                  for w in page.get_text("words")]
                    spans = [s for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]
                             if "lines" in block for line in block["lines"] for s in line["spans"]]
                    candidates = []
                    for span in spans:
                        if span["size"] >= 8 or not re.fullmatch(r"[A-Za-z0-9]{1,12}", span["text"]):
                            continue
                        for base in spans:
                            if (re.fullmatch(r"[A-Za-z]", base["text"]) and base["size"] > span["size"] * 1.2
                                    and abs(span["bbox"][0] - base["bbox"][2]) < 3
                                    and .2 < abs(span["origin"][1] - base["origin"][1]) < base["size"]):
                                a = span["bbox"]
                                candidates.append((span["text"], (a[0], height - a[3], a[2], height - a[1])))
                                break
                    self.source_subscripts[number] = candidates

    def obstacles(self, page, paragraph):
        result = []
        height=page.cropbox.box.y2-page.cropbox.box.y
        for area in self.frontmatter.get(page.page_number,[])+self.graphic_regions.get(page.page_number,[]):
            x0,y0,x1,y1=area["box"]
            result.append((x0,height-y1,x1,height-y0))
        if self.reference_boundary and page.page_number == self.reference_boundary["page"]:
            result.extend((x0,height-y1,x1,height-y0) for x0,y0,x1,y1 in self.reference_boundary["boxes"])
        for other in page.pdf_paragraph:
            if other is paragraph:
                continue
            box = bounds(other)
            if box:
                result.append(box)
        for name in ("pdf_figure", "pdf_form", "pdf_curve", "pdf_rectangle", "pdf_character"):
            for item in getattr(page, name, []):
                if name == "pdf_character" and not (item.char_unicode or "").strip():
                    continue
                box = bounds(getattr(item, "visual_bbox", None)) or bounds(item)
                if box:
                    # Give zero-width vector rules a real collision boundary.
                    result.append((box[0] - .3, box[1] - .3, box[2] + .3, box[3] + .3))
        return result

    def prepare_page(self, page):
        if id(page) in self.prepared:
            return
        self.prepared.add(id(page))
        self.exclude_frontmatter(page)
        from babeldoc.format.pdf.document_il import Box
        from linguafloat.document_scientific import repair_cached_chemical_scripts
        from linguafloat.document_geometry import join_fragmented_paragraphs, attach_formula_rules, preserve_symbol_labels, restore_reference_styles
        self.geometry_repairs.extend(dict(page=page.page_number+1, **change) for change in
            join_fragmented_paragraphs(page, self.source_words.get(page.page_number, []),
                                      self.source_characters.get(page.page_number, [])))
        repair_cached_chemical_scripts(page, self.source_words.get(page.page_number, []),
                                       self.source_characters.get(page.page_number, []))
        attached = attach_formula_rules(page)
        if attached:
            self.geometry_repairs.append(dict(page=page.page_number+1, kind="formula_rules", count=attached))
        preserved = preserve_symbol_labels(page, self.source_characters.get(page.page_number, []))
        if preserved:
            self.geometry_repairs.append(dict(page=page.page_number+1, kind="source_symbol_labels", count=preserved))
        restyled = restore_reference_styles(page, self.source_characters.get(page.page_number, []))
        if restyled:
            self.geometry_repairs.append(dict(page=page.page_number+1, kind="source_reference_style", count=restyled))
        self.remove_title_remnants(page)
        # Some titles are split into intersecting paragraph boxes. Join their
        # translated runs before layout; never delete text based on overlap alone.
        for first in list(page.pdf_paragraph):
            if first not in page.pdf_paragraph or not first.box or not first.pdf_style:
                continue
            size = first.pdf_style.font_size or 0
            if size < 16 or not any(c.pdf_same_style_unicode_characters for c in first.pdf_paragraph_composition):
                continue
            for second in list(page.pdf_paragraph):
                if first is second or not second.box or not second.pdf_style:
                    continue
                if abs((second.pdf_style.font_size or 0) - size) > .5:
                    continue
                if not any(c.pdf_same_style_unicode_characters for c in second.pdf_paragraph_composition):
                    continue
                a, b = bounds(first), bounds(second)
                smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                if smaller <= 0 or intersection(a, b) < smaller * .5:
                    continue
                runs = sorted((first, second), key=lambda p: page.pdf_paragraph.index(p))
                compositions = [c for p in runs for c in p.pdf_paragraph_composition]
                text = " ".join(p.unicode or "" for p in runs)
                first.pdf_paragraph_composition = compositions
                first.unicode = text
                first.box = Box(min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                page.pdf_paragraph.remove(second)
                self.warnings.append(f"原始第 {page.page_number + 1} 页：合并重叠标题框，保留全部译文。")
        for paragraph in page.pdf_paragraph:
            if paragraph.box:
                self.original_boxes[id(paragraph)] = copy.copy(paragraph.box)

    def exclude_frontmatter(self,page):
        from linguafloat.document_frontmatter import paragraph_in_plan
        plans=self.frontmatter.get(page.page_number,[])+self.graphic_regions.get(page.page_number,[])
        if not plans:return
        height=page.cropbox.box.y2-page.cropbox.box.y
        page.pdf_paragraph[:]=[p for p in page.pdf_paragraph if not paragraph_in_plan(p,height,plans)]

    def remove_title_remnants(self, page):
        titles = [p for p in page.pdf_paragraph if p.layout_label == "title" and p.box
                  and re.search(r"[\u4e00-\u9fff]", p.unicode or "")]
        for paragraph in list(page.pdf_paragraph):
            text = (paragraph.unicode or "").strip()
            if paragraph.layout_label != "abandon" or not re.fullmatch(r"[A-Za-z]", text) or not paragraph.box:
                continue
            box = bounds(paragraph)
            if not any(intersection(box, bounds(p)) > 0 for p in titles):
                continue
            for word, source_box in self.source_words.get(page.page_number, []):
                if (len(word) > 3 and word.startswith(text) and word.isalpha()
                        and abs(source_box[0] - box[0]) < 3 and intersection(box, source_box) > 0):
                    page.pdf_paragraph.remove(paragraph)
                    self.warnings.append(f"原始第 {page.page_number + 1} 页：清除标题单词 {word} 的孤立首字母残留 {text}。")
                    break

    def formula_subscripts(self, docs):
        preserved = []
        for page in docs.page:
            for paragraph in page.pdf_paragraph:
                if paragraph.layout_label != "fallback_line" or not paragraph.box or not paragraph.pdf_style:
                    continue
                if (paragraph.pdf_style.font_size or 99) >= 8:
                    continue
                text = (paragraph.unicode or "").strip()
                for label, source_box in self.source_subscripts.get(page.page_number, []):
                    if text == label and intersection(bounds(paragraph), source_box) > 0 and abs(paragraph.box.x - source_box[0]) < 3:
                        preserved.append((paragraph, copy.deepcopy(paragraph), page.page_number))
                        break
        return preserved

    def normalize_dropcap(self, units, paragraph, page):
        if paragraph.layout_label not in {None, "plain text"}:
            return units
        translated = [u for u in units if u.unicode and not u.unicode.isspace()]
        if len(translated) < 20 or not re.search(r"[\u4e00-\u9fff]", "".join(u.unicode for u in translated)):
            return units
        from statistics import median
        body_size = median(u.font_size for u in translated[1:])
        first = translated[0]
        if first.font_size < body_size * 1.6:
            return units
        # A translated oversized first letter is a drop-cap style, not a formula.
        units = list(units)
        last_index = units.index(first)
        for index in range(last_index, len(units)):
            unit = units[index]
            if not unit.unicode or not unit.font_size or unit.font_size < body_size * 1.6:
                break
            replacement = copy.copy(unit)
            replacement.font_size = body_size
            replacement.style = paragraph.pdf_style
            replacement.box_cache = replacement.width_cache = replacement.height_cache = None
            units[index] = replacement
            last_index = index
        if last_index + 1 < len(units):
            following = units[last_index + 1]
            formula = following.formular
            if formula and formula.is_corner_mark and not formula.pdf_curve and not formula.pdf_form:
                tail = "".join(c.char_unicode or "" for c in formula.pdf_character).strip()
                for word, source_box in self.source_words.get(page.page_number, []):
                    if (len(word) == 1 and (word + tail).upper() in {"THE", "THIS", "THAT", "WITH", "WHEN", "FOR", "IN", "ON", "AS", "AN"}
                            and abs(source_box[0] - paragraph.box.x) < 3
                            and 0 <= formula.box.x - source_box[2] <= body_size
                            and source_box[1] < formula.box.y < source_box[3]):
                        units.pop(last_index + 1)
                        self.warnings.append(f"原始第 {page.page_number + 1} 页：清除首字下沉单词 {word + tail} 的尾部残留 {tail}。")
                        break
        self.warnings.append(f"原始第 {page.page_number + 1} 页：首字下沉译文恢复正文大小。")
        return units

    def apply(self, engine, paragraph, page, placed, box, scale):
        from babeldoc.format.pdf.document_il import PdfParagraphComposition
        paragraph.box, paragraph.scale = box, scale
        paragraph.pdf_paragraph_composition = []
        for unit in placed:
            chars, curves, forms = unit.render()
            if unit.unicode and unit.unicode.strip() and (not chars or not chars[0].pdf_character_id):
                raise DocumentError(f"[PDF 字体缺字] 原始第 {page.page_number + 1} 页\n"
                                    f"字符 {unit.unicode!r} 未能生成字形，未发布不完整结果。译文缓存保留。")
            paragraph.pdf_paragraph_composition.extend(PdfParagraphComposition(pdf_character=c) for c in chars)
            page.pdf_curve.extend(curves)
            page.pdf_form.extend(forms)

    def find(self, engine, paragraph, page, units, initial_scale=1, use_english_line_break=True, apply_layout=False):
        self.control.checkpoint()
        # This policy deliberately searches from original size at final placement.
        # The engine's preliminary scale is unused; do not run the same search twice.
        if not apply_layout:
            return 1.0, None
        if not paragraph.box:
            raise DocumentError(f"[PDF 段落缺少坐标] 原始第 {page.page_number + 1} 页，无法安全排版。")
        units = self.normalize_dropcap(units, paragraph, page) if apply_layout else units
        original = self.original_boxes.get(id(paragraph), paragraph.box)
        obstacles = self.obstacles(page, paragraph)
        boxes = list(expanded_boxes(original, obstacles, bounds(page.cropbox)))
        size = getattr(paragraph.pdf_style, "font_size", 10) or 10
        if paragraph.layout_label == "title":
            from babeldoc.format.pdf.document_il import Box
            # Short source headings need room for translated words within their
            # existing column, not an arbitrary eight-point horizontal cap.
            neighbors = [p.box for p in page.pdf_paragraph if p is not paragraph and p.box
                         and p.layout_label == "plain text" and abs(p.box.x-original.x) < size
                         and 0 <= original.y2-p.box.y2 <= size*5]
            if neighbors:
                candidate = Box(original.x, original.y, max(b.x2 for b in neighbors), original.y2)
                if not any(intersection(bounds(candidate), o) > intersection(bounds(original), o)+.01 for o in obstacles):
                    boxes.extend(expanded_boxes(candidate, obstacles, bounds(page.cropbox)))
        spacings = (1.3, 1.2)
        for scale in font_scales(size):
            self.control.checkpoint()
            for box in boxes:
                for spacing in spacings:
                    placed, fits = engine._layout_typesetting_units(units, box, scale, spacing, paragraph, use_english_line_break)
                    if not fits or not placed:
                        continue
                    from linguafloat.document_geometry import space_formula_lines
                    placed, fits = space_formula_lines(placed, box, size*scale)
                    if not fits:
                        continue
                    # The engine tests baselines only; also reject glyphs outside
                    # the page, new collisions, and vertically overlapping lines.
                    if not self.safe_placement(placed, bounds(original), obstacles, bounds(page.cropbox)):
                        continue
                    if apply_layout:
                        self.apply(engine, paragraph, page, placed, box, scale)
                        self.records.append(dict(page=page.page_number + 1, text=paragraph.unicode,
                                                 size=size * scale, spacing=spacing, box=bounds(box),
                                                 expanded=bounds(box) != bounds(original)))
                    return scale, placed if apply_layout else None
        if not apply_layout:
            return 1.0, None
        self.defer(engine, paragraph, page, units)
        return paragraph.scale, None

    @staticmethod
    def safe_placement(placed, original, obstacles, page_box):
        lines = defaultdict(list)
        for unit in placed:
            box = bounds(unit)
            if not box:
                return False
            if box[0] < page_box[0] - .5 or box[1] < page_box[1] - .5 or box[2] > page_box[2] + .5 or box[3] > page_box[3] + .5:
                return False
            if any(intersection(box, other) > max(.5, intersection(original, other)) for other in obstacles):
                return False
            if not getattr(unit, "is_space", False):
                lines[round(getattr(unit, "_lf_line_y", box[1]), 1)].append(box)
        baselines = sorted(lines, reverse=True)
        for upper, lower in zip(baselines, baselines[1:]):
            if upper - lower < .5:
                continue
            if any(intersection(a, b) > .5 for a in lines[upper] for b in lines[lower]):
                return False
        return True

    def defer(self, engine, paragraph, page, units):
        original = self.original_boxes.get(id(paragraph), paragraph.box)
        from linguafloat.document_geometry import owns, box_of, union
        glyphs = [box_of(c) for c in self.source_characters.get(page.page_number, [])
                  if c.xobj_id == paragraph.xobj_id and (c.char_unicode or "").strip() and owns(original, c)]
        restore_box = union([original, *glyphs])
        text = paragraph.unicode or ""
        reason = "按原字号、1.3/1.2 倍行距、安全扩框及字号下限尝试后仍放不下，继续扩框可能遮挡相邻内容"
        self.retained_regions[page.page_number].append(dict(
            box=bounds(restore_box), translation=text, reason=reason, status="untranslated", coordinates="bottom-left"))
        self.warnings.append(f"原始第 {page.page_number + 1} 页，区域 {bounds(original)}：{reason}。"
                             f"已保留该处原文，未新增页面；未排入的译文：{text[:100]}。完整译文见“排版说明.txt”。")
        # Restore the original region at assembly, including its original fonts.
        # Keep its box as an obstacle for subsequent translated paragraphs.
        paragraph.unicode = ""
        paragraph.box = restore_box
        paragraph.pdf_paragraph_composition = []
        paragraph.scale = 1

    def defer_to_supplement(self, engine, paragraph, page, units):
        """Legacy explicit supplement renderer; strict layout never calls this."""
        from babeldoc.format.pdf.document_il import Box, PdfParagraph, PdfParagraphComposition, PdfSameStyleUnicodeCharacters
        crop = bounds(page.cropbox)
        box = Box(crop[0] + 40, crop[1] + 48, crop[2] - 40, crop[3] - 76)
        remaining = list(units)
        continuation_scale = max(1, 11 / (getattr(paragraph.pdf_style, "font_size", 11) or 11))
        first_index = len(self.continuations[page.page_number]) + 1
        inline = False
        if len(paragraph.unicode or "") <= 80 and len(units) <= 100:
            compact_box = Box(box.x, box.y2 - 88, min(box.x + 140, box.x2), box.y2)
            placed, fits = engine._layout_typesetting_units(units, compact_box, continuation_scale, 1.3, paragraph, False)
            if (fits and placed and len(placed) == len(units)
                    and self.safe_placement(placed, bounds(compact_box), [], crop)):
                box, inline = compact_box, True
        while remaining:
            self.control.checkpoint()
            low, high = 1, len(remaining)
            best = None
            while low <= high:
                count = (low + high) // 2
                placed, fits = engine._layout_typesetting_units(remaining[:count], box, continuation_scale, 1.3, paragraph, False)
                if fits and placed and self.safe_placement(placed, bounds(box), [], crop):
                    best = (count, placed)
                    low = count + 1
                else:
                    high = count - 1
            if best is None:
                raise DocumentError(f"[PDF 续页仍无法容纳内容] 原始第 {page.page_number + 1} 页\n"
                                    "段落含超宽公式或异常字形，完整页面也放不下，未截断或发布结果。\n"
                                    "处理建议：检查该页公式或尝试阅读版；已有译文保留，可仅重新排版。")
            count, placed = best
            fragment = copy.copy(page)
            fragment.pdf_font = list(page.pdf_font)
            fragment.pdf_paragraph = []
            fragment.pdf_character, fragment.pdf_curve, fragment.pdf_form = [], [], []
            fragment.pdf_figure, fragment.pdf_rectangle, fragment.pdf_xobject = [], [], []
            temp = PdfParagraph(box=copy.copy(box), pdf_style=copy.deepcopy(paragraph.pdf_style), unicode=paragraph.unicode)
            self.apply(engine, temp, fragment, placed, box, continuation_scale)
            bindings = []
            aliases = {}
            for composition in temp.pdf_paragraph_composition:
                char = composition.pdf_character
                font_id = char.pdf_style.font_id
                if font_id not in engine.font_mapper.fontid2font:
                    key = (char.xobj_id, font_id)
                    if key not in aliases:
                        scope = next((x.pdf_font for x in page.pdf_xobject if x.xobj_id == char.xobj_id), page.pdf_font)
                        original_font = next((f for f in scope if f.font_id == font_id), None)
                        if not original_font or not original_font.xref_id or not original_font.encoding_length:
                            raise DocumentError(f"[PDF 续页字体缺失] 原始第 {page.page_number + 1} 页，"
                                                "无法定位公式/字符原始字体。未发布不完整结果，译文缓存保留。")
                        self.font_counter += 1
                        alias = f"LFContinuation{self.font_counter}"
                        replacement_font = copy.copy(original_font)
                        replacement_font.font_id = alias
                        fragment.pdf_font.append(replacement_font)
                        aliases[key] = alias
                        bindings.append((alias, original_font.xref_id))
                    char.pdf_style = copy.copy(char.pdf_style)
                    char.pdf_style.font_id = aliases[key]
                char.xobj_id = -1
                fragment.pdf_character.append(char)
            self.font_bindings[id(fragment)] = bindings
            # Forms from nested resources need an explicit remap. Refuse rather
            # than silently losing vector equations or drawing the wrong object.
            if any(getattr(obj, "xobj_id", None) not in (None, -1) for obj in fragment.pdf_form + fragment.pdf_curve):
                raise DocumentError(f"[PDF 复杂公式续页受限] 原始第 {page.page_number + 1} 页包含嵌套图形资源，"
                                    "暂不能安全迁移。未发布不完整结果；译文缓存保留。")
            self.continuations[page.page_number].append(fragment)
            extent = (min(u.box.x for u in placed), min(u.box.y for u in placed),
                      max(u.box.x2 for u in placed), max(u.box.y2 for u in placed))
            self.continuation_layout[page.page_number].append(dict(box=extent, inline=inline))
            remaining = remaining[count:]
            if len(self.continuations[page.page_number]) > 500:
                raise DocumentError("单页译文超过 500 张续页，已停止排版；译文缓存保留。")
        marker = f"[P{page.page_number + 1}-{first_index}]"
        original = self.original_boxes.get(id(paragraph), paragraph.box)
        if inline:
            self.margin_references[page.page_number].append(
                dict(marker=marker, box=bounds(original), content_index=first_index - 1))
        obstacles = self.obstacles(page, paragraph)
        boxes = list(expanded_boxes(original, obstacles, crop))
        # Keep original small-print size, but never reduce it to force a reference.
        size = min(8, getattr(paragraph.pdf_style, "font_size", 8) or 8)
        fonts = {f.font_id: f for f in page.pdf_font}
        fonts.update(engine.font_mapper.fontid2font)
        for xobj in page.pdf_xobject:
            fonts[xobj.xobj_id] = {**fonts, **{f.font_id: f for f in xobj.pdf_font}}
        for label in (marker, f"[{first_index}]"):
            reference = copy.copy(paragraph)
            reference.unicode = label
            style = copy.deepcopy(paragraph.pdf_style)
            style.font_size = size
            reference.pdf_paragraph_composition = [PdfParagraphComposition(
                pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(unicode=label, pdf_style=style))]
            marker_units = engine.create_typesetting_units(reference, fonts)
            for candidate in boxes:
                self.control.checkpoint()
                placed, fits = engine._layout_typesetting_units(marker_units, candidate, 1, 1.2, reference, False)
                if not fits or len(placed) != len(marker_units) or not placed:
                    continue
                if len({round(unit.box.y, 1) for unit in placed}) != 1:
                    continue
                if not self.safe_placement(placed, bounds(original), obstacles, crop):
                    continue
                paragraph.unicode = label
                self.apply(engine, paragraph, page, placed, candidate, 1)
                destination = "同页补充栏" if inline else "合并续页"
                self.warnings.append(f"原始第 {page.page_number + 1} 页：译文移至{destination} {marker}，原位置标记为 {label}。")
                return
        # Never draw an unreadable reference or force it over nearby content.
        # The assembly stage adds a separate gutter without shrinking the page.
        if not inline:
            self.margin_references[page.page_number].append(dict(marker=marker, box=bounds(original)))
        paragraph.unicode = ""
        paragraph.pdf_paragraph_composition = []
        paragraph.scale = 1
        destination = "同页右侧补充栏" if inline else "合并续页"
        self.warnings.append(f"原始第 {page.page_number + 1} 页：小区域无法安全容纳标记 {marker}，"
                             f"对应编号及原文位置已列入右侧引用栏，完整译文保存在{destination}。")

    def append_continuations(self, pdf, config):
        if self.continuation_indices:
            return
        for number, fragments in sorted(self.continuations.items()):
            for index, fragment in enumerate(fragments):
                self.control.checkpoint()
                # Copy font/resource dictionaries, then replace the entire content
                # stream. Equations keep their original glyphs and relative positions.
                pdf.fullcopy_page(number)
                new_number = len(pdf) - 1
                fragment.page_number = new_number
                for widget in list(pdf[new_number].widgets() or []):
                    pdf[new_number].delete_widget(widget)
                for annot in list(pdf[new_number].annots() or []):
                    pdf[new_number].delete_annot(annot)
                for link in pdf[new_number].get_links():
                    pdf[new_number].delete_link(link)
                for alias, xref in self.font_bindings.get(id(fragment), []):
                    pdf.xref_set_key(pdf[new_number].xref, f"Resources/Font/{alias}", f"{xref} 0 R")
                available = self.creator.get_available_font_list(pdf, fragment)
                encoded = {f.font_id for f in fragment.pdf_font if f.encoding_length}
                missing = {c.pdf_style.font_id for c in fragment.pdf_character
                           if c.pdf_character_id is not None and
                           (c.pdf_style.font_id not in available or c.pdf_style.font_id not in encoded)}
                if missing:
                    raise DocumentError(f"[PDF 续页字体未嵌入] 原始第 {number + 1} 页，无法绘制全部字符。"
                                        "已阻止不完整输出；译文缓存保留。")
                self.creator.update_page_content_stream(False, fragment, pdf, config)
                pdf[new_number].insert_text((40, 24), f"Original page {number + 1} | Continuation P{number + 1}-{index + 1}", fontsize=9)
                self.continuation_indices[number].append(new_number)

    def __enter__(self):
        from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting, TypesettingUnit
        from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater
        from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
        from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly
        self._saved = dict(relocate=TypesettingUnit.relocate, find=Typesetting._find_optimal_scale_and_layout,
                           document=Typesetting.typesetting_document, write=PDFCreater.write,
                           add_font=FontMapper.add_font, units=Typesetting.create_typesetting_units,
                           translate=ILTranslator.translate, translate_llm=ILTranslatorLLMOnly.translate,
                           paragraph=ILTranslator.translate_paragraph, batch=ILTranslatorLLMOnly.translate_paragraph,
                           post_translate=ILTranslator.post_translate_paragraph)
        policy = self
        locations = {}

        def relocate(unit, x, y, scale):
            result = policy._saved["relocate"](unit, x, y, scale)
            result._lf_line_y = y
            return result

        def document(engine, docs):
            if policy.translation_failures:
                raise policy.translation_failures[0]
            if policy.before_layout:
                policy.before_layout(engine, docs)
            for page in docs.page:
                policy.prepare_page(page)
            return policy._saved["document"](engine, docs)

        def write(creator, *args, **kwargs):
            policy.creator = creator
            return policy._saved["write"](creator, *args, **kwargs)

        def add_font(mapper, pdf, docs):
            extended = copy.copy(docs)
            extended.page = [*docs.page, *(p for fragments in policy.continuations.values() for p in fragments)]
            return policy._saved["add_font"](mapper, pdf, extended)

        def create_units(engine, paragraph, fonts):
            from linguafloat.document_scientific import normalize_formula_baseline
            size = paragraph.pdf_style.font_size if paragraph.pdf_style else 0
            for part in paragraph.pdf_paragraph_composition:
                formula = part.pdf_formula
                if formula and formula.box and size:
                    normalize_formula_baseline(formula, size)
                    height = formula.box.y2 - formula.box.y
                    if height > size * 1.5 and (formula.y_offset or 0) < -(height-size)/2:
                        formula.y_offset = -(height-size)/2
            adapted = paragraph
            if any(part.pdf_character for part in paragraph.pdf_paragraph_composition):
                from babeldoc.format.pdf.document_il import PdfLine, PdfParagraphComposition
                # BabelDOC 0.6.2's bare-character branch reads a nonexistent
                # PdfParagraph.debug_info. Its line branch keeps the same glyphs.
                adapted = copy.copy(paragraph)
                adapted.pdf_paragraph_composition = [
                    PdfParagraphComposition(pdf_line=PdfLine(
                        box=part.pdf_character.box, pdf_character=[part.pdf_character]))
                    if part.pdf_character else part
                    for part in paragraph.pdf_paragraph_composition]
            units = policy._saved["units"](engine, adapted, fonts)
            expected = "".join(c.pdf_same_style_unicode_characters.unicode or ""
                               for c in paragraph.pdf_paragraph_composition if c.pdf_same_style_unicode_characters).replace("\n", "")
            actual = "".join(u.unicode for u in units if u.unicode)
            if actual != expected:
                raise DocumentError("[PDF 字体映射丢字] 部分译文没有对应字体，已停止发布。\n"
                                    f"段落片段：{(paragraph.unicode or '')[:100]}\n"
                                    "处理建议：检查文档字体组件是否完整；译文缓存保留，可仅重新排版。")
            return units

        def translated(engine, docs, method):
            for page in docs.page:
                policy.exclude_frontmatter(page)
                from linguafloat.document_references import filter_layout_paragraphs
                filter_layout_paragraphs(page, policy.reference_boundary)
                for index, paragraph in enumerate(page.pdf_paragraph):
                    locations[id(paragraph)] = f"原始第 {page.page_number + 1} 页 · 段落 {index + 1}"
            if policy.scientific:
                policy.scientific.prepare(docs)
            references = policy.scientific.detach_references(docs) if policy.scientific else []
            preserved = policy.formula_subscripts(docs)
            try:
                result = policy._saved[method](engine, docs)
            finally:
                if policy.scientific:
                    policy.scientific.restore_references(references)
            for paragraph, original, number in preserved:
                paragraph.pdf_paragraph_composition = original.pdf_paragraph_composition
                paragraph.unicode, paragraph.pdf_style = original.unicode, original.pdf_style
                paragraph.box = original.box
                policy.warnings.append(f"原始第 {number + 1} 页：保留公式上下标 {original.unicode}，不按普通文字翻译或缩小。")
            return result

        def located(engine, paragraph, *args, batch=False, **kwargs):
            translator = policy.scientific.translator if policy.scientific else None
            previous = translator.location if translator else None
            paragraphs = paragraph.paragraphs if batch else [paragraph]
            if translator:
                translator.location = "、".join(dict.fromkeys(locations.get(id(p), "段落 " + str(p.debug_id)) for p in paragraphs))
            try:
                return policy._saved["batch" if batch else "paragraph"](engine, paragraph, *args, **kwargs)
            finally:
                if translator:
                    translator.location = previous

        def post_translate(engine, paragraph, tracker, translate_input, translated_text):
            from linguafloat.document_scientific import check_formula_tokens, translate_formula_segments
            if not check_formula_tokens(translate_input, translated_text):
                if policy.scientific:
                    try:
                        translated_text = translate_formula_segments(translate_input, policy.scientific.translator)
                    except DocumentError as error:
                        policy.translation_failures.append(error)
                        raise
                if not check_formula_tokens(translate_input, translated_text):
                    error = DocumentError(f"[公式占位符校验失败] 段落 {paragraph.debug_id}：补译后公式仍有丢失、重复或顺序变化。未发布损坏公式。")
                    policy.translation_failures.append(error)
                    raise error
            return policy._saved["post_translate"](engine, paragraph, tracker, translate_input, translated_text)

        Typesetting.typesetting_document = document
        TypesettingUnit.relocate = relocate
        Typesetting._find_optimal_scale_and_layout = lambda engine, *args, **kwargs: policy.find(engine, *args, **kwargs)
        PDFCreater.write = write
        FontMapper.add_font = add_font
        Typesetting.create_typesetting_units = create_units
        ILTranslator.translate = lambda engine, docs: translated(engine, docs, "translate")
        ILTranslatorLLMOnly.translate = lambda engine, docs: translated(engine, docs, "translate_llm")
        ILTranslator.post_translate_paragraph = post_translate
        ILTranslator.translate_paragraph = lambda engine, paragraph, *a, **kw: located(engine, paragraph, *a, **kw)
        ILTranslatorLLMOnly.translate_paragraph = lambda engine, batch_paragraph, *a, **kw: located(engine, batch_paragraph, *a, batch=True, **kw)
        return self

    def __exit__(self, *_):
        from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting, TypesettingUnit
        from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater
        from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
        from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly
        Typesetting._find_optimal_scale_and_layout = self._saved["find"]
        TypesettingUnit.relocate = self._saved["relocate"]
        Typesetting.typesetting_document = self._saved["document"]
        PDFCreater.write = self._saved["write"]
        FontMapper.add_font = self._saved["add_font"]
        Typesetting.create_typesetting_units = self._saved["units"]
        ILTranslator.translate = self._saved["translate"]
        ILTranslatorLLMOnly.translate = self._saved["translate_llm"]
        ILTranslator.post_translate_paragraph = self._saved["post_translate"]
        ILTranslator.translate_paragraph = self._saved["paragraph"]
        ILTranslatorLLMOnly.translate_paragraph = self._saved["batch"]
