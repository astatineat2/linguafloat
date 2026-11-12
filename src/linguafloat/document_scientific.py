"""Source-grounded bibliography boundaries and original mathematical glyphs."""
from __future__ import annotations

import copy
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor


def normalize_formula_baseline(formula, size):
    """Anchor inline formulas to their own full-size glyphs, not another line."""
    if not formula.box or not size or formula.box.y2 - formula.box.y > size * 1.6:
        return
    bases = [c.box.y for c in formula.pdf_character
             if c.box and c.pdf_style and (c.char_unicode or "").strip()
             and c.pdf_style.font_size and .85 * size <= c.pdf_style.font_size <= 1.15 * size]
    if not bases:
        return  # Standalone superscripts/subscripts keep their intentional shift.
    clusters = {}
    for y in bases:
        clusters.setdefault(round(y, 1), []).append(y)
    group = max(clusters.values(), key=len)
    if max(bases) - min(bases) > size * .4:
        return  # Fractions and stacked equations are not inline baselines.
    formula.y_offset = formula.box.y - sum(group) / len(group)


def reference_title(text):
    """Only translate a title when both metadata boundaries are explicit."""
    quoted = re.search(r'[“"]([^”"]{12,})[”"]', text)
    if quoted:
        return text[:quoted.start(1)], quoted[1], text[quoted.end(1):]
    authors = re.match(r"^(.+?(?:et al\.|[A-Z]\.))\s+([A-Z](?:[a-z]|\s).*)$", text)
    if not authors:
        return None
    journal = re.search(r"\s+(?:Nature\b|Nat\.|N\.\s+J\.|SciPost\s+Phys\.|ACS\s+Nano\b|Nano\s+Lett\.|Adv\.|Phys\.|Sci\.|IEEE\b|J\.|Appl\.|Rev\.|Commun\.)", authors[2])
    if not journal or not re.search(r"\b(?:19|20)\d{2}\b", authors[2][journal.start():]):
        return None
    start = authors.start(2)
    end = start + journal.start()
    return text[:start], text[start:end], text[end:]


def reference_entries(page, in_references=False):
    import pymupdf as fitz
    words = page.get_text("words")
    vocabulary = {unicodedata.normalize("NFKC", w[4]).strip(".,;:()[]\"'").casefold() for w in words}
    headings = page.search_for("References") + page.search_for("REFERENCES")
    if not headings and not in_references:
        return []
    anchors = [w for w in words if re.fullmatch(r"\[\d{1,3}\]|\d{1,3}\.", w[4]) and w[7] == 0]
    columns = []
    for word in sorted(anchors, key=lambda w: (w[0], w[1])):
        # Publishers align either edge of the marker column. A second digit
        # moves the left edge but must not start an overlapping new column.
        col = next((c for c in columns if abs(c[0][0] - word[0]) < 4
                    or abs(c[0][2] - word[2]) < 4), None)
        if col is None:
            columns.append([word])
        else:
            col.append(word)
    entries = []
    for column in sorted(columns, key=lambda c: min(w[0] for w in c)):
        column.sort(key=lambda w: w[1])
        numbers = [int(re.sub(r"\D", "", w[4])) for w in column]
        if len(column) < 2 or any(b != a + 1 for a, b in zip(numbers, numbers[1:])):
            continue
        x0 = column[0][0]
        x1 = min((c[0][0] - 8 for c in columns if c[0][0] > x0 + 80), default=page.rect.width - 18)
        for i, anchor in enumerate(column):
            top = anchor[1] - 1
            bottom = column[i + 1][1] - 1 if i + 1 < len(column) else page.rect.height - 25
            rows = {}
            for word in words:
                if word is anchor or not (anchor[2] + 1 < word[0] < x1 and top <= (word[1]+word[3])/2 < bottom):
                    continue
                rows.setdefault((word[5], word[6]), []).append(word)
            ordered = sorted(rows.values(), key=lambda row: min(w[1] for w in row))
            chosen = []
            last_bottom = anchor[3]
            for row in ordered:
                if min(w[1] for w in row) - last_bottom > (anchor[3]-anchor[1]) * 1.5:
                    break
                chosen.extend(sorted(row, key=lambda w: w[0]))
                last_bottom = max(w[3] for w in row)
            if not chosen:
                continue
            box = fitz.Rect(chosen[0][:4])
            for word in chosen[1:]:
                box |= fitz.Rect(word[:4])
            tokens = []
            for word in chosen:
                token = unicodedata.normalize("NFKC", word[4])
                if tokens and tokens[-1][0].endswith("-") and tokens[-1][1] != (word[5], word[6]):
                    before, row = tokens.pop()
                    combined = before[:-1] + token
                    # Remove a line-wrap hyphen only with another intact occurrence.
                    token = combined if combined.strip(".,;:()[]\"'").casefold() in vocabulary else before + token
                tokens.append((token, (word[5], word[6])))
            text = " ".join(token for token, _ in tokens)
            entries.append(dict(number=anchor[4], marker=list(anchor[:4]), box=list(box), text=text))
    return entries


