"""Source evidence for two cases where trusting the PDF's extracted prose is unsafe."""
import hashlib
import io
import re

import pymupdf as pdf


def cropped_bibliography(page):
    """A terminal, numbered bibliography split across columns on an offset page."""
    from linguafloat.document_references import AUTHOR, NUMBER, _digital_lines, is_heading
    if page.rotation or not (page.cropbox.x0 or page.cropbox.y0) or page.number != len(page.parent)-1:
        return None
    lines=_digital_lines(page); w,h=page.rect.width,page.rect.height
    heads=[r for r in lines if is_heading(r['text']) and r['horizontal'] and r['box'][2]<w/2]
    if len(heads)!=1:return None
    head=heads[0]; columns=[]; numbers=[]
    for right in (False,True):
        rows=sorted((r for r in lines if (r['box'][0]>=w/2)==right and r['box'][1]<h*.93),key=lambda r:r['box'][1])
        starts=[i for i,r in enumerate(rows) if NUMBER.match(r['text']) and (right or r['box'][1]>head['box'][3])]
        if len(starts)<3:return None
        first=starts[0]; body=rows[first:]; box=pdf.Rect(body[0]['box']); entries=[]
        for r in body:
            text=r['text'].strip();b=pdf.Rect(r['box']);m=NUMBER.match(text)
            if not r['horizontal'] or (not right and b.x1>=w/2):return None
            if m:
                from linguafloat.document_references import LABEL
                if not AUTHOR.match(text[LABEL.match(text).end():]):return None
                numbers.append(int(next(g for g in m.groups() if g)));entries.append(text)
            else:
                if (not entries or b.x0<body[0]['box'][0]+3 or abs(r['size']-body[0]['size'])>1):return None
                entries[-1]+=' '+text
            box|=b
        if not all(re.search(r'\((?:19|20)\d\d\)',e) and re.search(r'\d+\s*[–-]\s*\d+|,\s*e\d+$',e) for e in entries):return None
        if not right:
            if first==0 or rows[first-1] is not head:return None
            box|=pdf.Rect(head['box'])
        # The guard bands must be empty of unrelated source text. They leave
        # room for type ascenders and for a reflowed final citation line.
        clip=(box+(-2,-2,2,body[-1]['size']*2)) & page.rect
        if any(clip.intersects(pdf.Rect(r['box'])) for r in lines if r not in body and (right or r is not head)):return None
        columns.append(clip)
    if numbers!=list(range(1,len(numbers)+1)) or columns[0].intersects(columns[1]):return None
    i=page.number; boxes=[list(b) for b in columns]; il=[list(b*~page.transformation_matrix) for b in columns]
    return dict(page=i,y=head['box'][1],title=head['text'],column='both',scanned=False,boxes=boxes,
                page_boxes={str(i):boxes},pdf_boxes={str(i):il},whole_pages=[],uncertain_pages=[],
                evidence='cropped-terminal-numbered-columns',policy='bibliography-only-v3')


# Verified outlines, not the misleading glyph names (comma / period).
_ANGLES={'23fe3853f2e51be09c93eb4b683262f6414e864ffdcf03e7efd258f8ce1c6150':('comma','002C','003C'),
         'b8429ad80ae9c8c316378c48e69d8a72cde8655489201fe77f9e2856088e800a':('period','002E','003E')}

_INCLUSIVE={'percent':'6e1177f73071b14b76a26b57ed275e5ca71589c6feaac72445fc206575cbe94b',
            'R':'f2a908ebf8aefe3d61bdaba88605d03d79536cc6fb49082ce75a5a74c6eb471c'}

# Verified painted outlines, not a mapping for glyph names across fonts.
_GREEK={
    'H9251':('56cefd3cb391e62a6ddde4f2b4e396428bffade1a2bc0a530065b342e74e5ae2','03b1'),
    'H9252':('08bb11473837c7c3b65ed4e9773137c224b2032976295be3e14876b862d240d0','03b2'),
    'H9262':('149b856dacd9c88f8cbbcb8f980a5316a9e26a512667426c663ffc32c1a3e3c1','03bc'),
    'H9254':('ce4b3af0977b9c8db026c06ac4a1ddaffc3b7039d6b717f5c6b9c1454493a509','03b4'),
    'dotlessi':('f0e05a9104d9d636b9111bdf7f1a5dcecc63ae51bf8cecc69cad88dcf0a8a102','03b4'),
    'caron':('a83bba70500e21eeea4cf168ec8abca70f0367d33162c08d86163d49845c08ce','03b2'),
}


