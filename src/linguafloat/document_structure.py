"""Native anchors and lossless, contiguous source segmentation.

Native boxes use unrotated, zero-origin page coordinates. Preserve regions
must be restored from the original PDF by the caller. No translations are
invented and mathematical formulas are never converted into ordinary prose.
"""
from __future__ import annotations
import copy
from itertools import groupby
import re

_DECORATIONS = frozenset("■▪●◆")
_WEIGHT = re.compile(r"bold|semibold|smbd|demi", re.I)
_INSTITUTION = re.compile(r"Faculty|University|School|Department|Institute|Laborator|Center|Centre")
_AUTHOR = re.compile(r"^(?P<name>[A-Z][A-Za-z’'. -]{3,70}?)\s+(?P<separator>[−–—])\s*")
_HAN = r"\u3400-\u9fff\U00020000-\U000323af"
_KEYWORD_LABEL = re.compile(r'^(?:key\s*words?|index\s+terms|关键词|关键字)\s*[:：]', re.I)


def _decoration_ink(page, bullet, title_box):
    """Find marker ink on the title line, not the next coloured subhead."""
    import pymupdf as f
    import numpy as np
    clip = f.Rect(bullet['bbox']) & page.rect
    clip &= f.Rect(clip.x0, title_box.y0-1, clip.x1, title_box.y1+1)
    if clip.is_empty:
        return None
    pix = page.get_pixmap(matrix=f.Matrix(4, 4), clip=clip, colorspace=f.csRGB, alpha=False)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).astype(int)
    color = bullet['color'] & 0xffffff
    wanted = np.array([(color >> 16) & 255, (color >> 8) & 255, color & 255])
    mask = np.max(abs(rgb-wanted), axis=2) < 22
    candidates = []
    for sy, sx in zip(*np.where(mask)):
        if not mask[sy, sx]:
            continue
        pending = [(int(sx), int(sy))]
        mask[sy, sx] = False
        pixels = []
        while pending:
            x, y = pending.pop()
            pixels.append((x, y))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x+dx, y+dy
                    if 0 <= nx < pix.width and 0 <= ny < pix.height and mask[ny, nx]:
                        mask[ny, nx] = False
                        pending.append((nx, ny))
        xs, ys = zip(*pixels)
        width, height = max(xs)-min(xs)+1, max(ys)-min(ys)+1
        if width >= 8 and height >= 8 and .6 <= width/height <= 1.6:
            candidates.append([max(clip.x0, (pix.x+min(xs)-1)/4), max(clip.y0, (pix.y+min(ys)-1)/4),
                               min(clip.x1, (pix.x+max(xs)+2)/4), min(clip.y1, (pix.y+max(ys)+2)/4)])
    return candidates[0] if len(candidates) == 1 else None


def _navigation(page, rows):
    """Keep the same grouping for source preservation and translated navigation."""
    from linguafloat.document_navigation import plan_navigation
    return [region for group in plan_navigation(page) for region in group['preserve']]


def _margin_numbers(page):
    """Candidates only: isolated numeric native lines in the outer 48 pt margin."""
    import pymupdf as f
    if page.rotation or page.cropbox.x0 or page.cropbox.y0:
        return []
    result = []
    for block in page.get_text('dict', flags=f.TEXTFLAGS_DICT & ~f.TEXT_PRESERVE_IMAGES)['blocks']:
        for line in block.get('lines', []):
            spans = [s for s in line['spans'] if s['text'].strip()]
            if tuple(line.get('dir', (1, 0))) != (1, 0) or len(spans) != 1:
                continue
            span = spans[0]
            text = span['text'].strip()
            folio = re.fullmatch(r'([0-9]{1,7})(?:\s+of\s+([0-9]{1,7}))?', text, re.I)
            if not folio or not 4 <= span['size'] <= 14:
                continue
            total = int(folio[2]) if folio[2] else None
            number = int(folio[1])
            if total is not None and (number != page.number + 1 or total != len(page.parent)):
                continue
            x0, y0, x1, y1 = span['bbox']
            if y1 <= 48:
                edge, distance = 'top', span['origin'][1]
            elif y0 >= page.rect.height-48:
                edge, distance = 'bottom', page.rect.height-span['origin'][1]
            else:
                continue
            result.append(dict(text=text, number=number, total=total, box=list(span['bbox']), font=re.sub(r'^[A-Z]{6}\+', '', span['font']),
                               size=span['size'], color=span['color'] & 0xffffff, edge=edge,
                               distance=distance, center=(x0+x1)/2))
    return result


