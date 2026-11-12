"""Geometry shared by table cells and chart labels, without interpreting data."""
from __future__ import annotations

from statistics import median


def plot_bounds(page, area):
    """Require intersecting long orthogonal rules; no guessing from an OCR box."""
    import cv2
    import numpy as np
    import pymupdf as f
    clip = f.Rect(area['box']) & page.rect
    pix = page.get_pixmap(matrix=f.Matrix(4, 4), clip=clip, colorspace=f.csGRAY)
    ink = (np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width) < 130).astype(np.uint8)
    lines = []
    for horizontal in (True, False):
        length = max(30, int((pix.width if horizontal else pix.height) * .45))
        mask = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, length) if horizontal else (length, 1), np.uint8))
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        found = []
        for x, y, w, h, pixels in stats[1:]:
            if (h if horizontal else w) > 8:
                continue
            found.append(f.Rect((pix.x+x)/4, (pix.y+y)/4, (pix.x+x+w)/4, (pix.y+y+h)/4))
        lines.append(found)
    hs, vs = lines
    candidates = []
    for top in hs:
        for bottom in hs:
            if bottom.y0 - top.y1 < clip.height * .4:
                continue
            sides = [v for v in vs if abs(v.y0-top.y0)<2 and abs(v.y1-bottom.y1)<2
                     and top.x0-2 <= v.x0 <= top.x1+2 and bottom.x0-2 <= v.x0 <= bottom.x1+2]
            if len(sides) < 2:
                continue
            left, right = min(sides, key=lambda b:b.x0), max(sides, key=lambda b:b.x1)
            if right.x0-left.x1 < clip.width*.45:
                continue
            if any(abs(a-b)>2 for a,b in ((top.x0,left.x0),(bottom.x0,left.x0),(top.x1,right.x1),(bottom.x1,right.x1))):
                continue
            candidates.append(f.Rect((left.x0+left.x1)/2,(top.y0+top.y1)/2,
                                     (right.x0+right.x1)/2,(bottom.y0+bottom.y1)/2))
    if not candidates:
        return None
    candidates.sort(key=lambda b:b.get_area(), reverse=True)
    if any(not (candidates[0]+(-2,-2,2,2)).contains(b) for b in candidates[1:]):
        return None  # Multiple independent panels need separate detection.
    return candidates[0]


