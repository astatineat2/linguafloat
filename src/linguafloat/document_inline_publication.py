"""Preserve a complete native editorial history as a single dated field row."""
from datetime import date
import re

import pymupdf as pdf

_MONTHS = 'January February March April May June July August September October November December'.split()
_LABELS = {'Received':'收稿', 'Revised':'修订', 'Editorial Decision':'编辑决定', 'Accepted':'录用'}
_FIELD = re.compile(r'('+'|'.join(_LABELS)+r')\s*:?\s+('+'|'.join(_MONTHS)+r')\s+(\d{1,2}),\s*(\d{4})')
_PMC = re.compile(r'([A-Z][A-Za-z .&-]{0,60}\.) Author manuscript; available in PMC (\d{4}) ('+'|'.join(_MONTHS)+r') (\d{1,2})\.')


def rows(page):
    if page.rotation or page.cropbox.x0 or page.cropbox.y0:
        return []
    out=[]
    for block in page.get_text('dict',flags=0)['blocks']:
        for line in block.get('lines',[]):
            if tuple(line['dir']) != (1.,0.): continue
            spans=line['spans']; raw=''.join(s['text'] for s in spans).strip()
            caption=re.fullmatch(r'Table\s+(\d+[A-Za-z]?)\s+\(?continued\)?\.?',raw,re.I)
            footer=_PMC.fullmatch(raw) if line['bbox'][1] >= .85*page.rect.height else None
            if footer:
                try: d=date(int(footer[2]),_MONTHS.index(footer[3])+1,int(footer[4]))
                except ValueError: continue
                text=f'{footer[1]} 作者手稿；{d.year}年{d.month}月{d.day}日起可在 PMC 查阅。'
            elif caption:
                box=pdf.Rect(line['bbox'])
                grids=[pdf.Rect(t.bbox) for t in page.find_tables().tables]
                if not any(0 <= grid.y0-box.y1 <= 24 and grid.x0-3 <= box.x0 < grid.x1 for grid in grids) or any(box.intersects(grid) for grid in grids):continue
                text='表'+caption[1]+'（续）'
            else:
                if page.number: continue
                parts=[_FIELD.fullmatch(t.strip()) for t in raw.split(';')]
                if not all(parts) or [p[1] for p in parts] != list(_LABELS): continue
                try: dates=[date(int(p[4]),_MONTHS.index(p[2])+1,int(p[3])) for p in parts]
                except ValueError: continue
                text='；'.join(f'{_LABELS[p[1]]}：{d.year}年{d.month}月{d.day}日' for p,d in zip(parts,dates))
            box=pdf.Rect(line['bbox']); size=spans[0]['size']
            if pdf.Font('cjk').text_length(text,fontsize=size)>box.width: continue
            if any(box.intersects(l['from']) for l in page.get_links()): continue
            out.append(dict(box=list(box),glyph_box=list(box),kind='inline_publication',raw=raw,
                            text=text,size=size,baseline=spans[0]['origin'][1]))
    return out


def write_row(page,row,*,offset=(0,0),clip=None):
    from linguafloat.document_clip import prepare_text_redaction
    dx,dy=offset; box=pdf.Rect(row['box'])+(dx,dy,dx,dy)
    area=pdf.Rect(clip) if clip is not None else box+(-.25,-.25,.25,.25)
    if not page.rect.contains(area) or any(area.intersects(l['from']) for l in page.get_links()):
        raise ValueError('出版日期行边界或链接冲突')
    prepare_text_redaction(page)
    page.add_redact_annot(area,fill=(1,1,1),cross_out=False)
    page.apply_redactions(images=0,graphics=0,text=0)
    writer=pdf.TextWriter(page.rect)
    writer.append((box.x0,row['baseline']+dy),row['text'],fontsize=row['size'],font=pdf.Font('cjk'))
    writer.write_text(page)
    result=page.get_textbox(area)
    if ''.join(result.split()) != ''.join(row['text'].split()): raise ValueError('独立文字行复制校验失败')