def _page_numbers(page):
    """Require a matching consecutive number on EVERY available adjacent page.

    An interior page needs both neighbours; a document endpoint needs its one
    neighbour. A standalone page, missing neighbour number, conflicting sequence,
    changed font/size/colour/position or ambiguous match is not preserved.
    """
    document = page.parent
    if document is None or len(document) < 2:
        return []
    neighbors = {}
    for index in (page.number-1, page.number+1):
        if 0 <= index < len(document):
            neighbor = document[index]
            if abs(neighbor.rect.width-page.rect.width) > 1 or abs(neighbor.rect.height-page.rect.height) > 1:
                return []
            neighbors[index] = _margin_numbers(neighbor)
    result = []
    for candidate in _margin_numbers(page):
        sequence = [dict(page=page.number+1, text=candidate['text'])]
        for index, others in neighbors.items():
            matches = [r for r in others if r['font'] == candidate['font'] and r['edge'] == candidate['edge']
                       and r['color'] == candidate['color'] and abs(r['size']-candidate['size']) <= .1
                       and abs(r['distance']-candidate['distance']) <= 1
                       and r['total'] == candidate['total']
                       and (abs(r['center']-candidate['center']) <= 2 or
                            (r['total'] is not None and abs(min(r['box'][0], page.rect.width-r['box'][2]) -
                                min(candidate['box'][0], page.rect.width-candidate['box'][2])) <= 2))]
            if len(matches) != 1 or matches[0]['number']-candidate['number'] != index-page.number:
                break
            sequence.append(dict(page=index+1, text=matches[0]['text']))
        else:
            result.append(dict(kind='page_number', box=candidate['box'], glyph_box=candidate['box'],
                               text=candidate['text'], font=candidate['font'], size=candidate['size'],
                               sequence=sorted(sequence, key=lambda r: r['page'])))
    return result


def _metadata_candidates(page):
    """Short isolated top-margin labels; badges may sit lower on page one."""
    import pymupdf as f
    if page.rotation or page.cropbox.x0 or page.cropbox.y0:
        return []
    lines = [line for b in page.get_text('dict', flags=f.TEXTFLAGS_DICT & ~f.TEXT_PRESERVE_IMAGES)['blocks']
             for line in b.get('lines', [])]
    drawings = None
    result = []
    for line in lines:
        spans = [s for s in line['spans'] if s['text'].strip()]
        if tuple(line.get('dir', (1, 0))) != (1, 0) or len(spans) != 1:
            continue
        span = spans[0]
        text, box = span['text'].strip(), f.Rect(span['bbox'])
        if (not 2 <= len(text) <= 32 or not any(c.isalpha() for c in text)
                or re.search(r'://|www\.|[./@]', text) or not 4 <= span['size'] <= 12
                or box.width > page.rect.width*.3 or box.y0 < 0 or box.y1 > page.rect.height*.14):
            continue
        if any(f.Rect(other['bbox']).intersects(box) for other in lines if other is not line):
            continue
        # Position alone is insufficient for an offset first-page label: require
        # a compact non-white filled badge, matched to subsequent pages below.
        if drawings is None:
            drawings = page.get_drawings()
        badges = [d for d in drawings if d.get('fill') is not None and min(d['fill']) < .9
                  and f.Rect(d['rect']).contains(box) and d['rect'].width <= max(80, box.width*4)
                  and d['rect'].height <= span['size']*3]
        badge = None
        if len(badges) == 1:
            d = badges[0]
            badge = dict(width=d['rect'].width, height=d['rect'].height, fill=list(d['fill']),
                         offset=[box.x0-d['rect'].x0, box.y0-d['rect'].y0])
        if box.y1 > 60 and (page.number != 0 or badge is None):
            continue
        result.append(dict(text=text, box=list(box), font=span['font'], size=span['size'],
                           color=span['color'], baseline=span['origin'][1], badge=badge))
    return result


