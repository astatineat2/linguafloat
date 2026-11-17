"""Source-grounded navigation, with no network or publisher-specific vocabulary.

Integration: keep native navigation in the normal preserve/restore pipeline.
Call repair_navigation(source, restored_page, translator) AFTER restoration and
BEFORE bilingual composition. Callers own request/cache policy and cancellation.
Low-level plan_navigation/render_navigation also accept externally cached text.
Coordinates are unrotated, zero-origin points. Unsupported geometry fails closed.
"""
from __future__ import annotations

from itertools import groupby
import re
import unicodedata
from urllib.parse import urlsplit


class NavigationError(ValueError):
    """A complete group cannot safely replace the restored native labels."""


class NavigationCommitError(RuntimeError):
    """Unexpected mutation failure: discard the output, do not report fallback."""


def _supported(page):
    return not (page.rotation or page.cropbox.x0 or page.cropbox.y0)


def _rows(page):
    """Split even a single native span by link ownership and large native gaps."""
    import pymupdf as f
    links = [l for l in page.get_links() if l.get('uri')]
    rows = []
    for block in page.get_text('rawdict', flags=f.TEXTFLAGS_RAWDICT & ~f.TEXT_PRESERVE_IMAGES)['blocks']:
        for line in block.get('lines', []):
            if tuple(line['dir']) != (1, 0):
                continue
            chunks, current, previous, owner = [], [], None, None
            for span in line['spans']:
                for char in span['chars']:
                    box = f.Rect(char['bbox'])
                    center = (box.tl + box.br) / 2
                    hits = [i for i, link in enumerate(links) if center in f.Rect(link['from'])]
                    key = hits[0] if len(hits) == 1 else None
                    if current and (key != owner or box.x0-previous.x1 > max(12, span['size']*1.5)):
                        chunks.append(current)
                        current = []
                    current.append((char, span))
                    previous, owner = box, key
            if current:
                chunks.append(current)
            for chunk in chunks:
                while chunk and chunk[0][0]['c'].isspace():
                    chunk.pop(0)
                while chunk and chunk[-1][0]['c'].isspace():
                    chunk.pop()
                if not chunk:
                    continue
                box = f.Rect(chunk[0][0]['bbox'])
                for char, _ in chunk[1:]:
                    box |= f.Rect(char['bbox'])
                spans = []
                for _, pairs in groupby(chunk, key=lambda pair: id(pair[1])):
                    pairs = list(pairs)
                    spans.append(dict(pairs[0][1], text=''.join(c['c'] for c, _ in pairs),
                                      origin=list(pairs[0][0]['origin'])))
                rows.append(dict(text=''.join(c['c'] for c, _ in chunk), box=list(box), spans=spans))
    return rows


