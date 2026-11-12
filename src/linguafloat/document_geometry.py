"""Source-grounded grouping and geometry repairs for preserved-layout PDFs."""
from __future__ import annotations

import copy
import re
from collections import Counter


def box_of(item):
    visual = getattr(item, "visual_bbox", None)
    return visual.box if visual and visual.box else getattr(item, "box", None)


def union(boxes):
    from babeldoc.format.pdf.document_il import Box
    boxes = [b for b in boxes if b is not None]
    return Box(min(b.x for b in boxes), min(b.y for b in boxes),
               max(b.x2 for b in boxes), max(b.y2 for b in boxes)) if boxes else None


def owns(box, char):
    b = box_of(char)
    return b and box.x - .3 <= (b.x+b.x2)/2 <= box.x2 + .3 and box.y - .3 <= (b.y+b.y2)/2 <= box.y2 + .3


def join_fragmented_paragraphs(page, words=(), source_characters=()):
    """Join a wide body block with fragments of the same detected source line.

    A source word crossing a tiny false heading is required. Ordinary adjacent
    paragraphs, true headings, captions and separate columns are not merged.
    """
    from babeldoc.format.pdf.document_il import Box
    from linguafloat.document_scientific import composition_chars
    changes = []
    for carrier in list(page.pdf_paragraph):
        if carrier not in page.pdf_paragraph or carrier.layout_label != "plain text" or not carrier.box or not carrier.pdf_style:
            continue
        size = carrier.pdf_style.font_size or 0
        b = carrier.box
        if not size or b.x2-b.x < 12*size or b.y2-b.y < 2*size:
            continue
        group = [p for p in page.pdf_paragraph if p.box and p.pdf_style
                 and p.layout_label in {"plain text", "title"}
                 and abs((p.pdf_style.font_size or 0)-size) < .6
                 and p.xobj_id == carrier.xobj_id
                 and b.x-.5 <= p.box.x and p.box.x2 <= b.x2+.5
                 and abs(p.box.y2-b.y2) < size*1.3
                 and (p is carrier or p.box.y2-p.box.y < size*1.6)]
        if len(group) < 3:
            continue
        false_titles = [p for p in group if p.layout_label == "title" and re.fullmatch(r"[A-Za-z]{1,3}", (p.unicode or "").strip())]
        linked_titles = [p for p in false_titles if any(re.fullmatch(r"[A-Za-z][A-Za-z-]+", w)
                         and wb[0] < p.box.x-.1 and wb[2] >= p.box.x2-1
                         and wb[1] < p.box.y2 and wb[3] > p.box.y for w, wb in words)]
        if not linked_titles or carrier.layout_id is None:
            continue
        group = [p for p in group if p in linked_titles or (p.layout_label == "plain text" and p.layout_id == carrier.layout_id)]
        if len(group) < 3:
            continue
        # Preserve the original content-stream order (the wide tail begins at
        # the end of the same line, despite having the leftmost paragraph box).
        group.sort(key=lambda p: (p.render_order if p.render_order is not None else page.pdf_paragraph.index(p)))
        translated = any(part.pdf_same_style_unicode_characters and re.search(r"[\u4e00-\u9fff]", part.pdf_same_style_unicode_characters.unicode or "")
                         for p in group for part in p.pdf_paragraph_composition)
        removed = []
        if translated and source_characters:
            for word, wb in words:
                if not re.fullmatch(r"[A-Za-z][A-Za-z-]+", word):
                    continue
                original = [c for c in source_characters if c.xobj_id == carrier.xobj_id
                            and owns(Box(*wb), c) and (c.char_unicode or "").strip()]
                if "".join(c.char_unicode or "" for c in sorted(original, key=lambda c: c.box.x)) != word:
                    continue
                owners = {}
                for char in original:
                    candidates = [p for p in group if owns(p.box, char)]
                    if candidates:
                        owner = min(candidates, key=lambda p: (p.box.x2-p.box.x)*(p.box.y2-p.box.y))
                        owners.setdefault(id(owner), []).append(char)
                if len(owners) < 2:
                    continue
                for p in group:
                    chars = owners.get(id(p), [])
                    token = "".join(c.char_unicode or "" for c in sorted(chars, key=lambda c: c.box.x))
                    if not token or token == word:
                        continue
                    for part in list(p.pdf_paragraph_composition):
                        run = part.pdf_same_style_unicode_characters
                        if run:
                            text = run.unicode or ""
                            cleaned = re.sub(r"^"+re.escape(token)+r"(?![A-Za-z])", "", text)
                            cleaned = re.sub(r"(?<![A-Za-z])"+re.escape(token)+r"$", "", cleaned)
                            if cleaned != text:
                                run.unicode = cleaned
                                removed.append(token)
                        elif not part.pdf_formula and len(token) <= 3 and "".join(c.char_unicode or "" for c in composition_chars(part)).strip() == token:
                            p.pdf_paragraph_composition.remove(part)
                            removed.append(token)
        carrier.box = union([p.box for p in group])
        carrier.pdf_paragraph_composition = [part for p in group for part in p.pdf_paragraph_composition]
        if translated:
            carrier.unicode = "".join(part.pdf_same_style_unicode_characters.unicode if part.pdf_same_style_unicode_characters
                                      else "".join(c.char_unicode or "" for c in composition_chars(part))
                                      for part in carrier.pdf_paragraph_composition)
        else:
            carrier.unicode = "".join(p.unicode or "" for p in group)
        for p in group:
            if p is not carrier:
                page.pdf_paragraph.remove(p)
        changes.append(dict(kind="joined_source_fragments", count=len(group), removed_fragments=removed))
    return changes


