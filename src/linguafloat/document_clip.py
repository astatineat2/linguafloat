"""Clip source artwork without importing a second copy of the surrounding prose."""
import re
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


_SPACING_SPAN = re.compile(rb'/Span\s*<<\s*/ActualText\s*<(?i:FEFF(?:200[0-AB]|202F|0020|00A0)+)>\s*>>\s*BDC\Z')
_TEXT_STATE = {b'BT', b'ET', b'Tf', b'Tm', b'Td', b'TD', b'T*', b'Tc', b'Tw', b'Tz', b'TL', b'Tr', b'Ts'}


def _strip_orphan_spacing(data):
    if b'/ActualText' not in data:
        return data
    m = f.mupdf
    stream = m.fz_open_buffer(m.fz_new_buffer_from_copied_data(data))
    token = m.PdfLexbuf(256)
    edits = []
    previous = 0
    pending = None
    while (kind := m.pdf_lex(stream, token)) != m.PDF_TOK_EOF:
        if kind == m.PDF_TOK_ERROR:
            return data
        if kind != m.PDF_TOK_KEYWORD:
            continue
        end = stream.fz_tell()
        op = data[end-token.m_internal.len:end]
        if op == b'BI':  # Never interpret inline image bytes as operators.
            return data
        if op == b'BDC':
            match = _SPACING_SPAN.search(data, previous, end)
            pending = match.span() if match else None
        elif op == b'EMC' and pending is not None:
            edits.append(pending)
            pending = None
        elif op not in _TEXT_STATE:
            pending = None
        previous = end
    if not edits:
        return data
    chunks, start = [], 0
    for begin, end in edits:
        chunks.extend((data[start:begin], b'/Span BMC '))
        start = end
    return b''.join([*chunks, data[start:]])


def show_source_region(target, rect, source, index, *, clip=None, rotate=0):
    keep = rect if clip is None else clip
    for retry in (0, 1, 2):
        with f.open() as local:
            local.insert_pdf(source, from_page=index, to_page=index, links=False, annots=False)
            page = local[0]
            if page.rotation:
                page.remove_rotation()
            expected = _glyphs(page, keep)
            if retry:
                prepare_text_redaction(page)
            # Keep redaction away from a neighboring ligature's text state.
            # The displayed clip is unchanged. Verify the resulting standalone
            # panel too, so invisible neighboring text is not imported.
            margin=min(3.,keep.height/3) if retry==2 else 0.
            clip_text(page,(keep+(-margin,-margin,margin,margin)) & page.rect)
            actual = _glyphs(page, keep)
            if not _same_glyphs(expected, actual):
                # Redaction may leave an empty marked span attached to the last
                # surviving glyph. Keep all text-state operators and live spans.
                for xref in set(page.get_contents()) | {x[0] for x in page.get_xobjects()}:
                    data = local.xref_stream(xref)
                    fixed = _strip_orphan_spacing(data)
                    if fixed != data:
                        local.update_stream(xref, fixed)
                actual = _glyphs(page, keep)
            if _same_glyphs(expected, actual):
                if margin:
                    with f.open() as check:
                        p=check.new_page(width=page.rect.width,height=page.rect.height)
                        p.show_pdf_page(keep,local,0,clip=keep)
                        src=source[index];opt=dict(clip=keep,matrix=f.Matrix(2,2),annots=False)
                        if (src.rotation or p.get_text().split()!=src.get_text(clip=keep).split()
                                or src.get_pixmap(**opt).samples!=p.get_pixmap(**opt).samples):
                            continue
                target.show_pdf_page(rect, local, 0, clip=keep, rotate=rotate)
                return
    from linguafloat.document_core import DocumentError
    raise DocumentError(f'[原文区域恢复失败] 原始第 {index + 1} 页：裁切后的复制文字或字形坐标与原文不一致，未发布不完整结果。')