def composition_chars(composition):
    for name in ("pdf_line", "pdf_formula", "pdf_same_style_characters"):
        value = getattr(composition, name, None)
        if value:
            return value.pdf_character
    return [composition.pdf_character] if composition.pdf_character else []


def protect_math(paragraph, fonts):
    """Keep embedded glyph IDs, including fonts whose Unicode map is invalid."""
    from babeldoc.format.pdf.document_il import Box, PdfFormula, PdfParagraphComposition
    compositions = paragraph.pdf_paragraph_composition
    chars = [c for part in compositions if not part.pdf_formula for c in composition_chars(part)]
    if not chars:
        return 0
    all_chars = [c for part in compositions for c in composition_chars(part)]
    # Existing formula runs may own the subscript and its trailing space.
    # Removing them would turn 'RuO' + '2 ' + 'film' into 'RuOfilm'.
    text = "".join(c.char_unicode or "" for c in all_chars)
    protected = set()
    cursor = 0
    for c in chars:
        token = c.char_unicode or ""
        name = fonts.get(c.pdf_style.font_id, "") if c.pdf_style else ""
        if re.search(r"mth|math|symbol|cmex|cmsy|advp4c", name, re.I) or re.search(r"[α-ωΑ-Ω∑∫√≤≥±×÷₀-₉⁰-⁹]", token):
            protected.add(id(c))
        cursor += len(token)
    # Element symbols, numerical powers and small baseline-shifted characters
    # must survive model translation with their original case and geometry.
    spans = list(re.finditer(r"\b(?:tCo|tTb|Ta|Pt|Co|Tb|Al|SiO\d*|Fe|Ni|RuO\d*)\b|[\ufb00-\ufb06]{3,}", text))
    cursor = 0
    for c in all_chars:
        length = len(c.char_unicode or "")
        if any(m.start() < cursor + length and m.end() > cursor for m in spans):
            protected.add(id(c))
        cursor += length
    base_size = paragraph.pdf_style.font_size if paragraph.pdf_style else 0
    for c in chars:
        if c.pdf_style and base_size and c.pdf_style.font_size < base_size * .8:
            protected.add(id(c))
    if not protected:
        paragraph.pdf_paragraph_composition = merge_formula_runs(compositions, base_size)
        return 0
    result = []
    count = 0
    for part in compositions:
        if part.pdf_formula or not composition_chars(part):
            result.append(part)
            continue
        group, state = [], None
        def flush():
            nonlocal count
            if not group:
                return
            if state:
                boxes = [(c.visual_bbox.box if c.visual_bbox else c.box) for c in group if c.box]
                box = Box(min(b.x for b in boxes), min(b.y for b in boxes), max(b.x2 for b in boxes), max(b.y2 for b in boxes))
                neighbors = [c for c in chars if id(c) not in protected and c.box and c.pdf_style
                             and c.pdf_style.font_size >= base_size * .9
                             and abs((c.box.y+c.box.y2-box.y-box.y2)/2) < base_size]
                nearest = min(neighbors, key=lambda c: (abs(c.box.y-box.y),
                    min(abs(c.box.x2-box.x), abs(c.box.x-box.x2))), default=None)
                offset = box.y - nearest.box.y if nearest else 0
                result.append(PdfParagraphComposition(pdf_formula=PdfFormula(box=box, pdf_character=list(group),
                    x_offset=0, y_offset=offset, x_advance=0)))
                count += 1
            else:
                result.extend(PdfParagraphComposition(pdf_character=c) for c in group)
        for char in composition_chars(part):
            wanted = id(char) in protected
            if group and wanted != state:
                flush()
                group = []
            state = wanted
            group.append(char)
        flush()
    paragraph.pdf_paragraph_composition = merge_formula_runs(result, base_size)
    for part in paragraph.pdf_paragraph_composition:
        if part.pdf_formula:
            normalize_formula_baseline(part.pdf_formula, base_size)
    return count