def _running_metadata(page):
    """Require a contiguous three-page run with matching text/style/position.

    An offset first-page badge additionally needs matching filled badge size,
    colour and text inset on pages two AND three. No label vocabulary is used.
    The evidence list is retained in the structure report for auditability.
    """
    document = page.parent
    if document is None or len(document) < 3:
        return []
    number = page.number
    candidates = {}
    for index in range(max(0, number-2), min(len(document), number+3)):
        other = document[index]
        if abs(other.rect.width-page.rect.width) < 1 and abs(other.rect.height-page.rect.height) < 1:
            candidates[index] = _metadata_candidates(other)

    def matches(a, b, first):
        if (a['text'] != b['text'] or a['font'] != b['font'] or a['color'] != b['color']
                or abs(a['size']-b['size']) > .1
                or any(abs(a['box'][i]-b['box'][i]) > 1 for i in (0, 2))):
            return False
        if abs(a['baseline']-b['baseline']) <= 1:
            return True
        if not first or abs(a['baseline']-b['baseline']) > 60 or not a['badge'] or not b['badge']:
            return False
        aa, bb = a['badge'], b['badge']
        return (all(abs(aa[k]-bb[k]) <= 1 for k in ('width', 'height'))
                and all(abs(x-y) <= .01 for x, y in zip(aa['fill'], bb['fill']))
                and all(abs(x-y) <= 1 for x, y in zip(aa['offset'], bb['offset'])))

    result = []
    for candidate in candidates.get(number, []):
        evidence = []
        for index, others in candidates.items():
            hits = [r for r in others if matches(candidate, r, number == 0 or index == 0)]
            if len(hits) == 1:
                evidence.append(dict(page=index+1, box=hits[0]['box']))
        pages = {e['page']-1 for e in evidence}
        if not any(number in range(start, start+3) and set(range(start, start+3)) <= pages
                   for start in range(max(0, number-2), number+1)):
            continue
        result.append(dict(candidate, kind='running_metadata', glyph_box=candidate['box'], evidence=evidence))
    return result


def source_structure(page):
    import pymupdf as f
    if page.rotation or page.cropbox.x0 or page.cropbox.y0:
        return dict(headings=[], authors=[], preserve=[])
    rows = [dict(text=''.join(s['text'] for s in line['spans']), box=list(line['bbox']), spans=line['spans'])
            for b in page.get_text('dict', flags=f.TEXTFLAGS_DICT & ~f.TEXT_PRESERVE_IMAGES)['blocks']
            for line in b.get('lines', []) if tuple(line.get('dir', (1, 0))) == (1, 0) and line.get('spans')]
    headings, preserve, entries, metadata_lines = [], [], [], []
    for row in rows:
        spans = [s for s in row['spans'] if s['text'].strip()]
        if (spans and _KEYWORD_LABEL.match(row['text'].strip())
                and all(abs(s['origin'][1]-spans[0]['origin'][1]) <= .75 for s in spans)):
            metadata_lines.append(dict(kind='keywords', box=row['box'], text=row['text'],
                                       baseline=spans[0]['origin'][1]))
        if len(spans) >= 2 and spans[0]['text'].strip() in _DECORATIONS:
            words = spans[1:]
            title = ''.join(s['text'] for s in words).strip()
            if re.fullmatch(r'[A-Z][A-Z &-]{4,90}', title):
                box = f.Rect(words[0]['bbox'])
                for span in words[1:]:
                    box |= f.Rect(span['bbox'])
                headings.append(dict(box=list(box), text=title))
                ink = _decoration_ink(page, spans[0], box)
                if ink:
                    preserve.append(dict(kind='decoration', box=ink, glyph_box=list(spans[0]['bbox'])))
        text = row['text'].strip()
        if re.fullmatch(r'(?:https?://|www\.|doi:|10\.\d{4,9}/|(?:orcid|pubs\.[\w.-]+)\.?(?:org)?/)[^\s]+', text, re.I):
            preserve.append(dict(kind='identifier', box=row['box'], glyph_box=row['box']))
        match = _AUTHOR.match(text)
        if (spans and match and _WEIGHT.search(spans[0]['font']) and _INSTITUTION.search(text)
                and match['name'].strip() == spans[0]['text'].strip()):
            entries.append((row, match['name'].strip(), match['separator']))
    author_bands = []
    for entry, name, separator in entries:
        x0, y0, x1, y1 = entry['box']
        middle = page.rect.width*.5
        column_left, column_right = (0, middle) if x1 < middle else (middle, page.rect.width)
        if x0 < column_left:
            continue
        column = [r for r in rows if column_left <= r['box'][0] and r['box'][2] <= column_right]
        following = [r for r in column if r['box'][1] > y0+2 and r['box'][0] <= x0+5]
        stop = min((r['box'][1] for r in following), default=y1)
        # Include detached ORCID/email fragments on the same continuation lines.
        content = [r for r in column if y0-.5 <= r['box'][1] < stop-.5 and r['box'][0] >= x0-1]
        if content and sum(abs(e['box'][0]-x0) < 5 for e, _, _ in entries) >= 2:
            box = f.Rect(content[0]['box'])
            for row in content[1:]:
                box |= f.Rect(row['box'])
            author_bands.append(dict(box=list(box), text=entry['text'], name=name,
                                     name_box=list(entry['spans'][0]['bbox']), separator=separator))
    preserve.extend(_navigation(page, rows))
    preserve.extend(_page_numbers(page))
    preserve.extend(_running_metadata(page))
    return dict(headings=headings, authors=author_bands, preserve=preserve, metadata_lines=metadata_lines)


