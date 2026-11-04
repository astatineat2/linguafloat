"""Local graphic text discovery and conservative ink-only replacements."""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

SCHEMA = "graphics-v4"

ELEMENTS = frozenset("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())


def source_digest(path):
    with open(path,"rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()


def natural_word(text):
    text = text.strip(".,:;()[]")
    if re.search(r"\d|[=∑∫√±×÷_^]",text):
        return False
    if text in {"mm","cm","nm","GHz","MHz","kHz","Hz","mT","nT","mA","kPa","MPa","GHz","mV","mol"}:
        return False
    chemical = text.replace("-", "")
    elements = re.findall(r"[A-Z][a-z]?", chemical)
    if len(elements) >= 2 and "".join(elements) == chemical and all(e in ELEMENTS for e in elements):
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z'-]{2,}",text) or re.search(r"[\u3400-\u9fff]{2,}",text))


def visible_native_words(page, rect):
    """Never prefer an invisible OCR layer over the visible scan."""
    import pymupdf as fitz
    hidden = [fitz.Rect(s["bbox"]) for s in page.get_texttrace()
              if s.get("type") == 3 or s.get("opacity", 1) <= .05]
    return [w for w in page.get_text("words", clip=rect)
            if not any(h.contains((fitz.Rect(w[:4]).tl + fitz.Rect(w[:4]).br) / 2) for h in hidden)]


def scientific_tokens(page):
    """Evidence from *source* typography/definitions, never a guessed formula."""
    tokens=set(re.findall(r'\(([A-Z]{2,6})\)',page.get_text()))
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines',[]):
            spans=line['spans']
            for previous,sub in zip(spans,spans[1:]):
                stem=re.search(r'\b([A-Za-z]{1,4})$',previous['text'])
                suffix=sub['text'].strip()
                if (stem and re.fullmatch(r'[A-Za-z0-9]{1,3}',suffix)
                    and sub['size']<previous['size']*.85
                    and abs(sub['origin'][1]-previous['origin'][1])>.5):
                    tokens.add(stem[1]+suffix)
    return tokens


def classify_content(page,area,region,analyzer):
    """Keep evidence for non-prose detections; do not count them as translations."""
    import numpy as np
    import pymupdf as f
    text=region['text'].strip()
    b=f.Rect(region['box'])
    if text in scientific_tokens(page):
        return 'protected','原页正文/图注明确定义的缩写或带上下标变量；保留原始字形。'
    related=[line for line in area.get('lines',[]) if (f.Rect(line['box'])&b).get_area()>b.get_area()*.5]
    if text in {'max','min','sin','cos','tan','log','ln','exp'} and any(
            re.search(r'[∆Δ∑∫√|/]|\([^)]*[=−+]',line['text']) for line in related):
        return 'protected','数学算子与后续公式共同构成表达式，保留原文，不作为自然语言翻译。'
    if region.get('confidence',1)>=.95:return None
    pix=page.get_pixmap(matrix=f.Matrix(4,4),clip=b & page.rect,colorspace=f.csRGB,alpha=False)
    rgb=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3).astype(int)
    ink=rgb.min(axis=2)<200
    colored=(rgb.max(axis=2)-rgb.min(axis=2)>40)&ink
    if (re.fullmatch(r'[wvmn]{3,}',text.lower()) and colored.sum()>max(5,ink.sum()*.35)):
        return 'non_text','低置信度重复字母与连续彩色曲线重合，属于波形误识别；保留数据图形。'
    photos=[im for im in page.get_image_info() if .75<im['width']/im['height']<1.35]
    if (len(photos)>=4 and page.get_text().lower().count('received')>=2
            and len(text)<=5 and len(text.split())==1 and np.mean(rgb.min(axis=2)<180)>.6
            and any(f.Rect(im['bbox']).contains((b.tl+b.br)/2) for im in photos)):
        return 'protected','作者简介页的人像照片内低置信度服饰/标记碎字；不改写人像内容。'
    if b.height>b.width*1.5 and hasattr(analyzer,'ocr'):
        key=f"graphics-content-v2:{source_digest_for_page(page)}:{region['id']}"
        cached=analyzer.store.get(key) if analyzer.store else None
        evidence=json.loads(cached) if cached else None
        if evidence is None:
            # Read upright at high resolution. This only classifies a ratio;
            # the recovered spelling is never substituted into the PDF.
            analyzer.control.checkpoint()
            clip=(b+(-3,-3,3,3))&page.rect
            analyzer.ocr.recognize(page,clip=clip,scale=4)
            pix=page.get_pixmap(matrix=f.Matrix(8,8),clip=clip,colorspace=f.csRGB,alpha=False)
            image=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
            upright=np.pad(np.rot90(image,3)[:,:,::-1].copy(),((12,12),(12,12),(0,0)),constant_values=255)
            result=analyzer.ocr.engine(upright,use_det=False,use_cls=False,use_rec=True)
            evidence={'text':(result.txts or [''])[0],'confidence':float((result.scores or [0])[0])}
            if analyzer.store:analyzer.store.put(key,json.dumps(evidence,ensure_ascii=False))
        if evidence['confidence']>.9 and re.fullmatch(r'[A-Za-z0-9₀-₉]{1,4}/[A-Za-z0-9₀-₉]{1,4}',evidence['text']):
            return 'protected','局部旋转重识别确认是数学比值；保持原始公式图像，未猜测或重写字符。'
    return None


def source_digest_for_page(page):
    # A per-source content signature also works for in-memory test documents.
    return hashlib.sha256(page.get_pixmap(matrix=__import__('pymupdf').Matrix(.5,.5)).samples).hexdigest()[:20]


class GraphicAnalyzer:
    def __init__(self, store, control, emit):
        from linguafloat.document_pdf import DocumentOcr
        self.store,self.control,self.emit = store,control,emit
        self.ocr = DocumentOcr()
        self.ocr.emit = emit
        self.model = None

    def discover(self,page,digest):
        import numpy as np
        import pymupdf as fitz
        key = f"{SCHEMA}:{digest}:{page.number}"
        cached = self.store.get(key) if self.store else None
        if cached:
            return json.loads(cached)
        self.control.checkpoint()
        if self.model is None:
            from babeldoc.docvision.doclayout import OnnxModel
            self.model = OnnxModel.from_pretrained()
        self.emit(dict(type="progress",stage="OCR 中",value=None,message=f"检测第 {page.number+1} 页图表"))
        pix = page.get_pixmap(matrix=fitz.Matrix(2,2),colorspace=fitz.csRGB,alpha=False)
        rgb = np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
        prediction = self.model.predict(rgb[:,:,::-1].copy(),imgsz=1024)[0]
        areas = []
        for item in prediction.boxes:
            label = prediction.names[int(item.cls)]
            if label not in {"table","figure"} or float(item.conf)<.8:
                continue
            box = fitz.Rect([float(v)/2 for v in item.xyxy]) & page.rect
            if any((box & fitz.Rect(a["box"])).get_area() > box.get_area()*.6 for a in areas):
                continue
            areas.append(dict(kind=label,box=list(box)))
        areas.sort(key=lambda a:(a["box"][1],a["box"][0]))
        result = []
        for index,area in enumerate(areas):
            self.control.checkpoint()
            rect = fitz.Rect(area["box"])
            lines = self.ocr.recognize(page,positioned=True,clip=(rect+(-2,-2,2,2)) & page.rect,scale=4,words=True)
            native=visible_native_words(page,rect)
            for line in lines:
                lb=fitz.Rect(line["box"])
                matches=[w for w in native if lb.contains((fitz.Rect(w[:4]).tl+fitz.Rect(w[:4]).br)/2)]
                # Prefer exact PDF text over OCR when the whole visible line has a text layer.
                if matches:
                    nb=fitz.Rect(matches[0][:4])
                    for word in matches[1:]:nb |= fitz.Rect(word[:4])
                    if (lb & nb).get_area()>=lb.get_area()*.6:
                        matches.sort(key=lambda w:(w[6],w[7]))
                        line.update(text=" ".join(w[4] for w in matches),confidence=1,
                                    words=[dict(text=w[4],confidence=1,box=list(w[:4])) for w in matches])
            regions = []
            for line in lines:
                words = line.get("words") or [dict(text=line["text"],box=line["box"],confidence=line["confidence"])]
                # Exclude number/unit/symbol runs, including axis suffixes such as H (A/m).
                groups,group = [],[]
                for word in words:
                    accepted = natural_word(word["text"]) or (word["text"].strip().lower() in {"of","in","to","at","on","by","for","and","the","a"})
                    if accepted:
                        if group:
                            previous,current = fitz.Rect(group[-1]["box"]),fitz.Rect(word["box"])
                            if current.x0-previous.x1 > max(previous.height,current.height)*2:
                                groups.append(group); group=[]
                        group.append(word)
                    elif group:
                        groups.append(group); group=[]
                if group: groups.append(group)
                for group in groups:
                    if not any(natural_word(w["text"]) for w in group):
                        continue
                    box = fitz.Rect(group[0]["box"])
                    for word in group[1:]: box |= fitz.Rect(word["box"])
                    text = " ".join(w["text"] for w in group)
                    confidence = min(line["confidence"],min(w.get("confidence",1) for w in group))
                    identity = hashlib.sha256(json.dumps([digest,page.number,area["kind"],[round(v,1) for v in box],text]).encode()).hexdigest()[:20]
                    regions.append(dict(id=identity,kind=area["kind"],box=list(box),text=text,
                                        confidence=confidence,rotation=90 if box.height>box.width*1.8 else 0,
                                        reason="OCR 置信度不足，请校对原文" if confidence<.95 else ""))
            # Keep geometry for *all* words, including protected values and units.
            # They are layout obstacles / exact source fragments, never API input.
            area["lines"] = lines
            area["regions"] = regions
            area["id"] = f"p{page.number+1}-{index+1}"
            result.append(area)
        if self.store: self.store.put(key,json.dumps(result,ensure_ascii=False))
        return result


def glyph_patch(page, region):
    """Erase only complete dark glyph components; reject rules or colored data."""
    import cv2
    import numpy as np
    import pymupdf as fitz
    from PIL import Image
    box = fitz.Rect(region["box"])
    clip = (box+(-3,-3,3,3)) & page.rect
    scale=4
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,colorspace=fitz.csRGB,alpha=False)
    rgb=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
    ink=(np.min(rgb,axis=2)<200).astype(np.uint8)
    count,labels,stats,centers=cv2.connectedComponentsWithStats(ink,8)
    mask=np.zeros(ink.shape,np.uint8)
    selected=[]
    selected_ids=[]
    for i in range(1,count):
        x,y,w,h,area=stats[i]
        cx,cy=centers[i]
        if not box.contains(fitz.Point((cx+pix.x)/scale,(cy+pix.y)/scale)):
            continue
        if (w>max(45,h*7) and h<=scale*2) or (h>max(45,w*7) and w<=scale*2):
            continue
        if x==0 or y==0 or x+w>=pix.width or y+h>=pix.height or w>max(45,h*7) or h>max(45,w*7):
            return None,"文字与边界或图线相连，需调整识别框"
        component=labels==i
        colors=rgb[component].astype(int)
        if np.any(colors.max(axis=1)-colors.min(axis=1)>24):
            return None,"文字区域含彩色曲线或数据点，不能自动擦除"
        mask[component]=255
        selected_ids.append(i)
        selected.append(fitz.Rect((x+pix.x)/scale,(y+pix.y)/scale,(x+w+pix.x)/scale,(y+h+pix.y)/scale))
    if not selected:
        return None,"识别框没有对应文字墨迹"
    original_mask=mask.copy()
    mask=cv2.dilate(mask,np.ones((9,9),np.uint8))
    protected=((ink>0)&~np.isin(labels,selected_ids)).astype(np.uint8)
    mask[cv2.dilate(protected,np.ones((3,3),np.uint8))>0]=0
    if np.any((original_mask>0)&(mask==0)):
        return None,"文字与其他墨迹无法安全分离"
    # Only paint on a verified white background, including the expanded mask edge.
    background=rgb[(mask>0)&(np.min(rgb,axis=2)>238)]
    if len(background)<np.count_nonzero(mask)*.15 or np.std(background.astype(float))>6:
        return None,"底色或纹理复杂，需局部译文对照"
    bounds=selected[0]
    for r in selected[1:]: bounds |= r
    color,alpha=io.BytesIO(),io.BytesIO()
    Image.new("RGB",(pix.width,pix.height),(255,255,255)).save(color,format="PNG")
    Image.fromarray(mask).save(alpha,format="PNG")
    return (color.getvalue(),list(fitz.Rect(pix.x/scale,pix.y/scale,(pix.x+pix.width)/scale,(pix.y+pix.height)/scale)),alpha.getvalue(),list(bounds)),""