def repair_cached_chemical_scripts(page, source_words, source_characters=()):
    """Reattach split scripts only with source-word and original-glyph evidence."""
    from babeldoc.format.pdf.document_il import Box, PdfFormula, PdfParagraphComposition
    templates = {}
    for word, box in source_words:
        match = re.fullmatch(r"((?:[A-Z][a-z]?)+)([0-9]+)[,.;]?", word)
        if not match:
            continue
        group = [c for c in source_characters if c.box and c.pdf_style
                 and box[0] - .5 <= (c.box.x+c.box.x2)/2 <= box[2] + .5
                 and box[1] - .5 <= c.box.y <= box[3] + .5]
        group.sort(key=lambda c: c.box.x)
        if "".join(c.char_unicode or "" for c in group).rstrip(",.;") != match[1] + match[2]:
            continue
        script = group[len(match[1])]
        if script.pdf_style.font_size < group[0].pdf_style.font_size * .8:
            templates.setdefault((match[1], match[2], script.pdf_style.font_id),
                                 (group[:len(match[1])], script))
    for paragraph in page.pdf_paragraph:
        for part in paragraph.pdf_paragraph_composition:
            f = part.pdf_formula
            if not f or f.pdf_curve or f.pdf_form:
                continue
            chars = f.pdf_character
            text = "".join(c.char_unicode or "" for c in chars)
            for match in re.finditer(r"\b((?:[A-Z][a-z]?)+)([0-9]+)\b", text):
                if any(len(c.char_unicode or "") != 1 for c in chars):
                    continue
                group = chars[match.start():match.end()]
                script = group[len(match[1]):]
                if not all(c.box and c.pdf_style for c in group):
                    continue
                if script[0].pdf_style.font_size >= group[0].pdf_style.font_size * .8:
                    continue
                key = (match[1], match[2], script[0].pdf_style.font_id)
                templates.setdefault(key, (group[:len(match[1])], script[0]))
    repaired = 0
    for paragraph in page.pdf_paragraph:
        result = []
        for part in paragraph.pdf_paragraph_composition:
            f = part.pdf_formula
            chars = f.pdf_character if f else []
            text = "".join(c.char_unicode or "" for c in chars)
            match = re.fullmatch(r"([0-9]+)([\s,.;]*)", text)
            if not match or not result or f.pdf_curve or f.pdf_form:
                result.append(part)
                continue
            previous = result[-1].pdf_same_style_unicode_characters
            anchor = chars[0]
            if not previous or not anchor.box or not anchor.pdf_style:
                result.append(part)
                continue
            candidates = []
            for (base, digits, font), template in templates.items():
                if digits != match[1] or font != anchor.pdf_style.font_id:
                    continue
                words = [(word, box) for word, box in source_words
                         if word.strip(".,;:()[]") == base + digits
                         and box[0] <= anchor.box.x <= box[2] + 1
                         and box[1] - 1 <= anchor.box.y <= box[3] + 1]
                positions = list(re.finditer(r"(?<![A-Za-z])" + re.escape(base) + r"(?![A-Za-z0-9])", previous.unicode or ""))
                if words and len(positions) == 1:
                    candidates.append((template, positions[0]))
            if len(candidates) != 1:
                result.append(part)
                continue
            (base_chars, old_anchor), position = candidates[0]
            ratio = anchor.pdf_style.font_size / old_anchor.pdf_style.font_size
            cloned = copy.deepcopy(base_chars)
            def transform(box):
                return Box(anchor.box.x + (box.x-old_anchor.box.x)*ratio,
                           anchor.box.y + (box.y-old_anchor.box.y)*ratio,
                           anchor.box.x + (box.x2-old_anchor.box.x)*ratio,
                           anchor.box.y + (box.y2-old_anchor.box.y)*ratio)
            for char in cloned:
                char.box = transform(char.box)
                if char.visual_bbox and char.visual_bbox.box:
                    char.visual_bbox.box = transform(char.visual_bbox.box)
                char.pdf_style.font_size *= ratio
            digits_chars = chars[:len(match[1])]
            group = cloned + digits_chars
            boxes = [c.box for c in group]
            formula = PdfFormula(box=Box(min(b.x for b in boxes), min(b.y for b in boxes),
                max(b.x2 for b in boxes), max(b.y2 for b in boxes)), pdf_character=group,
                x_offset=0, y_offset=0, x_advance=0)
            normalize_formula_baseline(formula, paragraph.pdf_style.font_size)
            prefix, suffix = copy.copy(previous), copy.copy(previous)
            prefix.unicode, suffix.unicode = previous.unicode[:position.start()], previous.unicode[position.end():]
            result.pop()
            if prefix.unicode:
                result.append(PdfParagraphComposition(pdf_same_style_unicode_characters=prefix))
            result.append(PdfParagraphComposition(pdf_formula=formula))
            if suffix.unicode:
                result.append(PdfParagraphComposition(pdf_same_style_unicode_characters=suffix))
            if match[2].strip():
                rest = copy.copy(previous)
                rest.unicode = match[2]
                result.append(PdfParagraphComposition(pdf_same_style_unicode_characters=rest))
            repaired += 1
        paragraph.pdf_paragraph_composition = result
    return repaired


