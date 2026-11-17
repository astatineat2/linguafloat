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


def native_grid_cells(page, area):
    """Read vector grid cells with the existing PDF engine, not OCR row bands.

    Require a populated two-dimensional grid; a chart frame is not a table.
    Cell geometry never changes the area's figure/table switch ownership.
    """
    import pymupdf as f
    bounds = (f.Rect(area['box']) + (-3, -3, 3, 3)) & page.rect
    from linguafloat.document_graphics import visible_native_words
    words = visible_native_words(page, bounds)
    if not words:
        return []
    cells, headers = [], []
    for grid, table in enumerate(page.find_tables(clip=bounds, strategy='lines').tables):
        if table.row_count == 1 and table.col_count >= 2 and area['kind'] == 'table':
            headers.append(table)
        if table.row_count < 2 or table.col_count < 2:
            continue
        entries = []
        for row, band in enumerate(table.rows, 1):
            for column, box in enumerate(band.cells, 1):
                if box is None or f.Rect(box).width < 6 or f.Rect(box).height < 6:
                    continue
                native = [w for w in words if f.Rect(box).contains(
                    (f.Rect(w[:4]).tl + f.Rect(w[:4]).br) / 2)]
                reliable = bool(native) and all('\ufffd' not in w[4] and not any(
                    ord(c) < 32 for c in w[4]) for w in native)
                entries.append(dict(box=list(box), row=row, column=column,
                                    grid=grid, native_reliable=reliable))
        if sum(c['native_reliable'] for c in entries) >= max(4, len(entries)*.5):
            cells.extend(entries)
    return cells or three_line_cells(page, area, words, headers)


def three_line_cells(page, area, words, headers):
    """Extend measured header columns over populated native data rows.

    Three-line tables often draw separate shaded header cells but no vertical
    rules in the body. Require those real column edges, a closing rule, and
    several populated aligned rows; figure-owned grids never change ownership.
    """
    import pymupdf as f
    if area['kind'] != 'table':
        return []
    bounds = f.Rect(area['box'])
    for table in sorted(headers, key=lambda t: t.col_count, reverse=True):
        header = [f.Rect(c) for c in table.rows[0].cells if c is not None]
        if (len(header) != table.col_count or table.bbox[2]-table.bbox[0] < bounds.width*.85
                or abs(table.bbox[1]-bounds.y0) > 12):
            continue
        bottom = [d['rect'] for d in page.get_drawings()
                  if d['rect'].width >= bounds.width*.85 and d['rect'].height <= 2
                  and abs(d['rect'].x0-table.bbox[0]) < 3
                  and table.bbox[3]+15 < d['rect'].y0 <= bounds.y1+3]
        if not bottom:
            continue
        end = max(r.y0 for r in bottom)
        anchors = []
        for word in sorted(words, key=lambda w: (w[1]+w[3], w[0])):
            box = f.Rect(word[:4])
            center = (box.tl+box.br)/2
            if not (header[0].x0 <= center.x < header[0].x1
                    and table.bbox[3] < center.y < end):
                continue
            if anchors and abs(center.y-median(anchors[-1])) < box.height*.3:
                anchors[-1].append(center.y)
            else:
                anchors.append([center.y])
        centers = [median(a) for a in anchors]
        typical = median(f.Rect(w[:4]).height for w in words)
        row_labels = []
        for center in centers:
            supported = any(header[0].x1 <= (w[0]+w[2])/2 < header[-1].x1
                and abs((w[1]+w[3])/2-center) < typical*.45 for w in words)
            if supported:
                row_labels.append([center])
            elif row_labels and 0 < center-row_labels[-1][-1] <= typical*1.5:
                row_labels[-1].append(center)
            else:
                row_labels = []
                break  # Uncorroborated row labels are not a safe data grid.
        if len(row_labels) < 3:
            continue
        edges = [table.bbox[3], *[(a[-1]+b[0])/2 for a, b in zip(row_labels, row_labels[1:])], end]
        rows = [header] + [[f.Rect(c.x0, y0, c.x1, y1) for c in header]
                           for y0, y1 in zip(edges, edges[1:])]
        result = []
        populated = True
        for row, band in enumerate(rows, 1):
            filled = 0
            for column, cell in enumerate(band, 1):
                native = [w for w in words if cell.contains(
                    (f.Rect(w[:4]).tl+f.Rect(w[:4]).br)/2)]
                reliable = bool(native) and all('\ufffd' not in w[4] and not any(
                    ord(c) < 32 for c in w[4]) for w in native)
                filled += bool(native)
                result.append(dict(box=list(cell), row=row, column=column,
                    grid=0, native_reliable=reliable))
            populated &= filled >= 2
        if populated:
            return result
    return []


