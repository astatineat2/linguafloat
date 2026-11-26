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


def source_style(parts, fallback):
    from linguafloat.document_scientific import composition_chars
    styles = [c.pdf_style for p in parts if not p.pdf_formula for c in composition_chars(p)
              if c.pdf_style and c.pdf_style.font_size and (c.char_unicode or '').strip()]
    if not styles:
        return copy.copy(fallback)
    counts = Counter((s.font_id, s.font_size) for s in styles)
    key = counts.most_common(1)[0][0]
    return copy.copy(next(s for s in styles if (s.font_id, s.font_size) == key))


def attach_detached_citations(page):
    """Rejoin a raised numeric native fragment to its unique preceding glyph.

    Only source coordinates decide ownership. No translated word matching or
    guessed citation placement is used. Subscripts, equal-size data and
    ambiguous owners remain untouched.
    """
    from babeldoc.format.pdf.document_il import PdfFormula, PdfParagraphComposition as Part
    from linguafloat.document_scientific import composition_chars
    changes = []
    for orphan in list(page.pdf_paragraph):
        if not re.fullmatch(r'\d{1,3}(?:\s*[,–−-]\s*\d{1,3})*', (orphan.unicode or '').strip()):
            continue
        scripts = [c for part in orphan.pdf_paragraph_composition for c in composition_chars(part)]
        if not scripts or any(not c.box or not c.pdf_style for c in scripts):
            continue
        if any(p.pdf_formula and (p.pdf_formula.pdf_curve or p.pdf_formula.pdf_form)
               for p in orphan.pdf_paragraph_composition):
            continue
        sb = union([c.box for c in scripts])
        script_size = max(c.pdf_style.font_size or 0 for c in scripts)
        matches = []
        for owner in page.pdf_paragraph:
            if owner is orphan or sum(c.isalpha() for c in owner.unicode or '') < 6:
                continue
            candidates = []
            for index, part in enumerate(owner.pdf_paragraph_composition):
                if part.pdf_formula:
                    continue
                for offset, c in enumerate(composition_chars(part)):
                    if not c.box or not c.pdf_style or not (c.char_unicode or '').strip():
                        continue
                    size = c.pdf_style.font_size or 0
                    gap, lift = sb.x-c.box.x2, sb.y-c.box.y
                    if (size and script_size < size*.85 and -.1 <= gap <= max(1.5,size*.3)
                            and size*.15 <= lift <= size*.85
                            and all(s.xobj_id == c.xobj_id for s in scripts)):
                        candidates.append((gap,index,offset,c))
            if candidates:
                matches.append((owner,min(candidates,key=lambda x:abs(x[0]))))
        if len(matches) != 1:
            continue
        owner,(_,index,offset,base) = matches[0]
        part=owner.pdf_paragraph_composition[index]
        chars=composition_chars(part)
        replacement=[]
        for subset in (chars[:offset+1],scripts,chars[offset+1:]):
            if not subset:
                continue
            if subset is scripts:
                replacement.append(Part(pdf_formula=PdfFormula(box=sb,pdf_character=scripts,
                    x_offset=0,y_offset=sb.y-base.box.y,x_advance=0,is_corner_mark=True)))
            elif part.pdf_character:
                replacement.extend(Part(pdf_character=c) for c in subset)
            else:
                kind='pdf_line' if part.pdf_line else 'pdf_same_style_characters'
                container=copy.copy(getattr(part,kind));container.pdf_character=list(subset)
                container.box=union([c.box for c in subset])
                replacement.append(Part(**{kind:container}))
        owner.pdf_paragraph_composition[index:index+1]=replacement
        from babeldoc.format.pdf.document_il.utils.layout_helper import get_char_unicode_string
        owner.unicode=get_char_unicode_string([c for p in owner.pdf_paragraph_composition for c in composition_chars(p)])
        page.pdf_paragraph.remove(orphan)
        changes.append(dict(kind='detached_citation',text=orphan.unicode,owner=owner.debug_id))
    return changes


