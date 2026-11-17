"""Source-verified publication dates and author identifiers before PDF assembly.

The BabelDOC result still has direct text operators here. After assembly they
sit inside a full-page Form XObject, where MuPDF redaction can remove the whole
translated page. Only an isolated, verified metadata band is edited here.
"""
from __future__ import annotations

from datetime import datetime
import re
from statistics import median


LABELS = {'Received': '收稿日期：', 'Revised': '修订日期：',
          'Accepted': '接受日期：', 'Published': '发表日期：'}


def split_identifier_links(source):
    """Only source-linked ORCIDs are eligible for translated-suffix cleanup."""
    import pymupdf as f
    result=[]
    for link in source.get_links():
        uri=link.get('uri','')
        if (link.get('kind')==f.LINK_URI and
                re.fullmatch(r'https://orcid\.org/\d{4}-\d{4}-\d{4}-[\dX]{4}',uri,re.I)):
            result.append((uri.removeprefix('https://'),f.Rect(link['from'])))
    return result


def prune_split_identifier_suffixes(page, source_links):
    """Remove source-linked ORCID tails from translated IL before PDF drawing.

    Removing only painted glyphs later leaves BabelDOC's ActualText in a Form
    XObject, so copying the finished PDF still yields the displaced tail.
    This exact-style token check runs for fresh and cached layout IL.
    """
    import pymupdf as f
    height=page.cropbox.box.y2
    removed=0
    for paragraph in page.pdf_paragraph:
        if not paragraph.box or not paragraph.unicode:
            continue
        box=f.Rect(paragraph.box.x,height-paragraph.box.y2,
                   paragraph.box.x2,height-paragraph.box.y)
        for ident, link_box in source_links:
            if (box & f.Rect(link_box.x0-4,link_box.y0-15,
                             link_box.x1+4,link_box.y1+15)).is_empty:
                continue
            parts=list(paragraph.pdf_paragraph_composition)
            for index,part in enumerate(parts):
                item=part.pdf_same_style_unicode_characters
                if not item:
                    continue
                token=(item.unicode or '').strip()
                if (len(token)<4 or not token.isascii() or token==ident or
                        token not in ident or
                        not re.fullmatch(r'[A-Za-z0-9./-]+',token)):
                    continue
                pattern=r"<style id='\d+'>"+re.escape(item.unicode)+r'</style>'
                updated,count=re.subn(pattern,'',paragraph.unicode,count=1)
                if count!=1:
                    continue
                paragraph.unicode=updated
                paragraph.pdf_paragraph_composition.remove(part)
                # The split tail may have a separator on both sides. Keep the
                # source-side separator and remove only the immediately
                # following translated separator and optional style space.
                following=parts[index+1:index+3]
                if (following and following[0].pdf_same_style_unicode_characters
                        and (following[0].pdf_same_style_unicode_characters.unicode or '').strip() in {';', '；'}):
                    for extra in following:
                        value=extra.pdf_same_style_unicode_characters
                        if not value or (value.unicode or '').strip() not in {';', '；', ''}:
                            break
                        tag=r"<style id='\d+'>"+re.escape(value.unicode)+r'</style>'
                        paragraph.unicode,count=re.subn(tag,'',paragraph.unicode,count=1)
                        if count and extra in paragraph.pdf_paragraph_composition:
                            paragraph.pdf_paragraph_composition.remove(extra)
                removed+=1
    return removed


def _rows(page):
    return [dict(text=''.join(s['text'] for s in line['spans']), box=line['bbox'], spans=line['spans'])
            for block in page.get_text('dict')['blocks'] for line in block.get('lines', [])]