def native_cell_layout(page, area):
    """Reserve each label's safe band inside its native cell, including tiers."""
    import pymupdf as f
    result = {}
    images = [f.Rect(im['bbox']) for im in page.get_image_info()]
    for region in area['regions']:
        b = f.Rect(region['box'])
        entry = next((c for c in area.get('cells', []) if
                      (f.Rect(c['box']) + (-.5, -.5, .5, .5)).contains(b)), None)
        if entry is None:
            continue
        cell = f.Rect(entry['box']) + (1, 1, -1, -1)
        own = [f.Rect(box) for box in region.get('source_boxes', [region['box']])]
        obstacles = list(images)
        for line in area.get('lines', []):
            for word in line.get('words', []) or [line]:
                wb = f.Rect(word['box'])
                if not any((wb & box).get_area() > wb.get_area()*.5 for box in own):
                    obstacles.append(wb)
        for wb in obstacles:
            if not cell.intersects(wb):
                continue
            if min(wb.y1, b.y1) > max(wb.y0, b.y0):
                if wb.x0 >= b.x1:
                    cell.x1 = min(cell.x1, wb.x0-.6)
                elif wb.x1 <= b.x0:
                    cell.x0 = max(cell.x0, wb.x1+.6)
                elif wb.intersects(b) and (wb.y0 >= (b.y0+b.y1)/2
                        or wb.y1 <= (b.y0+b.y1)/2):
                    # A neighbouring superscript can intrude diagonally into
                    # this run's bounds. Keep its corner out of the new lane.
                    minimum = max(6, b.height*.85)
                    if wb.y0 >= (b.y0+b.y1)/2 and wb.y0-.6-cell.y0 >= minimum:
                        cell.y1 = min(cell.y1, wb.y0-.6)
                    elif wb.y1 <= (b.y0+b.y1)/2 and cell.y1-wb.y1-.6 >= minimum:
                        cell.y0 = max(cell.y0, wb.y1+.6)
                    elif wb.x1 <= (b.x0+b.x1)/2:
                        cell.x0 = max(cell.x0, wb.x1+.6)
                    elif wb.x0 >= (b.x0+b.x1)/2:
                        cell.x1 = min(cell.x1, wb.x0-.6)
            if min(wb.x1, b.x1) > max(wb.x0, b.x0):
                # Split the whitespace between separate labels in a merged
                # cell. Do not align their different tiers onto one baseline.
                if wb.y0 >= b.y1-.05:
                    cell.y1 = min(cell.y1, (wb.y0+b.y1)/2-.3)
                elif wb.y1 <= b.y0+.05:
                    cell.y0 = max(cell.y0, (wb.y1+b.y0)/2+.3)
        result[region['id']] = dict(layout_box=list(cell), cell_box=entry['box'],
            row=entry['row'], column=entry['column'], geometry='table-rules', native_cell=True)
    return result


def ruled_cells(page, area):
    """Use enclosing table rules, not tiny formula/subscript OCR row centres."""
    import cv2
    import numpy as np
    import pymupdf as f
    native = native_cell_layout(page, area)
    if len(native) == len(area['regions']):
        return native
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
    result.update(native)
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
        # Centered headers and indented continuation lines are not column
        # separators. Bound this run with neighbours in its own row instead.
        x0 = max(bounds.x0, b.x0)
        x1 = bounds.x1-1
        own = [f.Rect(box) for box in r.get('source_boxes', [r['box']])]
        for line in lines:
            for w in line.get('words',[]) or [line]:
                wb=f.Rect(w['box'])
                if any((wb & q).get_area() > wb.get_area()*.5 for q in own):
                    continue
                if wb.x0>=b.x1 and wb.y1>y0 and wb.y0<y1:
                    x1=min(x1,wb.x0-1)
        result[r['id']] = dict(layout_box=[x0,y0+.25,x1,y1-.25],row=row+1,column=col+1)
    if page is not None:
        result.update(ruled_cells(page,area))
    return result


