"""Source-grounded bibliography boundaries and original mathematical glyphs."""
from __future__ import annotations

import copy
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor


def normalize_formula_baseline(formula, size):
    """Anchor inline formulas to their own full-size glyphs, not another line."""
    if getattr(formula, "_lf_caption_marker", False):
        return
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
    from linguafloat.document_references import _bibliographic_entry, _digital_lines, _reference_column_split, is_heading

    words = page.get_text("words")
    vocabulary = {unicodedata.normalize("NFKC", w[4]).strip(".,;:()[]\"'").casefold() for w in words}
    lines = _digital_lines(page)
    headings = [r["box"] for r in lines if r["horizontal"] and is_heading(r["text"])]
    if not headings and not in_references:
        return []
    anchors = [w for w in words if re.fullmatch(r"\[\d{1,3}\]|\d{1,3}\.", w[4]) and w[7] == 0]
    if headings:
        split = _reference_column_split(lines, page)

        def order(box, *, bottom=False):
            return (split is not None and box[0] >= split, box[3 if bottom else 1])

        start = min(order(box, bottom=True) for box in headings)
        anchors = [w for w in anchors if order(w) >= start]
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
            if _bibliographic_entry(text):
                entries.append(dict(number=anchor[4], marker=list(anchor[:4]), box=list(box), text=text))
    return entries


def composition_chars(composition):
    for name in ("pdf_line", "pdf_formula", "pdf_same_style_characters"):
        value = getattr(composition, name, None)
        if value:
            return value.pdf_character
    return [composition.pdf_character] if composition.pdf_character else []


def join_partial_scientific_tokens(paragraph):
    """Complete parser-split quantities/IDs using only original PDF glyphs.

    A formula slot can own the middle of a decimal range or an identifier's
    final digit. Translating the neighbouring pieces independently cannot
    preserve that logical token. Only spans touching an existing formula are
    joined; unrelated prose, punctuation and normal numbers stay unchanged.
    """
    from babeldoc.format.pdf.document_il import Box, PdfFormula, PdfParagraphComposition
    parts = paragraph.pdf_paragraph_composition
    if not any(p.pdf_formula for p in parts):
        return
    atoms, offsets, tokens, cursor = [], [], [], 0
    size = paragraph.pdf_style.font_size if paragraph.pdf_style else 0
    previous = None
    for part in parts:
        children = ([part] if part.pdf_formula or not composition_chars(part) else
                    [PdfParagraphComposition(pdf_character=c) for c in composition_chars(part)])
        for child in children:
            child_chars = composition_chars(child)
            token = ''.join(c.char_unicode or '' for c in child_chars)
            first = next((c for c in child_chars if (c.char_unicode or '').strip()), None)
            # Extraction can omit a visible word gap. This separator only guides
            # token matching; the original glyphs and their order stay untouched.
            if (previous and first and tokens and tokens[-1][-1:].isalnum()
                    and token[:1].isalnum() and previous.box and first.box and size
                    and (abs(first.box.y-previous.box.y) < size*.2
                         or (abs(first.box.y-previous.box.y) < size*.7
                             and any(c.pdf_style and (c.pdf_style.font_size or size) < size*.85
                                     for c in (previous, first))))
                    and first.box.x-previous.box.x2 > size*.22):
                tokens.append(' ')
                cursor += 1
            atoms.append(child)
            offsets.append((cursor, cursor + len(token)))
            tokens.append(token)
            cursor += len(token)
            if token:
                previous = next((c for c in reversed(child_chars)
                                 if (c.char_unicode or '').strip()), None)
    text = ''.join(tokens)
    number = r'[-+\u2212]?\d+(?:\.\d+)?(?:[eE][-+\u2212]?\d+)?'
    identifier = r'[A-Za-z][A-Za-z0-9]*\d[A-Za-z0-9]*(?![A-Za-z0-9_])'
    # A unit can touch a number in the extracted IL even when its PDF glyphs
    # have a word gap. Never backtrack into a decimal/range just to exclude it.
    quantity = (number + r'(?:[\u2212\u2013-]' + number + r')?[%\uff05]?'
                r'(?![0-9_]|\.\d|[eE][-+\u2212]?\d|[\u2212\u2013-]\d)')
    pattern = r'(?<![A-Za-z0-9_.])(?:' + identifier + '|' + quantity + ')'
    # The exceptional case is a single native slot straddling two partial
    # decimals. Do not re-tokenize intact uncertainty values in old caches.
    broken_centres = {a for part,(a,b) in zip(atoms,offsets) if part.pdf_formula and
                      re.fullmatch(r'\d+\s*±\s*\d+',text[a:b]) and text[:a].endswith('.') and
                      re.match(r'\.\d',text[b:])}
    compound = [m for m in re.finditer(r'(?<![A-Za-z0-9_.])'+number+r'\s*±\s*'+number+r'(?![0-9_.])',text)
                if any(m.start()<a<m.end() for a in broken_centres)] if broken_centres else []
    matches = [m for m in re.finditer(pattern,text) if not any(c.start()<m.end() and m.start()<c.end() for c in compound)]
    groups = []
    for match in sorted(matches + compound,key=lambda m:m.start()):
        members = {i for i, (a, b) in enumerate(offsets) if a < match.end() and b > match.start()}
        formulas = [i for i in members if atoms[i].pdf_formula]
        if not formulas or len(members) <= 1:
            continue
        def spills_outside_token(i):
            a, b = offsets[i]
            before, after = text[a:match.start()].strip(), text[match.end():b].strip()
            # A native slot can already own the unit prefix after the fraction.
            # Keep that slot whole; do not consume any following prose glyphs.
            unit_tail = (after in {'μ', 'µ'} and a > match.start() and
                         re.fullmatch(r'[-+−]?\d+\.\d+', match[0]) and
                         re.fullmatch(r'\d+', text[a:match.end()]))
            # A formula can own the equals sign and just the integral part.
            # Complete the decimal in situ; never absorb a larger equation.
            assignment = (before == '=' and not after and
                          re.fullmatch(r'[-+−]?\d+\.\d+', match[0]) and
                          re.fullmatch(r'[-+−]?\d+', text[match.start():b]))
            return (before and not assignment) or (after and not unit_tail)

        if any(spills_outside_token(i) for i in formulas):
            continue  # Never split a larger pre-existing equation to complete a token.
        chars = [c for i in sorted(members) for c in composition_chars(atoms[i])]
        painted = [c for c in chars if (c.char_unicode or '').strip()]
        if (any(c.vertical or not c.box for c in painted)
                or len({c.xobj_id for c in painted}) > 1):
            continue
        bases = [c.box.y for c in painted if c.pdf_style and size
                 and (c.pdf_style.font_size or 0) >= size*.85]
        if bases and max(bases)-min(bases) > size*.4:
            continue  # Paint order alone cannot join tokens across lines/columns.
        # A raised reference after a regular word is not the numeric suffix
        # of an identifier. Leave the prose translatable and retain the glyphs.
        word_number = re.fullmatch(r'([A-Z]?[a-z]{2,})(\d{1,3})', match[0])
        if bases and word_number and word_number[1].lower() not in {'sin', 'cos', 'tan', 'exp', 'log', 'ln'}:
            scripts = [c for i in formulas for c in composition_chars(atoms[i])
                       if (c.char_unicode or '').strip()]
            if scripts and all(c.char_unicode.isdigit() and c.pdf_style and c.box
                               and (c.pdf_style.font_size or size) < size*.85
                               and c.box.y-max(bases) >= size*.15 for c in scripts):
                continue
        if size and any(b.box.x-a.box.x2 > size*1.5 or b.box.x < a.box.x-size*.25
                        for a, b in zip(painted, painted[1:])):
            continue
        for group in groups[:]:
            if members & group:
                members |= group
                groups.remove(group)
        groups.append(members)
    if not groups:
        return
    owners = {i:g for g in groups for i in g}
    result = []
    for i, part in enumerate(atoms):
        group = owners.get(i)
        if group is None:
            result.append(part)
            continue
        if i != min(group):
            continue
        selected = [atoms[j] for j in sorted(group)]
        chars = [c for p in selected for c in composition_chars(p)]
        originals = [p.pdf_formula for p in selected if p.pdf_formula]
        boxes = [c.box for c in chars if c.box] + [f.box for f in originals if f.box]
        box = Box(min(b.x for b in boxes), min(b.y for b in boxes),
                  max(b.x2 for b in boxes), max(b.y2 for b in boxes))
        anchor = originals[0]
        result.append(PdfParagraphComposition(pdf_formula=PdfFormula(box=box, pdf_character=chars,
            pdf_curve=[c for f in originals for c in f.pdf_curve],
            pdf_form=[o for f in originals for o in f.pdf_form], x_offset=0,
            y_offset=box.y-anchor.box.y+(anchor.y_offset or 0), x_advance=0)))
    paragraph.pdf_paragraph_composition = result