def graphic_overlay(page, region, text, target, control):
    import pymupdf as fitz
    from linguafloat.document_scan_layout import fit_single_line
    from linguafloat.document_layout import font_scales
    box=fitz.Rect(region["box"])
    font=fitz.Font({"ja":"japan","ko":"korea"}.get(target,"china-s"))
    if any(not font.has_glyph(ord(c)) for c in text if not c.isspace()):return None
    rotated=region.get("rotation")==90
    width,height=(box.height,box.width) if rotated else (box.width,box.height)
    # OCR boxes include ascenders/descenders; preserve legible type, never stretch.
    size=max(6,min(18,height*.9))
    with fitz.open() as temp:
        p=temp.new_page(width=width+12,height=height+12)
        local=dict(box=[6,6,6+width,6+height])
        for scale in font_scales(size):
            control.checkpoint()
            value=fit_single_line(p,local,text,font,size*scale)
            if value:
                with fitz.open(stream=value,filetype="pdf") as fitted,fitz.open() as overlay:
                    out=overlay.new_page(width=page.rect.width,height=page.rect.height)
                    out.show_pdf_page(box,fitted,0,clip=fitz.Rect(local["box"]),rotate=90 if rotated else 0)
                    return overlay.tobytes(),dict(size=size*scale)
    return None