def plan_navigation(page):
    """Return JSON-safe groups, items and all preserve regions (including icons).

    Evidence: >=3 short aligned links, separated by >=20pt, spanning >=45%
    of page width, same HTTP(S) document path. Nearby same-document upper
    links and short same-baseline labels belong to that toolbar. No keywords.
    """
    import pymupdf as f
    if not _supported(page):
        return []
    rows = _rows(page)
    links = [l for l in page.get_links() if l.get('uri')]
    linked = {}
    for index, row in enumerate(rows):
        if row['box'][1] > page.rect.height*.4 or not 3 <= len(row['text']) <= 45:
            continue
        if max(s['size'] for s in row['spans']) > 12:
            continue
        box = f.Rect(row['box'])
        matches = []
        for link in links:
            target = urlsplit(link['uri'])
            if target.scheme not in ('http', 'https') or not target.netloc:
                continue
            if (box & f.Rect(link['from'])).get_area() >= box.get_area()*.85:
                matches.append((link, (target.scheme, target.netloc.lower(), target.path)))
        if len(matches) == 1:
            linked[index] = matches[0]
    groups, used = [], set()
    for index in linked:
        if index in used:
            continue
        baseline = rows[index]['spans'][0]['origin'][1]
        cluster = sorted((i for i in linked if abs(rows[i]['spans'][0]['origin'][1]-baseline) <= .75),
                         key=lambda i: rows[i]['box'][0])
        if len(cluster) < 3 or len({linked[i][1] for i in cluster}) != 1:
            continue
        if rows[cluster[-1]]['box'][2]-rows[cluster[0]]['box'][0] < page.rect.width*.45:
            continue
        if any(rows[b]['box'][0]-rows[a]['box'][2] < 20 for a, b in zip(cluster, cluster[1:])):
            continue
        target = linked[index][1]
        selected = {i for i, row in enumerate(rows) if len(row['text']) <= 45
                    and max(s['size'] for s in row['spans']) <= 16
                    and all(abs(s['origin'][1]-baseline) <= 2 for s in row['spans'])}
        selected.update(i for i, (_, other) in linked.items() if other == target
                        and 0 < baseline-rows[i]['spans'][0]['origin'][1] <= 50
                        and rows[i]['box'][0] > page.rect.width*.5)
        preserve, items = [], []
        group_id = f'nav-{page.number}-{len(groups)}'
        for i in sorted(selected, key=lambda i: (rows[i]['box'][1], rows[i]['box'][0])):
            row, spans = rows[i], rows[i]['spans']
            preserve.append(dict(kind='navigation', box=row['box'], glyph_box=row['box'],
                                 text=row['text'], baseline=spans[0]['origin'][1]))
            # Layered glyphs are icons, even if their text looks alphabetic.
            if any(f.Rect(row['box']).intersects(f.Rect(rows[j]['box'])) for j in selected if j != i):
                continue
            if sum(c.isalpha() for c in row['text']) < 3:
                continue
            if any((s['font'], s['size'], s['color']) != (spans[0]['font'], spans[0]['size'], spans[0]['color'])
                   or abs(s['origin'][1]-spans[0]['origin'][1]) > .1 for s in spans):
                continue
            s = spans[0]
            link = linked.get(i)
            items.append(dict(id=f'{group_id}-{len(items)}', text=row['text'], box=row['box'],
                              origin=list(s['origin']), size=s['size'], color=s['color'] & 0xffffff,
                              bold=bool(s['flags'] & 16), italic=bool(s['flags'] & 2),
                              link=(dict(uri=link[0]['uri'], box=list(link[0]['from'])) if link else None)))
        if not all(any(item['box'] == rows[i]['box'] for item in items) for i in cluster):
            continue
        # Restore intersecting icon layers once. Sequentially restoring their
        # separate boxes erases the outer glyph when painting the inner layer.
        merged = []
        for region in preserve:
            intersecting = [r for r in merged if f.Rect(r['box']).intersects(f.Rect(region['box']))]
            for other in intersecting:
                region = dict(region, box=list(f.Rect(region['box']) | f.Rect(other['box'])),
                              glyph_box=list(f.Rect(region['glyph_box']) | f.Rect(other['glyph_box'])),
                              text=other['text']+region['text'])
                merged.remove(other)
            merged.append(region)
        groups.append(dict(id=group_id, baseline=baseline, items=items, preserve=merged))
        used.update(selected)
    return groups