def blank_label_lane(page, area, region, size):
    """Bounded growth into white pixels only; source/erase boxes never grow.

    Split gaps with other detections, including protected symbols. The final
    overlay still has to pass the retained-ink and replacement-ink checks.
    """
    import numpy as np
    import pymupdf as f
    b = f.Rect(region['box'])
    # Translation may need several extra glyphs along the reading direction.
    # Keep cross-line growth small so dense rows cannot borrow each other's
    # bands. Every added strip still requires measured white pixels.
    along, across = min(24, size*3), min(8, size)*.6
    dx, dy = (across, along) if region.get('rotation') in (90, 270) else (along, across)
    limit = (b + (-dx, -dy, dx, dy)) & f.Rect(area['box']) & page.rect
    if limit.is_empty or not limit.contains(b):
        return list(b)
    own = [f.Rect(q) for q in region.get('source_boxes', [region['box']])]
    obstacles = [f.Rect(w['box']) for line in area.get('lines', [])
                 for w in line.get('words', []) or [line]]
    obstacles += [f.Rect(r['box']) for r in area['regions'] if r['id'] != region['id']]
    for other in obstacles:
        if any((other & q).get_area() > other.get_area()*.5 for q in own):
            continue
        if other.x1 > limit.x0 and other.x0 < limit.x1:
            if other.y1 <= b.y0:
                limit.y0 = max(limit.y0, (other.y1+b.y0)/2+.25)
            elif other.y0 >= b.y1:
                limit.y1 = min(limit.y1, (other.y0+b.y1)/2-.25)
        if other.y1 > limit.y0 and other.y0 < limit.y1:
            if other.x1 <= b.x0:
                limit.x0 = max(limit.x0, (other.x1+b.x0)/2+.25)
            elif other.x0 >= b.x1:
                limit.x1 = min(limit.x1, (other.x0+b.x1)/2-.25)
    if not limit.contains(b):
        return list(b)
    pix = page.get_pixmap(matrix=f.Matrix(4, 4), clip=limit, colorspace=f.csRGB, alpha=False)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    def white(rect):
        x0, y0 = max(0, int(rect.x0*4-pix.x)), max(0, int(rect.y0*4-pix.y))
        x1, y1 = min(pix.width, int(np.ceil(rect.x1*4-pix.x))), min(pix.height, int(np.ceil(rect.y1*4-pix.y)))
        return bool(x1>x0 and y1>y0 and np.all(rgb[y0:y1, x0:x1] >= 250))
    lane = f.Rect(b)
    # Quarter-point steps avoid jumping over thin rules or pale data marks.
    for side in (1, 3, 0, 2):
        sign = -1 if side < 2 else 1
        while abs(lane[side]-limit[side]) > .01:
            candidate = f.Rect(lane)
            candidate[side] = max(limit[side], lane[side]-.25) if sign<0 else min(limit[side], lane[side]+.25)
            strip = (f.Rect(candidate.x0, candidate.y0, candidate.x1, lane.y0) if side==1 else
                     f.Rect(candidate.x0, lane.y1, candidate.x1, candidate.y1) if side==3 else
                     f.Rect(candidate.x0, candidate.y0, lane.x0, candidate.y1) if side==0 else
                     f.Rect(lane.x1, candidate.y0, candidate.x1, candidate.y1))
            if not white(strip):
                break
            lane = candidate
    return list(lane)


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
    cells = table_cells(area,page) if area['kind']=='table' else native_cell_layout(page,area)
    if cells:
        for r in regions:
            if r['id'] in cells:
                r.update(cells[r['id']],group=area['id']+'-cell-'+r['id'],preferred_size=size)
        # Share the first baseline, not the bottom edge: long labels may wrap
        # in a tall cell while the adjacent cell reserves its lower unit tier.
        for row in {r['row'] for r in regions if r.get('geometry')=='table-rules' and not r.get('native_cell')}:
            peers=[r for r in regions if r.get('geometry')=='table-rules' and r['row']==row and not r.get('native_cell')]
            top=max(r['layout_box'][1] for r in peers)
            for r in peers:
                r['layout_box'][1]=top
                r['align_top']=True
        if len(cells) == len(regions):
            return regions
    plot=plot_bounds(page,area)
    for r in regions:
        if r['id'] in cells:
            continue
        b=f.Rect(r['box'])
        native_size = r.get('native_size')
        r.update(preferred_size=max(6, min(18, native_size)) if native_size and native_size > 0 else size,
                 layout_box=list(b))
        if not r.get('reason'):
            r['layout_box'] = blank_label_lane(page, area, r, r['preferred_size'])
        # A short connector between immutable tokens (TRSB at B=M=0) may
        # need its surrounding whitespace for one CJK glyph. Measure both
        # neighbours; never expand across their source boxes or move them.
        if r.get('source') == 'native' and len(r['text']) <= 3 and r.get('rotation', 0) == 0:
            lane = f.Rect(b.x0-1, b.y0, b.x1+1, b.y1) & f.Rect(area['box'])
            for line in area.get('lines', []):
                for word in line.get('words', []):
                    wb = f.Rect(word['box'])
                    if (wb & b).get_area() > wb.get_area()*.5 or wb.y1 <= b.y0 or wb.y0 >= b.y1:
                        continue
                    if wb.x1 <= b.x0:
                        lane.x0 = max(lane.x0, wb.x1+.3)
                    elif wb.x0 >= b.x1:
                        lane.x1 = min(lane.x1, wb.x0-.3)
            if lane.contains(b):
                r['layout_box'] = list(lane)
        is_y=r.get('rotation') in (90, 270)
        # Axis candidates must lie outside the plot, not among legends/ticks.
        is_x=bool(plot and b.y0>plot.y1 and b.width>b.height*2)
        if is_y or is_x:
            if plot is None:
                if is_y and stationary_axis_label(page, area, b):
                    r.update(geometry='axis-in-place', group=area['id']+'-'+r['id'], layout_box=list(b))
                    continue
                # In a multipanel figure a reliable local label need not move
                # to a guessed axis. Use its measured blank lane in place.
                if (is_y and r.get('source') in {'native', 'ocr'}
                        and r.get('confidence', 0) >= .95
                        and f.Rect(r['layout_box']).width >= r['preferred_size']):
                    r.update(geometry='label-in-place', group=area['id']+'-'+r['id'])
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
                        adjacent=(b.y1-1<=wb.y0<=b.y1+4*b.width if r.get('rotation')==270 else
                                  b.y0-4*b.width<=wb.y1<=b.y0+1)
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
                if 'source_boxes' in r:
                    r['source_boxes']=[*r['source_boxes'], list(suffix)]
            full=f.Rect(r['box'])
            r.update(axis='y' if is_y else 'x',plot_box=list(plot),group=area['id']+'-'+r['id'])
            r['layout_box']=list(f.Rect(full.x0,plot.y0,full.x1,plot.y1) if is_y else
                                 f.Rect(plot.x0,full.y0,plot.x1,full.y1))
        elif plot and plot.contains(b):
            # Being inside one plot does not prove shared legend membership.
            # Preserve each measured lane; fit/erase each label independently.
            # apply_graphics still checks all neighbouring accepted overlays.
            r.update(group=area['id']+'-'+r['id'])
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


