"""Recover a misencoded overprinted diaeresis only from a certified font outline."""
from collections import defaultdict
from functools import lru_cache
import io
import re
import unicodedata

import pymupdf as pdf


@lru_cache(maxsize=16)
def _two_dots(data,gid):
    from fontTools.cffLib import CFFFontSet
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.pens.boundsPen import BoundsPen
    c=CFFFontSet();c.decompile(io.BytesIO(data),None);top=c[c.fontNames[0]];pen=RecordingPen()
    top.CharStrings[top.charset[gid]].draw(pen);boxes=[];bounds=None
    for op,args in pen.value:
        if op=='moveTo':bounds=BoundsPen(None)
        if bounds is None or op not in ('moveTo','curveTo','lineTo','closePath'):return False
        getattr(bounds,op)(*args)
        if op=='closePath':boxes.append(bounds.bounds);bounds=None
    if len(boxes)!=2 or not all(boxes):return False
    (x,y,r,t),(a,b,u,v)=sorted(boxes);h=t-y
    return h>0 and y>3*h and .65*h<r-x<1.4*h and .65*h<u-a<1.4*h and .5*h<a-r<2*h and max(abs(y-b),abs(t-v))<.12*h


def _lines(page):
    return [[(ch,s) for s in l['spans'] for ch in s['chars']] for b in page.get_text('rawdict',flags=0)['blocks'] for l in b.get('lines',[]) if l['dir']==(1,0)]


def source_words(page):
    if '€' not in page.get_text():return {}
    traces={(s['font'],tuple(origin)):gid for s in page.get_texttrace() for char,gid,origin,_ in s['chars'] if char==8364}
    fonts=defaultdict(set)
    for x,ext,_,name,*_ in page.get_fonts():
        if ext=='cff':fonts[name.split('+')[-1]].add(x)
    found={}
    for line in _lines(page):
        text=''.join(c['c'] for c,_ in line)
        for m in re.finditer(r'[A-Za-z]*€[A-Za-z€]+',text):
            token=m[0];cs=line[m.start():m.end()];good=True
            for i,ch in enumerate(token):
                if ch!='€':continue
                if i+1==len(token) or token[i+1] not in 'aeiouyAEIOUY':good=False;break
                mark,span=cs[i];base=cs[i+1][0];bb=pdf.Rect(base['bbox']);mb=pdf.Rect(mark['bbox'])
                if not bb.contains(pdf.Point((mb.x0+mb.x1)/2,(bb.y0+bb.y1)/2)):good=False;break
                xs=fonts[span['font']];gid=traces.get((span['font'],tuple(mark['origin'])))
                if len(xs)!=1 or gid is None:good=False;break
                try:ok=_two_dots(page.parent.extract_font(next(iter(xs)))[3],gid)
                except (ValueError,IndexError,KeyError,AssertionError):ok=False
                if not ok:good=False;break
            if good:found[token]=unicodedata.normalize('NFC',re.sub('€(.)',r'\1'+chr(776),token))
    return found


# Match only the layout engine's independently positioned, single glyph text objects.
# Each replacement also needs a unique extracted origin and the original font resource.
_NUM=rb'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
_OP=re.compile(rb'\bBT\s*/([^\s/]+)\s+('+_NUM+rb')\s+Tf\s+1\s+0\s+0\s+1\s+('+_NUM+rb')\s+('+_NUM+rb')\s+Tm\s*(?:<[0-9a-fA-F]{2,8}>|\((?:\\.|[^\\()]){1,4}\))\s*Tj\s+ET\b')


def repair_typeset_words(source,page,*,exclude=()):
    if page.rotation or page.rect!=source.rect or page.cropbox.tl!=pdf.Point(0,0):return 0
    words=source_words(source)
    if not words or '€' not in page.get_text():return 0
    lines=_lines(page);chars=[(c,s,i) for i,l in enumerate(lines) for c,s in l];text=''.join(c['c'] for c,_,_ in chars)
    fonts={v[4]:v[3].split('+')[-1] for v in page.get_fonts()};ops=defaultdict(list);edits=defaultdict(dict);streams={}
    def key(x,y):return round(x,2),round(y,2)
    for x in page.get_contents():
        buf=page.parent.xref_stream(x);streams[x]=buf
        if b' BI ' in buf:continue
        for op in _OP.finditer(buf):ops[key(float(op[3]),float(op[4]))].append((x,op))
    used=0;face=pdf.Font('tiro'); resource='LFVerifiedAccent'
    if resource in fonts:return 0
    for m in re.finditer('|'.join(map(re.escape,sorted(words,key=len,reverse=True))),text):
        cs=chars[m.start():m.end()]; first,last=cs[0][2],cs[-1][2]; dest=[c for c in cs if c[2]==last];anchor=dest[0][0]; end=dest[-1][0]; fs=dest[-1][1]['size']
        if any(pdf.Rect(c['bbox']).intersects(box) for c,_,_ in cs for box in exclude):continue
        if last-first>1 or (last!=first and not 0<end['origin'][1]-cs[0][0]['origin'][1]<fs*2.5):continue
        if m.end()<len(chars) and chars[m.end()][2]==last and re.match('[A-Za-z]',text[m.end()]):continue
        group=[]
        for c,s,_ in cs:
            pos=pdf.Point(c['origin'])*~page.transformation_matrix
            hits=[(x,o) for x,o in ops[key(*pos)] if re.sub('[^a-z0-9]','',fonts.get(o[1].decode(),'').lower())==re.sub('[^a-z0-9]','',s['font'].lower())]
            if len(hits)!=1:break
            group.append(hits[0])
        if len(group)!=len(cs) or len(set((x,o.start()) for x,o in group))!=len(group):continue
        word=words[m[0]];width=end['bbox'][2]-anchor['origin'][0];scale=width/face.text_length(word,fontsize=fs)
        if not .65<=scale<=1.55:continue
        pdfpos=pdf.Point(anchor['origin'])*~page.transformation_matrix
        encoded=''.join(f'{face.has_glyph(ord(ch)):04x}' for ch in word)
        paint=f'BT /{resource} {fs:.6f} Tf {scale:.7f} 0 0 1 {pdfpos.x:.6f} {pdfpos.y:.6f} Tm <{encoded}> Tj ET'.encode()
        for j,(x,o) in enumerate(group):edits[x][o.span()]=paint if j==len(cs)-len(dest) else b''
        used+=1
    if used:
        page.insert_font(fontname=resource,fontbuffer=face.buffer)
        for x,changes in edits.items():
            buf=streams[x]
            for (a,b),val in sorted(changes.items(),reverse=True):buf=buf[:a]+val+buf[b:]
            # Do not overwrite a content stream shared with another page.
            new=page.parent.get_new_xref();page.parent.update_object(new,'<<>>');page.parent.update_stream(new,buf)
            seq=[new if item==x else item for item in page.get_contents()]
            page.parent.xref_set_key(page.xref,'Contents','['+' '.join(f'{i} 0 R' for i in seq)+']')
    return used