def split_source_columns(page, source_lines):
    """Split a merged source paragraph only when native lines prove a gutter.

    This runs before translation, preserving the glyph objects and their IDs.
    Full-width lines and formulas crossing the proposed gutter veto the split.
    """
    from babeldoc.format.pdf.document_il import Box, PdfParagraphComposition
    from linguafloat.document_scientific import composition_chars
    changes = []
    rebuilt = []
    for paragraph in page.pdf_paragraph:
        b = paragraph.box
        if not b or b.x2-b.x < 200 or b.y2-b.y < 18:
            rebuilt.append(paragraph)
            continue
        middle = (b.x+b.x2)/2
        lines = [r for r in source_lines if r[0] >= b.x-3 and r[2] <= b.x2+3
                 and b.y-3 <= (r[1]+r[3])/2 <= b.y2+3 and r[2]-r[0] > (b.x2-b.x)*.18]
        left = [r for r in lines if r[2] < middle]
        right = [r for r in lines if r[0] > middle]
        if len(left) < 3 or len(right) < 3 or len(left)+len(right) != len(lines):
            rebuilt.append(paragraph)
            continue
        low, high = max(r[2] for r in left), min(r[0] for r in right)
        overlap = min(max(r[3] for r in left), max(r[3] for r in right))-max(min(r[1] for r in left), min(r[1] for r in right))
        if high-low < 5 or overlap < 18:
            rebuilt.append(paragraph)
            continue
        gutter = (low+high)/2
        groups = [[], []]
        safe = True
        for part in paragraph.pdf_paragraph_composition:
            chars = composition_chars(part)
            if not chars or any(not c.box for c in chars):
                safe = False
                break
            sides = [int((c.box.x+c.box.x2)/2 > gutter) for c in chars]
            formula_box = union([part.pdf_formula.box, *(box_of(c) for c in chars)]) if part.pdf_formula else None
            # Formula boxes can include trailing advances through the gutter.
            # Keep the expression atomic, but veto only if its glyphs span both
            # sides or its box reaches the opposite column's native text.
            if part.pdf_formula and (len(set(sides)) != 1 or
                                     (sides[0] == 0 and formula_box.x2 > high + .5) or
                                     (sides[0] == 1 and formula_box.x < low - .5)):
                safe = False
                break
            if len(set(sides)) == 1:
                groups[sides[0]].append(part)
            else:
                # A detected line can itself contain both columns. Split its
                # glyph list without recreating fonts, symbols or coordinates.
                for side in (0, 1):
                    subset = [c for c, s in zip(chars, sides) if s == side]
                    name = "pdf_line" if part.pdf_line else "pdf_same_style_characters"
                    container = copy.copy(getattr(part, name))
                    if container is None:
                        safe = False
                        break
                    container.pdf_character = subset
                    container.box = union([c.box for c in subset])
                    groups[side].append(PdfParagraphComposition(**{name: container}))
        if not safe or not all(groups):
            rebuilt.append(paragraph)
            continue
        for side, parts in enumerate(groups):
            clone = copy.copy(paragraph)
            clone.pdf_paragraph_composition = parts
            clone.pdf_style = source_style(parts, paragraph.pdf_style)
            column_lines = left if side == 0 else right
            clone.box = Box(min(r[0] for r in column_lines), b.y,
                            max(r[2] for r in column_lines), b.y2)
            # The engine's glyph bounds include formula advances outside the
            # visible source line. They must not widen the translation frame
            # back across the verified native gutter.
            clone.unicode = " ".join("".join(c.char_unicode or "" for c in composition_chars(p)) for p in parts)
            if clone.debug_id is not None:
                clone.debug_id = f"{clone.debug_id}-col{side+1}"
            rebuilt.append(clone)
        changes.append(dict(page=page.page_number+1, kind="source_columns", gutter=[low, high],
                            source_box=[b.x,b.y,b.x2,b.y2]))
    page.pdf_paragraph[:] = rebuilt
    return changes


