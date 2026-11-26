"""Recover bounded prose on searchable scans using visible raster observations."""
from collections import Counter
from difflib import SequenceMatcher
import re


def canonical(s):
    return ' '.join(s.replace('℃', '°C').split())


_LINKAGE = re.compile(r'[αβ]\s*[-‐‑–]\s*[1-9]\s*[,，]\s*[1-9](?!\d)')
_LENGTH = r'(?<![\d.])\d+(?:\.\d+)?\s*(?:mm|cm|[μµ]m|nm|m)(?![A-Za-z])'
_DIMENSION = re.compile(_LENGTH + r'\s*×\s*' + _LENGTH)
_MICRON = re.compile(r'(?<![\d.])\d+(?:\.\d+)?\s*[μµ]m(?![A-Za-z])')


def inline_notation_prose(text):
    from linguafloat.document_scan_layout import risky_math
    rest=text
    for pattern in (_LINKAGE, _DIMENSION, _MICRON):
        rest=pattern.sub(' ',rest)
    return (rest != text and len(re.findall(r'\b[A-Za-z]{3,}\b', rest)) >= 3
            and not risky_math(rest) and not re.search(r'[{}<>∝≈∞⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]', rest))


def linkage_signature(text):
    text = text.translate(str.maketrans('‐‑–，', '---,'))
    return (Counter(re.sub(r'\s+', '', m) for m in _LINKAGE.findall(text)),
            Counter(re.findall(r'(?<![A-Za-z])([DL])\s*-(?=\s*[A-Za-z\u3400-\u9fff])', text)))


def validate_inline_notation(source, target):
    from linguafloat.document_core import DocumentError
    from linguafloat.document_scan_layout import validate_scan_values
    if canonical(source).casefold() == canonical(target).casefold():
        raise DocumentError('扫描正文以原文返回，未作为译文接受')
    validate_scan_values(source,target)
    if linkage_signature(source) != linkage_signature(target):
        raise DocumentError('译文中的连接位点或 D/L 构型与扫描原行不一致')
    def measures(pattern,text):
        return Counter(re.sub(r'\s+', '', m.replace('µ','μ')) for m in pattern.findall(text))
    if any(measures(p,source)!=measures(p,target) for p in (_DIMENSION,_MICRON)):
        raise DocumentError('扫描原文与译文的尺寸或单位不同')


def hidden_row(page, box):
    return ' '.join(w[4] for w in sorted(page.get_text('words'),key=lambda w:w[0]) if
                    box.x0-1 < (w[0]+w[2])/2 < box.x1+1 and
                    abs((w[1]+w[3]-box.y0-box.y1)/2) < box.height*.40)


def matching_crops(page, box, analyzer, expected):
    for scale in (3,4):
        analyzer.translator.control.checkpoint()
        obs=analyzer.ocr.recognize(page,positioned=True,clip=box+(-1,0,1,0),scale=scale,single_line=True)
        if len(obs)!=1 or obs[0]['confidence']<.97 or canonical(obs[0]['text'])!=canonical(expected): return None
        yield obs[0]


def recover_truncated_row(page, line, analyzer):
    import pymupdf as f
    if not line['horizontal'] or line['confidence']>=.95: return line
    box=f.Rect(line['box']);hidden=hidden_row(page,box)
    if (len(canonical(line['text'])) > len(hidden)*.45 or
            len(re.findall(r'\b[A-Za-z]{2,}\b', hidden))<4): return line
    votes=list(matching_crops(page,box,analyzer,hidden))
    if len(votes)!=2: return line
    return dict(line,text=votes[0]['text'],confidence=min(v['confidence'] for v in votes),confirmed_row=True)


def confirmed_inline_notation(page, line, analyzer):
    import pymupdf as f
    text=line['text']
    if not inline_notation_prose(text) or line['confidence'] < .97: return False
    box=f.Rect(line['box']); hidden=hidden_row(page,box)
    observed, extracted = linkage_signature(text), linkage_signature(hidden)
    if any(known and known != read for read,known in zip(observed,extracted)): return False
    votes=[(text,line['confidence']),*((v['text'],v['confidence']) for v in matching_crops(page,box,analyzer,text))]
    return len(votes)==3 and choose_row(votes,hidden)==canonical(text)


def choose_row(observations, hidden):
    # The hidden layer can veto a reading; it never supplies replacement text.
    hidden = canonical(hidden)
    counts = Counter(canonical(s) for s, score in observations if score >= .87)
    eligible = []
    for text, count in counts.items():
        if count < 2 or not hidden or re.search(r"(?:^|\s)['‘’](?=[A-Z])", text):
            continue
        exact = text.replace(' ', '') == hidden.replace(' ', '')
        if not exact:
            if len(text) < 18 or SequenceMatcher(None,text,hidden,autojunk=False).ratio() < .81:
                continue
            # A missing glyph in a sample code or unit is not a spelling variant.
            tokens = re.findall(r'[A-Za-z0-9°]+', hidden)
            def confusable(s): return s.replace('1','I').replace('0','O')
            if any(not any(confusable(t) == confusable(u) for u in tokens)
                   for t in re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Z][A-Za-z0-9]*(?![A-Za-z0-9])',text)):
                continue
            if ('°' in hidden) != ('°' in text): continue
            # Compare legible decimal literals separately: several agreeing
            # crops from the same model can still drop a digit in a long row.
            decimals=r'(?<![\d.])\d+\.\d+(?![\d.])'
            known=re.findall(decimals,hidden)
            seen=iter(re.findall(decimals,text))
            if not all(any(value==item for item in seen) for value in known):continue
            if not any(canonical(s)==text and score>=.97 for s,score in observations): continue
        eligible.append((exact,count,text))
    eligible.sort(reverse=True)
    if not eligible or len(eligible)>1 and eligible[0][:2]==eligible[1][:2]: return None
    return eligible[0][2]