def protect_math(paragraph, fonts):
    """Keep embedded glyph IDs, including fonts whose Unicode map is invalid."""
    from babeldoc.format.pdf.document_il import Box, PdfFormula, PdfParagraphComposition
    join_partial_scientific_tokens(paragraph)
    compositions = paragraph.pdf_paragraph_composition
    chars = [c for part in compositions if not part.pdf_formula for c in composition_chars(part)]
    all_chars = [c for part in compositions for c in composition_chars(part)]
    # Existing formula runs may own the subscript and its trailing space.
    # Removing them would turn 'RuO' + '2 ' + 'film' into 'RuOfilm'.
    text = "".join(c.char_unicode or "" for c in all_chars)
    protected = set()
    cursor = 0
    for c in chars:
        token = c.char_unicode or ""
        name = fonts.get((c.xobj_id, c.pdf_style.font_id),
                         fonts.get(c.pdf_style.font_id, "")) if c.pdf_style else ""
        if re.search(r"mth|math|symbol|cmex|cmsy|advp4c", name, re.I) or re.search(r"[α-ωΑ-Ω∑∫√≤≥±×÷⋉⋊₀-₉⁰-⁹]", token):
            protected.add(id(c))
        cursor += len(token)
    # Element symbols, numerical powers and small baseline-shifted characters
    # must survive model translation with their original case and geometry.
    spans = list(re.finditer(r"\b(?:tCo|tTb|Ta|Pt|Co|Tb|Al|SiO\d*|Fe|Ni|RuO\d*)\b|[\ufb00-\ufb06]{3,}", text))
    # Recognize stoichiometric words, including a script *between* elements
    # (Mn3Sn), before separating pre-existing formula compositions. Validate
    # element symbols so ordinary labels such as Figure2 do not become math.
    elements = set("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
                   "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La "
                   "Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi "
                   "Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt "
                   "Ds Rg Cn Nh Fl Mc Lv Ts Og".split())
    for match in re.finditer(r"\b(?:[A-Z][a-z]?[0-9₀-₉]*)+\b", text):
        if re.search(r"[0-9₀-₉]", match[0]) and all(
                symbol in elements for symbol in re.findall(r"[A-Z][a-z]?", match[0])):
            spans.append(match)
    cursor = 0
    for c in all_chars:
        length = len(c.char_unicode or "")
        if any(m.start() < cursor + length and m.end() > cursor for m in spans):
            protected.add(id(c))
        cursor += length
    base_size = paragraph.pdf_style.font_size if paragraph.pdf_style else 0
    for c in chars:
        # Some publishers paint a microscopic ASCII space between every word.
        # Its size alone is not evidence of a subscript. Existing formula runs
        # (including their internal spaces) are not part of this pass.
        if (c.pdf_style and base_size and c.pdf_style.font_size < base_size * .8
                and c.char_unicode != ' '):
            protected.add(id(c))
    if not protected:
        paragraph.pdf_paragraph_composition = merge_formula_runs(compositions, base_size)
        for part in paragraph.pdf_paragraph_composition:
            if part.pdf_formula:
                normalize_formula_baseline(part.pdf_formula, base_size)
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
    if not size or not any(p.pdf_formula for p in parts):
        return parts
    for part in parts:
        formula = part.pdf_formula
        if not formula or getattr(formula, "_lf_caption_marker", False):
            continue
        # Parser formula boxes may omit late-painted scripts. Expand from
        # original glyphs, never from guessed Unicode or a replacement font.
        boxes = [formula.box] if formula.box else []
        for char in formula.pdf_character:
            if char.box:
                boxes.append(char.box)
            if char.visual_bbox and char.visual_bbox.box:
                boxes.append(char.visual_bbox.box)
        boxes.extend(item.box for item in [*formula.pdf_curve, *formula.pdf_form] if item.box)
        if boxes:
            box = Box(min(b.x for b in boxes), min(b.y for b in boxes),
                      max(b.x2 for b in boxes), max(b.y2 for b in boxes))
            if formula.box:
                formula.y_offset = (formula.y_offset or 0) + box.y - formula.box.y
            formula.box = box
    if any(p.pdf_formula for p in parts):
        # Native parser lines can still contain the equation's base characters.
        # Expose their original glyphs to the existing word/geometry guards so
        # a separate exponent cannot wrap independently from its base.
        parts = [child for part in parts for child in (
            [PdfParagraphComposition(pdf_character=c) for c in composition_chars(part)]
            if not part.pdf_formula and composition_chars(part) else [part])]
    blocked = set()
    offsets, offset, tokens, previous = [], 0, [], None
    for part in parts:
        char = part.pdf_character
        # Text runs can be painted in a different order from the expression.
        # Do not mistake spatially separated identifiers for a prose word.
        if previous and char and previous.box and char.box and (
                abs(previous.box.y-char.box.y) > size*.35
                or char.box.x-previous.box.x2 > size*.35
                or char.box.x < previous.box.x):
            tokens.append("\ufffc")
            offset += 1
        token = part.pdf_character.char_unicode or "" if part.pdf_character else "\ufffc"
        offsets.append((offset, offset + len(token)))
        tokens.append(token)
        offset += len(token)
        previous = char
    text = "".join(tokens)
    for match in re.finditer(r"[A-Za-z]+", text):
        # Uppercase mathematical operators applied to an argument (SO(2),
        # SU(n), O(n), ...) may straddle already protected argument glyphs.
        # Merely being uppercase is insufficient: prose acronyms stay text.
        operator = (match[0].isupper() and len(match[0]) <= 3
                    and text[match.end():].lstrip().startswith(("(", "[", "{")))
        if len(match[0]) >= 2 and not operator and match[0] not in {"nm", "mm", "cm", "ns", "ms", "sin", "cos", "exp", "ln"}:
            blocked.update(i for i, (a, b) in enumerate(offsets) if a < match.end() and b > match.start())
    # Sentence punctuation and single-letter prose labels are boundaries, not
    # evidence of a mathematical attachment (e.g. a citation followed by d,).
    for match in re.finditer(r"[.,;:!?]|(?<![A-Za-z])[A-Za-z](?=\s*,)", text):
        blocked.update(i for i, (a, b) in enumerate(offsets) if a < match.end() and b > match.start())
    # A unit exponent next to a grouped prose value must not acquire just
    # the last three digits across the punctuation boundary above.
    for match in re.finditer(r'(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)', text):
        blocked.update(i for i, (a, b) in enumerate(offsets) if a < match.end() and b > match.start())
    for i, part in enumerate(parts):
        f = part.pdf_formula
        if not f:
            continue
        # A raised reference after an author citation is not the integer
        # part of the measurement which starts the following clause.
        raised_digits = ((f.y_offset or 0) > 0 and not f.pdf_curve and not f.pdf_form
                         and all(c.pdf_style and (c.pdf_style.font_size or size) < size*.85
                                 for c in f.pdf_character)
                         and re.fullmatch(r'\d+(?:[,–-]\d+)*', ''.join(
                             c.char_unicode or '' for c in f.pdf_character).strip()))
        author_citation = raised_digits and re.search(r'\bet\s+al\.\s*[,;:]?\s*$', text[max(0, offsets[i][0]-24):offsets[i][0]], re.I)
        if getattr(f, "_lf_caption_marker", False) or author_citation:
            blocked.add(i)
        else:
            # Runtime markers do not survive every cache format, and the late
            # rule-attachment pass has no font map. Conservatively leave a
            # baseline single letter followed by prose punctuation independent.
            chars = [c for c in f.pdf_character if (c.char_unicode or "").strip()]
            following = "".join(c.char_unicode or "" for p in parts[i+1:i+4] for c in composition_chars(p)).lstrip()
            if (len(chars) == 1 and re.fullmatch(r"[a-z]", chars[0].char_unicode or "")
                    and chars[0].pdf_style and (chars[0].pdf_style.font_size or 0) >= size*.85
                    and following.startswith((",", ".", ";", ":"))):
                blocked.add(i)
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
    def combine(group):
        if len(group) == 1 and group[0].pdf_formula:
            return group[0]
        boxes = [bounds(p) for p in group]
        original = [p.pdf_formula for p in group if p.pdf_formula]
        anchor = original[0]
        box = Box(min(b.x for b in boxes), min(b.y for b in boxes), max(b.x2 for b in boxes), max(b.y2 for b in boxes))
        chars = [c for p in group for c in composition_chars(p)]
        # Preserve original PDF paint order when supplied by the parser. Never
        # sort glyphs by x/y: nested scripts and overprints are not linear text.
        ordered = iter(sorted((c for c in chars if c.render_order is not None),
                              key=lambda c: (c.render_order, c.sub_render_order or 0)))
        # Inferred spaces have no paint order. Leave their slots intact while
        # restoring the relative paint order of every original glyph.
        chars = [next(ordered) if c.render_order is not None else c for c in chars]
        return PdfParagraphComposition(pdf_formula=PdfFormula(
            box=box, pdf_character=chars,
            pdf_curve=[c for f in original for c in f.pdf_curve],
            pdf_form=[f for formula in original for f in formula.pdf_form],
            x_offset=0, y_offset=box.y-anchor.box.y+(anchor.y_offset or 0), x_advance=0))

    def compatible(indices):
        group = [parts[i] for i in indices]
        chars = [c for p in group for c in composition_chars(p)]
        if any(c.vertical for c in chars) or len({c.xobj_id for c in chars if c.xobj_id is not None}) > 1:
            return False
        graphic = any(p.pdf_formula and (p.pdf_formula.pdf_curve or p.pdf_formula.pdf_form) for p in group)
        bases = [c.box.y for c in chars if c.box and c.pdf_style and
                 (c.char_unicode or "").strip() and (c.pdf_style.font_size or 0) >= size*.85]
        if bases and max(bases)-min(bases) > size*.4:
            # Existing stacked/ruled formulas retain the old formula-to-formula
            # path, but must not acquire ordinary text from the next line.
            return graphic and all(p.pdf_formula for p in group)
        return True

    result, run = [], []
    def flush_run():
        if not run:
            return
        # Connected spatial components inside a prose-delimited run. Membership
        # is independent of parser composition order, including late scripts.
        components = [{i} for i in run]
        for a, i in enumerate(run):
            for j in run[a+1:]:
                if not adjacent(bounds(parts[i]), bounds(parts[j])):
                    continue
                left = next(g for g in components if i in g)
                right = next(g for g in components if j in g)
                if left is right or not compatible(left | right):
                    continue
                left.update(right)
                components.remove(right)
        owners = {i:g for g in components for i in g}
        for i in run:
            group = owners[i]
            if not any(parts[j].pdf_formula for j in group):
                result.append(parts[i])
            elif i == min(group):
                result.append(combine([parts[j] for j in sorted(group)]))
    for i, part in enumerate(parts):
        if i in blocked or bounds(part) is None:
            flush_run()
            run = []
            result.append(part)
        else:
            run.append(i)
    flush_run()
    return result