def protected_pdf_box(area, height):
    if 'pdf_box' in area:
        return tuple(area['pdf_box'])
    x0,y0,x1,y1=area['box']
    return (x0,height-y1,x1,height-y0)


def source_graphic_area(page, area):
    """Map displayed detector coordinates back to the native PDF before applying IL masks."""
    import pymupdf as f
    matrix=page.transformation_matrix * page.rotation_matrix
    return dict(area,pdf_box=list(f.Rect(area['box']) * ~matrix))


def exclude_graphic_glyphs(page, areas):
    """Remove protected source glyphs before requests, not whole mixed blocks.

    Caption/body portions of a detector-merged paragraph remain independently
    translatable. Formula compositions stay atomic. Restoration uses the same
    figure rectangles, so excluded content is recovered from the original PDF.
    """
    if not areas:
        return
    from babeldoc.format.pdf.document_il import Box, PdfParagraphComposition
    from linguafloat.document_scientific import composition_chars
    height = page.cropbox.box.y2-page.cropbox.box.y
    boxes = [Box(*protected_pdf_box(r,height)) for r in areas]
    rebuilt = []
    for paragraph in page.pdf_paragraph:
        pieces, touched = [], False
        for part in paragraph.pdf_paragraph_composition:
            chars = composition_chars(part)
            selected = [c for c in chars if not any(owns(b, c) for b in boxes)]
            if len(selected) == len(chars):
                pieces.append(part)
                continue
            touched = True
            if not selected:
                continue
            if part.pdf_formula:
                # A boundary-cut expression is not safe to send as a fragment.
                # Keep it together outside the protected area; original figure
                # restoration still takes precedence over its interior pixels.
                pieces.append(part)
                continue
            name = "pdf_line" if part.pdf_line else "pdf_same_style_characters"
            container = copy.copy(getattr(part, name))
            if container is not None:
                container.pdf_character = selected
                container.box = union([c.box for c in selected])
                pieces.append(PdfParagraphComposition(**{name: container}))
        if not touched:
            rebuilt.append(paragraph)
        elif pieces:
            paragraph.pdf_paragraph_composition = pieces
            paragraph.pdf_style = source_style(pieces, paragraph.pdf_style)
            glyphs = [c for p in pieces for c in composition_chars(p)]
            paragraph.box = union([c.box for c in glyphs])
            paragraph.unicode = " ".join("".join(c.char_unicode or "" for c in composition_chars(p)) for p in pieces)
            rebuilt.append(paragraph)
    page.pdf_paragraph[:] = rebuilt