def add_side_legend(page, source, records, font_pair):
    """Put verified, space-limited figure translations beside the unchanged plot.

    The side panel is added only to an explicit graphics follow-up. Its entries
    retain the source label for visual matching and a stable numbered reference;
    no text, line or data point within the original page rectangle is moved.
    """
    import pymupdf as f
    from linguafloat.document_fonts import FitzFontResolver, FontPolicyError
    if page.rect != source.rect or not all(font_pair):
        return []
    eligible=[r for r in records if r.get('status')=='needs_review'
              and r.get('kind')=='figure' and r.get('translation')
              and '本组未通过统一排版检查' in r.get('reason','')]
    if not eligible:
        return []
    resolver=FitzFontResolver(*font_pair)
    width,size,line_height=185,7.0,8.6
    top,bottom=43,source.rect.height-22
    try:
        formatted=[]
        for number,record in enumerate(eligible,1):
            source_label=(record.get('source_line') or record.get('text') or '').strip()
            translation=record['translation'].strip()
            if not source_label or not translation:
                continue
            value=f'#{number}  {source_label}：{translation}'
            metrics=GraphicFontMetrics(resolver,value)
            lines=wrapped_lines(value,metrics,size,width-20)
            if not lines:
                continue
            formatted.append((record,number,lines,4+len(lines)*line_height+4))
        if not formatted:
            return []
        for columns in (2,3):
            placements=[]; col=0; y=top
            for record,number,lines,height in formatted:
                if y+height>bottom:
                    col+=1;y=top
                if col>=columns:
                    break
                placements.append((record,number,lines,col,y))
                y+=height
            if len(placements)==len(formatted):
                break
        else:
            return []
        old_width=page.rect.width
        page.set_mediabox(f.Rect(0,0,old_width+width*columns+14,source.rect.height))
        page.draw_line((old_width+6,12),(old_width+6,source.rect.height-12),
                       color=(.65,.57,.78),width=.6)
        with f.open() as overlay:
            canvas=overlay.new_page(width=page.rect.width,height=page.rect.height)
            resolver.draw_line(canvas,(old_width+15,27),'图内文字补译',fontsize=9,
                               bold=True,color=(.34,.24,.48))
            for record,number,lines,col,y in placements:
                x=old_width+14+col*width
                for offset,line in enumerate(lines):
                    resolver.draw_line(canvas,(x,y+offset*line_height),line,fontsize=size,
                                       color=(.14,.14,.14))
                record.update(status='legend',legend_number=number,
                              legend_box=[x,y-size,x+width-12,y+len(lines)*line_height],
                              reason='原位空间不足；译文列于图旁，原图标签和数据保持原样。')
            page.show_pdf_page(page.rect,overlay,0)
        return [r for r,_,_,_,_ in placements]
    except FontPolicyError:
        return []