def _enzyme_prefixes(translate_input):
    anchors = {}
    for p in translate_input.placeholders:
        formula = getattr(p, 'formula', None)
        if formula is None:
            continue
        glyphs = ''.join(c.char_unicode or '' for c in formula.pdf_character).strip()
        if re.fullmatch(r'(?:[αβ]-)?[DL]-|[αβ]-', glyphs):
            suffix = translate_input.unicode.partition(p.placeholder)[2]
            if re.match(r'\s*[a-z]{3,}ase\b', suffix, re.I):
                anchors[p.placeholder] = glyphs
    return anchors


def _starts_identifier(text):
    return re.match(r'\s*[A-Za-z]{1,10}\d+[A-Za-z0-9]*\b', re.sub(r'<[^>]+>', '', text), re.ASCII) is not None


def _split_crystal_head(source):
    return re.match(r'^\s*(?:the\s+)?crystal structures? of\s*(?:the\s*)?$', re.sub(r'<[^>]+>', '', source), re.I)


def _has_crystal_head(text):
    clause=re.split(r'[。！？；;]', re.sub(r'<[^>]+>', '', text), maxsplit=1)[0]
    return bool(re.search(r'晶[体體](?:学|學)?(?:三[维維])?[结結][构構]|晶[体體]中.{0,12}[结結][构構]',clause))