def _join_metadata_lines(page, lines):
    """Undo engine mid-word paragraph cuts only with complete native-line proof.

    Reuses every composition and glyph in its original order. No reconstructed
    text, dropped glyphs, inferred missing letters, or formula flattening.
    """
    from babeldoc.format.pdf.document_il import Box
    from linguafloat.document_geometry import box_of, owns, union, source_style
    from linguafloat.document_scientific import composition_chars
    changes = []
    height = page.cropbox.box.y2
    for line in lines:
        x0, y0, x1, y1 = line['box']
        area = Box(x0-1, height-y1-1, x1+1, height-y0+1)
        candidates = []
        for index, paragraph in enumerate(page.pdf_paragraph):
            parts = paragraph.pdf_paragraph_composition
            if not parts or any(p.pdf_formula or not composition_chars(p) for p in parts):
                continue
            chars = [c for part in parts for c in composition_chars(part)]
            if chars and all(box_of(c) and owns(area, c) for c in chars):
                candidates.append((index, paragraph, chars))
        if len(candidates) < 2:
            continue
        first, last = candidates[0][0], candidates[-1][0]
        if last-first+1 != len(candidates):
            continue  # Never move unrelated paragraphs across a merge.
        chars = [c for _, _, glyphs in candidates for c in glyphs]
        if len({id(c) for c in chars}) != len(chars):
            continue
        text = ''.join(c.char_unicode or '' for c in chars)
        if re.sub(r'\s+', '', text) != re.sub(r'\s+', '', line['text']):
            continue
        # Wrong native order cannot be repaired by concatenating plausible words.
        if any(box_of(b).x < box_of(a).x-.5 for a, b in zip(chars, chars[1:])):
            continue
        parts = [part for _, paragraph, _ in candidates for part in paragraph.pdf_paragraph_composition]
        clone = copy.copy(candidates[0][1])
        clone.pdf_paragraph_composition = parts
        clone.unicode = text
        clone.box = union([c.box for c in chars])
        clone.pdf_style = source_style(parts, clone.pdf_style)
        clone.first_line_indent = False
        clone.debug_id = f'{clone.debug_id}-metadata-{line["kind"]}'
        page.pdf_paragraph[first:last+1] = [clone]
        changes.append(dict(kind='native_metadata_line', metadata_kind=line['kind'],
                            merged_paragraphs=len(candidates), glyphs=len(chars)))
    return changes


