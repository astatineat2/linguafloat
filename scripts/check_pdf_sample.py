"""Offline checks on matching source / translated pages (not the original bilingual panel)."""
import argparse
from collections import Counter, defaultdict
import json
import re
import unicodedata
from pathlib import Path

import pymupdf as pdf

EMAIL = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', re.ASCII)


def rows(page):
    for block in page.get_text('dict', flags=pdf.TEXTFLAGS_DICT & ~pdf.TEXT_PRESERVE_IMAGES)['blocks']:
        for line in block.get('lines', []):
            spans = line['spans']
            yield pdf.Rect(line['bbox']), unicodedata.normalize('NFKC', ''.join(s['text'] for s in spans)).strip(), spans


def _double_strokes(paths):
    tracks = defaultdict(list)
    for path in paths:
        if path['type'] != 's' or len(path['items']) != 1 or path['items'][0][0] != 'l':
            continue
        r = path['rect']
        if r.height < .05 and 2 <= r.width <= 12:
            tracks[round(r.x0,1), round(r.x1,1)].append(r.y0)
    return {(x, end, round((lo+hi)/2,1)) for (x,end), ys in tracks.items()
            for lo,hi in zip(sorted(ys),sorted(ys)[1:]) if .5 <= hi-lo <= 2.8}


def _bond_ends(words, x, end, y):
    near = [w for w in words if w[1] < y < w[3] and w[4] in {'C','N','O','S','P'}]
    return {(a[4], b[4]) for a in near if abs(a[2]-x)<1 for b in near if abs(b[0]-end)<1}


def compare(source, target):
    findings = []
    def flag(rule, text, box=None, severity='fail'):
        findings.append(dict(rule=rule, evidence=text, box=list(box) if box else None, severity=severity))
    a, b = list(rows(source)), list(rows(target))
    def emails(lines):
        return Counter(x for _, text, _ in lines for x in EMAIL.findall(text))
    for address in (emails(a)-emails(b)).elements():
        flag('missing-visible-email', address)
    def links(page):
        return Counter(l['uri'] for l in page.get_links() if l.get('uri','').startswith(('http:', 'https:', 'mailto:')))
    for uri in (links(source)-links(target)).elements():
        flag('missing-link-target', uri)
    if source.rect != target.rect or source.rotation or target.rotation:
        flag('geometry-needs-review','Unsupported page geometry',severity='review');return findings
    # Consecutive source numbers starting separate aligned lines, not inline citations.
    sequence=[];numbered_bands=[]
    markers=[(box,int(m[1])) for box,text,_ in a if (m:=re.match(r'^(\d{1,2})[.)]\s+',text))]
    for box,n in markers+[(None,0)]:
        if sequence and not (n==sequence[-1][1]+1 and abs(box.x0-sequence[0][0].x0)<3 and 0<box.y0-sequence[-1][0].y0<sequence[-1][0].height*2):
            if len(sequence)>=3:
                numbered_bands.append(sequence[0][0] | sequence[-1][0])
                for r,i in sequence:
                    if not any(abs(tb.x0-r.x0)<5 and abs(tb.y0-r.y0)<r.height*.75 and re.match(rf'^{i}[.、)）]',t) for tb,t,_ in b):
                        flag('numbered-row-not-separate',str(i),r)
            sequence=[]
        if box is not None:sequence.append((box,n))
    # Independently locate ruled tables from the printed caption and bounding rules.
    paths = source.get_drawings()
    strokes = _double_strokes(paths)
    if strokes:
        original_words, translated_words = source.get_text('words'), target.get_text('words')
        for x,end,y in strokes & _double_strokes(target.get_drawings()):
            atoms = _bond_ends(original_words,x,end,y)
            if len(atoms)==1 and not atoms.intersection(_bond_ends(translated_words,x,end,y)):
                flag('detached-vector-bond', '='.join(next(iter(atoms))),pdf.Rect(x-8,y-6,end+8,y+6))
    rules=[d['rect'] for d in paths if d['rect'].height<2 and d['rect'].width>source.rect.width*.25]
    tables=[]
    for cap,text,_ in a:
        if not re.match(r'^Table\s+\d+\b',text,re.I):continue
        above=sorted((q for q in rules if 0<=q.y0-cap.y0<55 and q.x0-8<=cap.x0<q.x1),key=lambda q:q.y0)
        if not above:
            if not any(abs(box.x0-cap.x0)<8 and 0<box.y0-cap.y0<55 for box in numbered_bands):
                flag('table-geometry-needs-review',text,cap,'review')
            continue
        top=above[0];ends=[q for q in rules if q.y0>top.y1+20 and abs(q.x0-top.x0)<3 and abs(q.x1-top.x1)<3]
        if ends:tables.append(pdf.Rect(top.x0-1,top.y0,top.x1+1,min(q.y1 for q in ends)))
        else:flag('table-geometry-needs-review',text,cap,'review')
    for r,text,spans in a:
        if not any(t.contains((r.tl+r.br)/2) for t in tables):continue
        nearby=[u for box,u,_ in b if abs(box.y0-r.y0)<r.height*.6 and box.intersects(r)]
        if not nearby and text:flag('possibly-empty-cell',text,r,'review')
        elif text in nearby and not all(s['flags']&2 for s in spans):
            if re.fullmatch(r'[A-Za-z][a-z]+(?:[ -][A-Za-z]?[a-z]+){0,2}',text):flag('unchanged-short-cell',text,r,'review')
            elif len(re.findall(r'\b[a-z]{2,}\b',text,re.I))>=5:flag('unchanged-table-line',text,r,'review')
    return findings


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('translated',type=Path);args=p.parse_args()
    with pdf.open(args.source) as src,pdf.open(args.translated) as dst:
        if len(src)!=len(dst):p.error('Page counts differ: supply aligned samples')
        results=[dict(page=i+1,**issue) for i in range(len(src)) for issue in compare(src[i],dst[i])]
    print(json.dumps(results,ensure_ascii=False,indent=2));return 1 if results else 0

if __name__=='__main__':raise SystemExit(main())