def stationary_axis_label(page, area, box):
    """Allow an unchanged title box beside a measured neutral vertical axis.

    Perspective charts have no rectangular plot bounds. This evidence permits
    in-place replacement only; glyph_patch and clear_of_original still decide
    whether source ink can be removed and the complete replacement can fit.
    """
    import cv2
    import numpy as np
    import pymupdf as f
    clip = (f.Rect(area['box']) | box) & page.rect
    pix = page.get_pixmap(matrix=f.Matrix(4, 4), clip=clip, colorspace=f.csRGB, alpha=False)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).astype(int)
    neutral = ((rgb.max(axis=2) - rgb.min(axis=2) <= 24) & (rgb.max(axis=2) < 180)).astype(np.uint8)
    length = max(120, int(pix.height * .4), int(box.height * 4))
    mask = cv2.morphologyEx(neutral, cv2.MORPH_OPEN, np.ones((length, 1), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    for x, y, w, h, _ in stats[1:]:
        if w > 8:
            continue
        rule = f.Rect((pix.x+x)/4, (pix.y+y)/4, (pix.x+x+w)/4, (pix.y+y+h)/4)
        if (box.x1 + 2 <= rule.x0 <= box.x1 + max(36, box.width * 4)
                and rule.y0 <= box.y0 + 1 and rule.y1 >= box.y1 - 1):
            return True
    return False


def ruled_cells(page, area):
    """Use enclosing table rules, not tiny formula/subscript OCR row centres."""
    import cv2
    import numpy as np
    import pymupdf as f
    clip = (f.Rect(area['box']) + (-3,-3,3,3)) & page.rect
    pix = page.get_pixmap(matrix=f.Matrix(4,4), clip=clip, colorspace=f.csGRAY)
    ink = (np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width)<150).astype(np.uint8)
    axes = []
    glyph_height=median(f.Rect(r['box']).height for r in area['regions'])
    for horizontal in (True,False):
        length = max(24, int((pix.width if horizontal else pix.height)*.08),
                     int(glyph_height*4*1.8) if not horizontal else 0)
        mask=cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((1,length) if horizontal else (length,1),np.uint8))
        _,_,stats,_=cv2.connectedComponentsWithStats(mask)
        axes.append([f.Rect((pix.x+x)/4,(pix.y+y)/4,(pix.x+x+w)/4,(pix.y+y+h)/4)
                     for x,y,w,h,_ in stats[1:] if (h if horizontal else w)<=8])
    hs,vs=axes
    result={}
    for region in area['regions']:
        b=f.Rect(region['box']);cx,cy=(b.x0+b.x1)/2,(b.y0+b.y1)/2
        above=[r for r in hs if r.x0<=cx<=r.x1 and r.y1<cy]
        below=[r for r in hs if r.x0<=cx<=r.x1 and r.y0>cy]
        left=[r for r in vs if r.y0<=cy<=r.y1 and r.x1<cx]
        right=[r for r in vs if r.y0<=cy<=r.y1 and r.x0>cx]
        if not all((above,below,left,right)):continue
        top,bottom=max(above,key=lambda r:r.y1),min(below,key=lambda r:r.y0)
        l,r=max(left,key=lambda r:r.x1),min(right,key=lambda r:r.x0)
        if not (top.x0<=l.x1+1 and top.x1>=r.x0-1 and bottom.x0<=l.x1+1 and bottom.x1>=r.x0-1):continue
        cell=f.Rect(l.x1+1,top.y1+1,r.x0-1,bottom.y0-1)
        if cell.width<6 or cell.height<6:continue
        # Keep adjacent variables/units where they are. A two-tier header is one
        # cell, but its label has only the upper safe band, not the whole cell.
        for line in area.get('lines',[]):
            for word in line.get('words',[]) or [line]:
                wb=f.Rect(word['box']);wc=(wb.tl+wb.br)/2
                if not cell.contains(wc) or (wb&b).get_area()>wb.get_area()*.15:continue
                if wb.x0>=b.x1-1 and abs(wc.y-cy)<b.height*.45:
                    cell.x1=min(cell.x1,wb.x0-1)
                elif wc.y>cy+b.height*.35:
                    # OCR superscript boxes are loose. Use visible vector ink
                    # where present, falling back to the OCR bound on scans.
                    outlines=[d['rect'] for d in page.get_drawings() if d['type']=='f'
                              and wb.contains((d['rect'].tl+d['rect'].br)/2)
                              and d['rect'].height>1 and d['rect'].width<wb.width+2]
                    boundary=min(q.y0 for q in outlines) if outlines else wb.y0
                    cell.y1=min(cell.y1,boundary-.6)
        row=1+len({round(q.y0,0) for q in hs if q.x0<=cx<=q.x1 and q.y0<top.y0-3})
        col=1+len({round(q.x0,0) for q in vs if q.y0<=cy<=q.y1 and q.x0<l.x0-3})
        result[region['id']]=dict(layout_box=list(cell),row=row,column=col,cell_box=[l.x1,top.y1,r.x0,bottom.y0],geometry='table-rules')
    return result


def table_cells(area, page=None):
    """Infer row bands and column starts from complete OCR lines, including data."""
    import pymupdf as f
    lines = area.get('lines') or area['regions']
    boxes = [f.Rect(line['box']) for line in lines]
    bounds = f.Rect(area['box'])
    typical = median(b.height for b in boxes)
    rows = []
    for b in sorted(boxes, key=lambda b:(b.y0+b.y1)/2):
        cy = (b.y0+b.y1)/2
        if rows and abs(cy-median(rows[-1])) < typical*.55:
            rows[-1].append(cy)
        else:
            rows.append([cy])
    centers = [median(row) for row in rows]
    columns = []
    for b in sorted(boxes,key=lambda b:b.x0):
        if columns and abs(b.x0-median(columns[-1])) < typical:
            columns[-1].append(b.x0)
        else:
            columns.append([b.x0])
    starts = [median(col) for col in columns]
    result = {}
    for r in area['regions']:
        b = f.Rect(r['box'])
        row = min(range(len(centers)),key=lambda i:abs(centers[i]-(b.y0+b.y1)/2))
        col = min(range(len(starts)),key=lambda i:abs(starts[i]-b.x0))
        y0 = (centers[row-1]+centers[row])/2 if row else max(bounds.y0,centers[0]-(centers[1]-centers[0])/2) if len(centers)>1 else bounds.y0
        y1 = (centers[row]+centers[row+1])/2 if row+1<len(centers) else min(bounds.y1,centers[-1]+(centers[-1]-centers[-2])/2) if len(centers)>1 else bounds.y1
        x0 = starts[col]
        x1 = min(columns[col+1])-2 if col+1<len(starts) else bounds.x1-1
        # Never expand over protected words in the same OCR line.
        for line in lines:
            for w in line.get('words',[]):
                wb=f.Rect(w['box'])
                if wb.x0>b.x1+1 and y0<(wb.y0+wb.y1)/2<y1:
                    x1=min(x1,wb.x0-1)
        result[r['id']] = dict(layout_box=[x0,y0+.25,x1,y1-.25],row=row+1,column=col+1)
    if page is not None:
        result.update(ruled_cells(page,area))
    return result


