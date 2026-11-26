"""Restore external actions only when their visible target can be established."""
import re
import pymupdf as pdf


def restore(source, target, offset=(0,0)):
    if source.rotation or target.rotation or source.cropbox.x0 or source.cropbox.y0:return 0
    dx,dy=offset;panel=source.rect+(dx,dy,dx,dy);existing=target.get_links()
    present={l.get('uri') for l in existing if l['from'].intersects(panel)};added=0
    for link in source.get_links():
        uri=link.get('uri','');box=pdf.Rect(link['from']) & source.rect
        if not re.match(r'^(?:https?://\S+|mailto:[^\s@]+@\S+)$',uri,re.I) or uri in present or box.is_empty:continue
        region=box+(dx,dy,dx,dy);src=' '.join(source.get_textbox(box).split())
        # Identical native artwork or text at the same position: do not attach
        # links to unrelated translated text merely because a box overlaps it.
        if src and src==' '.join(target.get_textbox(region).split()):
            a=source.get_pixmap(matrix=pdf.Matrix(2,2).pretranslate(dx,dy),clip=box);b=target.get_pixmap(matrix=pdf.Matrix(2,2),clip=region)
            same=(a.width,a.height,a.samples)==(b.width,b.height,b.samples)
        else:same=False
        if not same:
            term=re.sub(r'^(?:https?://|mailto:)','',uri,flags=re.I)
            if len(term)<6 or not (uri.lower().startswith('mailto:') or '.' in term):continue
            terms=[term]
            if re.match(r'^doi\.org/10\.\d{4,9}/',term,re.I):terms.append(term.split('/',1)[1])
            if re.fullmatch(r'www\.[\w.-]+/?',term,re.I):terms.append(term[4:].rstrip('/'))
            found=[]
            for literal in terms:
                if literal.casefold() not in src.casefold():continue
                matches=target.search_for(literal,clip=panel)
                if len(matches)==1:
                    found=matches;break
            if not found and re.match(r'^https?://refhub\.elsevier\.com/[^/]+/sref\d+$',uri,re.I):
                author=re.match(r'^(.{8,170}?\((?:19|20)\d{2}[a-z]?\))',src)
                if author and re.match(r'^[A-ZÀ-Ž]',author[1]) and ',' in author[1]:
                    found=target.search_for(author[1],clip=panel)
            if len(found)!=1:continue
            region=found[0]
        if any(region.intersects(l['from']) and l.get('uri')!=uri for l in existing):continue
        if any(l.get('uri')==uri and l['from'].intersects(region) for l in existing):continue
        record={'kind':pdf.LINK_URI,'from':region,'uri':uri};target.insert_link(record);existing.append(record);added+=1
    return added