def validate_block(source, target):
    from linguafloat.document_core import DocumentError
    from linguafloat.document_scan_layout import validate_scan_values
    validate_scan_values(source,target)
    def signature(s):
        s=canonical(s).translate(str.maketrans('（）％','()%'))
        return (Counter(re.findall(r'(?<![A-Za-z])[A-Z][A-Z][A-Za-z\d]*(?![A-Za-z])',s)),
                Counter(re.findall(r'(?:[A-Z]\s*\(\s*)?\d+(?:\.\d+)?\s*±\s*\d+(?:\.\d+)?',s.replace(' ',''))),
                Counter(re.findall(r'[⁰¹²³⁴⁵⁶⁷⁸⁹]+[A-Za-z]+|°C|×',s)))
    if signature(source)!=signature(target):
        raise DocumentError('扫描段落的科学标识、量纲或误差对应关系发生变化，未回填')


def owned_holds(box, regions):
    import pymupdf as f
    touched=[]
    for region in regions:
        bounds=f.Rect(region['box']) | f.Rect(region.get('erase_box',region['box']))
        if not bounds.intersects(box):continue
        if not region.get('reason') or region.get('preserved') or not box.contains(bounds):return []
        touched.append(region)
    return touched


def replace_held(old, candidates):
    import pymupdf as f
    result=list(old)
    for candidate in candidates:
        if not candidate.get('raster_consensus'):continue
        subset=owned_holds(f.Rect(candidate['box']),result)
        if subset:
            first=result.index(subset[0]);result=[r for r in result if r not in subset]
            result.insert(first,candidate)
    return result


def corroborate(page, regions, areas, analyzer, *, held=None):
    import pymupdf as f
    from linguafloat.document_scan_layout import ink_patch,join_ocr_lines
    allowed={'plain text','figure_caption','table_caption'}
    obstacles=[f.Rect(a['box']) for a in areas if a['label'] not in allowed]
    words=page.get_text('words'); result=list(regions)
    for r in list(regions):
        if r not in result or not r.get('reason') or r.get('label') not in allowed or r.get('preserved'): continue
        group=[r]; box=f.Rect(r['box']); label=r['label']
        if label=='plain text':
            for other in result:
                if other is r or other.get('label')!=label or not other.get('reason') or other.get('preserved'):continue
                b=f.Rect(other['box']); overlap=box & b
                if (not overlap.is_empty and overlap.width>=.9*max(b.width,box.width) and
                        overlap.height < min(b.height,box.height)*.5):
                    group.append(other); box |= b
            if len(group)<2: continue
        if any(box.intersects(b) for b in obstacles) or any(box.intersects(f.Rect(o['box'])) for o in result if o not in group):continue
        if held is not None and not owned_holds(box,held):continue
        if not all(any(a['label']==label and a['confidence']>=.8 and f.Rect(a['box']).contains((f.Rect(l['box']).tl+f.Rect(l['box']).br)/2) for a in areas) for g in group for l in g['lines']):continue
        rows=sorted((l for g in group for l in g['lines']),key=lambda l:sum(l['box'][1::2]))
        if not rows or len(rows)>48 or not all(l['horizontal'] for l in rows):continue
        boxes=[f.Rect(l['box']) for l in rows]
        if any(abs((a.y0+a.y1-b.y0-b.y1)/2)<min(a.height,b.height)*.60 for a,b in zip(boxes,boxes[1:])):continue
        if any(s['type']!=3 and s.get('opacity',1)>0 and box.intersects(f.Rect(s['bbox'])) for s in page.get_texttrace()):continue
        lines=[]
        for l,b in zip(rows,boxes):
            analyzer.translator.control.checkpoint()
            hidden=' '.join(w[4] for w in sorted(words,key=lambda w:w[0]) if b.x0-1<(w[0]+w[2])/2<b.x1+1 and abs((w[1]+w[3]-b.y0-b.y1)/2)<b.height*.40)
            votes=[(l['text'],l['confidence'])]; candidate=None
            for scale,pad in ((4.1667,-.6),(3,0),(4,0),(5,.6),(3.5,1.3)):
                local=analyzer.ocr.recognize(page,positioned=True,clip=b+(-1,-pad,1,pad),scale=scale,single_line=True)
                if len(local)==1: votes.append((local[0]['text'],local[0]['confidence']))
                candidate=choose_row(votes,hidden)
                if candidate:break
            if not candidate:break
            lines.append(dict(l,text=candidate))
        if len(lines)!=len(rows):continue
        text=join_ocr_lines(lines)
        if len(re.findall(r'\b[a-z]{3,}\b',text))<4 or re.search(r'[∑∫√÷≤≥≡∂∇α-ωΑ-Ω_^{}]',text):continue
        if label!='plain text' and not re.match(r'^(?:Fig(?:ure)?\.?|Table)\s+\d',text,re.I):continue
        recovered=dict(r,box=list(box),lines=lines,text=text,atomic=True,raster_consensus=True,reason='')
        if ink_patch(page,recovered)[1]:continue
        index=result.index(r);result[index]=recovered
        for other in group[1:]:result.remove(other)
    return result