def apply_graphics(source, target, translator, analyzer, digest, overrides=None):
    import pymupdf as fitz
    from linguafloat.document_core import DocumentError
    job=translator.job
    from linguafloat.document_graphic_layout import plan_area, fit_label, clear_of_original
    records=[]
    placements=[]
    for area in analyzer.discover(source,digest):
        boundary = job.reference_boundary if job.skip_references else {}
        if boundary and (source.number > boundary["page"] or
                         source.number == boundary["page"] and any(
                             fitz.Rect(area["box"]).intersects(fitz.Rect(box)) for box in boundary["boxes"])):
            continue
        enabled=job.translate_tables if area["kind"]=="table" else job.translate_figures
        if not enabled:continue
        candidate_area=dict(area,regions=[])
        for region in area['regions']:
            classification=classify_content(source,area,region,analyzer)
            if classification:
                records.append(dict(region,page=source.number+1,status=classification[0],reason=classification[1]))
            else:candidate_area['regions'].append(region)
        regions=plan_area(source,candidate_area)
        prepared=[]
        for index,entry in enumerate(regions):
            translator.control.checkpoint()
            region=dict(entry)
            correction=(overrides or {}).get(region["id"],{})
            if correction.get("box"):
                edited=fitz.Rect(correction["box"])
                if edited.is_empty or not fitz.Rect(area["box"]).contains(edited):
                    raise DocumentError("图表校对框必须位于所属图表内。")
                region["box"]=list(edited)
            if correction.get("source"):
                region["text"]=correction["source"]
                region["reason"]=""
            record=dict(page=source.number+1,**region,status="pending")
            if correction.get("skip"):
                records.append(dict(record,status="skipped",reason="用户选择保留原文"));continue
            reason=region.get("reason","")
            patch=None
            if not reason:patch,reason=glyph_patch(source,region)
            if reason:
                records.append(dict(record,status="needs_review",reason=reason));continue
            value=correction.get("translation","").strip()
            translator.location=f"PDF 第 {source.number+1} 页 · {area['kind']} {area['id']} · {index+1}"
            analyzer.emit(dict(type="progress",stage="翻译中",stage_key=f"graphics-{source.number}",value=None,
                               work_done=index,work_total=len(regions),message=translator.location))
            if not value:
                value=translator.translate_fragment(region["text"],"Scientific table cell or figure label. Translate natural language only; preserve scientific terminology. Return only the translated label.",part="图表文字")
            translator.control.checkpoint()
            prepared.append((region,patch,record,value))
        groups={}
        for entry in prepared:
            groups.setdefault(entry[0].get("group",entry[0]["id"]),[]).append(entry)
        for group in groups.values():
            preferred=min(entry[0]["preferred_size"] for entry in group)
            sizes=[preferred]
            while sizes[-1] > 6:
                sizes.append(max(6,round(sizes[-1]-.25,2)))
            fitted_group=None
            for size in sizes:
                candidates=[fit_label(target,r,value,job.target,translator.control,size,source)
                            for r,patch,record,value in group]
                if all(candidates):
                    if clear_of_original(source,group,candidates):
                        fitted_group=candidates;break
                    for r,_,_,_ in group:
                        r['_fit_failure']='新文字与保留的表线、数据或其他标签墨迹相碰；需要调整可用空间，不能覆盖科研内容。'
            if fitted_group is None:
                failures=[f"区域 {r['id']}：{r['_fit_failure']}" for r,_,_,_ in group if r.get('_fit_failure')]
                for r,patch,record,value in group:
                    location=f"第 {r['row']} 行、第 {r['column']} 列" if r.get('row') else r['id']
                    records.append(dict(record,status="needs_review",translation=value,
                        reason=f"{location}：本组未通过统一排版检查。"+'；'.join(failures)+" 已保留原文和有效译文缓存，可修正排版后仅重新排版。"))
                continue
            for (r,patch,record,value),fitted in zip(group,fitted_group):
                placements.append((r,patch,fitted))
                records.append(dict(record,status="translated",translation=value,**fitted[1]))
    # Remove source text before drawing any replacement: adjacent OCR boxes can overlap.
    for region,patch,fitted in placements:
        target.add_redact_annot(fitz.Rect(region["box"]),fill=False,cross_out=False)
    if placements:
        target.apply_redactions(images=0,graphics=0,text=0)
    for region,patch,fitted in placements:
        target.insert_image(fitz.Rect(patch[1]),stream=patch[0],mask=patch[2])
    for region,patch,fitted in placements:
        with fitz.open(stream=fitted[0],filetype="pdf") as overlay:
            target.show_pdf_page(target.rect,overlay,0)
    return records