def date_rows(source):
    """Accept only an aligned run with full, unambiguous English source dates."""
    rows = _rows(source)
    found = []
    for row in rows:
        match = re.fullmatch(r'(Received|Revised|Accepted|Published):?(?:\s+.*)?',row['text'].strip(),re.I)
        if not match or row['box'][1] < source.rect.height*.65:
            continue
        name=match[1].title()
        peers=sorted((p for p in rows if abs(p['box'][1]-row['box'][1])<1.5
                      and p['box'][0]>=row['box'][0]-.5 and p['box'][0]<source.rect.width*.78),
                     key=lambda p:p['box'][0])
        original=' '.join(p['text'] for p in peers)
        date=re.sub(r'^.*?\b'+name+r'\s*:\s*','',original,count=1,flags=re.I).strip()
        try:value=datetime.strptime(date,'%B %d, %Y')
        except ValueError:continue
        found.append(dict(name=name,date=value,box=row['box'],size=row['spans'][0]['size'],
                          baseline=row['spans'][0]['origin'][1],right=max(p['box'][2] for p in peers)))
    found.sort(key=lambda r:r['box'][1])
    if (len(found)<3 or len({r['name'] for r in found})!=len(found)
            or max(r['box'][0] for r in found)-min(r['box'][0] for r in found)>2
            or any(not 8<=b['box'][1]-a['box'][1]<=17 for a,b in zip(found,found[1:]))):
        return []
    return found


def suppress_date_paragraphs(page, source_rows, page_size):
    """Remove a verified translated metadata paragraph before PDF creation.

    Redaction of the direct page can leave its ActualText in a Form XObject
    when the bilingual page is composed. Source geometry and all field labels
    are required, so arbitrary prose containing a date is never suppressed.
    """
    if len(source_rows)<3:
        return 0
    _,height=page_size
    required={LABELS[row['name']] for row in source_rows}
    labels='|'.join(re.escape(label) for label in required)
    date_pattern=rf'(?:\s*(?:{labels})\s*\d{{4}}年\d{{1,2}}月\d{{1,2}}日\s*)+'
    candidates=[]
    for paragraph in list(page.pdf_paragraph):
        if not paragraph.box or not paragraph.unicode:
            continue
        text=re.sub(r'<[^>]+>','',paragraph.unicode)
        top=height-paragraph.box.y2
        if (not re.fullmatch(date_pattern,text) or
                abs(paragraph.box.x-min(row['box'][0] for row in source_rows))>35 or
                min(abs(top-row['box'][1]) for row in source_rows)>45):
            continue
        candidates.append((paragraph,text))
    if any(sum(text.count(label) for _,text in candidates)!=1 for label in required):
        return 0
    for paragraph,_ in candidates:
        page.pdf_paragraph.remove(paragraph)
    return len(candidates)


def _text_outside(page,clip):
    import pymupdf as f
    return sorted((c['c'],tuple(round(v,1) for v in c['bbox']))
        for block in page.get_text('rawdict')['blocks'] for line in block.get('lines',[])
        for span in line['spans'] for c in span['chars']
        if (f.Rect(c['bbox']).get_area()>.05 and not f.Rect(c['bbox']).intersects(clip)
            and f.Rect(c['bbox']).width>3))


def _draw(resolver,page,point,text,size,color,bold=False):
    resolver.draw_line(page,point,text,fontsize=size,bold=bold,color=color)


