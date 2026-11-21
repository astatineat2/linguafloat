"""Clip source artwork without importing a second copy of the surrounding prose."""
import pymupdf as f


def clip_text(page, keep):
    bounds = page.rect
    if keep.is_empty or not bounds.contains(keep):
        raise ValueError('Invalid restored region')
    # Preserve complete glyphs crossing the visible edge; artwork is never redacted.
    safe = f.Rect(keep)
    glyphs = [f.Rect(c['bbox']) for block in page.get_text('rawdict',flags=0)['blocks']
              for line in block.get('lines',()) for span in line['spans'] for c in span['chars']]
    for glyph in glyphs:
        if glyph.intersects(keep): safe |= glyph
    if not any(not safe.intersects(g) for g in glyphs): return
    for rect in (f.Rect(bounds.x0,bounds.y0,bounds.x1,safe.y0-.02),
                 f.Rect(bounds.x0,safe.y1+.02,bounds.x1,bounds.y1),
                 f.Rect(bounds.x0,safe.y0,safe.x0-.02,safe.y1),
                 f.Rect(safe.x1+.02,safe.y0,bounds.x1,safe.y1)):
        if not rect.is_empty: page.add_redact_annot(rect,fill=False,cross_out=False)
    page.apply_redactions(images=0,graphics=0,text=0)


def _glyphs(page, rect):
    return [(c['c'], c['origin'], c['bbox'])
            for b in page.get_text('rawdict', flags=0, clip=rect)['blocks']
            for line in b.get('lines', ()) for span in line['spans']
            for c in span['chars'] if not c['c'].isspace()]


def _same_glyphs(before, after):
    return len(before) == len(after) and all(
        a == b and all(abs(x-y) <= .02 for x, y in zip(p+r, q+s))
        for (a, p, r), (b, q, s) in zip(before, after))


def _explicit_text_origins(data, *, strict=False):
    # BT already resets both text matrices. Spelling that out prevents MuPDF's
    # redactor from carrying a removed text object's advance into the next one.
    if b'BT' not in data and not strict:
        return data
    m = f.mupdf
    stream = m.fz_open_buffer(m.fz_new_buffer_from_copied_data(data))
    token = m.PdfLexbuf(256)
    chunks = []
    start = 0
    while (kind := m.pdf_lex(stream, token)) != m.PDF_TOK_EOF:
        if kind == m.PDF_TOK_ERROR:
            if strict: return None
            break
        if kind != m.PDF_TOK_KEYWORD or token.m_internal.len != 2:
            continue
        end = stream.fz_tell()
        op = data[end-2:end]
        if op == b'BI':  # Do not tokenize inline image bytes.
            if strict: return None
            break
        if op == b'BT':
            chunks.extend((data[start:end], b' 1 0 0 1 0 0 Tm '))
            start = end
    return b''.join([*chunks, data[start:]]) if chunks else data


def prepare_text_redaction(page, *, strict=False):
    streams = set(page.get_contents()) | {x[0] for x in page.get_xobjects()}
    edits = {}
    for xref in streams:
        data = page.parent.xref_stream(xref)
        fixed = _explicit_text_origins(data, strict=strict)
        if fixed is None:
            return False
        if fixed != data:
            edits[xref] = fixed
    for xref, fixed in edits.items():
        page.parent.update_stream(xref, fixed)
    return True


def show_source_region(target, rect, source, index, *, clip=None, rotate=0):
    keep = rect if clip is None else clip
    for retry in (False, True):
        with f.open() as local:
            local.insert_pdf(source, from_page=index, to_page=index, links=False, annots=False)
            page = local[0]
            if page.rotation:
                page.remove_rotation()
            expected = _glyphs(page, keep)
            if retry:
                prepare_text_redaction(page)
            clip_text(page, keep)
            if _same_glyphs(expected, _glyphs(page, keep)):
                target.show_pdf_page(rect, local, 0, clip=keep, rotate=rotate)
                return
    raise ValueError('Source clipping changed retained glyphs; region was not committed')
