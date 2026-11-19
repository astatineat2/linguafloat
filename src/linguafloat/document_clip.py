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


def show_source_region(target, rect, source, index):
    with f.open() as local:
        local.insert_pdf(source,from_page=index,to_page=index,links=False,annots=False)
        if local[0].rotation:local[0].remove_rotation()
        clip_text(local[0],rect)
        target.show_pdf_page(rect,local,0,clip=rect)