def repair_dates(source,page,font_pair):
    import pymupdf as f
    rows=date_rows(source)
    if not rows:return []
    from linguafloat.document_fonts import FitzFontResolver
    resolver=FitzFontResolver(*font_pair)
    size=min(9,max(8,median(r['size'] for r in rows)))
    x=rows[0]['box'][0]
    right=min(source.rect.width*.78,max(r['right'] for r in rows)+35)
    clip=f.Rect(x-1,rows[0]['box'][1]-2,right,rows[-1]['box'][3]+12)
    labels=[(r,f'{r["date"].year}年{r["date"].month}月{r["date"].day}日') for r in rows]
    label_width=max(sum(face.text_length(s,fontsize=size) for s,face in resolver.runs(LABELS[r['name']],bold=True))
                    for r,_ in labels)
    if any(x+label_width+4+sum(face.text_length(s,fontsize=size) for s,face in resolver.runs(value))>right-2
           for _,value in labels):return []
    before=_text_outside(page,clip)
    page.add_redact_annot(clip,fill=(1,1,1),cross_out=False)
    page.apply_redactions(images=0,graphics=0,text=0)
    if _text_outside(page,clip)!=before:
        raise RuntimeError('出版日期修复触及其他正文，已停止输出')
    with f.open() as overlay:
        drawing=overlay.new_page(width=page.rect.width,height=page.rect.height)
        for row,value in labels:
            baseline=row['baseline']
            _draw(resolver,drawing,(x,baseline),LABELS[row['name']],size,(.18,.39,.28),True)
            _draw(resolver,drawing,(x+label_width+4,baseline),value,size,(.05,.05,.05))
        page.show_pdf_page(page.rect,overlay,0)
    return [dict(kind='publication_dates',box=list(clip),count=len(labels),size=size)]


def repair_author_identifiers(source,page,font_pair):
    """Clear split ORCID fragments; defer replacement text until the final page.

    MuPDF does not reliably expose ActualText inside a page Form XObject after
    bilingual composition.  Drawing on the final page keeps the identifier a
    single copyable unit in both outputs.
    """
    import pymupdf as f
    from linguafloat.document_fonts import FitzFontResolver
    resolver=FitzFontResolver(*font_pair)
    def same_row(a,b):
        return abs(a.y0-b.y0)<2 and abs(a.y1-b.y1)<2
    def owned_piece(span, ident):
        token=span['text'].strip()
        if len(token)<4 or not token.isascii():
            return False
        if token in ident:
            return True
        compact=token.replace(' ','')
        return (len(compact)>=4 and compact in ident and
                re.fullmatch(r'[a-z0-9./-]+',compact,re.I) is not None)
    def blue(span):
        color=span['color'] & 0xffffff
        return (color & 255) > ((color>>8)&255)*1.2 and (color & 255)>((color>>16)&255)*1.5
    links=[l for l in source.get_links() if l.get('kind')==f.LINK_URI and l.get('uri')]
    result=[];seen=set()
    for link in links:
        uri=link['uri']
        if not re.fullmatch(r'https://orcid\.org/\d{4}-\d{4}-\d{4}-[\dX]{4}',uri,re.I) or uri in seen:
            continue
        seen.add(uri)
        source_boxes=[f.Rect(l['from']) for l in links if l.get('uri')==uri]
        lo=min(b.y0 for b in source_boxes);hi=max(b.y1 for b in source_boxes)
        ident=uri.removeprefix('https://')
        fragments=[]
        blue_spans=sorted((s for row in _rows(page) for s in row['spans']
                           if lo-13<=s['bbox'][1]<=hi+13 and blue(s)),
                          key=lambda s:(round(s['bbox'][1]),s['bbox'][0]))
        group=[]
        for span in [*blue_spans,None]:
            if (span is not None and (not group or
                    abs(span['bbox'][1]-group[-1]['bbox'][1])<.6 and
                    span['bbox'][0]-group[-1]['bbox'][2]<2)):
                group.append(span)
            else:
                token=''.join(s['text'] for s in group).strip()
                if owned_piece(dict(text=token),ident):
                    fragments.extend(f.Rect(s['bbox']) for s in group)
                group=[span] if span is not None else []
        # A split identifier may be translated into a longer prose paragraph.
        # MuPDF can put its trailing token and the following punctuation in
        # separate spans on the same row.  Remove that verified blue token,
        # and its adjacent punctuation, before drawing the complete address.
        trailing=[]
        for row in _rows(page):
            spans=row['spans']
            for index,span in enumerate(spans):
                box=f.Rect(span['bbox'])
                if (not blue(span) or not owned_piece(span,ident) or
                        not lo-13<=box.y0<=hi+13):
                    continue
                trailing.append(box)
                for neighbor in (spans[index-1:index] + spans[index+1:index+2]):
                    nbox=f.Rect(neighbor['bbox'])
                    if (same_row(box,nbox) and abs(nbox.x0-box.x1)<2.5 and
                            neighbor['text'].strip() in {';', '；', ',', '，'}):
                        trailing.append(nbox)
        fragments.extend(trailing)
        fragments=list(dict.fromkeys(tuple(b) for b in fragments))
        fragments=[f.Rect(b) for b in fragments]
        email=next((l['uri'][7:] for l in links if l.get('uri','').startswith('mailto:')
                    and lo-4<=f.Rect(l['from']).y0<=hi+4),None)
        y=lo+1
        if email:
            match=page.search_for(email)
            if len(match)==1:y=max(y,match[0].y1+2)
        x=max(50,min(b.x0 for b in source_boxes))
        size=9.0
        width=sum(face.text_length(s,fontsize=size) for s,face in resolver.runs(ident))
        x=min(x,max(50,source.rect.width*.5-6-width))
        destination=f.Rect(x,y,x+width+1,y+size+2)
        if destination.x1>source.rect.width*.5-5:
            continue
        # Strictly reject a destination occupied by unrelated words. Prior
        # author fragments may cross it but are removed in this same repair.
        blockers=[f.Rect(s['bbox']) for row in _rows(page) for s in row['spans']
                  if s['text'].strip() and (f.Rect(s['bbox']) & destination).get_area()>.2
                  and not any(f.Rect(s['bbox'])==b for b in fragments)]
        if blockers:
            for delta in (1,2,3,4):
                candidate=destination+(0,delta,0,delta)
                if not any((b & candidate).get_area()>.2 for b in blockers):
                    destination=candidate;break
            else:continue
        before=_text_outside(page,f.Rect(0,lo-13,source.rect.width*.5,hi+17))
        for box in fragments:
            page.add_redact_annot(box,fill=(1,1,1),cross_out=False)
        if fragments:page.apply_redactions(images=0,graphics=0,text=0)
        after=_text_outside(page,f.Rect(0,lo-13,source.rect.width*.5,hi+17))
        if after!=before:
            from collections import Counter
            removed=list((Counter(before)-Counter(after)).elements())[:8]
            added=list((Counter(after)-Counter(before)).elements())[:8]
            raise RuntimeError(f'ORCID 修复触及其他正文，已停止输出: {removed!r} {added!r}')
        result.append(dict(uri=uri,text=ident,box=list(destination),source_boxes=[list(b) for b in source_boxes]))
    return result