def merge_formula_runs(parts, size):
    """Keep adjacent equation pieces together across font/style boundaries."""
    from babeldoc.format.pdf.document_il import Box, PdfFormula, PdfParagraphComposition
    if not size:
        return parts
    if any(p.pdf_formula for p in parts):
        # Native parser lines can still contain the equation's base characters.
        # Expose their original glyphs to the existing word/geometry guards so
        # a separate exponent cannot wrap independently from its base.
        parts = [child for part in parts for child in (
            [PdfParagraphComposition(pdf_character=c) for c in composition_chars(part)]
            if not part.pdf_formula and composition_chars(part) else [part])]
    text = "".join((p.pdf_character.char_unicode or "") if p.pdf_character else "\ufffc" for p in parts)
    blocked = set()
    offsets, offset = [], 0
    for part in parts:
        token = part.pdf_character.char_unicode or "" if part.pdf_character else "\ufffc"
        offsets.append((offset, offset + len(token)))
        offset += len(token)
    for match in re.finditer(r"[A-Za-z]+", text):
        if len(match[0]) >= 2 and match[0] not in {"nm", "mm", "cm", "ns", "ms", "sin", "cos", "exp", "ln"}:
            blocked.update(i for i, (a, b) in enumerate(offsets) if a < match.end() and b > match.start())
    chosen = {i for i, p in enumerate(parts) if p.pdf_formula}
    def bounds(part):
        if part.pdf_formula:
            return part.pdf_formula.box
        char = part.pdf_character
        return (char.visual_bbox.box if char.visual_bbox else char.box) if char else None
    def adjacent(a, b):
        if a is None or b is None:
            return False
        return (b.x2 >= a.x - size * .65 and b.x <= a.x2 + size * .65
                and min(a.y2, b.y2) >= max(a.y, b.y) - size * .3)
    changed = True
    while changed:
        changed = False
        for i, part in enumerate(parts):
            if i in chosen or i in blocked or not part.pdf_character:
                continue
            if ((i-1 in chosen and adjacent(bounds(parts[i-1]), bounds(part))) or
                    (i+1 in chosen and adjacent(bounds(part), bounds(parts[i+1])))):
                chosen.add(i)
                changed = True
    result, group = [], []
    def flush():
        if not group:
            return
        if len(group) == 1 and group[0].pdf_formula:
            result.append(group[0])
            return
        boxes = [bounds(p) for p in group]
        original = [p.pdf_formula for p in group if p.pdf_formula]
        anchor = original[0]
        box = Box(min(b.x for b in boxes), min(b.y for b in boxes), max(b.x2 for b in boxes), max(b.y2 for b in boxes))
        result.append(PdfParagraphComposition(pdf_formula=PdfFormula(
            box=box, pdf_character=[c for p in group for c in composition_chars(p)],
            pdf_curve=[c for f in original for c in f.pdf_curve],
            pdf_form=[f for formula in original for f in formula.pdf_form],
            x_offset=0, y_offset=box.y-anchor.box.y+(anchor.y_offset or 0), x_advance=0)))
    for i, part in enumerate(parts):
        if i in chosen:
            boxes = [bounds(p) for p in group]
            box = Box(min(b.x for b in boxes), min(b.y for b in boxes), max(b.x2 for b in boxes), max(b.y2 for b in boxes)) if boxes else None
            if group and not adjacent(box, bounds(part)):
                flush()
                group = []
            group.append(part)
        else:
            flush()
            group = []
            result.append(part)
    flush()
    return result