class GraphicFontMetrics:
    """Measure the same mixed runs that the selected-font writer will draw."""
    def __init__(self, resolver, text, bold=False, italic=False):
        self.resolver, self.bold, self.italic = resolver, bold, italic
        faces = [face for _, face in resolver.runs(''.join(text.splitlines()), bold=bold, italic=italic)]
        self.faces = faces
        self.ascender = max((face.ascender for face in faces), default=1)
        self.descender = min((face.descender for face in faces), default=0)

    def padding(self, size):
        return tuple(max((face.ink_padding(size)[i] for face in self.faces), default=0) for i in range(4))

    def text_length(self, text, fontsize):
        if not text:
            return 0
        left, _, right, _ = self.padding(fontsize)
        return left+right+sum(face.text_length(run, fontsize=fontsize) for run, face in
            self.resolver.runs(text, bold=self.bold, italic=self.italic))


def compose_native_headers(source, area, prepared, records):
    """Join validated translations and native scientific runs after cache lookup.

    Only corroborated native first rows qualify. Translation/cache boundaries
    stay unchanged; a failed member keeps the entire cell on its old safe path.
    """
    import pymupdf as f
    from linguafloat.document_graphics import glyph_patch
    if area['kind'] != 'table':
        return prepared
    headers = [c for c in area.get('cells', []) if c['row'] == 1 and c['native_reliable']]
    eligible = [e for e in prepared if e[0].get('row') == 1 and e[0].get('native_cell')
                and e[0].get('source') == 'native' and not e[0].get('rotation')]
    rejected = set()
    for cell in headers:
        bounds = f.Rect(cell['box'])
        expected = {r['id'] for r in area['regions'] if bounds.contains(
            (f.Rect(r['box']).tl+f.Rect(r['box']).br)/2)}
        members = [e for e in eligible if e[0]['cell_box'] == cell['box']]
        if expected != {e[0]['id'] for e in members}:
            rejected.update(e[0]['id'] for e in members)
            for entry in members:
                records.append(dict(entry[2],status='needs_review',translation=entry[3],
                    reason='同一表头存在未成功或已跳过的片段，整格保留原文，避免半译及符号错配。'))
    prepared = [e for e in prepared if e[0]['id'] not in rejected]
    eligible = [e for e in eligible if e[0]['id'] not in rejected]
    natural_columns = {e[0]['column'] for e in eligible}
    if len(natural_columns) < 2:
        return prepared
    data_columns = {c['column'] for c in area.get('cells', []) if c['row'] > 1
        and any(f.Rect(c['box']).contains((f.Rect(r['box']).tl+f.Rect(r['box']).br)/2)
                for r in area['regions'])}
    if data_columns and len(natural_columns-data_columns) < 1:
        return prepared  # Prose-filled rows alone do not prove a header.
    output, consumed = [], set()
    for cell in headers:
        bounds = f.Rect(cell['box'])
        expected = [r for r in area['regions'] if bounds.contains(
            (f.Rect(r['box']).tl+f.Rect(r['box']).br)/2)]
        members = [e for e in eligible if e[0]['cell_box'] == cell['box']]
        if {r['id'] for r in expected} != {e[0]['id'] for e in members}:
            continue
        words = []
        for line in area['lines']:
            if line.get('source') != 'native' or line.get('rotation', 0):
                continue
            for word in line.get('words', []) or [line]:
                if bounds.contains((f.Rect(word['box']).tl+f.Rect(word['box']).br)/2):
                    words.append(dict(word, baseline=line.get('baseline', word['box'][3])))
        if not words:
            continue
        # Reconstruct visual reading order by baseline, not superscript top.
        typical = median(w.get('size', 8) for w in words)
        rows = []
        for word in sorted(words, key=lambda w:w['baseline']):
            if not rows or abs(word['baseline']-rows[-1][0]['baseline']) > typical*.65:
                rows.append([])
            rows[-1].append(word)
        words = [w for row in rows for w in sorted(row, key=lambda w:w['box'][0])]
        parts, added = [], set()
        for word in words:
            owner = next((e for e in members if any(
                (f.Rect(b)+(-.02,-.02,.02,.02)).contains(f.Rect(word['box']))
                for b in e[0].get('source_boxes', []))), None)
            if owner:
                if owner[0]['id'] not in added:
                    value = ''.join(owner[3].splitlines()).strip()
                    # Match a source bracket whose closing glyph is protected.
                    if '(' in owner[0]['text']:
                        value = value.replace('\uff08', '(')
                    if ')' in owner[0]['text']:
                        value = value.replace('\uff09', ')')
                    parts.append(dict(text=value,
                        bold=owner[0].get('native_bold', False),
                        italic=owner[0].get('native_italic', False)))
                    added.add(owner[0]['id'])
            else:
                parts.append(dict(text=word['text'], clip=word['box'],
                                  baseline=word['baseline']))
        if added != {e[0]['id'] for e in members}:
            continue
        region = dict(members[0][0] if members else eligible[0][0])
        region.pop('_fit_failure', None)
        region.update(id=f"{area['id']}-header-{cell.get('grid', 0)}-{cell['column']}",
            box=list(bounds), source_boxes=[w['box'] for w in words], text=' '.join(w['text'] for w in words),
            cell_box=list(bounds), layout_box=list(bounds+(.65,.65,-.65,-.65)),
            row=1, column=cell['column'], header_cell=True, header_parts=parts,
            group=f"{area['id']}-header-{cell.get('grid', 0)}",
            _header_members=[e[2] for e in members],
            _header_translations=[e[3] for e in members])
        patch, reason = glyph_patch(source, region)
        if reason:
            continue
        record = {k:v for k,v in region.items() if not k.startswith('_') and k != 'header_parts'}
        record['page'] = source.number+1
        output.append((region, patch, record, ' '.join(p['text'] for p in parts)))
        consumed.update(e[0]['id'] for e in members)
    return [e for e in prepared if e[0]['id'] not in consumed]+output


