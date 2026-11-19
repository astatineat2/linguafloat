"""Reflow a linked, three-tier journal masthead using already translated text."""
from statistics import median
import re


def external_web_uri(page, link):
    import pymupdf as f

    if link.get('kind') == f.LINK_URI:
        return link.get('uri')
    # MuPDF can report an original scheme-less /URI as LINK_LAUNCH.
    if link.get('kind') == f.LINK_LAUNCH and link.get('xref', 0) > 0:
        doc, xref = page.parent, link['xref']
        if doc.xref_get_key(xref, 'A/S') == ('name', '/URI'):
            kind, value = doc.xref_get_key(xref, 'A/URI')
            if kind == 'string' and re.fullmatch(r'www\.[^\s/]+(?:/[^\s]*)?', value):
                return value
    return None


def restore_masthead_actions(page, links):
    """insert_pdf can rewrite a scheme-less /URI as a remote named action."""
    import pymupdf as f

    for link in links:
        if not link['uri'].startswith('www.'):
            continue
        box = f.Rect(link['from'])
        matches = [existing for existing in page.get_links()
                   if all(abs(a-b) < .1 for a,b in zip(existing['from'],box))]
        if len(matches) == 1:
            xref = matches[0]['xref']
            page.parent.xref_set_key(xref, 'A', f"<</S/URI/URI{f.get_pdf_str(link['uri'])}>>")
        elif not matches:
            page.insert_link(dict(kind=f.LINK_URI, uri=link['uri'], **{'from': box}))


def plan_masthead(page):
    import pymupdf as f

    if page.number or page.rotation or page.cropbox.tl != f.Point(0, 0):
        return None
    links = [(f.Rect(link['from']), uri) for link in page.get_links()
             if (uri := external_web_uri(page, link))]
    if len(links) < 2:
        return None
    lines = [line for block in page.get_text('dict', flags=f.TEXTFLAGS_DICT & ~f.TEXT_PRESERVE_IMAGES)['blocks']
             for line in block.get('lines', ()) if tuple(line['dir']) == (1, 0)]
    for item in page.get_drawings():
        fill, band = item.get('fill'), item['rect']
        edges = item['items']
        rectangle = (len(edges) == 1 and edges[0][0] == 're') or (
            3 <= len(edges) <= 4 and all(edge[0] == 'l' for edge in edges)
            and {(p.x, p.y) for _, *points in edges for p in points}
            == {(p.x, p.y) for p in (band.tl, band.tr, band.bl, band.br)})
        if (not fill or min(fill) < .8 or max(fill)-min(fill) > .05 or min(fill) > .99
                or item['type'] != 'f' or not rectangle
                or band.width < page.rect.width*.45 or band.y1 > page.rect.height*.25):
            continue
        rows = []
        for line in lines:
            spans = [s for s in line['spans'] if s['text'].strip()]
            if not spans:
                continue
            box = f.Rect(line['bbox'])
            y = spans[0]['origin'][1]
            if band.y0 <= y <= band.y1 and band.x0 <= box.x0 and box.x1 <= band.x1:
                rows.append(dict(box=box, baseline=y, size=median(s['size'] for s in spans), spans=spans))
        rows.sort(key=lambda row: row['baseline'])
        if len(rows) != 3 or rows[1]['size'] < 1.35*max(rows[0]['size'], rows[2]['size']):
            continue
        center = band.tl.x+band.width/2
        if any(abs((r['box'].x0+r['box'].x1)/2-center) > band.width*.035 for r in rows):
            continue
        for index in (0, 2):
            row = rows[index]
            hits = [(box, uri) for box, uri in links if (box & row['box']).get_area() >= box.get_area()*.85]
            if len(hits) != 1:
                break
            box, uri = hits[0]
            # Use the link's center strip, avoiding ascenders/descenders crossing its rectangle.
            chars = [c for run in page.get_texttrace() for c in run['chars']
                     if box.contains((f.Rect(c[3]).tl+f.Rect(c[3]).br)/2)]
            text = ''.join(chr(c[0]) for c in chars).strip()
            source = ''.join(s['text'] for s in row['spans']).strip()
            if not text or source.count(text) != 1 or source == text:
                break
            color = min(row['spans'], key=lambda s: abs(s['bbox'][0]-box.x0))['color']
            row.update(anchor=text, uri=uri, color=f.sRGB_to_pdf(color))
        else:
            return band, rows
    return None


