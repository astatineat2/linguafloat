"""Local replacement of a verified, contiguous BabelDOC glyph run.

This deliberately recognizes only isolated, explicitly positioned text shows;
it is not a general PDF content interpreter. Unknown operators fail closed.
"""
import re


_N = rb'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
_RUN = re.compile(rb'(?<!\S)q\s*(?:/Span\s*<<\s*/ActualText\s*<[0-9A-Fa-f]+>\s*>>\s*BDC\s*)?'
                  rb'(?:(?:'+_N+rb'\s+){1,4}(?:g|rg|k)\s+)?BT\s*/[\w.-]+\s+'+_N+rb'\s+Tf\s+'
                  rb'(?:'+_N+rb'\s+){4}(?P<x>'+_N+rb')\s+(?P<y>'+_N+rb')\s+Tm\s*'
                  rb'(?:<[0-9A-Fa-f]+>|\((?:[^()\\]|\\.)*\))\s*Tj\s+ET\s+Q(?:\s+EMC)?(?=\s|$)')


def glyph_slice(stream, bounds):
    """Return byte offsets in an untransformed engine stream (PDF coordinates)."""
    x0, y0, x1, y1 = bounds
    hits = [m for m in _RUN.finditer(stream) if x0 <= float(m['x']) <= x1 and y0 <= float(m['y']) <= y1]
    if not hits or any(stream[a.end():b.start()].strip() for a,b in zip(hits, hits[1:])):
        raise ValueError('Target is not a contiguous, isolated glyph run')
    return hits[0].start(), hits[-1].end()


def trim_hidden_runs(doc,page,owner,bounds):
    """Remove local caption duplicates from identity-mapped, disjoint clip forms.

    Other forms, unsupported transforms and text mixed with artwork stay intact.
    """
    import pymupdf as f
    x0,y0,x1,y1=bounds
    forms=page.get_xobjects(); parents={owner}; count=0
    direct={x for x,_,p,b in forms if p in parents and not f.Rect(b).intersects(bounds)
            and doc.xref_get_key(x,'Matrix')[1].replace(' ','')=='[100100]'}
    group=re.compile(rb'(?<!\S)q ('+_N+rb') 0 0 ('+_N+rb') ('+_N+rb') ('+_N+rb') cm 0 g BT ((?:(?!\b(?:BT|ET|Do|cm)\b).)+?) ET Q',re.S)
    for x,_,p,_ in forms:
        if p not in direct or doc.xref_get_key(x,'Matrix')[1].replace(' ','')!='[100100]': continue
        s=doc.xref_stream(x)
        def remove(m):
            nonlocal count
            sx,sy,tx,ty=map(float,m.groups()[:4]); text=m[5]
            if not(x0<=tx<=x1 and y0<=ty<=y1 and sx==sy and sx>0): return m[0]
            points=re.findall(rb'1 0 0 1 ('+_N+rb') ('+_N+rb') Tm',text)
            if (not points or len(points)!=text.count(b'Tm') or re.search(rb'\b(?:Td|TD|T\*)\b',text)
                    or not all(x0<=tx+float(a)*sx<=x1 and y0<=ty+float(b)*sy<=y1 for a,b in points)):
                return m[0]
            count+=1
            return b'q Q'
        cleaned=group.sub(remove,s)
        if cleaned!=s:doc.update_stream(x,cleaned)
    return count


def graft_run(doc, page_number, stream, owner, donor, donor_page, bounds):
    """Graft a run with its own font resources, in the original reading position.

    owner/stream must have been matched to the translated panel, not the source.
    Caller verifies copied text, page links and unmodified pixels before delivery.
    """
    import pymupdf as f
    original = doc.xref_stream(stream)
    a,b = glyph_slice(original,bounds)
    with f.open() as tmp:
        tmp.insert_pdf(donor, from_page=donor_page, to_page=donor_page, links=False, annots=False)
        dp = tmp[0]
        content = dp.read_contents()
        c,e = glyph_slice(content, bounds)
        x = tmp.get_new_xref(); tmp.update_object(x,'<<>>'); tmp.update_stream(x,content[c:e]); dp.set_contents(x)
        stash = doc.new_page(width=dp.rect.width,height=dp.rect.height)
        form = stash.show_pdf_page(stash.rect,tmp,0)
        doc.delete_page(stash.number)
    name = 'LFVerifiedRun'
    typ, resources = doc.xref_get_key(owner, 'Resources')
    root, prefix = (int(resources.split()[0]),'') if typ=='xref' else (owner,'Resources/')
    typ, objects = doc.xref_get_key(root,prefix+'XObject')
    if typ=='xref':
        root, prefix = int(objects.split()[0]), ''
    else:
        prefix += 'XObject/'
    if doc.xref_get_key(root,prefix+name)[0]!='null':
        raise ValueError('Unexpected resource collision')
    doc.xref_set_key(root,prefix+name,f'{form} 0 R')
    doc.update_stream(stream,original[:a]+f'q /{name} Do Q'.encode()+original[b:])