def fit_header(page, region, target, control, size, source):
    """Measure and center complete mixed header lines; copy science as vectors."""
    import re
    import pymupdf as f
    from linguafloat.document_graphics import native_erase_boxes
    resolver = region.get('_font_resolver')
    box = f.Rect(region['layout_box'])
    font = f.Font({'ja':'japan','ko':'korea'}.get(target,'china-s')) if resolver is None else None
    parts = region['header_parts']
    if font is not None:
        missing = [f'U+{ord(c):04X}' for part in parts if 'clip' not in part
                   for c in part['text'] if not c.isspace() and not font.has_glyph(ord(c))]
        if missing:
            region['_fit_failure'] = '目标字体缺少字形 '+', '.join(dict.fromkeys(missing))+'；完整表头保留原文。'
            return None
    native_chars = [(s['size'], c) for b in source.get_text('rawdict')['blocks']
        for l in b.get('lines', []) for s in l['spans'] for c in s['chars'] if not c['c'].isspace()]
    with f.open() as artwork, f.open() as canvas, f.open() as overlay:
        atoms = []
        for part in parts:
            if 'clip' in part:
                clip = f.Rect(part['clip'])
                artwork.insert_pdf(source.parent, from_page=source.number, to_page=source.number)
                art = artwork[-1]
                # Isolate every native run, not just translated prose. An upper
                # row's word box can intersect a lower row's scientific script.
                unwanted = [c['bbox'] for _,c in native_chars
                    if f.Rect(region['cell_box']).contains((f.Rect(c['bbox']).tl+f.Rect(c['bbox']).br)/2)
                    and not (clip+(-.02,-.02,.02,.02)).contains(f.Rect(c['bbox']))]
                if unwanted:
                    erasures = native_erase_boxes(source, dict(box=region['box'], source_boxes=unwanted))
                    if not erasures:
                        region['_fit_failure'] = '科学字形与邻行字符无法可靠分离，整格保留原文。'
                        return None
                    for b in erasures:
                        art.add_redact_annot(f.Rect(b), fill=False, cross_out=False)
                    art.apply_redactions(images=0, graphics=0, text=0)
                art_page = len(artwork)-1
                # Original baselines can be taken from a superscript-only line.
                # The largest native character size identifies its base tier.
                chars = [(sz, c['origin'][1]) for sz,c in native_chars
                    if (clip+(-.02,-.02,.02,.02)).contains(f.Rect(c['bbox']))]
                baseline = median(y for sz,y in chars if sz >= max(t for t,_ in chars)*.9) if chars else part['baseline']
                atom = dict(part, width=clip.width, ascent=baseline-clip.y0,
                            descent=clip.y1-baseline, vectors=[(clip, 0, baseline, art_page)],
                            last_clip=clip, last_baseline=baseline, anchor=baseline,
                            native_size=max((sz for sz,_ in chars), default=clip.height), last_dx=0)
                if atoms and atoms[-1].get('vectors'):
                    previous = atoms[-1]
                    last = previous['last_clip']
                    native_size = max(previous['native_size'], atom['native_size'])
                    same_tier = (-.5 <= clip.x0-last.x1 <= native_size*1.5
                        and abs(baseline-previous['last_baseline']) <= native_size*.85)
                    if same_tier:
                        # Adjacent scripts share their original coordinate frame;
                        # only source line wraps (e.g. a species) are normalized.
                        baseline = previous['anchor']
                        atom['ascent'], atom['descent'] = baseline-clip.y0, clip.y1-baseline
                    gap = native_size*.24 if re.search(r'[A-Za-z.]$', previous['text']) and re.match(r'[A-Za-z0-9]', part['text']) else native_size*.12
                    offset = previous['last_dx']+clip.x0-last.x0 if same_tier else previous['width']+gap
                    previous['vectors'].append((clip, offset, baseline, art_page))
                    previous['width'] = max(previous['width'], offset+clip.width)
                    previous['ascent'] = max(previous['ascent'], atom['ascent'])
                    previous['descent'] = max(previous['descent'], atom['descent'])
                    previous['text'] += (' ' if gap >= size*.2 else '')+part['text']
                    previous.update(last_clip=clip,last_baseline=atom['last_baseline'],anchor=baseline,
                                    native_size=native_size,last_dx=offset)
                else:
                    atoms.append(atom)
                continue
            value = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', part['text'])
            metrics = GraphicFontMetrics(resolver, value, part['bold'], part['italic']) if resolver else font
            for token in re.findall(r'[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*|[^\s]', value):
                atoms.append(dict(text=token, width=metrics.text_length(token, fontsize=size),
                    ascent=metrics.ascender*size, descent=-metrics.descender*size,
                    bold=part['bold'], italic=part['italic']))
        # Keep bracketed units and protected clusters together whenever they fit.
        chunks, current, depth = [], [], 0
        for atom in atoms:
            if atom['text'][0] in '([\uff08':
                depth += 1
            current.append(atom)
            if atom['text'][-1] in ')]\uff09':
                depth = max(0, depth-1)
            if not depth:
                chunks.append(current); current=[]
        if current:
            chunks.extend([[a] for a in current])
        chunks = [g for chunk in chunks for g in
                  ([chunk] if sum(a['width'] for a in chunk) <= box.width else [[a] for a in chunk])]
        lines, line = [], []
        for chunk in chunks:
            if line and sum(a['width'] for a in line+chunk) > box.width:
                # Opening punctuation belongs with the following content.
                carry = []
                while line and line[-1]['text'] in '([\uff08':
                    carry.insert(0, line.pop())
                lines.append(line); line=carry
            line.extend(chunk)
        if line:
            lines.append(line)
        if not lines or any(sum(a['width'] for a in line)>box.width for line in lines):
            region['_fit_failure'] = '完整表头中的科学标识超出单元格宽度；未拆散或缩放原始科学字形。'
            return None
        dimensions = [(max(a['ascent'] for a in line), max(a['descent'] for a in line)) for line in lines]
        height = sum(a+d for a,d in dimensions)+size*.08*(len(lines)-1)
        if height > box.height:
            region['_fit_failure'] = '完整表头在正常字号下超出单元格高度；未改动数据或表格边界。'
            return None
        control.checkpoint()
        p = canvas.new_page(width=box.width+20, height=box.height+20)
        if font:
            p.insert_font(fontname='LFHeader', fontbuffer=font.buffer)
        y = 10+(box.height-height)/2
        geometry = []
        for line, (ascent, descent) in zip(lines, dimensions):
            x = 10+(box.width-sum(a['width'] for a in line))/2
            baseline = y+ascent
            positioned = []
            for atom in line:
                positioned.append((atom,x))
                x += atom['width']
            # Vector clips go first; their background must not paint new text.
            for atom,x in positioned:
                if atom.get('vectors'):
                    for clip, dx, original_baseline, art_page in atom['vectors']:
                        dest = f.Rect(x+dx, baseline+clip.y0-original_baseline,
                                      x+dx+clip.width, baseline+clip.y1-original_baseline)
                        p.show_pdf_page(dest, artwork, art_page, clip=clip)
            for atom,x in positioned:
                if not atom.get('vectors'):
                    if resolver:
                        resolver.draw_line(p, (x,baseline), atom['text'], fontsize=size,
                            bold=atom['bold'], italic=atom['italic'], color=(.12,.12,.12))
                    else:
                        p.insert_text((x,baseline), atom['text'], fontname='LFHeader', fontsize=size, color=(.12,.12,.12))
            geometry.append(dict(width=sum(a['width'] for a in line), text=''.join(a['text'] for a in line)))
            y += ascent+descent+size*.08
        actual = [f.Rect(c['bbox']) for b in p.get_text('rawdict')['blocks']
                  for l in b.get('lines', []) for s in l['spans'] for c in s['chars'] if not c['c'].isspace()]
        if not actual:
            return None
        ink = f.Rect(actual[0])
        for bound in actual[1:]:
            ink |= bound
        dx,dy = 10+box.width/2-(ink.x0+ink.x1)/2, 10+box.height/2-(ink.y0+ink.y1)/2
        if not f.Rect(10,10,10+box.width,10+box.height).contains(ink+(dx,dy,dx,dy)):
            region['_fit_failure'] = '表头实际字形超出完整单元格；未覆盖相邻内容。'
            return None
        out = overlay.new_page(width=page.rect.width, height=page.rect.height)
        out.show_pdf_page(box, canvas, 0, clip=f.Rect(10-dx,10-dy,10+box.width-dx,10+box.height-dy))
        metadata = dict(size=size, rows=len(lines), layout_box=list(box), row=1,
            column=region['column'], header_cell=True, header_content=''.join(a['text'] for a in atoms),
            header_lines=geometry, native_vectors=True)
        if resolver:
            metadata['font_policy'] = resolver.report()
        return overlay.tobytes(), metadata