def split_structures(page, structure):
    """Split contiguous segments, keeping original glyphs and formulas atomic.

    Ambiguous boundaries, unlocated compositions and boundary-crossing formulas
    veto the paragraph change. Only source-restored decorations/navigation/page numbers may
    be removed. Formulas are never demoted to prose based on their alphabet.
    """
    from babeldoc.format.pdf.document_il import Box
    from linguafloat.document_geometry import union, source_style, owns, box_of
    from linguafloat.document_scientific import composition_chars
    crop = page.cropbox.box
    if crop.x or crop.y:
        return []
    height = crop.y2
    plans = [dict(p, kind='heading') for p in structure.get('headings', [])]
    plans += [dict(p, kind='author') for p in structure.get('authors', [])]

    def area(box, padding=1):
        x0, y0, x1, y1 = box
        return Box(x0-padding, height-y1-padding, x1+padding, height-y0+padding)

    areas = [area(p['box']) for p in plans]
    restored = [(r['kind'], area(r['glyph_box'], padding=0)) for r in structure.get('preserve', [])
                if r['kind'] in ('decoration', 'navigation', 'page_number', 'running_metadata', 'inline_publication')]
    rebuilt, changes = [], []
    for paragraph in page.pdf_paragraph:
        segments = []
        safe, removed = True, 0

        def append(key, part):
            if not segments or segments[-1][0] != key:
                segments.append((key, []))
            segments[-1][1].append(part)

        for part in paragraph.pdf_paragraph_composition:
            chars = composition_chars(part)
            if not chars or any(not box_of(c) for c in chars):
                safe = False
                break
            keys = []
            for char in chars:
                token = (char.char_unicode or '').strip()
                if any(owns(box, char) and (kind != 'decoration' or token in _DECORATIONS)
                       for kind, box in restored):
                    keys.append('restored')
                    continue
                hits = [i for i, box in enumerate(areas) if owns(box, char)]
                if len(hits) > 1:
                    safe = False
                    break
                keys.append(hits[0] if hits else -1)
            if not safe:
                break
            if part.pdf_formula:
                if len(set(keys)) != 1:
                    safe = False
                    break
                if keys[0] == 'restored':
                    if part.pdf_formula.pdf_curve or part.pdf_formula.pdf_form:
                        safe = False
                        break
                    removed += len(chars)
                else:
                    append(keys[0], part)
                continue
            for key, indices in groupby(range(len(chars)), key=lambda i: keys[i]):
                selected = [chars[i] for i in indices]
                if key == 'restored':
                    removed += len(selected)
                    continue
                if len(selected) == len(chars):
                    append(key, part)
                else:
                    name = 'pdf_line' if part.pdf_line else 'pdf_same_style_characters'
                    container = getattr(part, name)
                    if container is None:
                        safe = False
                        break
                    piece, container = copy.copy(part), copy.copy(container)
                    container.pdf_character = selected
                    container.box = union([c.box for c in selected])
                    setattr(piece, name, container)
                    append(key, piece)
            if not safe:
                break
        if not safe or (not removed and all(key == -1 for key, _ in segments)):
            rebuilt.append(paragraph)
            continue
        clones = []
        for index, (key, parts) in enumerate(segments):
            glyphs = [c for part in parts for c in composition_chars(part)]
            clone = copy.copy(paragraph)
            clone.pdf_paragraph_composition = parts
            clone.pdf_style = source_style(parts, paragraph.pdf_style)
            clone.box = union([c.box for c in glyphs] + [part.pdf_formula.box for part in parts if part.pdf_formula])
            clone.unicode = ''.join(c.char_unicode or '' for c in glyphs)
            clone.debug_id = f'{paragraph.debug_id}-structure-{index}'
            if key >= 0:
                clone.first_line_indent = False
                if plans[key]['kind'] == 'heading':
                    clone.layout_label = 'title'
            clones.append(clone)
        if len(clones) == 1 and not removed:
            clone = clones[0]
            if (clone.pdf_paragraph_composition == paragraph.pdf_paragraph_composition
                    and all(getattr(clone, attr) == getattr(paragraph, attr) for attr in
                            ('box', 'pdf_style', 'unicode', 'layout_label', 'first_line_indent'))):
                rebuilt.append(paragraph)
                continue
        rebuilt.extend(clones)
        changes.append(dict(kind='native_structure', parts=len(segments), restored_glyphs=removed))
    page.pdf_paragraph[:] = rebuilt
    changes.extend(_join_metadata_lines(page, structure.get('metadata_lines', [])))
    return changes


def normalize_cjk_runs(paragraph):
    """Remove ASCII spaces only at Han-to-Han/punctuation boundaries.

    Latin words/identifiers, NBSP, tabs, newlines, formula and native-glyph
    compositions are untouched. Chinese elsewhere in a run does not justify
    changing bracket-adjacent Latin spaces.
    """
    changed = 0
    for part in paragraph.pdf_paragraph_composition:
        run = part.pdf_same_style_unicode_characters
        if not run or not re.search(f'[{_HAN}]', run.unicode or ''):
            continue
        old = run.unicode
        text = re.sub(f'(?<=[{_HAN}]) +(?=[{_HAN}，。；：！？、）】])', '', old)
        text = re.sub(f'(?<=[（【]) +(?=[{_HAN}])', '', text)
        if text != old:
            run.unicode = text
            changed += 1
    return changed