def plan_area(page, area):
    import pymupdf as f
    regions=[dict(r) for r in area['regions']]
    if not regions:
        return regions
    # A group-level estimate, never independently shrinking individual cells.
    heights=[min(f.Rect(r['box']).width,f.Rect(r['box']).height) for r in regions]
    size=max(6,min(18,round(median(heights)*.82,2)))
    visible=[span for span in page.get_texttrace() if span.get('type')!=3 and span.get('opacity',1)>.05]
    native_sizes=[span['size'] for span in visible if any(
        (f.Rect(span['bbox']) & f.Rect(r['box'])).get_area()>f.Rect(span['bbox']).get_area()*.65
        for r in regions)]
    if native_sizes:size=max(6,min(18,median(native_sizes)))
    if area['kind']=='table':
        cells=table_cells(area,page)
        for r in regions:
            r.update(cells[r['id']],group=area['id'],preferred_size=size)
        # Share the first baseline, not the bottom edge: long labels may wrap
        # in a tall cell while the adjacent cell reserves its lower unit tier.
        for row in {r['row'] for r in regions if r.get('geometry')=='table-rules'}:
            peers=[r for r in regions if r.get('geometry')=='table-rules' and r['row']==row]
            top=max(r['layout_box'][1] for r in peers)
            for r in peers:
                r['layout_box'][1]=top
                r['align_top']=True
        return regions
    plot=plot_bounds(page,area)
    for r in regions:
        b=f.Rect(r['box'])
        r.update(preferred_size=size,layout_box=list(b))
        is_y=r.get('rotation')==90
        # Axis candidates must lie outside the plot, not among legends/ticks.
        is_x=bool(plot and b.y0>plot.y1 and b.width>b.height*2)
        if is_y or is_x:
            if plot is None:
                if is_y and stationary_axis_label(page, area, b):
                    r.update(geometry='axis-in-place', group=area['id']+'-stationary-axes')
                    continue
                r['reason']='无法可靠识别绘图区边界，未移动坐标轴标题；请提供清晰原 PDF 以检查图线结构。'
                continue
            # Collect adjacent protected suffixes geometrically. Preserve their
            # source artwork, even when OCR reads rotated (T) incorrectly as E.
            suffixes=[]
            for line in area.get('lines',[]):
                for w in line.get('words',[]) or [line]:
                    wb=f.Rect(w['box'])
                    if (wb & b).get_area()>wb.get_area()*.2:
                        continue
                    if is_y:
                        aligned=abs((wb.x0+wb.x1-b.x0-b.x1)/2)<b.width*.55
                        adjacent=b.y0-4*b.width<=wb.y1<=b.y0+1
                    else:
                        aligned=abs((wb.y0+wb.y1-b.y0-b.y1)/2)<b.height*.55
                        adjacent=b.x1-1<=wb.x0<=b.x1+5*b.height
                    if aligned and adjacent:
                        suffixes.append(wb)
            suffix=None
            if suffixes:
                suffix=suffixes[0]
                for wb in suffixes[1:]:suffix |= wb
                # Include only a tightly bounded run next to the name.
                r['suffix_box']=list(suffix)
                r['box']=list(b | suffix)
            full=f.Rect(r['box'])
            r.update(axis='y' if is_y else 'x',plot_box=list(plot),group=area['id']+'-axes')
            r['layout_box']=list(f.Rect(full.x0,plot.y0,full.x1,plot.y1) if is_y else
                                 f.Rect(plot.x0,full.y0,plot.x1,full.y1))
        elif plot and plot.contains(b):
            r.update(group=area['id']+'-legend')
    legends=[r for r in regions if r.get('group')==area['id']+'-legend']
    if len(legends)>1:
        left=min(f.Rect(r['box']).x0 for r in legends)
        # Keep text away from the plot edge and the legend's original line keys.
        right=max(f.Rect(r['box']).x1 for r in legends)
        for r in legends:
            r['layout_box'][0]=left
            r['layout_box'][2]=right
    return regions


def wrapped_lines(text,font,size,width):
    """Unicode wrapping; prefer whitespace, never drop characters."""
    lines=[]
    for paragraph in text.splitlines() or ['']:
        current=''
        for char in paragraph:
            if current and font.text_length(current+char,fontsize=size)>width:
                cut=current.rfind(' ')
                if cut>0 and not char.isspace():
                    lines.append(current[:cut]);current=current[cut+1:]+char
                else:
                    lines.append(current.rstrip());current=char.lstrip()
            else:current+=char
        if current:lines.append(current.rstrip())
    return lines