def attach_measurement_nucleus(ti, text):
    """Move an intact isotope slot with NMR when Chinese inverts 'spectrum of X'."""
    if not isinstance(text,str) or 'NMR' not in text or '的' not in text:
        return text
    for ph in ti.placeholders:
        formula=getattr(ph,'formula',None)
        if not formula or not re.fullmatch(r'\s*\d{1,3}[A-Z][a-z]?\s*',''.join(c.char_unicode or '' for c in formula.pdf_character)):
            continue
        token=re.escape(ph.placeholder)
        source=re.search(token+r'\s*NMR\s+(?:spectrum|spectra)\s+of\s+([A-Za-z][\w-]*)(?=[,.;:)\s]|$)',ti.unicode)
        if source:
            text=re.sub(token+r'\s*('+re.escape(source[1])+r'\s*的\s*)(?=NMR\b|NMR谱)',
                        lambda m:m[1]+ph.placeholder,text)
    return text


_DECIMAL_PREFIX = re.compile(r'(?<![A-Za-z0-9_.])[-+\u2212]?\d+\.$')


def _formula_boundaries(translate_input, placeholders):
    """Classify boundaries from the source glyphs, not from translated punctuation."""
    kinds = {}
    for p in placeholders:
        formula = p.formula
        glyphs = ''.join(c.char_unicode or '' for c in formula.pdf_character) if formula else ''
        if not glyphs.strip():
            # Missing glyph data cannot establish that a former decimal is safe to move.
            kinds[p.placeholder] = 'unknown'
            continue
        if (formula.y_offset or 0) > 0:
            prefix = re.sub(r'<[^>]+>', '', translate_input.unicode.partition(p.placeholder)[0])
            label = glyphs.strip()
            if re.fullmatch(r'\d+(?:[,\u2013-]\d+)*', label) and re.search(r'\bet\s+al\.\s*[,;:]?\s*$', prefix, re.I):
                kinds[p.placeholder] = 'author-citation'
                continue
            single_after_sentence = re.fullmatch(r'\d+', label) and re.search(
                r'(?:[A-Za-z]{2,}\d*|\bv\d+(?:\.\d+)+)\.$', prefix, re.I)
            if re.fullmatch(r'\d+(?:,\d+)+', label) or single_after_sentence:
                kinds[p.placeholder] = 'citation'
                continue
        # A fractional continuation must start with a digit immediately after
        # the point. Space or a letter starts a new token, not a decimal tail.
        if re.match(r'\d', glyphs):
            kinds[p.placeholder] = 'decimal'
    return kinds