def check_formula_tokens(translate_input, output):
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    tokens = [p.placeholder for p in translate_input.placeholders if isinstance(p, FormulaPlaceholder)]
    if not tokens:
        return True
    pattern = re.compile("|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)))
    unknown = set(re.findall(r"\{[vV]\d+\}", output)) - set(re.findall(r"\{[vV]\d+\}", translate_input.unicode))
    return not unknown and pattern.findall(output) == pattern.findall(translate_input.unicode)


def _formula_fragment_problem(source, text, tokens):
    if not isinstance(text, str):
        return "片段译文不是文字"
    if not text.strip():
        return "片段返回空译文"
    if any(token in text for token in tokens) or re.search(r"\{[vV]\d+\}", text):
        return "片段夹带公式标记"
    if re.findall(r"<[^>]+>", source) != re.findall(r"<[^>]+>", text):
        return "片段格式标签丢失、增加或顺序变化"
    from linguafloat.document_context import scientific_quantities
    if scientific_quantities(source) != scientific_quantities(text):
        return "片段小数、百分比或数量级发生变化"
    return ""


def _formula_reply(response, fragments):
    try:
        data = json.loads(response)
    except (ValueError, TypeError) as exc:
        raise ValueError("补译响应不是有效 JSON") from exc
    expected = {item["id"] for item in fragments}
    if not isinstance(data, list) or len(data) != len(expected):
        raise ValueError("片段数量不匹配")
    values = {}
    for item in data:
        if (not isinstance(item, dict) or type(item.get("id")) is not int
                or item["id"] not in expected or item["id"] in values):
            raise ValueError("片段编号缺失、重复或不匹配")
        values[item["id"]] = item.get("output")
    return values


def translate_formula_segments(translate_input, translator):
    """Reconstruct immutable formulas around translated, explicitly keyed prose."""
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    from linguafloat.document_core import DocumentError
    tokens = [p.placeholder for p in translate_input.placeholders if isinstance(p, FormulaPlaceholder)]
    if not tokens:
        return translate_input.unicode
    pattern = "(" + "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)) + ")"
    pieces = re.split(pattern, translate_input.unicode)
    fragments = [dict(id=i, input=text) for i, text in enumerate(pieces)
                 if i % 2 == 0 and re.search(r"[A-Za-z]{2,}|[\u3400-\u9fff]", re.sub(r"<[^>]+>", "", text))]
    if not fragments:
        return translate_input.unicode
    messages = translator.messages("")
    messages[0]["content"] += (
        " Formula-segment repair v1. Return a JSON array of {id, output} objects, one for every fragment."
        " Translate each fragment independently using the context only to resolve meaning."
        " Never move words between fragments. Do not output any formula placeholders."
        " Preserve all style tags exactly; fragments may begin or end with incomplete sentences."
        " Do not translate or output context. Do not add explanations or Markdown fences.")
    messages[1]["content"] = json.dumps(dict(fragments=fragments, context=translate_input.unicode), ensure_ascii=False)
    # Content recovery shares the transport retry budget: malformed responses
    # must not create another independently replenished retry loop.
    budget = [max(0, min(3, int(getattr(translator, "retries", 0))))]
    store = getattr(translator, "store", None)
    control = getattr(translator, "control", None)
    recovered = False

    def checkpoint():
        if control is not None:
            control.checkpoint()

    def discard(request):
        if store is not None:
            store.discard(translator.cache_key(request))

    def stop(reason):
        location = getattr(translator, "location", "文档")
        raise DocumentError(f"[公式旁文字补译失败] {location}：{reason}。未改动原公式，未发布不完整译文。"
                            "已有合格译文缓存保留。")

    def reserve(reason):
        nonlocal recovered
        checkpoint()
        if getattr(getattr(translator, "job", None), "relayout_only", False):
            stop(reason + "；仅重新排版不会发送补译请求")
        if budget[0] <= 0:
            stop(reason + "；当前重试额度已用尽")
        budget[0] -= 1
        recovered = True

    while True:
        response = translator.request(messages, budget=budget, part="公式间文字补译")
        checkpoint()
        try:
            values = _formula_reply(response, fragments)
            break
        except ValueError as exc:
            discard(messages)
            reserve(str(exc))

    problems = {item["id"]: _formula_fragment_problem(item["input"], values[item["id"]], tokens)
                for item in fragments}
    if any(problems.values()):
        discard(messages)
    for item in fragments:
        identity, source = item["id"], item["input"]
        problem = problems[identity]
        if not problem:
            continue
        # Only the rejected prose is requested again; formula slots and the
        # other validated fragments never leave their original positions.
        single = translator.messages("")
        single[0]["content"] += (
            " Formula-segment single repair v1. The user JSON contains fragment and reference-only context."
            " Translate only fragment as plain text, not JSON. Keep its grammatical role and every style tag."
            " Context is not a translation target. Never copy any formula placeholders from context."
            " Do not move, add or omit words to complete the surrounding sentence."
            " Return nonempty translated text without explanations or Markdown fences.")
        single[1]["content"] = json.dumps(dict(fragment=source, context=translate_input.unicode[:6000]), ensure_ascii=False)
        while problem:
            reserve(f"片段 {identity}：{problem}")
            text = translator.request(single, budget=budget, part=f"公式间文字补译 · 片段 {identity}")
            checkpoint()
            problem = _formula_fragment_problem(source, text, tokens)
            if not problem and text.lstrip().startswith(("```", '[{', '{"')):
                problem = "单片段补译未返回纯文本"
            if problem:
                discard(single)
            else:
                values[identity] = text

    for identity, text in values.items():
        pieces[identity] = text
    result = "".join(pieces)
    if not check_formula_tokens(translate_input, result):
        discard(messages)
        stop("重组后的公式标记丢失、增加或顺序变化")
    checkpoint()
    if recovered and store is not None:
        store.put(translator.cache_key(messages), json.dumps(
            [dict(id=item["id"], output=values[item["id"]]) for item in fragments], ensure_ascii=False))
    return result


