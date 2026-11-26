"""Externally ruled table notes skipped by the layout engine's vertical-text path."""
import hashlib
import io
import re
from statistics import median

import pymupdf as pdf


def groups(page, areas):
    tables=[pdf.Rect(a['box']) for a in areas if a['kind']=='table']
    if not tables or page.rotation:return []
    lines=[l for b in page.get_text('dict',flags=0)['blocks'] for l in b.get('lines',())
           if tuple(l['dir'])==(0.,-1.)]
    if not lines:return []
    rules=[v['rect'] for v in page.get_drawings() if v['rect'].width<=1.5]
    out=[]
    for grid in tables:
        if not any(abs(r.x1-grid.x1)<2 and r.height>=grid.height*.8 for r in rules):continue
        rows=[];right=grid.x1; start=None
        for line in sorted(lines,key=lambda l:l['bbox'][0]):
            b=pdf.Rect(line['bbox']);size=max(s['size'] for s in line['spans'])
            if b.x0<grid.x1 or b.y1>grid.y1+1 or b.y0<grid.y0-1:continue
            if b.x0-right>size*1.5 or (start is not None and abs(b.y1-start)>size):break
            if any(b.intersects(pdf.Rect(a['box'])) for a in areas):break
            if not re.search('[A-Za-z]{2}', ''.join(s['text'] for s in line['spans'])):break
            rows.append(line);right=b.x1;start=b.y1
        if rows and len(re.findall(r'\b[A-Za-z]{3,}\b',' '.join(s['text'] for r in rows for s in r['spans'])))>=5:out.append(rows)
    return out


# Fingerprints of inspected CFF paths, independent of font subset name, glyph ID,
# document, and page. The same legacy fonts encode visible symbols as letters.
_OUTLINES={'4629a13338940b214fc03efa4abe910b5a0d70fb3e2c33152ef9e6cf09e6301f':'*',
           'd7661253031ceca66ae00aa8d873275c7c8a1797edc7542a32f45c935ee5c759':'<',
           '3f04e23bfb423e24d77f75391edc5300bd4d4652124586c9690d14651b3a951c':'†'}


def source_text(page,rows):
    from fontTools.cffLib import CFFFontSet
    from fontTools.pens.recordingPen import RecordingPen
    fonts={v[3].split('+')[-1]:v[0] for v in page.get_fonts(full=True) if v[1]=='cff'}
    traces=page.get_texttrace();memo={};out=[]
    for line in rows:
        parts=[]
        for span in line['spans']:
            value=span['text']; name=span['font']; key=value.strip()
            if name in fonts and 0<len(key)<=2 and len(set(key))==1:
                b=pdf.Rect(span['bbox']);gids={gid for tr in traces if tr['font']==name
                           for u,gid,origin,_ in tr['chars'] if chr(u)==key[0] and (b+(-.2,-.2,.2,.2)).contains(pdf.Point(origin))}
                if len(gids)==1:
                    gid=gids.pop();ident=(name,gid)
                    if ident not in memo:
                        c=CFFFontSet();c.decompile(io.BytesIO(page.parent.extract_font(fonts[name])[-1]),None)
                        top=c[c.fontNames[0]];pen=RecordingPen();top.CharStrings[top.charset[gid]].draw(pen)
                        memo[ident]=_OUTLINES.get(hashlib.sha256(repr(pen.value).encode()).hexdigest())
                    if memo[ident]:value=value.replace(key[0],memo[ident])
            parts.append(value)
        out.append(''.join(parts))
    return out


def _native_note_tables(page):
    rules=[r['rect'] for r in page.get_drawings() if r['rect'].width<=1.5 and r['rect'].height>80]
    areas=[]
    for block in page.get_text('dict',flags=0)['blocks']:
        for line in block.get('lines',[]):
            if tuple(line['dir'])!=(0.,-1.) or not re.match(r'^Notes?\s*:', ''.join(s['text'] for s in line['spans']),re.I):continue
            b=pdf.Rect(line['bbox']);near=[r for r in rules if 0<=b.x0-r.x1<=5 and abs(b.y1-r.y1)<2 and b.y0>=r.y0-1]
            if not near:continue
            right=max(near,key=lambda r:r.x1);left=[r for r in rules if right.x1-r.x0>40 and abs(r.y1-right.y1)<2 and abs(r.y0-right.y0)<2]
            if left:areas.append(dict(kind='table',box=[min(r.x0 for r in left),right.y0,right.x1,right.y1]))
    return areas