class DocumentEnhancer:
    def __init__(self,translator,emit):
        self.translator,self.emit=translator,emit
        self.records=[]
        self.graphics=[]
        self.analyzer=None
        self.digest=None
        if translator.job.translate_tables or translator.job.translate_figures:
            self.digest=source_digest(translator.job.source)
            self.analyzer=GraphicAnalyzer(translator.store,translator.control,emit)

    def apply(self,source,target):
        from linguafloat.document_frontmatter import repair_frontmatter
        self.records.extend(repair_frontmatter(source,target,self.translator))
        if self.analyzer:
            overrides=self.translator.job.graphic_overrides.get(self.digest,{})
            self.graphics.extend(apply_graphics(source,target,self.translator,self.analyzer,self.digest,overrides))

    def report(self,report):
        report["untranslated_regions"]={str(k):v for k,v in report.get("untranslated_regions",{}).items()}
        report.setdefault("frontmatter",[]).extend(self.records)
        report.setdefault("graphics_regions",[]).extend(self.graphics)
        for entry in self.graphics:
            if entry["status"] not in {"needs_review","skipped"}:continue
            report.setdefault("untranslated_regions",{}).setdefault(str(entry["page"]-1),[]).append(
                dict(entry,source=entry["text"],coordinates="top-left"))
        pending=sum(r["status"]=="needs_review" for r in self.graphics)
        if pending:
            report.setdefault("warnings",[]).append(f"图表文字有 {pending} 处未安全排入；下方列出页码、区域和原因。已保存有效译文缓存，修正排版后可仅重新排版。")