def check_formula_tokens(translate_input, output):
    return not _formula_boundary_problem(translate_input, output)


def _formula_boundary_problem(translate_input, output):
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    placeholders = [p for p in translate_input.placeholders if isinstance(p, FormulaPlaceholder)]
    tokens = [p.placeholder for p in placeholders]
    if not tokens:
        return ''
    if any(_starts_identifier(output.partition(t)[2]) for t in _enzyme_prefixes(translate_input)):
        return '构型前缀后误接了编号而非酶名'
    if (re.search(r'[\u3400-\u9fff]',output) and _split_crystal_head(translate_input.unicode.partition(tokens[0])[0])
            and not _has_crystal_head(output)):
        return '跨公式重组后遗漏了晶体结构主语'
    pattern = re.compile("|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)))
    unknown = set(re.findall(r"\{[vV]\d+\}", output)) - set(re.findall(r"\{[vV]\d+\}", translate_input.unicode))
    if unknown or pattern.findall(output) != pattern.findall(translate_input.unicode):
        return '重组后的公式标记丢失、增加或顺序变化'
    kinds = _formula_boundaries(translate_input, placeholders)
    plain = re.sub(r'<[^>]+>', '', output)
    for token, kind in kinds.items():
        if kind == 'author-citation':
            before, _, after = plain.partition(token)
            if re.search(r'[将把取加用]\s*$', before) and re.match(r'\s*\d', after):
                return '重组后引文上标错位'
    # Compare only the source-derived number and citation links. Strip style
    # tags here so that a closing tag cannot hide an adjacent decimal point.
    def boundary_values(text):
        text = re.sub(r'<[^>]+>', '', text)
        edges = []
        previous_end = 0
        for m in pattern.finditer(text):
            kind = kinds.get(m[0])
            if kind in {'citation', 'author-citation'}:
                # Citation placement is sentence-level. Chinese may move the
                # identifier within this fragment, but not across its citation.
                edges.append((m[0],prose_numbers(text[previous_end:m.start()])))
            elif kind in {'decimal', 'unknown'}:
                prefix = _DECIMAL_PREFIX.search(text, previous_end, m.start())
                edges.append((m[0], prefix[0] if prefix else None))
            previous_end = m.end()
        return edges
    for (token, expected), (_, actual) in zip(boundary_values(translate_input.unicode), boundary_values(output), strict=True):
        if expected != actual:
            return ('重组后引文上标前的数值边界改变' if kinds[token] in {'citation', 'author-citation'}
                    else '重组后跨公式的小数边界改变')
    return ''


def prose_numbers(text):
    """Count every prose value, including both ends of a hyphenated range."""
    from collections import Counter
    from decimal import Decimal
    from linguafloat.document_context import normalize_numeric_ranges, separate_dotted_cross_references
    text = re.sub(r'\{[vV]\d+\}', ' ', re.sub(r'<[^>]+>', '', text))
    # Both adjoining bonds are required: a signed measurement or a numeric
    # range is not a lexical locant such as word-3-acid.
    text = re.sub(r'(?<=[A-Za-z㐀-鿿])-(?=\d+\s*[-‐‑]\s*[A-Za-z㐀-鿿])', ' ', text)
    text = normalize_numeric_ranges(separate_dotted_cross_references(text))
    pattern = r'([-+−]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+−]?\d+)?)(\s*[%％])?'
    return Counter((Decimal(value.replace('−', '-')), bool(percent))
                   for value, percent in re.findall(pattern, text))


def missing_body_translation(source, output, target):
    """Reject a complete unconverted English sentence, not names or scientific labels."""
    if target not in ('zh-Hans','zh-Hant') or not isinstance(output,str):
        return False
    def visible(value):
        return re.sub(r'<[^>]+>|\{[vV]\d+\}', ' ', value)
    grammar = {'is','are','was','were','has','have','had','can','could','may','showed','suggest','found','used'}
    def prose(s):
        words=re.findall(r'[a-z]+',s.lower())
        return (len(words)>=8 and bool(grammar.intersection(words)) and
                bool({'the','a','an','of','in','that','for','with','this','we'}.intersection(words)))
    source,output=visible(source),visible(output)
    if not re.search(r'[\u3400-\u9fff]',output):
        return prose(source)
    # Chinese in a neighbouring sentence or on the other side of an anchor
    # is not evidence that this complete source sentence was translated.
    normalize=lambda s: ' '.join(s.casefold().split())
    result=normalize(output)
    return any(prose(s) and normalize(s) in result for s in re.split(r'(?<=[.!?])\s+',source))


def _formula_quantities(value):
    from linguafloat.document_context import scientific_quantities
    return scientific_quantities(re.sub(r'(?<![A-Za-z0-9_])pH(?=[+−-]?\d)', 'pH ', value))


def _numeric_difference(source, text, problem):
    compare = {'片段正文数值发生变化': prose_numbers, '片段小数、百分比或数量级发生变化': _formula_quantities}.get(problem)
    if not compare:
        return ''
    before, after = compare(source), compare(text)
    def display(counts):
        def number(v):
            # Keep large exponents compact. Do not round via Decimal.normalize().
            s=str(v)
            return s.rstrip('0').rstrip('.') if '.' in s and 'E' not in s else s
        return ', '.join(f'{number(v)[:40]}{"%" if pct else ""}×{n}' for (v,pct),n in list(counts.items())[:6]) or '无'
    return f'原文有、候选缺：{display(before-after)}；候选多出：{display(after-before)}'


_REPAIR_NUMBER = re.compile(r'\[\[Q(\d+)\]\]')


def _lock_repair_numbers(source):
    values = []

    def replace(match):
        if match[0].startswith('<'):
            return match[0]
        index = len(values)
        values.append(match[0])
        return f'[[Q{index}]]'

    return re.sub(r'<[^>]+>|(?:(?<![A-Za-z0-9.])[-+\u2212])?'
                  r'(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+\u2212]?\d+)?(?:\s*[%\uff05])?',
                  replace, source), values


def _restore_repair_numbers(text, values):
    if not isinstance(text, str) or _REPAIR_NUMBER.findall(text) != [str(i) for i in range(len(values))]:
        return None
    result = _REPAIR_NUMBER.sub(lambda match: values[int(match[1])], text)
    return None if '[[Q' in result else result


def _formula_fragment_problem(source, text, tokens):
    if not isinstance(text, str):
        return "片段译文不是文字"
    if not text.strip():
        return "片段返回空译文"
    if any(token in text for token in tokens) or re.search(r"\{[vV]\d+\}", text):
        return "片段夹带公式标记"
    from linguafloat.document_richtext import style_structure_matches
    if not style_structure_matches(source, text, fragments=True):
        return "片段格式标签丢失、增加或嵌套关系变化"
    if _formula_quantities(source) != _formula_quantities(text):
        return "片段小数、百分比或数量级发生变化"
    if prose_numbers(source) != prose_numbers(text):
        return "片段正文数值发生变化"
    if missing_plural_identifiers(source,text):
        return "片段中的科学缩写缺失"
    if lost_elevated_comparison(source,text):
        return "比较分句中显著升高的限定或方向缺失"
    # A following immutable slot may carry the closing parenthesis. Text
    # moved after the open acronym would then be read *inside* that name.
    suffix=re.search(r'[（(]\s*([A-Z]{2,}(?:[-/][A-Z0-9]+)*)\s*$',re.sub(r'<[^>]+>','',source))
    if suffix and not re.search(r'[（(]\s*'+re.escape(suffix[1])+r'\s*$',re.sub(r'<[^>]+>','',text)):
        return "片段末尾括号内的科学缩写错位"
    return ""


def lost_elevated_comparison(source, output):
    # The first statistical result must not borrow the qualifier of a later comparison.
    plain=lambda s: re.sub(r'<[^>]+>', '', s)
    first=re.split(r'\b(?:and (?:demonstrated|showed)|although)\b|[.;](?!\d)',plain(source),maxsplit=1,flags=re.I)[0]
    if not re.match(r'\s*Compared (?:with|to)\b',first,re.I) or not re.search(r'\bsignificantly (?:elevated|increased|higher)\b',first,re.I) or not re.search(r'[\u3400-\u9fff]',output):return False
    clause=re.split(r'[。；]|[，,]\s*(?:且|并且|并|而且)(?=[^。；]{0,30}(?:相比|高于|比较|相较))',plain(output),maxsplit=1)[0]
    return not (re.search(r'显著|明显|统计学',clause) and re.search(r'高|升|增',clause))


def plural_identifiers(source):
    return sorted(set(re.findall(r'(?<![A-Za-z0-9])([A-Z]{2,})s(?![A-Za-z0-9])',source)))


def missing_plural_identifiers(source, output):
    # Plural scientific abbreviations can also be rendered in singular form
    # in Chinese. Do not confuse plural -s with lowercase letters in a name.
    terms = plural_identifiers(source)
    return any(not re.search(r'(?<![A-Za-z0-9])'+term+r's?(?![A-Za-z0-9])',output) for term in terms)


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


def _translate_label_sentences(ti, translator, budget):
    """Translate prose around short inline labels without cutting through its clauses."""
    if getattr(getattr(translator,'job',None),'target',None) not in ('zh-Hans','zh-Hant') or '<' in ti.unicode:
        return None
    tags={}; raw=ti.unicode
    for i,p in enumerate(ti.placeholders,1):
        formula=getattr(p,'formula',None)
        if not formula or raw.count(p.placeholder)!=1: return None
        label=''.join(c.char_unicode or '' for c in formula.pdf_character).strip()
        if not re.fullmatch(r'[A-Za-z0-9α-ωΑ-Ω+−,./%-]{1,16}',label):return None
        tags[p.placeholder]=f'<k{i}>{label}</k{i}>'
    if len(tags)<3 or not re.search(r'\{[vV]\d+\}\s*[A-Za-z]{1,8}\s*\{[vV]\d+\}',raw):return None
    sentences=re.split(r'(?<=[.!?])\s+(?=(?:[A-Z][a-z]+\d+|The|This|These|Those|It|We|They|Its|In|A|An|However|Thus|Removing|Significantly)\b)',raw)
    slots=re.compile(r'\{[vV]\d+\}')
    def mark(s):
        return re.sub(r'\{[vV]\d+\}',lambda m:tags.get(m[0],m[0]),s)
    packet=[dict(id=i,input=mark(s),slots=[tags[t] for t in slots.findall(s)]) for i,s in enumerate(sentences)]
    msgs=translator.messages('');msgs[0]['content']+=(
        ' Translate WHOLE sentences into faithful Chinese; all predicates, objects, qualifications and identifiers must survive. '
        'Return a JSON array of {id,output}, where output is an ARRAY of Chinese strings. '
        'The page already supplies every scientific label in the listed slots, in that fixed order. '
        'Write the prose before, between and after them. With n slots return exactly n+1 strings (empty is OK); '
        'with no slots return one complete translation. Never output tags or reproduce a slot in your strings. '
        'Compose the whole sentence together, not each gap independently. Rephrase using a following clause when '
        'needed to keep the fixed labels attached to the correct scientific entities. Never shorten to fit.')
    msgs[1]['content']=json.dumps(dict(sentences=packet),ensure_ascii=False)
    # A miss on this optional route is not a failed paragraph. Only enter
    # the guarded request path for a cache hit or an online translation.
    if translator.job.relayout_only and translator.store.get(translator.cache_key(msgs)) is None:
        return None
    reply=translator.request(msgs,budget=budget,part='完整句内科学标识补译')
    try:
        values=_formula_reply(reply,packet);out=[]
        for i,s in enumerate(sentences):
            gaps=values[i]; anchors=slots.findall(s)
            if not isinstance(gaps,list) or len(gaps)!=len(anchors)+1 or not all(isinstance(g,str) for g in gaps):raise ValueError
            prose=' '.join(gaps)
            if not re.search(r'[\u3400-\u9fff]',prose) or slots.search(prose) or re.search(r'</?[A-Za-z][^>]*>',prose):raise ValueError
            v=gaps[0]+''.join(t+g for t,g in zip(anchors,gaps[1:]))
            strip=lambda q:slots.sub(' ',q)
            if _formula_fragment_problem(strip(s),prose,[]):raise ValueError
            ids=r'(?<![a-zA-Z0-9])[a-zA-Z]{2,}\d+[a-zA-Z0-9]*'
            if sorted(re.findall(ids,strip(v)))!=sorted(re.findall(ids,strip(s))):raise ValueError
            out.append(v.strip())
        result=''.join(out)
        if not check_formula_tokens(ti,result):raise ValueError
        return result
    except ValueError:
        if getattr(translator,'store',None) is not None:translator.store.discard(translator.cache_key(msgs))
        return None


def translate_formula_segments(translate_input, translator):
    """Reconstruct immutable formulas around translated, explicitly keyed prose."""
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    from linguafloat.document_core import DocumentError
    placeholders = [p for p in translate_input.placeholders if isinstance(p, FormulaPlaceholder)]
    tokens = [p.placeholder for p in placeholders]
    pattern = "(" + "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)) + ")" if tokens else None
    pieces = re.split(pattern, translate_input.unicode) if pattern else [translate_input.unicode]
    # Some native formula runs include both the fraction and its unit. Keep
    # the integer and decimal point out of the model's movable prose.
    fixed_tails={}
    boundaries = _formula_boundaries(translate_input, placeholders)
    for i in range(1,len(pieces),2):
        tail = _DECIMAL_PREFIX.search(pieces[i - 1])
        if boundaries.get(pieces[i]) == 'decimal' and tail:
            fixed_tails[i-1]=tail[0]
            pieces[i-1]=pieces[i-1][:tail.start()]
    fragments = [dict(id=i, input=text) for i, text in enumerate(pieces)
                 if i % 2 == 0 and re.search(r"[A-Za-z]{2,}|[\u3400-\u9fff]", re.sub(r"<[^>]+>", "", text))]
    if not fragments:
        return translate_input.unicode
    budget = [max(0, min(3, int(getattr(translator, "retries", 0))))]
    complete = _translate_label_sentences(translate_input, translator, budget)
    if complete is not None:
        return complete
    messages = translator.messages("")
    messages[0]["content"] += (
        " Formula-segment repair v2. Return a JSON array of {id, output} objects, one for every fragment."
        " Translate each fragment independently using the context only to resolve meaning."
        " Never move words between fragments. Do not output any formula placeholders."
        " Preserve all style tags exactly, including their nesting and scope; never merge or remove spans."
        " Preserve numbers exactly, including a trailing decimal point next to a protected formula."
        " Fragments may begin or end with incomplete sentences."
        " Do not translate or output context. Do not add explanations or Markdown fences.")
    messages[1]["content"] = json.dumps(dict(fragments=fragments, context=translate_input.unicode), ensure_ascii=False)
    constraints=[]
    for item in fragments:
        terms=plural_identifiers(item['input'])
        if terms: constraints.append(f"Fragment {item['id']}: retain the literal abbreviations {', '.join(terms)} even when expanding their names.")
        counts=prose_numbers(item['input'])
        if any(percent for _,percent in counts) or sum(counts.values())>=4:
            expected=', '.join(f'{n}{"%" if pct else ""} ({count}x)' for (n,pct),count in counts.items())
            constraints.append(f"Fragment {item['id']}: check numeric values and occurrence counts: {expected}. Keep digits, signs and percentages within the same statement; do not translate numerals into words or copy this checklist.")
        if re.search(r'\bsignificantly (?:elevated|increased|higher)\b',item['input'],re.I):
            constraints.append(f"Fragment {item['id']}: explicitly translate the direction and significance for each comparator; do not move either to a later clause.")
    if constraints: messages[0]['content']+=' '+' '.join(constraints)
    # Both allowances are per paragraph, never replenished for each fragment.
    content_left = max(0, min(3, int(getattr(translator, 'content_repairs', 2))))
    store = getattr(translator, "store", None)
    control = getattr(translator, "control", None)
    recovered = False

    def checkpoint():
        if control is not None:
            control.checkpoint()

    def discard(request):
        if store is not None:
            store.discard(translator.cache_key(request))

    evidence = ''
    def stop(reason):
        location = getattr(translator, "location", "文档")
        error = DocumentError(f"[公式旁文字补译失败] {location}：{reason}。未改动原公式，未发布不完整译文。"
                            "已有合格译文缓存保留。")
        error.fragment_evidence = evidence
        raise error

    def reserve(reason):
        nonlocal recovered, content_left
        checkpoint()
        if getattr(getattr(translator, "job", None), "relayout_only", False):
            stop(reason + "；仅重新排版不会发送补译请求")
        if content_left <= 0:
            stop(reason + "；内容纠错额度已用尽")
        content_left -= 1
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

    anchors = _enzyme_prefixes(translate_input)
    split_head = (len(pieces)>2 and _split_crystal_head(pieces[0]) and
                  getattr(getattr(translator,'job',None),'target',None) in ('zh-Hans','zh-Hant'))
    def problem_for(item, value):
        reason = _formula_fragment_problem(item['input'], value, tokens)
        if not reason and missing_body_translation(item['input'],value,getattr(getattr(translator,'job',None),'target',None)):
            reason='正文片段仍为原文'
        index = item['id']
        if not reason and index and pieces[index-1] in anchors and _starts_identifier(value):
            return '构型前缀后误接了编号而非酶名'
        if (not reason and split_head and index==2 and isinstance(values.get(0),str)
                and not _has_crystal_head(values[0]+value)):
            return '跨公式重组后遗漏了晶体结构主语'
        return reason
    problems = {item["id"]: problem_for(item, values[item["id"]])
                for item in fragments}
    if any(problems.values()):
        discard(messages)
    rejected = [item for item in fragments if problems[item['id']]]
    if len(rejected)>1 and content_left and not getattr(getattr(translator,'job',None),'relayout_only',False):
        packet=[]
        for item in rejected:
            i=item['id']; why=problems[i]
            rule=_numeric_difference(item['input'],values[i],why) or why
            if i and pieces[i-1] in anchors:
                rule+='；前置 '+anchors[pieces[i-1]]+' 修饰酶名，勿接编号。'
            if split_head and i==2 and not _has_crystal_head(values[0] if isinstance(values.get(0),str) else ''):
                rule+='；该实体的晶体结构是主语，应在实体名后译出。'
            packet.append(dict(item,check=rule))
        group=translator.messages('')
        group[0]['content']+=(' Correct ONLY the listed fragments. Return one JSON array of {id,output}; '
            'do not alter IDs. Keep scientific values, grammatical relations, style and all descriptive clauses. '
            'Do not copy placeholders. Context is read-only. The check field is a correction instruction, not text to translate.')
        group[1]['content']=json.dumps(dict(fragments=packet,context=translate_input.unicode[:6000]),ensure_ascii=False)
        reserve('不合格片段定向纠错')
        response=translator.request(group,budget=budget,part='公式间文字补译 · 不合格片段组')
        checkpoint()
        try:
            replacements=_formula_reply(response,rejected)
        except ValueError:
            discard(group)  # Never guess how malformed IDs map back to the paragraph.
        else:
            values.update(replacements)
            problems.update((item['id'],problem_for(item,values[item['id']])) for item in rejected)
            if any(problems.values()): discard(group)
    for item in fragments:
        identity, source = item["id"], item["input"]
        problem = problems[identity]
        if not problem:
            continue
        # Only the rejected prose is requested again; formula slots and the
        # other validated fragments never leave their original positions.
        single = translator.messages("")
        single[0]["content"] += (
            " Formula-segment single repair v3. The user JSON contains fragment and reference-only context."
            " Translate only fragment as plain text, not JSON. Keep its grammatical role and every style tag."
            " Context is not a translation target. Never copy any formula placeholders from context."
            " Do not move, add or omit words to complete the surrounding sentence."
            " Include the measured entity, its abbreviation and quantity even in a passive construction."
            " When the fragment ends with an unclosed parenthesized acronym, leave it at the very end;"
            " put the measured entity before the method. The closing parenthesis belongs to the next slot."
            " Return nonempty translated text without explanations or Markdown fences.")
        single[1]["content"] = json.dumps(dict(fragment=source, context=translate_input.unicode[:6000]), ensure_ascii=False)
        if identity and pieces[identity-1] in anchors:
            single[0]['content'] += (' The preceding immutable prefix '+anchors[pieces[identity-1]]+
                ' modifies the enzyme name at the beginning of this fragment. Begin with that enzyme name, not its identifier. Do not repeat the prefix.')
        if split_head and identity==2 and not _has_crystal_head(values[0]):
            single[0]['content'] += (' Chinese word order places the head noun from the preceding fragment after its modifier. '
                'Keep the crystal structure of the named entity as the subject, not the entity alone. Include 晶体结构 (or 晶體結構) in its first sentence.')
        if constraints: single[0]['content']+=' '+' '.join(c for c in constraints if c.startswith(f'Fragment {identity}:'))
        text = values[identity]
        context_repair_tried = False
        while problem:
            diff = _numeric_difference(source, text, problem)
            if diff or not evidence:
                evidence = f'片段 {identity}'+('；'+diff if diff else '')
            reserve(f"片段 {identity}：{problem}")
            request = single
            locked = None
            if diff and context_repair_tried and '[[Q' not in source:
                # Do not repeat the same prompt or expose rejected numbers to the model.
                masked, locked = _lock_repair_numbers(source)
                request = translator.messages('')
                request[0]['content'] += (
                    ' Translate only the isolated_fragment field as plain text, even if it is part of a sentence. '
                    ' Copy each [[Qn]] marker exactly once, in order; it stands for an immutable original number. '
                    ' Never substitute a numeral for a marker and never introduce new numbers. '
                    ' Preserve units, tags, scientific names, and every clause exactly in meaning. '
                    ' The surrounding text is already present. Do not add a list number, heading, or explanation.')
                request[1]['content'] = json.dumps(dict(isolated_fragment=masked), ensure_ascii=False)
            else:
                if diff:
                    single[1]['content'] = json.dumps(dict(fragment=source, context=translate_input.unicode[:6000], numeric_check=diff),ensure_ascii=False)
                context_repair_tried = True
            text = translator.request(request, budget=budget, part=f"公式间文字补译 · 片段 {identity}")
            checkpoint()
            if locked is not None:
                text = _restore_repair_numbers(text, locked)
            problem = ('片段数值标记缺失、重复或顺序变化' if locked is not None and text is None
                       else problem_for(item, text))
            if not problem and text.lstrip().startswith(("```", '[{', '{"')):
                problem = "单片段补译未返回纯文本"
            if problem:
                discard(request)
            else:
                values[identity] = text
                evidence = ''

    for identity, text in values.items():
        pieces[identity] = text
    result = ''.join(piece+fixed_tails.get(i,'') for i,piece in enumerate(pieces))
    if missing_body_translation(translate_input.unicode, result, getattr(getattr(translator,'job',None),'target',None)):
        discard(messages)
        stop("正文整段未得到目标语言译文")
    if problem := _formula_boundary_problem(translate_input, result):
        discard(messages)
        stop(problem)
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
                fonts.update({(obj.xobj_id, f.font_id): f.name or "" for f in obj.pdf_font})
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