def _characters(page, rect):
    """Every touched character; partial intersections veto the erasure."""
    import pymupdf as f
    found = []
    for block in page.get_text('rawdict', flags=f.TEXTFLAGS_RAWDICT & ~f.TEXT_PRESERVE_IMAGES)['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                for char in span['chars']:
                    box = f.Rect(char['bbox'])
                    if not rect.intersects(box) or char['c'].isspace():
                        continue
                    if not (rect + (-.15, -.15, .15, .15)).contains(box):
                        raise NavigationError('导航擦除边界与相邻字形相交')
                    found.append((char['c'], tuple(round(v, 2) for v in box)))
    return sorted(found)


def _erase(page, boxes):
    # MuPDF removes touched links along with text, leaving a stale link chain.
    # Keep the original annotation array (and native xrefs) instead of rebuilding
    # links from lossy dictionaries. This preserves URI and internal destinations.
    if any(a.type[0] == 12 for a in (page.annots() or [])):
        raise NavigationError('页面存在未应用的擦除注释')
    kind, annotations = page.parent.xref_get_key(page.xref, 'Annots')
    if kind == 'xref':
        annotations = page.parent.xref_object(int(annotations.split()[0]))
    for box in boxes:
        page.add_redact_annot(box, fill=False, cross_out=False)
    page.apply_redactions(images=0, graphics=0, text=0)
    page.parent.xref_set_key(page.xref, 'Annots', annotations)


def render_navigation(source, page, group, translations, *, font_pair=('SimSun', 'Times New Roman'), control=None):
    """Preflight whole group, then replace only labels; leave icons/gaps native.

    translations maps exact item ids to single-line Chinese strings. Missing,
    extra, unchanged, non-Chinese, oversized or unsafe text raises NavigationError
    BEFORE mutation. Font/config/cancel exceptions propagate. Uses one scale
    (1.0..0.8, >=6pt) for all labels, preserving relative size, origins and colour.
    White label backgrounds only: a raster ink collision vetoes coloured/complex
    backgrounds. On an unexpected commit exception the caller must discard the
    output document; this is not a PDF object-store rollback transaction.
    """
    import pymupdf as f
    import numpy as np
    from linguafloat.document_fonts import FitzFontResolver
    if not _supported(source) or not _supported(page) or source.rect != page.rect:
        raise NavigationError('导航页面坐标不一致')
    if source.parent is page.parent:
        raise NavigationError('导航回填需要独立输出文档')
    if group not in plan_navigation(source):
        raise NavigationError('导航计划与原页不匹配')
    items = group['items']
    if set(translations) != {i['id'] for i in items}:
        raise NavigationError('导航译文编号不完整')
    for item in items:
        text = translations[item['id']]
        if (not isinstance(text, str) or not text.strip() or len(text) > 120
                or any(unicodedata.category(c).startswith('C') for c in text)
                or text.strip() == item['text'].strip() or not re.search(r'[\u3400-\u9fff]', text)):
            raise NavigationError('导航译文不是完整单行中文')
    boxes = [f.Rect(i['box']) for i in items]
    for box in boxes:
        native = _characters(source, box)
        if not native or _characters(page, box) != native:
            raise NavigationError('导航目标页尚未恢复原文或包含其他文字')
    resolver = FitzFontResolver(*font_pair)
    with f.open() as trial, f.open() as overlay:
        trial.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
        _erase(trial[0], boxes)
        fitted = None
        for scale in (1., .95, .9, .85, .8):
            if control:
                control.checkpoint()
            canvas = overlay.new_page(width=page.rect.width, height=page.rect.height)
            drawn = []
            for item in items:
                size = item['size']*scale
                text = translations[item['id']].strip()
                runs = resolver.runs(text, bold=item['bold'], italic=item['italic'])
                width = sum(face.text_length(run, fontsize=size) for run, face in runs)
                if size < 6 or width + runs[-1][1].ink_padding(size)[2] > item['box'][2]-item['origin'][0]:
                    break
                rgb = tuple(((item['color'] >> shift) & 255)/255 for shift in (16, 8, 0))
                resolver.draw_line(canvas, item['origin'], text, fontsize=size,
                                   bold=item['bold'], italic=item['italic'], color=rgb)
                drawn.append(dict(id=item['id'], source=item['text'], text=text, origin=item['origin'], size=size))
            if len(drawn) != len(items):
                continue
            safe = True
            for item in items:
                x, y = item['origin']
                clip = f.Rect(x-1, y-item['size']*1.5, item['box'][2]+1, y+item['size']*.6)
                if not page.rect.contains(clip):
                    safe = False
                    break
                new = canvas.get_pixmap(matrix=f.Matrix(3, 3), clip=clip, alpha=True)
                old = trial[0].get_pixmap(matrix=f.Matrix(3, 3), clip=clip, colorspace=f.csRGB, alpha=False)
                ink = np.frombuffer(new.samples, np.uint8).reshape(new.height, new.width, 4)[:, :, 3] > 0
                background = np.frombuffer(old.samples, np.uint8).reshape(old.height, old.width, 3)
                # One-pixel safety halo catches adjacent icon/line antialiasing.
                expanded = ink.copy()
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    expanded |= np.roll(ink, (dy, dx), axis=(0, 1))
                if np.any(expanded & (background.min(axis=2) < 245)):
                    safe = False
                    break
            if safe:
                fitted = drawn
                break
        if fitted is None:
            raise NavigationError('完整导航译文无法在原锚点安全排入')
        # Redaction can remove link annotations. Re-create only affected links,
        # preserving exact URIs (including query/fragment) and original hit boxes.
        links = [dict(kind=f.LINK_URI, uri=l['uri'], **{'from': f.Rect(l['from'])})
                 for l in page.get_links() if l.get('uri') and any(f.Rect(l['from']).intersects(b) for b in boxes)]
        for item in items:
            if item['link']:
                link = dict(kind=f.LINK_URI, uri=item['link']['uri'], **{'from': f.Rect(item['link']['box'])})
                if not any(l['uri'] == link['uri'] and l['from'] == link['from'] for l in links):
                    links.append(link)
        if control:
            control.checkpoint()
        try:
            _erase(page, boxes)
            page.show_pdf_page(page.rect, overlay, len(overlay)-1)
            existing = page.get_links()
            for link in links:
                if not any(l.get('uri') == link['uri'] and f.Rect(l['from']) == link['from'] for l in existing):
                    page.insert_link(link)
        except Exception as error:
            raise NavigationCommitError('导航提交失败，必须丢弃当前输出文档') from error
    return dict(kind='navigation', page=source.number+1, group=group['id'], status='translated',
                scale=scale, items=fitted, font_policy=resolver.report(), coordinates='top-left')


def repair_navigation(source, page, translator):
    """Adapter to existing translate_fragment/cache API; call after preservation.

    Only zh-Hans/zh-Hant targets are supported. Never catches cancellation or
    request/config failures as local layout failures (diagnostic enforces this).
    The caller must append returned records to its layout/failure report.
    """
    import pymupdf as f
    if translator.job.target not in ('zh-Hans', 'zh-Hant'):
        return []
    records = []
    boundary = translator.job.reference_boundary if translator.job.skip_references else {}
    for group in plan_navigation(source):
        if boundary and (source.number > boundary['page'] or (source.number == boundary['page']
                and any(f.Rect(i['box']).intersects(f.Rect(b)) for i in group['items'] for b in boundary['boxes']))):
            continue
        try:
            texts = {}
            for item in group['items']:
                translator.control.checkpoint()
                translator.location = f"PDF 第 {source.number+1} 页 · 导航 {item['id']}"
                texts[item['id']] = translator.translate_fragment(item['text'],
                    'Document navigation label. Translate to concise Chinese, one line, no explanation. '
                    'Keep the complete meaning. Neighbor labels: ' + ' | '.join(i['text'] for i in group['items']),
                    part='navigation')
            records.append(render_navigation(source, page, group, texts,
                font_pair=(getattr(translator.job, 'chinese_font', '') or 'SimSun',
                           getattr(translator.job, 'latin_font', '') or 'Times New Roman'), control=translator.control))
        except NavigationCommitError:
            raise
        except Exception as error:
            from linguafloat.document_failures import diagnostic
            issue = diagnostic(error, '导航翻译/排版')
            records.append(dict(issue, kind='navigation', page=source.number+1, group=group['id'],
                                status='untranslated', coordinates='top-left',
                                boxes=[i['box'] for i in group['items']]))
    return records
