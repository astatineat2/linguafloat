"""Expose native text inside full-page Form wrappers without repainting glyphs."""
import re


# Only inline containers whose entire program is geometry, clipping and Do.
# Unknown operators, marked content, strings and inline images stay untouched.
_NAME = rb'/([A-Za-z0-9_]+)\s+Do\b'
_CALL = re.compile(_NAME)
_GEOMETRY = re.compile(rb'(?:\s++|[-+]?(?:\d+\.?\d*|\.\d+)(?=[\s/]|$)|(?:q|Q|re|cm|W\*?|n)(?=[\s/]|$)|' + _NAME + rb')*+')


def promote_panel_text(page):
    import pymupdf as f
    doc = page.parent
    program = page.read_contents()
    resources = doc.xref_get_key(page.xref, 'Resources')
    owner = page.xref
    extra = {}
    def text_weight(ref, seen):
        if ref in seen or len(seen) >= 24: return 0
        data = doc.xref_stream(ref)
        if not _GEOMETRY.fullmatch(data): return data.count(b'BT')
        result = 0
        for call in _CALL.finditer(data):
            kind, value = doc.xref_get_key(ref, 'Resources/XObject/'+call[1].decode())
            if kind == 'xref':
                child = int(value.split()[0])
                if doc.xref_get_key(child, 'Subtype')[1] == '/Form':
                    result = max(result,text_weight(child, seen | {ref}))
        return result

    depth = 0
    while depth < 24 and _GEOMETRY.fullmatch(program):
        calls = list(_CALL.finditer(program))
        candidates = []
        resolved = {}
        for match in calls:
            name = match[1].decode()
            if name in extra:
                ref = extra[name]
            else:
                typ, value = doc.xref_get_key(owner, 'Resources/XObject/' + name)
                if typ != 'xref': return 0
                ref = int(value.split()[0])
            resolved[name] = ref
            if doc.xref_get_key(ref, 'Subtype')[1] != '/Form': continue
            if set(doc.xref_get_keys(ref)) - {'Type','Subtype','BBox','Matrix','Resources','Length','Filter','DecodeParms','FormType'}: continue
            box = doc.xref_get_key(ref,'BBox')[1]
            if not box.startswith('['): continue
            x,y,r,t = map(float, box[1:-1].split())
            m = doc.xref_get_key(ref,'Matrix')[1]
            vals = list(map(float,m[1:-1].split())) if m.startswith('[') else [1,0,0,1,0,0]
            # Do not promote thumbnails, scaled labels or small restorations.
            if vals[:4] != [1,0,0,1] or abs(r-x-page.rect.width)>.1 or abs(t-y-page.rect.height)>.1: continue
            data = doc.xref_stream(ref)
            candidates.append((text_weight(ref,set()), match.start(), ref, vals, (x,y,r-x,t-y), data))
        if not candidates: break
        _, start, ref, matrix, rect, data = max(candidates, key=lambda c:c[:2])
        new_resources = doc.xref_get_key(ref, 'Resources')
        if new_resources[0] not in ('dict','xref'): break
        new_extra = {}
        def expand(match):
            if match.start() == start:
                nums = lambda v: ' '.join(format(k,'.12f').rstrip('0').rstrip('.') or '0' for k in v).encode()
                return b'q '+nums(matrix)+b' cm '+nums(rect)+b' re W n\n'+data+b'\nQ'
            sibling = resolved[match[1].decode()]
            alias = 'LFPanel'+str(sibling)
            if doc.xref_get_key(ref, 'Resources/XObject/'+alias)[0] != 'null':
                raise ValueError('Conflicting panel resource alias')
            new_extra[alias] = sibling
            return ('/'+alias+' Do').encode()
        program = _CALL.sub(expand, program)
        owner, resources, extra = ref, new_resources, new_extra
        depth += 1
    if not depth or not re.search(rb'(?<![A-Za-z])BT(?![A-Za-z])',program): return 0
    res = doc.get_new_xref()
    doc.update_object(res, resources[1] if resources[0]=='dict' else doc.xref_object(int(resources[1].split()[0])))
    if extra:
        kind, value = doc.xref_get_key(res,'XObject')
        if kind == 'xref': doc.xref_set_key(res,'XObject',doc.xref_object(int(value.split()[0])))
        for alias, sibling in extra.items(): doc.xref_set_key(res,'XObject/'+alias,f'{sibling} 0 R')
    stream = doc.get_new_xref(); doc.update_object(stream,'<<>>');doc.update_stream(stream, program)
    doc.xref_set_key(page.xref, 'Resources',f'{res} 0 R');page.set_contents(stream)
    return depth


def promote_verified_panel(page):
    """Keep the original content/resources unless the rewritten page is equivalent."""
    doc = page.parent
    if page.rotation or doc.is_form_pdf or doc.get_ocgs() or page.first_annot:
        return False
    program = page.read_contents()
    if not _CALL.search(program) or not _GEOMETRY.fullmatch(program):
        return False
    keys = {k:doc.xref_get_key(page.xref,k)[1] for k in ('Resources','Contents')}
    initial = (page.get_text('words'), page.get_pixmap().digest)
    keep = False
    try:
        keep = bool(promote_panel_text(page)) and initial == (page.get_text('words'), page.get_pixmap().digest)
        return keep
    except (RuntimeError, ValueError, OverflowError):
        return False
    finally:
        if not keep:
            for key, value in keys.items(): doc.xref_set_key(page.xref, key, value)