def fit_label(page,region,text,target,control,size,source=None):
    """Render a fixed size in a planned cell, or center an axis with its suffix."""
    import pymupdf as f
    box=f.Rect(region.get('layout_box',region['box']))
    rotated=region.get('rotation')==90
    width,height=(box.height,box.width) if rotated else (box.width,box.height)
    font=f.Font({'ja':'japan','ko':'korea'}.get(target,'china-s'))
    missing=[f'U+{ord(c):04X}' for c in text if not c.isspace() and not font.has_glyph(ord(c))]
    if missing:
        region['_fit_failure']='目标字体缺少字形 '+', '.join(dict.fromkeys(missing))+'；需要补充相应语言字体，未删除缺字。'
        return None
    suffix=f.Rect(region['suffix_box']) if region.get('suffix_box') and source is not None else None
    sw=(suffix.height if rotated else suffix.width) if suffix else 0
    sh=(suffix.width if rotated else suffix.height) if suffix else 0
    gap=size*.35 if suffix else 0
    axis=bool(region.get('axis'))
    lines=[text] if axis else wrapped_lines(text,font,size,width)
    line_height=size*1.12
    ink_height=size  # CJK glyph body, with consistent baselines across each row.
    total_height=(len(lines)-1)*line_height+ink_height
    if not lines or max(font.text_length(s,fontsize=size) for s in lines)+sw+gap>width or max(total_height,sh)>height+.25:
        region['_fit_failure']=f'可用区域 {width:.1f}×{height:.1f}pt 放不下完整译文（已尝试换行和统一 {size:g}pt 字号）；需检查单元格/轴标题边界。'
        return None
    region.pop('_fit_failure',None)
    control.checkpoint()
    with f.open() as temp,f.open() as overlay:
        p=temp.new_page(width=width+20,height=height+20)
        p.insert_font(fontname='LFGraphic',fontbuffer=font.buffer)
        content_width=font.text_length(text,fontsize=size)+sw+gap if axis else width
        x=10+(width-content_width)/2 if axis else 10
        y=10+(0 if region.get('align_top') else (height-total_height)/2)+size*.88
        for i,line in enumerate(lines):
            p.insert_text((x,y+i*line_height),line,fontname='LFGraphic',fontsize=size,color=(.12,.12,.12))
        if suffix:
            sx=x+font.text_length(text,fontsize=size)+gap
            dest=f.Rect(sx,10+(height-sh)/2,sx+sw,10+(height+sh)/2)
            # Preserve original vector glyphs / pixels and superscripts, unscaled.
            p.show_pdf_page(dest,source.parent,source.number,clip=suffix,rotate=270 if rotated else 0)
        out=overlay.new_page(width=page.rect.width,height=page.rect.height)
        out.show_pdf_page(box,temp,0,clip=f.Rect(10,10,10+width,10+height),rotate=90 if rotated else 0)
        return overlay.tobytes(),dict(size=size,rows=len(lines),layout_box=list(box),
             axis=region.get('axis'),plot_box=region.get('plot_box'),row=region.get('row'),column=region.get('column'))


def clear_of_original(source, group, overlays):
    """Check new ink against preserved rules/data and other new labels at 4x."""
    import cv2
    import numpy as np
    import pymupdf as f
    rect=f.Rect(group[0][0].get('layout_box',group[0][0]['box']))
    for r,_,_,_ in group:rect |= f.Rect(r.get('layout_box',r['box']))
    rect=(rect+(-1,-1,1,1)) & source.rect
    with f.open() as background:
        background.insert_pdf(source.parent,from_page=source.number,to_page=source.number)
        p=background[0]
        for r,patch,_,_ in group:
            p.add_redact_annot(f.Rect(r['box']),fill=False,cross_out=False)
        p.apply_redactions(images=0,graphics=0,text=0)
        for r,patch,_,_ in group:p.insert_image(f.Rect(patch[1]),stream=patch[0],mask=patch[2])
        pix=p.get_pixmap(matrix=f.Matrix(4,4),clip=rect,colorspace=f.csGRAY)
        retained=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width)<150
        occupied=retained.copy()
        for data,_ in overlays:
            with f.open(stream=data,filetype='pdf') as doc:
                pix=doc[0].get_pixmap(matrix=f.Matrix(4,4),clip=rect,colorspace=f.csGRAY)
                ink=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width)<150
                if np.any(ink & cv2.dilate(occupied.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)):
                    return False
                occupied |= ink
    return True