def _rule_symbol(page, rule):
    """Require an isolated italic/math glyph and a short, aligned source rule."""
    from linguafloat.document_scientific import composition_chars
    fonts = {font.font_id: font.name or "" for font in page.pdf_font}
    for obj in page.pdf_xobject:
        if obj.xobj_id == rule.xobj_id:
            fonts.update({font.font_id: font.name or "" for font in obj.pdf_font})
    matches = []
    b = rule.box
    for paragraph in page.pdf_paragraph:
        if paragraph.xobj_id != rule.xobj_id:
            continue
        chars = [c for part in paragraph.pdf_paragraph_composition for c in composition_chars(part)
                 if (c.char_unicode or "").strip() and box_of(c)]
        for part in paragraph.pdf_paragraph_composition:
            if part.pdf_formula:
                continue
            for char in composition_chars(part):
                c = box_of(char)
                style = char.pdf_style
                if not c or not style or not re.fullmatch(r"[A-Za-zα-ωΑ-Ω]", char.char_unicode or ""):
                    continue
                if not re.search(r"italic|regita|oblique|math|symbol|cmsy|cmmi", fonts.get(style.font_id, ""), re.I):
                    continue
                size = style.font_size or 0
                if (not size or abs(b.x-c.x) > .8 or abs(b.x2-c.x2) > .8
                        or min(abs(b.y-c.y), abs(b.y-c.y2)) > size*.3):
                    continue
                if any(other is not char and abs((box_of(other).y+box_of(other).y2-c.y-c.y2)/2) < size*.3
                       and min(abs(box_of(other).x-c.x2), abs(box_of(other).x2-c.x)) < size*.14
                       for other in chars):
                    continue
                matches.append((paragraph, part, char))
    return matches[0] if len(matches) == 1 else None


def attach_formula_rules(page):
    """Give verified fractions and variable accents one geometry owner."""
    from linguafloat.document_scientific import merge_formula_runs
    from babeldoc.format.pdf.document_il import PdfFormula, PdfParagraphComposition
    formulas = []
    for paragraph in page.pdf_paragraph:
        size = paragraph.pdf_style.font_size if paragraph.pdf_style else 0
        paragraph.pdf_paragraph_composition = merge_formula_runs(paragraph.pdf_paragraph_composition, size)
        formulas.extend((part.pdf_formula, paragraph.xobj_id) for part in paragraph.pdf_paragraph_composition if part.pdf_formula)
    attached = 0
    for curve in list(getattr(page, "pdf_curve", [])):
        b = curve.box
        if not b or b.x2-b.x < 2 or b.y2-b.y > .8:
            continue
        matches = []
        for formula, xobj_id in formulas:
            if curve.xobj_id != xobj_id:
                continue
            f = formula.box
            if not f:
                continue
            text = "".join(c.char_unicode or "" for c in formula.pdf_character).strip()
            if (re.fullmatch(r"[A-Za-zα-ωΑ-Ω]", text) and abs(f.x-b.x)<.8 and abs(f.x2-b.x2)<.8
                    and min(abs(f.y-b.y), abs(f.y2-b.y)) < max(1, (f.y2-f.y)*.3)):
                matches.append(formula)
                continue
            if not (f.x-.5 <= b.x and b.x2 <= f.x2+.5 and f.y < b.y < f.y2):
                continue
            chars = [box_of(c) for c in formula.pdf_character if (c.char_unicode or "").strip()
                     and box_of(c) and b.x <= (box_of(c).x+box_of(c).x2)/2 <= b.x2]
            if any(c.y >= b.y-.15 for c in chars) and any(c.y2 <= b.y+.15 for c in chars):
                matches.append(formula)
        if not matches:
            candidate = _rule_symbol(page, curve)
            if candidate:
                from linguafloat.document_scientific import composition_chars
                paragraph, part, char = candidate
                formula = PdfFormula(box=union([box_of(char)]), pdf_character=[char],
                                     x_offset=0, y_offset=0, x_advance=0)
                parts = [PdfParagraphComposition(pdf_formula=formula) if c is char
                         else PdfParagraphComposition(pdf_character=c) for c in composition_chars(part)]
                index = paragraph.pdf_paragraph_composition.index(part)
                paragraph.pdf_paragraph_composition[index:index+1] = parts
                formulas.append((formula, paragraph.xobj_id))
                matches.append(formula)
        if len(matches) == 1:
            formula = matches[0]
            if curve not in formula.pdf_curve:
                formula.pdf_curve.append(curve)
            formula.box = union([formula.box, b])
            page.pdf_curve.remove(curve)
            attached += 1
    return attached


