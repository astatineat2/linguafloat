"""Source-grounded author, affiliation and copyright layout, in page coordinates."""
from __future__ import annotations

import re
from statistics import median


def plan_frontmatter(page):
    import pymupdf as fitz
    lines = [dict(text="".join(s["text"] for s in line["spans"]), box=line["bbox"], spans=line["spans"])
             for block in page.get_text("dict")["blocks"] for line in block.get("lines", [])]
    if page.rotation or page.cropbox.x0 or page.cropbox.y0:
        return []
    body = [fitz.Rect(l["box"]) for l in lines if page.rect.height*.25 < l["box"][1] < page.rect.height*.85
            and len(l["text"]) > 45]
    if not body:
        return []
    left, right = min(b.x0 for b in body), max(b.x1 for b in body)
    center = (left+right)/2
    affiliations = [l for l in lines if l["box"][1] < page.rect.height*.27
                    and re.match(r"^[1-9]\s*[A-Za-z]", l["text"])
                    and re.search(r"\b(Department|Institute|University|Laborator|School|Center|Centre)", l["text"].lstrip('0123456789 '), re.I)]
    affiliations.sort(key=lambda l:l['box'][1])
    groups=[]
    if len(affiliations)>=2:
        nums=[int(re.match(r'\d+',l['text'])[0]) for l in affiliations]
        aligned=all(abs(l['box'][0]-affiliations[0]['box'][0])<3 for l in affiliations)
        centered=all(abs((l['box'][0]+l['box'][2])/2-center)<8 for l in affiliations)
        if nums==list(range(1,len(nums)+1)) and (aligned or centered):
            ordered=sorted(lines,key=lambda l:l['box'][1]); starts=[ordered.index(l) for l in affiliations]
            for i,k in enumerate(starts):
                group=dict(ordered[k]);end=starts[i+1] if i+1<len(starts) else len(ordered)
                for row in ordered[k+1:end]:
                    rect=fitz.Rect(row['box']);prev=fitz.Rect(group['box']);height=fitz.Rect(ordered[k]['box']).height
                    if (not -1.5<=rect.y0-prev.y1<=height*.65 or abs(rect.x0-prev.x0)>3
                            or re.match(r'^[*#\d]|https?://',row['text']) or '@' in row['text']
                            or abs(rect.height-height)>2 or (i+1==len(starts) and not group['text'].rstrip().endswith((',', ' of',' and')))):
                        if i+1<len(starts):groups=[];break
                        break
                    group=dict(text=group['text']+' '+row['text'],box=list(prev|rect),spans=group['spans']+row['spans'])
                else:
                    groups.append(group);continue
                if i+1<len(starts):break
                groups.append(group)
    affiliations=groups
    plans = []
    if len(affiliations) >= 2:
        first = min(l["box"][1] for l in affiliations)
        authors = [l for l in lines if 0 < first-l["box"][3] < 25 and l["text"].count(",") >= 1
                   and any(s["flags"] & 1 for s in l["spans"])
                   and abs((l["box"][0]+l["box"][2])/2-center) < 8]
        if len(authors) == 1:
            l = authors[0]
            plans.append(dict(kind="authors", box=[left, l["box"][1]-1, right, l["box"][3]+1], lines=[l]))
        top, bottom = min(l["box"][1] for l in affiliations), max(l["box"][3] for l in affiliations)
        next_y = min((l["box"][1] for l in lines if l["box"][1] > bottom+1), default=bottom+5)
        prev_y=max((l['box'][3] for l in lines if l['box'][3]<top),default=top-2)
        plans.append(dict(kind="affiliations", box=[left, max(top-1,(prev_y+top)/2), right, min((next_y+bottom)/2,bottom+5)],
                          lines=sorted(affiliations, key=lambda l:l["box"][1])))
    copyright_lines = [l for l in lines if l["box"][1] > page.rect.height*.85 and "©" in l["text"]
                       and re.search(r"permission|copyright|reserved", l["text"], re.I)]
    for line in copyright_lines:
        following = [l for l in lines if 0 < l["box"][1]-line["box"][3] < 7 and "http" in l["text"]]
        if len(following) != 1:
            continue
        block = [line, following[0]]
        bottom = following[0]["box"][3]
        next_y = min((l["box"][1] for l in lines if l["box"][1] > bottom+1), default=bottom+10)
        plans.append(dict(kind="copyright", box=[left,line["box"][1]-1,right,min(next_y-2,bottom+10)], lines=block))
    return plans


def paragraph_in_plan(paragraph, page_height, plans):
    import pymupdf as fitz
    box = paragraph.box
    if not box:
        return False
    rect = fitz.Rect(box.x, page_height-box.y2, box.x2, page_height-box.y)
    return any((rect & fitz.Rect(p["box"])).get_area() > rect.get_area()*.65 for p in plans)