def preview_page(message,emit):
    import base64
    from linguafloat.document_pdf import open_document
    from linguafloat.document_core import JobStore,JobControl,DocumentError
    source=message["source"]
    digest=source_digest(source)
    store=JobStore(Path(message["database"]))
    try:
        with open_document(source) as doc:
            number=int(message.get("page",0))
            if not 0<=number<len(doc):raise DocumentError("预览页码超出范围。")
            analyzer=GraphicAnalyzer(store,JobControl(),emit)
            areas=analyzer.discover(doc[number],digest)
            import pymupdf as fitz
            records=[]
            with fitz.open() as preview:
                preview.insert_pdf(doc,from_page=number,to_page=number)
                if message.get("manual_preview"):
                    from types import SimpleNamespace
                    corrections=message.get("overrides",{}).get(digest,{})
                    selected={}
                    for area in areas:
                        for region in area["regions"]:
                            value=corrections.get(region["id"],{})
                            selected[region["id"]]=value if value.get("translation","").strip() else {"skip":True}
                    job=SimpleNamespace(translate_tables=True,translate_figures=True,target=message.get("target","zh-Hans"))
                    def no_api(*args,**kwargs):
                        raise DocumentError("本地校对预览不允许发送翻译请求。")
                    translator=SimpleNamespace(job=job,control=analyzer.control,translate_fragment=no_api)
                    records=apply_graphics(doc[number],preview[0],translator,analyzer,digest,selected)
                image=preview[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes("png")
            result=dict(type="graphics_preview",digest=digest,page=number,total=len(doc),areas=areas,
                      width=doc[number].rect.width,height=doc[number].rect.height,
                      records=records,manual_preview=bool(message.get("manual_preview")),
                      image=base64.b64encode(image).decode("ascii"))
    finally:
        store.close()
    # Signal only after the PDF and database are safely closed.
    emit(result)