def preserve_symbol_labels(page, source_characters):
    """Keep nonlinguistic figure labels in their exact source glyph positions."""
    from babeldoc.format.pdf.document_il import PdfFormula, PdfParagraphComposition
    from linguafloat.document_scientific import composition_chars
    count = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.layout_label != "fallback_line" or not paragraph.box:
            continue
        text = re.sub(r"<[^>]+>|\{v\d+\}", "", paragraph.unicode or "")
        if re.search(r"[\u4e00-\u9fff]|[A-Za-z]{3,}", text):
            continue
        chars = [c for c in source_characters if c.xobj_id == paragraph.xobj_id and owns(paragraph.box, c)]
        source_text = "".join(c.char_unicode or "" for c in chars)
        if not chars or re.search(r"[a-z]{3,}", source_text):
            continue
        # A crop can intersect adjacent labels; reject rather than absorb them.
        if any(box_of(c).x < paragraph.box.x-1 or box_of(c).x2 > paragraph.box.x2+1 for c in chars):
            continue
        formulas = [p.pdf_formula for p in paragraph.pdf_paragraph_composition if p.pdf_formula]
        selected = {(c.render_order, c.pdf_character_id, c.xobj_id) for c in chars}
        if any((c.render_order, c.pdf_character_id, c.xobj_id) not in selected
               for part in paragraph.pdf_paragraph_composition for c in composition_chars(part)
               if (c.char_unicode or "").strip()):
            continue
        formula = PdfFormula(box=union([box_of(c) for c in chars]), pdf_character=copy.deepcopy(chars),
                             pdf_curve=[c for f in formulas for c in f.pdf_curve],
                             pdf_form=[f for original in formulas for f in original.pdf_form],
                             x_offset=0, y_offset=0, x_advance=0)
        paragraph.pdf_paragraph_composition = [PdfParagraphComposition(pdf_formula=formula)]
        paragraph.unicode = source_text
        count += 1
    return count


def restore_reference_styles(page, source_characters):
    """A reference must inherit its body font, not the nearby References heading."""
    count = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.layout_label != "reference" or not paragraph.box or not paragraph.pdf_style:
            continue
        chars = [c for c in source_characters if c.xobj_id == paragraph.xobj_id and c.pdf_style and owns(paragraph.box, c)
                 and (c.char_unicode or "").strip()]
        if len(chars) < 10:
            continue
        fonts = Counter((c.pdf_style.font_id, c.pdf_style.font_size) for c in chars)
        dominant, number = fonts.most_common(1)[0]
        if number < len(chars)*.7 or dominant == (paragraph.pdf_style.font_id, paragraph.pdf_style.font_size):
            continue
        style = next(c.pdf_style for c in chars if (c.pdf_style.font_id, c.pdf_style.font_size) == dominant)
        paragraph.pdf_style = copy.deepcopy(style)
        for part in paragraph.pdf_paragraph_composition:
            if part.pdf_same_style_unicode_characters:
                part.pdf_same_style_unicode_characters.pdf_style = copy.deepcopy(style)
        count += 1
    return count


def shift_unit(unit, dy):
    """Translate a placed unit without applying its formula offset a second time."""
    if not dy:
        return unit
    result = copy.copy(unit)
    def moved(box):
        b = copy.copy(box)
        b.y += dy
        b.y2 += dy
        return b
    if unit.unicode:
        result.y += dy
    else:
        if unit.formular:
            result.formular = copy.deepcopy(unit.formular)
            result.formular.box = moved(result.formular.box)
            chars = result.formular.pdf_character
            for drawing in [*result.formular.pdf_curve, *result.formular.pdf_form]:
                if drawing.box:
                    drawing.box = moved(drawing.box)
                matrix = list(drawing.relocation_transform or [1, 0, 0, 1, 0, 0])
                matrix[5] += dy
                drawing.relocation_transform = matrix
        else:
            result.char = copy.deepcopy(unit.char)
            chars = [result.char]
        for char in chars:
            char.box = moved(char.box)
            if char.visual_bbox and char.visual_bbox.box:
                char.visual_bbox.box = moved(char.visual_bbox.box)
    result.box_cache = result.height_cache = result.width_cache = None
    result._lf_line_y = getattr(unit, "_lf_line_y", unit.box.y) + dy
    return result


def space_formula_lines(units, box, size):
    """Keep actual formula extents inside the frame and clear adjacent lines."""
    lines = {}
    for unit in units:
        lines.setdefault(round(getattr(unit, "_lf_line_y", unit.box.y), 3), []).append(unit)
    result, ceiling = [], box.y2
    for baseline in sorted(lines, reverse=True):
        line = lines[baseline]
        ink = [u.box for u in line if not u.is_space]
        if not ink:
            result.extend(line)
            continue
        top, bottom = max(b.y2 for b in ink), min(b.y for b in ink)
        dy = min(0, ceiling-top)
        result.extend(shift_unit(u, dy) for u in line)
        ceiling = bottom+dy-max(.7, size*.12)
    return result, all(u.box.y >= box.y-.25 for u in result if not u.is_space)