def centered_overlay(page, plan, texts, target, control, font_pair=("", "")):
    """Keep identifiers raised and choose two copyright lines before three."""
    import pymupdf as fitz
    from linguafloat.document_layout import font_scales
    box = fitz.Rect(plan["box"])
    size = median(s["size"] for l in plan["lines"] for s in l["spans"] if len(s["text"]) > 2)
    font = fitz.Font({"ja":"japan", "ko":"korea"}.get(target,"china-s"))
    resolver = None
    if font_pair[0]:
        from linguafloat.document_fonts import FitzFontResolver
        resolver = FitzFontResolver(*font_pair)
    def width(text, fontsize):
        return (sum(face.text_length(run, fontsize=fontsize) for run, face in resolver.runs(text))
                if resolver else font.text_length(text, fontsize=fontsize))
    def draw(p, origin, text, fontsize):
        if resolver:
            resolver.draw_line(p, origin, text, fontsize=fontsize)
        else:
            p.insert_text(origin, text, fontname="LFMeta", fontsize=fontsize)
    if any(not font.has_glyph(ord(c)) for _, text in texts for c in text if not c.isspace()):
        return None
    for scale in font_scales(size):
        fs = size*scale
        for spacing in (1.3,1.2):
            control.checkpoint()
            rows = []
            for label, text in texts:
                text = re.sub(r"\s+", " ", text).strip()
                # Wrap at whitespace or between CJK glyphs, never inside a URL.
                tokens = re.findall(r"https?://\S+|[\u3400-\u9fff]|[^\s\u3400-\u9fff]+\s*", text)
                row = ""
                first = True
                for token in tokens:
                    prefix = width(label,fs*.75)+2 if first and label else 0
                    if row and width(row+token,fs)+prefix > box.width:
                        rows.append((label if first else "",row.strip()))
                        first,row = False,""
                    row += token
                if row:
                    rows.append((label if first else "",row.strip()))
            if plan["kind"] == "copyright" and len(rows) > 3:
                continue
            if any(width(t,fs)+width(n,fs*.75)+2 > box.width for n,t in rows):
                continue
            height = fs + max(0,len(rows)-1)*fs*spacing
            if height > box.height:
                continue
            with fitz.open() as overlay:
                p = overlay.new_page(width=page.rect.width,height=page.rect.height)
                if not resolver:
                    p.insert_font(fontname="LFMeta",fontbuffer=font.buffer)
                y = box.y0+fs
                for label,text in rows:
                    label_width = width(label,fs*.75)+2 if label else 0
                    line_width = width(text,fs)+label_width
                    x = (box.x0+box.x1-line_width)/2
                    if label:
                        draw(p,(x,y-fs*.32),label,fs*.75)
                    draw(p,(x+label_width,y),text,fs)
                    y += fs*spacing
                overlay.subset_fonts()
                return overlay.tobytes(), dict(size=fs,rows=len(rows), font_policy=resolver.report() if resolver else {})
    return None


def repair_frontmatter(source, page, translator):
    import pymupdf as fitz
    from linguafloat.document_core import DocumentError
    records = []
    for plan in plan_frontmatter(source):
        boundary = translator.job.reference_boundary if translator.job.skip_references else {}
        if boundary and source.number > boundary["page"]:
            continue
        if boundary and source.number == boundary["page"] and any(
                fitz.Rect(plan["box"]).intersects(fitz.Rect(box)) for box in boundary["boxes"]):
            continue
        translator.control.checkpoint()
        rect = fitz.Rect(plan["box"])
        kind = plan["kind"]
        def prepare():
            texts = []
            for line in plan["lines"]:
                text = line["text"]
                label = ""
                if kind == "affiliations":
                    match = re.match(r"^(\d+)\s*(.*)",text)
                    label,text = match.groups()
                if "http" in text:
                    # Keep the complete legal link in its own row.
                    reference=re.fullmatch(r"See (https?://\S+) for more information\.",text.strip())
                    value = "更多信息："+reference[1] if reference and translator.job.target=="zh-Hans" else text
                else:
                    translator.location = f"PDF 第 {source.number+1} 页 · {kind} {label}"
                    value = translator.translate_fragment(text, "Academic front matter. Preserve institution names, addresses, postal codes and copyright identifiers.",part=kind)
                texts.append((label,value))
            fitted = centered_overlay(page,plan,texts,translator.job.target,translator.control,
                                      (getattr(translator.job,"chinese_font",""), getattr(translator.job,"latin_font","")))
            if not fitted:
                raise DocumentError(f"[论文信息排版失败] 第 {source.number+1} 页 {kind}：无法在原页安全区域内完整排入。译文缓存已保留。")
            return fitted
        issue = {}
        try:
            fitted = None if kind == "authors" else prepare()
        except Exception as error:
            from linguafloat.document_failures import diagnostic
            issue = diagnostic(error, "论文信息翻译/排版")
            fitted = None
        # This band is source-verified metadata, with neighboring body/footer excluded.
        page.add_redact_annot(rect,fill=False,cross_out=False)
        page.apply_redactions(images=0,graphics=0,text=0)
        if fitted is None:
            from linguafloat.document_clip import show_source_region
            show_source_region(page,rect,source.parent,source.number)
        else:
            with fitz.open(stream=fitted[0],filetype="pdf") as overlay:
                page.show_pdf_page(page.rect,overlay,0)
            if kind == "copyright":
                for line in plan["lines"]:
                    for url in re.findall(r"https?://[^\s]+",line["text"]):
                        page.insert_link({"kind":fitz.LINK_URI,"from":rect,"uri":url})
        records.append(dict(page=source.number+1,kind=kind,box=list(rect),
            **(dict(issue, status="untranslated", coordinates="top-left") if issue else (fitted[1] if fitted else {}))))
    return records