def split_caption_groups(page, words):
    """Locate caption rows with a source word crossing a detector boundary."""
    labels = {'figure_caption', 'table_caption'}
    marker = re.compile(r"^(?:Fig(?:ure)?\.?|Table|\u56fe|\u8868)\s*\d+", re.I)
    captions, fallback = {}, []
    for p in page.pdf_paragraph:
        if not p.box or not p.pdf_style or not p.pdf_style.font_size:
            continue
        if p.layout_label in labels:
            key = (p.layout_label, p.layout_id if p.layout_id is not None else id(p), p.xobj_id)
            captions.setdefault(key, []).append(p)
        elif p.layout_label == 'fallback_line':
            fallback.append(p)

    def boundary(left, right):
        a, b = left.box, right.box
        size = left.pdf_style.font_size
        if (abs((a.y+a.y2-b.y-b.y2)/2) > size*.28 or
                not -.35 <= b.x-a.x2 <= size*.8):
            return False
        return any(re.fullmatch(r'[A-Za-z][A-Za-z0-9-]+', word) and
                   w[0]+.4 < min(a.x2,b.x) <= max(a.x2,b.x) < w[2]-.4 and
                   w[1] <= (b.y+b.y2)/2 <= w[3] for word,w in words)

    groups = []
    for anchors in captions.values():
        first = min(anchors, key=lambda p:(-p.box.y2,p.box.x))
        size, linked, prefixes = first.pdf_style.font_size, [], []
        for p in fallback:
            if p.xobj_id != first.xobj_id or abs(p.pdf_style.font_size-size) > .35:
                continue
            is_prefix = (abs((p.box.y+p.box.y2-first.box.y-first.box.y2)/2) <= size*.28
                         and -.35 <= first.box.x-p.box.x2 <= size*.8
                         and marker.match(p.unicode or ''))
            if is_prefix:
                prefixes.append(p)
            if any(boundary(p,a) or boundary(a,p) for a in anchors):
                linked.append(p)
        if linked and len(prefixes) <= 1 and (prefixes or marker.match(first.unicode or '')):
            group = [*anchors, *linked, *(p for p in prefixes if p not in linked)]
            left = min(p.box.x for p in group)
            for p in fallback:
                if (p in group or p.xobj_id != first.xobj_id or abs(p.pdf_style.font_size-size) > .35
                        or (p.unicode or '').strip().lower() not in {'or','and'}):
                    continue
                b = p.box
                if not any(abs(a.box.y+a.box.y2-b.y-b.y2) <= size*.56 and
                           -.35 <= b.x-a.box.x2 <= size*.8 for a in group):
                    continue
                def backed(run):
                    box = run.box
                    row = sorted((w[0],word) for word,w in words if box.x-.5 <= (w[0]+w[2])/2 <= box.x2+.5
                                 and abs(w[1]+w[3]-box.y-box.y2) <= size*.8)
                    return ''.join(word for _,word in row) == ''.join((run.unicode or '').split())
                if not backed(p):
                    continue
                wraps = [r for r in fallback if r not in group and r.xobj_id==p.xobj_id
                         and abs(r.pdf_style.font_size-size)<=.35 and abs(r.box.x-left)<=size*.5
                         and size*.65 < b.y-r.box.y < size*2 and backed(r)]
                if len(wraps)==1:
                    group.extend((p,wraps[0]))
            groups.append((first, group))
    owners = Counter(id(p) for _, group in groups for p in group)
    return [(first, group) for first, group in groups if all(owners[id(p)] == 1 for p in group)]


def repair_split_captions(page, words):
    from babeldoc.format.pdf.document_il.utils.layout_helper import get_char_unicode_string
    from linguafloat.document_scientific import composition_chars

    changes = []
    for carrier, group in split_caption_groups(page, words):
        if any(part.pdf_same_style_unicode_characters
               for p in group for part in p.pdf_paragraph_composition):
            continue  # Translated fragments cannot be recombined losslessly.
        size = carrier.pdf_style.font_size
        rows = []
        for p in sorted(group, key=lambda p:(-(p.box.y+p.box.y2)/2,p.box.x)):
            y = (p.box.y+p.box.y2)/2
            if not rows or abs(rows[-1][0]-y) > size*.28:
                rows.append((y, []))
            rows[-1][1].append(p)
        ordered = [p for _, row in rows for p in sorted(row,key=lambda p:p.box.x)]
        parts = [part for p in ordered for part in p.pdf_paragraph_composition]
        chars = [c for part in parts for c in composition_chars(part)]
        if not chars or len(chars) != len({id(c) for c in chars}):
            continue
        text = ' '.join(get_char_unicode_string(
            [c for p in sorted(row,key=lambda p:p.box.x) for part in p.pdf_paragraph_composition
             for c in composition_chars(part)]).strip() for _,row in rows)
        carrier.box = union([p.box for p in group])
        carrier.pdf_paragraph_composition = parts
        carrier.unicode = text
        joined = {id(p) for p in group}
        at = min(i for i,p in enumerate(page.pdf_paragraph) if id(p) in joined)
        page.pdf_paragraph[:] = [p for p in page.pdf_paragraph if id(p) not in joined]
        page.pdf_paragraph.insert(at, carrier)
        changes.append(dict(kind='joined_split_caption', count=len(group)))
    return changes


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
            if getattr(formula, '_lf_caption_marker', False):
                continue
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