def fit_label(page,region,text,target,control,size,source=None):
    """Render a fixed size in a planned cell, or center an axis with its suffix."""
    import pymupdf as f
    if region.get('header_parts') and source is not None:
        from linguafloat.document_fonts import FontPolicyError
        try:
            return fit_header(page, region, target, control, size, source)
        except FontPolicyError as error:
            region['_fit_failure'] = str(error)
            return None
    box=f.Rect(region.get('layout_box',region['box']))
    rotation=region.get('rotation', 0)
    rotated=rotation in (90, 270)
    width,height=(box.height,box.width) if rotated else (box.width,box.height)
    resolver = region.get('_font_resolver')
    if resolver is not None:
        from linguafloat.document_fonts import FontPolicyError
        try:
            font = GraphicFontMetrics(resolver, text, region.get('native_bold', False), region.get('native_italic', False))
        except FontPolicyError as error:
            region['_fit_failure'] = str(error)
            return None
        missing = []
    else:
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
    if resolver is not None:
        left, top, right, bottom = font.padding(size)
        ink_height = (font.ascender-font.descender)*size+top+bottom
        line_height = max(line_height, ink_height)
    total_height=(len(lines)-1)*line_height+ink_height
    if not lines or max(font.text_length(s,fontsize=size) for s in lines)+sw+gap>width or max(total_height,sh)>height+.25:
        region['_fit_failure']=f'可用区域 {width:.1f}×{height:.1f}pt 放不下完整译文（已尝试换行和统一 {size:g}pt 字号）；需检查单元格/轴标题边界。'
        return None
    region.pop('_fit_failure',None)
    control.checkpoint()
    with f.open() as temp,f.open() as overlay:
        p=temp.new_page(width=width+20,height=height+20)
        if resolver is None:
            p.insert_font(fontname='LFGraphic',fontbuffer=font.buffer)
        content_width=font.text_length(text,fontsize=size)+sw+gap if axis else width
        x=10+(width-content_width)/2 if axis else 10
        offset=0 if region.get('align_top') else (height-total_height)/2
        if region.get('native_cell') and not region.get('header_cell') and not rotated and not region.get('align_top'):
            original=f.Rect(region['box'])
            offset=max(0,min(height-total_height,(original.y0+original.y1)/2-box.y0-total_height/2))
        y=10+offset+size*.88
        if resolver is not None:
            y=10+offset+font.ascender*size+top
        for i,line in enumerate(lines):
            if region.get('header_cell'):
                x=10+(width-font.text_length(line,fontsize=size))/2
            if resolver is None:
                p.insert_text((x,y+i*line_height),line,fontname='LFGraphic',fontsize=size,color=(.12,.12,.12))
            else:
                resolver.draw_line(p, (x+left, y+i*line_height), line, fontsize=size,
                                   bold=font.bold, italic=font.italic, color=(.12,.12,.12))
        if suffix:
            sx=x+font.text_length(text,fontsize=size)+gap
            dest=f.Rect(sx,10+(height-sh)/2,sx+sw,10+(height+sh)/2)
            # Preserve original vector glyphs / pixels and superscripts, unscaled.
            p.show_pdf_page(dest,source.parent,source.number,clip=suffix,rotate=(360-rotation)%360 if rotated else 0)
        out=overlay.new_page(width=page.rect.width,height=page.rect.height)
        out.show_pdf_page(box,temp,0,clip=f.Rect(10,10,10+width,10+height),rotate=rotation if rotated else 0)
        metadata=dict(size=size,rows=len(lines),layout_box=list(box),
             axis=region.get('axis'),plot_box=region.get('plot_box'),row=region.get('row'),column=region.get('column'))
        if resolver is not None:
            metadata['font_policy'] = resolver.report()
        return overlay.tobytes(), metadata


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
            for box in r.get('erase_boxes', r.get('source_boxes', [r['box']])):
                p.add_redact_annot(f.Rect(box),fill=False,cross_out=False)
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
