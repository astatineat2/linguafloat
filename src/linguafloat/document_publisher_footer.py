"""Restore a digital article's repeated DOI and journal imprint as a unit."""
import re

import pymupdf as fitz


_DOI = re.compile(r'(?:https?://(?:dx\.)?doi\.org/|doi:\s*)10\.\d{4,9}/\S+', re.I)
_IMPRINT = re.compile(r'(?=.*[^\W\d_]).+?\b(?:18|19|20)\d{2}\b[,;\s]+\d+[\w()]*[,;:\s]+'
                      r'[A-Za-z]?\d+(?:\s*[-\u2010-\u2014\u2212]\s*[A-Za-z]?\d+)?\.?$')


def _candidate(page):
    if page.rotation or page.cropbox.x0 or page.cropbox.y0:
        return None
    clip = fitz.Rect(0, page.rect.height * .91, page.rect.width, page.rect.height)
    rows = [(fitz.Rect(line['bbox']), ' '.join(''.join(s['text'] for s in line['spans']).split()))
            for block in page.get_text('dict', clip=clip, flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']
            for line in block.get('lines', []) if line['dir'] == (1, 0)]
    pairs = []
    for doi, url in rows:
        if not _DOI.fullmatch(url) or doi.y0 < page.rect.height * .93:
            continue
        for citation, text in rows:
            if not _IMPRINT.fullmatch(text):
                continue
            line_height = max(doi.height, citation.height)
            if (line_height > 14 or abs(doi.y0 - citation.y0) > 1.65 * line_height
                    or min(abs(doi.x0-citation.x0), abs(doi.x1-citation.x1)) > line_height):
                continue
            # Allow one small line of engine reflow, not the entire bottom margin.
            pad = min(10, line_height * 1.5)
            region = ((doi | citation) + (-pad, -pad, pad, pad)) & page.rect
            if any(region.intersects(box) for box, _ in rows if box not in (doi, citation)):
                continue
            pairs.append(((url, text), doi, region))
    return pairs[0] if len(pairs) == 1 else None


def publisher_footer_box(page):
    item = _candidate(page)
    if item:
        key, doi, box = item
        for delta in (-1, 1, -2, 2):
            n = page.number + delta
            if 0 <= n < len(page.parent):
                other = page.parent[n]
                if other.rect != page.rect:
                    continue
                match = _candidate(other)
                if (match and match[0] == key and abs(match[1].y0 - doi.y0) <= 4
                        and min(abs(match[1].x0-doi.x0), abs(match[1].x0 - (page.rect.width-doi.x1))) <= 4):
                    return box
    return None


def restore_printed_urls(source, target, clip=None):
    """Bind a unique, intact printed URL to its original HTTP(S) action."""
    from collections import defaultdict
    actions = defaultdict(set)
    if source.rotation or target.rotation:
        return 0  # Link and extracted-word coordinates use different spaces on rotated pages.
    textpage = None
    for link in source.get_links():
        uri = link.get('uri', '')
        if link['kind'] != fitz.LINK_URI or not uri.startswith(('https://','http://')):
            continue
        if textpage is None:
            textpage = source.get_textpage()
        label = source.get_textbox(link['from'], textpage=textpage).strip()
        if label == uri or (label.startswith('www.') and label == uri.split('://',1)[1]):
            actions[label].add(uri)
    if not actions:
        return 0
    locations = defaultdict(list)
    for *coords, word, _, _, _ in target.get_text('words', clip=clip):
        if word in actions:
            locations[word].append(fitz.Rect(coords))
    fitz.JM_refresh_links(target._pdf_page())
    occupied = [l['from'] for l in target.get_links()]
    count = 0
    for label, boxes in locations.items():
        if len(boxes) != 1 or len(actions[label]) != 1 or any(boxes[0].intersects(r) for r in occupied):
            continue
        target.insert_link(dict(kind=fitz.LINK_URI, uri=next(iter(actions[label])), **{'from':boxes[0]}))
        occupied.append(boxes[0]); count += 1
    if count:
        fitz.JM_refresh_links(target._pdf_page())
    return count


def restore_publisher_footer(source, target):
    if source.rect != target.rect or target.rotation or target.cropbox.x0 or target.cropbox.y0:
        return
    rect = publisher_footer_box(source)
    if rect is None:
        return
    for block in target.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']:
        for line in block.get('lines', []):
            box = fitz.Rect(line['bbox'])
            if rect.intersects(box) and not rect.contains(box):
                return
    opts = dict(clip=rect, matrix=fitz.Matrix(2, 2), annots=False)
    if (source.get_pixmap(**opts).samples != target.get_pixmap(**opts).samples
            or source.get_textbox(rect) != target.get_textbox(rect)):
        if not _replace_footer(source, target, rect):
            return
    fitz.JM_refresh_links(source._pdf_page())
    fitz.JM_refresh_links(target._pdf_page())
    links = [link for link in source.get_links()
             if link['kind'] == fitz.LINK_URI and rect.contains(link['from'])]
    existing = target.get_links()
    for link in existing[:]:
        if (link['kind'] == fitz.LINK_URI and rect.contains(link['from'])
                and not any(item['uri'] == link['uri'] and item['from'].intersects(link['from']) for item in links)):
            target.delete_link(link)
            existing.remove(link)
    for link in links:
        if not any(item.get('uri') == link['uri'] and item['from'].intersects(link['from']) for item in existing):
            target.insert_link({'kind': fitz.LINK_URI, 'from': link['from'], 'uri': link['uri']})
            existing.append(link)
    fitz.JM_refresh_links(target._pdf_page())


def _explicit_text_matrices(page):
    """Make BT's implicit identity reset explicit before MuPDF text filtering."""
    doc = page.parent
    for xref in set(page.get_contents() + [item[0] for item in page.get_xobjects()]):
        data = doc.xref_stream(xref)
        buffer = fitz.JM_BufferFromBytes(data)
        stream = fitz.mupdf.fz_open_buffer(buffer)
        lexer = fitz.mupdf.PdfLexbuf(256)
        ends = []
        while True:
            token = fitz.mupdf.pdf_lex(stream, lexer)
            if token == fitz.mupdf.PDF_TOK_EOF:
                break
            if token == fitz.mupdf.PDF_TOK_KEYWORD:
                word = lexer.m_internal.scratch
                if word == 'BI':
                    # Binary inline-image data cannot be traversed as PDF tokens.
                    return False
                if word == 'BT':
                    ends.append(fitz.mupdf.fz_tell(stream))
        for end in reversed(ends):
            data = data[:end] + b' 1 0 0 1 0 0 Tm ' + data[end:]
        if ends:
            doc.update_stream(xref, data)
    return True


def _replace_footer(source, target, rect):
    bounds = target.rect
    complement = [box for box in (
        fitz.Rect(0, 0, bounds.x1, rect.y0),
        fitz.Rect(0, rect.y1, bounds.x1, bounds.y1),
        fitz.Rect(0, rect.y0, rect.x0, rect.y1),
        fitz.Rect(rect.x1, rect.y0, bounds.x1, rect.y1)) if not box.is_empty]

    def rectangle(box):
        r = box * ~target.transformation_matrix
        return f'{r.x0:.8f} {r.y0:.8f} {r.width:.8f} {r.height:.8f} re\n'.encode('ascii')

    with fitz.open() as rendered, fitz.open() as original:
        for clean, page, erased in ((rendered, target, [rect]), (original, source, complement)):
            clean.insert_pdf(page.parent, from_page=page.number, to_page=page.number,
                             annots=False, links=False)
            if not _explicit_text_matrices(clean[0]):
                return False
            for box in erased:
                clean[0].add_redact_annot(box, fill=False, cross_out=False)
            # Clipping alone leaves searchable text behind. Erase only text on
            # disposable copies; vector/image paint is bounded by the final clip.
            clean[0].apply_redactions(images=0, graphics=0, text=0)
        doc = target.parent
        # Detach instead of overwriting streams that another page may share.
        xref = doc.get_new_xref()
        doc.update_object(xref, '<<>>')
        doc.update_stream(xref, b'')
        target.set_contents(xref)
        for clean, path in ((rendered, rectangle(bounds) + rectangle(rect) + b'W* n\n'),
                            (original, rectangle(rect) + b'W n\n')):
            if clean[0].get_contents():
                target.show_pdf_page(bounds, clean, 0)
                xref = target.get_contents()[-1]
                doc.update_stream(xref, b'q\n' + path + doc.xref_stream(xref) + b'\nQ')
    return True