def repair_masthead(source, page, control, fonts):
    import pymupdf as f
    from linguafloat.document_fonts import FitzFontResolver
    from linguafloat.document_graphic_layout import clear_of_original

    plan = plan_masthead(source)
    if not plan or page.rect != source.rect:
        return None
    band, rows = plan
    box = f.Rect(band.x0+1, min(band.y0, rows[0]['box'].y0)-.5, band.x1-1, band.y1+.5)
    control.checkpoint()
    raw = page.get_text('dict', clip=box, flags=f.TEXTFLAGS_DICT & ~f.TEXT_PRESERVE_IMAGES)
    # The engine retains the source font-size transitions inside the merged
    # paragraph. Require both exact anchors and exactly three ordered tiers.
    tier = []
    threshold = (rows[1]['size']+max(rows[0]['size'],rows[2]['size']))/2
    for block in raw['blocks']:
        for line in block.get('lines', ()):
            for span in line['spans']:
                large = span['size'] > threshold
                if not tier or large != tier[-1][0]:
                    tier.append((large, []))
                tier[-1][1].append(span['text'])
    if [large for large, _ in tier] != [False, True, False]:
        return None
    texts = [''.join(parts).strip() for _, parts in tier]
    if not texts[1] or any(texts[i].count(rows[i]['anchor']) != 1 for i in (0, 2)):
        return None

    resolver = FitzFontResolver(*fonts) if all(fonts) else None
    overlays, links = [], []
    for row, text in zip(rows, texts):
        size, y = row['size'], row['baseline']
        flags = row['spans'][0]['flags']
        bold, italic = bool(flags & 16), bool(flags & 2)

        def width(value):
            if not resolver:
                return f.get_text_length(value, fontname='china-s', fontsize=size)
            return sum(face.text_length(part, fontsize=size)
                       for part, face in resolver.runs(value, bold=bold, italic=italic))

        advance = width(text)
        if advance > box.width-4:
            return None  # No guessing, truncation, or wrapping across the next tier.
        x = (band.x0+band.x1-advance)/2
        anchor = row.get('anchor')
        before, after = text.split(anchor) if anchor else (text, '')
        with f.open() as overlay:
            p = overlay.new_page(width=page.rect.width, height=page.rect.height)
            for i, value in enumerate((before, anchor, after)):
                if not value:
                    continue
                color = row['color'] if i == 1 else f.sRGB_to_pdf(row['spans'][0]['color'])
                if resolver:
                    resolver.draw_line(p, (x, y), value, fontsize=size, bold=bold, italic=italic, color=color)
                else:
                    p.insert_text((x, y), value, fontname='china-s', fontsize=size, color=color)
                x += width(value)
            if anchor:
                hit = p.search_for(anchor)
                if len(hit) != 1:
                    return None
                links.append(dict(kind=f.LINK_URI, uri=row['uri'], **{'from': hit[0]}))
            overlays.append((overlay.tobytes(), {}))
    group = [(dict(source='native', box=list(box)), None, {}, '')]
    if not clear_of_original(page, group, overlays):
        return None
    page.add_redact_annot(box, fill=False, cross_out=False)
    page.apply_redactions(images=0, graphics=0, text=0)
    for data, _ in overlays:
        with f.open('pdf', data) as overlay:
            page.show_pdf_page(page.rect, overlay, 0)
    for link in links:
        page.insert_link(link)
    return dict(page=source.number+1, kind='journal_masthead', status='translated', box=list(box),
                sizes=[row['size'] for row in rows], rows=texts,
                links=[dict(link, **{'from': list(link['from'])}) for link in links])