class ScientificLayout:
    def __init__(self, source, translator, warnings):
        import pymupdf as fitz
        self.references = {}
        self.source_words = {}
        self.translator, self.warnings = translator, warnings
        self.prepared = set()
        self.affected = False
        with fitz.open(source) as doc:
            in_refs = False
            for page in doc:
                self.source_words[page.number] = [(w[4], (w[0], page.rect.height-w[3], w[2], page.rect.height-w[1]))
                                                  for w in page.get_text("words")] if not page.rotation and not page.cropbox.x0 and not page.cropbox.y0 else []
                entries = reference_entries(page, in_refs)
                in_refs = bool(entries)
                self.references[page.number] = (page.rect.height, entries)
                self.affected |= bool(entries) or any(re.search(r"mth|math|symbol|cmex|cmsy|advp4c", f[3], re.I)
                                                     for f in page.get_fonts(full=True))

    def prepare(self, docs):
        from babeldoc.format.pdf.document_il import Box, PdfParagraphComposition, PdfSameStyleUnicodeCharacters
        pending = []
        for page in docs.page:
            if id(page) in self.prepared:
                continue
            self.prepared.add(id(page))
            from linguafloat.document_geometry import join_fragmented_paragraphs, attach_formula_rules
            join_fragmented_paragraphs(page, self.source_words.get(page.page_number, []))
            height, entries = self.references.get(page.page_number, (0, []))
            fonts = {f.font_id: f.name or "" for f in page.pdf_font}
            for obj in page.pdf_xobject:
                fonts.update({f.font_id: f.name or "" for f in obj.pdf_font})
            # Replace only fully assigned source characters. Unassigned content
            # stays in its original paragraph; references never absorb a heading.
            for entry in entries:
                bx = entry["box"]
                mark = entry["marker"]
                target = [mark[0] - .5, height - bx[3] - .5, bx[2] + .5, height - min(mark[1], bx[1]) + .5]
                matched, template = [], None
                for paragraph in page.pdf_paragraph:
                    remaining = []
                    changed = False
                    for part in paragraph.pdf_paragraph_composition:
                        chars = composition_chars(part)
                        picked = [c for c in chars if c.box and target[0] <= (c.box.x+c.box.x2)/2 <= target[2]
                                  and target[1] <= (c.box.y+c.box.y2)/2 <= target[3]]
                        if not picked:
                            remaining.append(part)
                            continue
                        if part.pdf_formula and len(picked) != len(chars):
                            remaining.append(part)
                            continue
                        template = template or paragraph
                        changed = True
                        matched.extend(picked)
                        remaining.extend(PdfParagraphComposition(pdf_character=c) for c in chars if c not in picked)
                    paragraph.pdf_paragraph_composition = remaining
                    if changed:
                        paragraph.unicode = "".join(c.char_unicode or "" for part in remaining for c in composition_chars(part))
                        leftover = [c.box for part in remaining for c in composition_chars(part) if c.box]
                        if leftover:
                            paragraph.box = Box(min(b.x for b in leftover), min(b.y for b in leftover),
                                                max(b.x2 for b in leftover), max(b.y2 for b in leftover))
                if not matched or template is None:
                    continue
                parts = reference_title(entry["text"])
                text = entry["text"]
                if re.search(r"\b(?:[A-Z][a-z]?\d)|[₀-₉⁰-⁹]|[\x00-\x08\x0e-\x1f]", text):
                    page.pdf_character.extend(matched)
                    self.warnings.append(f"原始第 {page.page_number+1} 页参考文献 {entry['number']}：含化学式或特殊字体，保留整条原始字形和编号。")
                    continue
                if not parts:
                    self.warnings.append(f"原始第 {page.page_number+1} 页参考文献 {entry['number']}：题名边界不明确，保留该条原文及编号。")
                ref = copy.copy(template)
                from collections import Counter
                styles = Counter((c.pdf_style.font_id, c.pdf_style.font_size) for c in matched
                                 if c.pdf_style and (c.char_unicode or "").strip() and c.box.x2 > bx[0]-.5)
                if styles:
                    body_style = styles.most_common(1)[0][0]
                    ref.pdf_style = copy.deepcopy(next(c.pdf_style for c in matched if c.pdf_style
                        and (c.pdf_style.font_id, c.pdf_style.font_size) == body_style))
                ref.box = Box(bx[0], height-bx[3], bx[2], height-bx[1])
                ref.unicode, ref.first_line_indent, ref.layout_label = text, False, "reference"
                ref.debug_id = f"lf-reference-{page.page_number}-{entry['number']}"
                ref.pdf_paragraph_composition = [PdfParagraphComposition(pdf_same_style_unicode_characters=
                    PdfSameStyleUnicodeCharacters(pdf_style=ref.pdf_style, unicode=text))]
                # No model sees these independently placed source numbering glyphs.
                page.pdf_character.extend(c for c in matched if c.box.x2 <= bx[0] - .5)
                page.pdf_paragraph.append(ref)
                if parts:
                    pending.append((ref, parts, page.page_number + 1, entry['number']))
            page.pdf_paragraph[:] = [p for p in page.pdf_paragraph if p.pdf_paragraph_composition]
            for paragraph in page.pdf_paragraph:
                if paragraph.layout_label != "reference":
                    protect_math(paragraph, fonts)
            attach_formula_rules(page)
        if pending:
            def translate(item):
                ref, parts, page_number, number = item
                previous = getattr(self.translator, "location", "文档")
                self.translator.location = f"原始第 {page_number} 页 · 参考文献 {number}"
                try:
                    context = "Reference title only; keep all formulas and element symbols unchanged."
                    if hasattr(self.translator, "gate"):
                        result = self.translator.translate_fragment(parts[1], context, part="参考文献题名")
                    else:
                        result = self.translator.translate_fragment(parts[1], context)
                    return parts[0] + result + parts[2]
                finally:
                    self.translator.location = previous
            maximum = getattr(getattr(self.translator, "gate", None), "maximum", 1)
            # Only network work is concurrent; PDF objects are updated in order.
            with ThreadPoolExecutor(max_workers=maximum) as pool:
                futures = [pool.submit(translate, item) for item in pending]
                try:
                    for item, future in zip(pending, futures):
                        text = future.result()
                        ref = item[0]
                        ref.unicode = text
                        ref.pdf_paragraph_composition[0].pdf_same_style_unicode_characters.unicode = text
                except BaseException:
                    for future in futures:
                        future.cancel()
                    raise

    def detach_references(self, docs):
        removed = []
        for page in docs.page:
            refs = [(i, p) for i, p in enumerate(page.pdf_paragraph) if p.layout_label == "reference"]
            page.pdf_paragraph[:] = [p for p in page.pdf_paragraph if p.layout_label != "reference"]
            removed.append((page, refs))
        return removed

    @staticmethod
    def restore_references(removed):
        for page, refs in removed:
            for i, paragraph in refs:
                page.pdf_paragraph.insert(min(i, len(page.pdf_paragraph)), paragraph)