def operator_maps(doc):
    """Return private ToUnicode replacements, never edits to shared font streams."""
    from fontTools.cffLib import CFFFontSet
    from fontTools.pens.recordingPen import RecordingPen
    out={}
    for x,ext,kind,*_ in {f for page in doc for f in page.get_fonts()}:
        if ext!='cff' or kind!='Type1':continue
        typ,ref=doc.xref_get_key(x,'ToUnicode'); et,enc=doc.xref_get_key(x,'Encoding')
        if et=='xref':
            diff=doc.xref_get_key(int(enc.split()[0]),'Differences')[1]
            if re.search(r'/(?:H9251|dotlessi)\b',diff):
                c=CFFFontSet();c.decompile(io.BytesIO(doc.extract_font(x)[-1]),None);top=c[c.fontNames[0]]
                fields={};code=None
                for tok in re.findall(r'/[\w.]+|\d+',diff):
                    if tok.isdigit():code=int(tok);continue
                    if code is None or code>255:break
                    name=tok[1:]; item=_GREEK.get(name)
                    if item and name in top.CharStrings:
                        pen=RecordingPen();top.CharStrings[name].draw(pen)
                        if hashlib.sha256(repr(pen.value).encode()).hexdigest()==item[0]:fields[code]=(name,item[1])
                    code+=1
                if typ=='xref' and fields:
                    cmap=doc.xref_stream(int(ref.split()[0])); new=cmap
                    for k,(name,value) in fields.items():
                        old={'dotlessi':'0131','caron':'02c7'}.get(name)
                        if old:new=re.sub(rb'(<%02x>\s*)<%s>'%(k,old.encode()),lambda m:m[1]+b'<'+value.encode()+b'>',new,flags=re.I)
                    if new!=cmap:out[x]=new
                elif (typ=='null' and fields and set(top.charset)=={'.notdef',*(n for n,_ in fields.values())}
                      and re.fullmatch(r'\[\s*\d+\s*(?:/H\d+\s*)+\]',diff) and len(fields)==diff.count('/')):
                    pairs=' '.join(f'<{k:02x}> <{v}>' for k,(_,v) in fields.items())
                    out[x]=('/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo '
                            '<< /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /VerifiedGreek def '
                            '/CMapType 2 def 1 begincodespacerange <00> <ff> endcodespacerange '
                            f'{len(fields)} beginbfchar {pairs} endbfchar endcmap CMapName currentdict /CMap defineresource pop end end').encode()
                continue
        if typ=='null' and (et,enc)==('name','/WinAnsiEncoding'):
            # Never invent a partial mapping for a text face. Match the entire
            # two-glyph repertoire, its encoding, and both painted outlines.
            if doc.xref_get_key(x,'FirstChar')[1]!='37' or doc.xref_get_key(x,'LastChar')[1]!='82':continue
            c=CFFFontSet();c.decompile(io.BytesIO(doc.extract_font(x)[-1]),None);top=c[c.fontNames[0]]
            if set(top.charset)!={'.notdef',*_INCLUSIVE}:continue
            for name,digest in _INCLUSIVE.items():
                pen=RecordingPen();top.CharStrings[name].draw(pen)
                if hashlib.sha256(repr(pen.value).encode()).hexdigest()!=digest:break
            else:
                out[x]=(b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap '
                        b'/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def '
                        b'/CMapName /VerifiedOperators def /CMapType 2 def '
                        b'1 begincodespacerange <00> <ff> endcodespacerange '
                        b'2 beginbfchar <25> <2264> <52> <2265> endbfchar '
                        b'endcmap CMapName currentdict /CMap defineresource pop end end')
            continue
        if typ!='xref' or et!='xref':continue
        diff=doc.xref_get_key(int(enc.split()[0]),'Differences')[1]
        if not re.fullmatch(r'\[\s*\d+\s*/comma\s+\d+\s*/period\s*\]',diff):continue
        cmap=doc.xref_stream(int(ref.split()[0])); new=cmap
        c=CFFFontSet();c.decompile(io.BytesIO(doc.extract_font(x)[-1]),None); top=c[c.fontNames[0]]
        for code,name in re.findall(r'(\d+)\s*/(comma|period)',diff):
            if name not in top.CharStrings:continue
            pen=RecordingPen();top.CharStrings[name].draw(pen)
            item=_ANGLES.get(hashlib.sha256(repr(pen.value).encode()).hexdigest())
            if not item or item[0]!=name:continue
            pattern=rb'(<%02x>\s*)<%s>'%(int(code),item[1].encode());new=re.sub(pattern,lambda m:m[1]+b'<'+item[2].encode()+b'>',new,flags=re.I)
        if new!=cmap:out[x]=new
    return out


def apply_operator_maps(doc,maps):
    for font,data in maps.items():
        x=doc.get_new_xref();doc.update_object(x,'<<>>');doc.update_stream(x,data)
        doc.xref_set_key(font,'ToUnicode',f'{x} 0 R')


def restore_columns(target,source,boxes,offset=(0,0)):
    from linguafloat.document_clip import prepare_text_redaction,show_source_region
    from linguafloat.document_core import DocumentError
    areas=[pdf.Rect(b) for b in boxes];dest=[b+(*offset,*offset) for b in areas]
    if not all(source.rect.contains(b) and target.rect.contains(t) for b,t in zip(areas,dest)):
        raise DocumentError('参考文献列超出页面，未恢复。')
    if not prepare_text_redaction(target,strict=True):raise DocumentError('参考文献列无法安全擦除。')
    for t in dest:target.add_redact_annot(t,fill=(1,1,1),cross_out=False)
    target.apply_redactions(images=0,graphics=0,text=0)
    for b,t in zip(areas,dest):
        show_source_region(target,t,source.parent,source.number,clip=b)
        if target.get_textbox(t).split()!=source.get_textbox(b).split():raise DocumentError('参考文献列的复制顺序与原文不同。')