def repair(source,target,areas,translator):
    if source.rotation==target.rotation==90 and not source.cropbox.x0 and not source.cropbox.y0:
        # Never change the input PDF's rotation, even transiently. Native text
        # and drawing coordinates of both pages are unrotated for this pass.
        with pdf.open() as native:
            native.insert_pdf(source.parent,from_page=source.number,to_page=source.number)
            p=native[0];p.set_rotation(0);tables=_native_note_tables(p)
            if not tables:return []
            target.set_rotation(0)
            try:
                records=_repair(p,target,tables,translator,source.number)
                if records or translator.job.target!='zh-Hans':return records
                # Some output streams are already turned into display coordinates.
                # Confirm the complete line, not merely the presence of "Note:".
                from linguafloat.document_inline_publication import write_row
                from linguafloat.document_graphics import validate_graphic_fragment
                from linguafloat.document_references import boundary_intersects
                for i,lines in enumerate(groups(p,tables),1):
                    if len(lines)!=1:continue
                    line=lines[0];src=''.join(s['text'] for s in line['spans']);box=pdf.Rect(line['bbox'])*source.rotation_matrix
                    if not target.rect.contains(box) or (translator.job.skip_references and boundary_intersects(translator.job.reference_boundary,source.number,box)):continue
                    if ''.join(target.get_textbox(box).split())!=''.join(src.split()):continue
                    if any(box.intersects(l['from']) for l in target.get_links()):continue
                    pos=translator.location;translator.location=f'原始第 {source.number+1} 页 · 旋转表注 {i}'
                    try:
                        value=translator.translate_fragment(src,src,part='表外旋转说明',validate=lambda v:validate_graphic_fragment(src,v))
                    finally:translator.location=pos
                    validate_graphic_fragment(src,value)
                    size=median(s['size'] for s in line['spans']);extent=box+(-.4,-.4,.4,.4)
                    if not re.search('[\u3400-\u9fff]',value) or pdf.Font('cjk').text_length(value,fontsize=size)>box.width:continue
                    write_row(target,dict(box=list(box),text=value,baseline=box.y1-size*.36,size=size),clip=extent)
                    records.append(dict(page=source.number+1,stage='旋转表注',source=src,box=list(box),coordinates='top-left',status='translated'))
                return records
            finally:target.set_rotation(90)
    return _repair(source,target,areas,translator,source.number)


def _repair(source,target,areas,translator,number):
    from linguafloat.document_graphics import graphic_font_resolver,validate_graphic_fragment
    from linguafloat.document_core import DocumentError
    from linguafloat.document_failures import diagnostic
    from linguafloat.document_clip import prepare_text_redaction
    from linguafloat.document_references import boundary_intersects
    results=[]; job=translator.job
    if job.target!='zh-Hans' or target.rect!=source.rect:return results
    for idx,rows in enumerate(groups(source,areas),1):
        box=pdf.Rect(rows[0]['bbox'])
        for r in rows[1:]:box|=pdf.Rect(r['bbox'])
        # A CJK face can extend farther above the baseline than the source
        # Latin face. Reserve that space only when no protected area is touched.
        left=min([3.]+[max(0,box.x0-pdf.Rect(a['box']).x1-.1) for a in areas if pdf.Rect(a['box']).x1<=box.x0])
        box=box+(-left,-1,3,1)
        if not source.rect.contains(box) or any(box.intersects(pdf.Rect(a['box'])) for a in areas):continue
        if job.skip_references and boundary_intersects(job.reference_boundary,number,box):continue
        native=''.join(s['text'] for l in rows for s in l['spans']); actual=target.get_textbox(box)
        if ''.join(actual.split())!=''.join(native.split()):continue  # Do not overwrite a translated or reflowed block.
        if any(box.intersects(l['from']) for l in target.get_links()):continue
        prior=translator.location;entry=dict(page=number+1,box=list(box),coordinates='top-left',stage='旋转表注',source=native)
        try:
            texts=source_text(source,rows)
            if any('�' in t or re.search(r'\bP\s+[a-z]\s+0\.',t) for t in texts):raise DocumentError('统计符号字形未核实')
            resolver=graphic_font_resolver(job.chinese_font,job.latin_font)
            with pdf.open() as overlay:
                p=overlay.new_page(width=box.height,height=box.width)
                expected=[]
                translator.location=f'原始第 {number+1} 页 · 旋转表注 {idx}'
                for row,t in zip(rows,texts):
                    m=re.match(r'^([*†‡]*)(?:(P)\s*([<≤≥=>])\s*(\d+(?:\.\d+)?)([.,;:]?)\s*)?',t)
                    prose=t[m.end():];size=median(s['size'] for s in row['spans']);size=max(size,row['spans'][-1]['size'])
                    def validate(v):
                        validate_graphic_fragment(prose,v)
                        if not re.search('[\u3400-\u9fff]',v):raise DocumentError('旋转表注未取得中文')
                    value=translator.translate_fragment(prose,'\n'.join(texts),part='表外旋转说明',validate=validate) if prose.strip() else ''
                    if value:validate(value)
                    y=row['spans'][-1]['origin'][0]-box.x0;x=box.y1-row['bbox'][3]
                    pieces=[(m[1],size*.7,False,y-size*.28),(m[2] or '',size,True,y),
                            ((' '+m[3]+' '+m[4]+m[5]+' ') if m[2] else '',size,False,y),(value,size,False,y)]
                    for text,pt,italic,base in pieces:
                        if not text:continue
                        expected.append(text)
                        if resolver:x+=resolver.draw_line(p,(x,base),text,fontsize=pt,italic=italic)
                        else:
                            font=pdf.Font('tiit' if italic else 'cjk');writer=pdf.TextWriter(p.rect)
                            if any(not font.has_glyph(ord(c)) for c in text if not c.isspace()):raise DocumentError('旋转表注字体缺字')
                            writer.append((x,base),text,font=font,fontsize=pt);writer.write_text(p);x+=font.text_length(text,fontsize=pt)
                        if x>p.rect.width:raise DocumentError('完整表注在原字号下越界')
                if any(not p.rect.contains(pdf.Rect(w[:4])) for w in p.get_text('words')):raise DocumentError('旋转表注字形越界')
                if ''.join(p.get_text().split())!=''.join(''.join(expected).split()):raise DocumentError('旋转表注复制文字不完整')
                prepare_text_redaction(target);target.add_redact_annot(box,fill=False,cross_out=False);target.apply_redactions(images=0,graphics=0,text=0)
                target.show_pdf_page(box,overlay,0,rotate=90);entry['status']='translated'
        except DocumentError as exc:entry.update(diagnostic(exc,'旋转表注'),status='untranslated',reason=str(exc))
        finally:translator.location=prior
        results.append(entry)
    return results