def draw_author_identifiers(page, contacts, font_pair, *, offset=(0,0)):
    """Draw and link each verified ORCID directly on the final PDF page."""
    import pymupdf as f
    from linguafloat.document_fonts import FitzFontResolver
    resolver=FitzFontResolver(*font_pair)
    dx,dy=offset
    with f.open() as overlay:
        canvas=overlay.new_page(width=page.rect.width,height=page.rect.height)
        for contact in contacts:
            box=f.Rect(contact['box'])+(dx,dy,dx,dy)
            resolver.draw_line(canvas,(box.x0,box.y0+9),contact['text'],fontsize=9,
                               color=(.05,.32,.64))
        page.show_pdf_page(page.rect,overlay,0)
    for contact in contacts:
        box=f.Rect(contact['box'])+(dx,dy,dx,dy)
        page.insert_link({'kind':f.LINK_URI,'from':box,'uri':contact['uri']})


def is_replaced_identifier(source,region,repairs):
    """Retained-region boxes use bottom-left coordinates; links use top-left."""
    import pymupdf as f
    if region.get('preserve_reason')!='identifier':return False
    a=f.Rect(region['box']) * source.transformation_matrix * source.rotation_matrix
    return any((a & f.Rect(b)).get_area() > a.get_area()*.65
               for r in repairs for b in r['source_boxes'])
