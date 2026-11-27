"""Offline, version-bound exports. The GUI only needs the stdlib part of this module."""
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from functools import lru_cache
from urllib.parse import quote, urlsplit
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED


MANUSCRIPT = '.translation-v1.zip'
UNMAPPED = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffd\ufffe\uffff]')


def _image_asset(page,box,archive,assets,scale=2):
    import pymupdf as f
    data=page.get_pixmap(matrix=f.Matrix(scale,scale),clip=box*page.rotation_matrix,alpha=False).tobytes('png')
    name='assets/'+hashlib.sha256(data).hexdigest()+'.png'
    if name not in assets: archive.writestr(name,data); assets.add(name)
    return name


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _ordered(items, width, origin=0):
    """Read each column to its end, restarting at a spanning heading or figure."""
    middle = origin + width / 2
    band = []
    result = []
    def flush():
        left = [v for v in band if v['box'][0] < middle]
        right = [v for v in band if v['box'][0] >= middle]
        columns = left and right and max(v['box'][2] for v in left) <= min(v['box'][0] for v in right)+2
        result.extend(left + right if columns else band)
        band.clear()
    for v in sorted(items, key=lambda x:(round(x['box'][1], 1), x['box'][0])):
        a, _, b, _ = v['box']
        if a < middle-width*.12 and b > middle+width*.12:
            # A narrow caption immediately above a full-width table belongs
            # after both preceding columns, not at the end of the left one.
            captions=[c for c in band if c['kind']=='caption' and a-2<=c['box'][0]<b
                      and 0<=v['box'][1]-c['box'][3]<16] if v['kind'] in {'image','table'} else []
            for c in captions: band.remove(c)
            flush(); result.extend(captions); result.append(v)
        else:
            band.append(v)
    flush()
    return result


def _page(page, paragraphs, regions, archive, assets, clip=None):
    import pymupdf as f
    from collections import defaultdict
    items, art, anchors, groups = [], [], [], defaultdict(list)
    visible = page.rect * page.derotation_matrix
    if clip is not None:
        visible &= clip
    tables=[]
    formulas=[]
    for r in regions:
        if r.get('preserve_reason') in {'identifier','page_number','running_metadata'}: continue
        box = (f.Rect(r['box']) * page.transformation_matrix) & visible
        if not box.is_empty:
            art.append(box)
            if r.get('area_kind')=='table': tables.append(box)
            if r.get('word_role')=='formula': formulas.append(box)
    for r in paragraphs:
        if r.get('box'):
            anchors.append((f.Rect(r['box']) * page.transformation_matrix, r.get('role','')))
    links = [(f.Rect(v['from']) * page.derotation_matrix, v['uri']) for v in page.get_links()
             if v.get('uri') and urlsplit(v['uri']).scheme.lower() in {'http','https','mailto'}]
    for image in page.get_image_info():
        box = f.Rect(image['bbox']) & visible
        if not box.is_empty and not any(a.contains(box) for a in art):
            art.append(box)
    # Collapse overlapping image tiles and masks, keeping surrounding captions out.
    merged = []
    for box in sorted(art, key=lambda b:-abs(b)):
        for previous in merged:
            if previous.intersects(box):
                previous.include_rect(box); break
        else:
            merged.append(box)
    art = merged
    for box in art:
        if abs(box) >= .85 * abs(visible):
            raise ValueError(f'第 {page.number+1} 页含整页底图，无法确认可编辑正文与图像的边界。')
        if box in tables and not any(box.intersects(b) for b,_ in links):
            cells=_plain_grid(page,box)
            if cells:
                items.append(dict(kind='table',box=list(box),rows=cells)); continue
        name=_image_asset(page,box,archive,assets)
        items.append(dict(kind='image',box=list(box),asset=name,links=list(dict.fromkeys(uri for b,uri in links if box.contains((b.tl+b.br)*.5)))))
        if box in formulas:
            items[-1]['word_role'] = 'formula'
    blocks = page.get_text('rawdict', flags=f.TEXTFLAGS_RAWDICT & ~f.TEXT_PRESERVE_IMAGES, clip=visible)['blocks']
    for idx, block in enumerate(blocks):
        for line in block.get('lines',[]):
            if not line['spans']:
                continue
            main = max(line['spans'], key=lambda s: s['size'])
            baseline = main['origin'][1]
            for span in line['spans']:
                box = f.Rect(span['bbox']); text = ''.join(ch['c'] for ch in span['chars']); center = (box.tl + box.br)*.5
                if not text.strip() or any(a.contains(center) for a in art): continue
                match = [(abs(a),i) for i,(a,_) in enumerate(anchors) if a.contains(center)]
                # PyMuPDF can group adjacent columns into one native block.
                key = ('anchor',min(match)[1]) if match else ('block',idx, int(box.x0 >= visible.x0+visible.width/2))
                uri = next((uri for b,uri in links if b.contains(center)),None)
                subscript = span['size'] < main['size']*.85 and span['origin'][1]-baseline > main['size']*.1
                if UNMAPPED.search(text):
                    # Never discard or guess an unmapped scientific sign. Embed the
                    # actual painted glyph inline and leave its neighbours editable.
                    for ch in span['chars']:
                        cb=f.Rect(ch['bbox']) & visible; run=dict(text=ch['c'],flags=span['flags'],uri=uri,subscript=subscript)
                        if UNMAPPED.search(ch['c']):
                            if cb.is_empty: raise ValueError(f'第 {page.number+1} 页的未映射字形没有可核对的范围。')
                            run.update(text='\ufffc',asset=_image_asset(page,cb,archive,assets,4),width=cb.width)
                        groups[key].append((cb,run,span['size'],baseline))
                else: groups[key].append((box,dict(text=text,flags=span['flags'],uri=uri,subscript=subscript),span['size'],baseline))
    for key, spans in groups.items():
        box=f.Rect(spans[0][0]); runs=[]; last=None
        # PDF drawing order can place late repairs (e.g. a contact label) last.
        lines=[]
        for entry in sorted(spans,key=lambda s:(s[3],s[0].x0)):
            if lines and abs(entry[3]-lines[-1][0]) < max(1,entry[2]*.22): lines[-1][1].append(entry)
            else: lines.append((entry[3],[entry]))
        for row_no,(_,row) in enumerate(lines):
            for col,(b,run,size,_) in enumerate(sorted(row,key=lambda s:s[0].x0)):
                box.include_rect(b)
                if runs and ((row_no and col==0) or b.x0-last.x1>size*.15):
                    a,c=runs[-1]['text'][-1],run['text'][0]
                    if a.isascii() and c.isascii() and a.isalnum() and c.isalnum() and not (run['flags']&1 or run.get('subscript')):
                        runs.append(dict(text=' ',flags=0,uri=None))
                runs.append(run); last=b
        text=''.join(r['text'] for r in runs).strip(); role=anchors[key[1]][1] if key[0]=='anchor' else ''
        kind='caption' if 'caption' in role or re.match(r'^(?:图|表|Figure|Table)\s*\d+[.．:：\s]',text,re.I) else 'heading' if role=='title' else 'paragraph'
        item = dict(kind=kind,box=list(box),runs=runs)
        metadata_title = ((page.parent.metadata or {}).get('title') or '').strip() if page.number == 0 else ''
        if metadata_title and ' '.join(text.split()).casefold() == ' '.join(metadata_title.split()).casefold():
            item['word_role'] = 'main_title'
        elif role in {'main_title', 'subtitle', 'author', 'affiliation', 'abstract', 'keywords', 'heading1', 'heading2', 'heading3'}:
            item['word_role'] = role
        items.append(item)
    if not items and (page.get_drawings() or page.get_text().strip()):
        raise ValueError(f'第 {page.number+1} 页无法可靠分离正文。')
    return _ordered(items, visible.width, visible.x0)


def _plain_grid(page,box):
    """Only promote simple ruled grids; math, styled or merged cells keep their artwork."""
    import pymupdf as f
    spans=[s for b in page.get_text('dict',clip=box,flags=f.TEXTFLAGS_DICT & ~f.TEXT_PRESERVE_IMAGES)['blocks'] for l in b.get('lines',[]) for s in l['spans']]
    if not spans or any(s['flags'] & 19 or not s['text'].isascii() for s in spans): return None
    if any(box.intersects(f.Rect(i['bbox'])) for i in page.get_image_info()): return None
    result=page.find_tables(clip=box,strategy='lines_strict').tables
    if len(result)!=1: return None
    t=result[0]; rows=t.extract()
    if len(rows)<2 or t.col_count<2 or any(cell is None for row in rows for cell in row): return None
    from collections import Counter
    characters=lambda strings: Counter(c for s in strings for c in s if not c.isspace())
    if characters(s['text'] for s in spans)!=characters(cell for row in rows for cell in row): return None
    return rows


def create_manuscript(mono, directory, identity, report, mapping, *, outputs=None, checkpoint=lambda:None):
    import pymupdf as f
    from collections import defaultdict
    path=Path(directory)/MANUSCRIPT; assets=set(); paragraphs=defaultdict(list)
    for row in report.get('paragraphs',[]): paragraphs[row['page']-1].append(row)
    indexes={r['start']+k:(r['source'],k==0) for r in mapping for k in range(r['count'])}
    with f.open(mono) as doc, ZipFile(path,'x',ZIP_DEFLATED) as archive:
        pages=[]
        if set(indexes)!=set(range(len(doc))): raise ValueError('正文底稿页码映射不完整。')
        for i,page in enumerate(doc):
            checkpoint(); src,first=indexes[i]; regions=report.get('normal_preserved_regions',{}).get(str(src),[]) if first else []
            if first: regions=regions+[dict(r, word_role='formula') for r in report.get('export_formulas',{}).get(str(src),[])]
            items=_page(page,paragraphs[src] if first else [],regions,archive,assets)
            pages.append(dict(source_page=src+1,blocks=items))
        if not any(b.get('runs') for p in pages for b in p['blocks']): raise ValueError('没有可验证的可编辑正文。')
        manifest=dict(schema=1,job=identity,pages=pages,outputs={Path(p).name:digest(p) for p in (outputs or [mono])},
                      issues=report.get('untranslated_regions',{}))
        archive.writestr('manuscript.json',json.dumps(manifest,ensure_ascii=False))
    return dict(sha256=digest(path),assets=bool(assets))


@lru_cache(maxsize=256)
def _fingerprint(path, stat):
    return digest(path)


def _checked_digest(path):
    stat = path.stat()
    return _fingerprint(path, (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))


def _read_manifest(archive, identity):
    manifest = json.loads(archive.read('manuscript.json'))
    if manifest['schema'] != 1 or manifest['job'] != identity:
        raise ValueError('所选结果与编辑底稿不一致。')
    return manifest


def _output_matches(manifest, path, fingerprint):
    expected = manifest['outputs'].get(path.name)
    return bool(expected and path.is_file() and fingerprint(path) == expected)


def _matching_manuscript(identity, info, outputs):
    if not outputs or not (record := info.get('export_manuscript')):
        return False
    package = outputs[0].parent / MANUSCRIPT
    try:
        if _checked_digest(package) != record['sha256']:
            return False
        with ZipFile(package) as archive:
            manifest = _read_manifest(archive, identity)
            return all(_output_matches(manifest, path, _checked_digest) for path in outputs)
    except (OSError, ValueError, KeyError, TypeError, BadZipFile):
        return False


def _legacy_result(identity, info, outputs):
    expected = info.get('graphics_manifest_sha256')
    if not expected or not outputs or any(path.suffix.lower() != '.pdf' for path in outputs):
        return False
    manifest = outputs[0].parent / '.graphics-source' / 'manifest.json'
    try:
        if _checked_digest(manifest) != expected:
            return False
        return json.loads(manifest.read_bytes()).get('parent_job_id') == identity
    except (OSError, ValueError, AttributeError):
        return False


def resolve_result(identity, info, roots=(), *, located=None):
    """Recover moved results using history-bound evidence, without writing history."""
    outputs = list(map(Path, info.get('outputs', [])))
    if not outputs or all(path.is_file() for path in outputs):
        return info
    original = outputs[0].parent
    if located is not None:
        located = Path(located)
        if located.name not in {path.name for path in outputs} or not located.is_file():
            return info
        directories = [located.parent]
    else:
        # Probe specific task directories under known roots, not directory trees.
        directories = [candidate for base in map(Path, roots) if base.is_absolute()
                       for candidate in (base / original.name, base / original.parent.name / original.name)]
    for directory in dict.fromkeys(directories):
        candidates = [directory / path.name for path in outputs]
        available = [path for path in candidates if path.is_file()]
        if not available or (located is not None and located not in available):
            continue
        if _matching_manuscript(identity, info, candidates):
            return {**info, 'outputs': list(map(str, candidates))}
        # Older tasks did not hash the public PDF. Their unchanged, history-bound
        # task manifest and the recorded output name identify the moved folder.
        if not (directory / MANUSCRIPT).exists() and _legacy_result(identity, info, candidates):
            try:
                for path in available:
                    with path.open('rb') as handle:
                        if handle.read(5) != b'%PDF-':
                            break
                else:
                    resolved = [before if before.is_file() else after for before, after in zip(outputs, candidates)]
                    return {**info, 'outputs': list(map(str, resolved))}
            except OSError:
                continue
    return info


def choices(info, identity=None):
    result={}
    outputs=list(map(Path, info.get('outputs',[])))
    for i,p in enumerate(outputs):
        if p.is_file() and p.suffix.lower() in {'.pdf','.docx'}:
            result[f'output:{i}']=(p.name,p.suffix)
    record=info.get('export_manuscript') or {}
    package_exists = bool(outputs and (outputs[0].parent/MANUSCRIPT).is_file())
    if record.get('sha256') and package_exists and (
            identity is None or _matching_manuscript(identity, info, outputs)):
        result['docx']=('Word 可编辑译稿','.docx')
        result['markdown']=('Markdown 译稿（含资源）','.zip') if record.get('assets') else ('Markdown 译稿','.md')
    elif (not record.get('sha256') or not package_exists) and any(
            suffix.lower() == '.pdf' for _, suffix in result.values()):
        result['docx'] = ('从 PDF 转换为 Word', '.docx')
        result['markdown'] = ('从 PDF 转换为 Markdown', '.md')
    return result


def _pdf_input(info):
    paths = [Path(p) for p in info['outputs'] if Path(p).is_file() and Path(p).suffix.lower() == '.pdf']
    if not paths:
        raise ValueError('未找到可转换的 PDF。')
    return min(paths, key=lambda p: p.name.endswith('.双语.pdf'))


def _pdf_pages(doc, path, identity, scope):
    import pymupdf as f
    from linguafloat.document_review import HEADER

    known = scope == 'history' and path.name.endswith('.双语.pdf')
    if known:
        manifest_path = path.parent / '.graphics-source' / 'manifest.json'
        data = json.loads(manifest_path.read_bytes()) if manifest_path.is_file() else {}
        if data and data.get('parent_job_id') != identity:
            raise ValueError('双语 PDF 与任务清单不匹配。')
        if data.get('bilingual_layout') == 'alternating':
            rows = data.get('pages', [])
            if not rows or sum(r['count']+1 for r in rows) != len(doc) or any(r['count'] < 1 for r in rows):
                raise ValueError('缺少可靠的交替双语页映射，未混合导出。')
            index = 0
            for row in rows:
                for p in range(index+1, index+1+row['count']):
                    yield row['source']+1, doc[p], None
                index += 1+row['count']
            return
    for page in doc:
        view = page.rect
        clip = None
        number = page.number+1
        match = HEADER.fullmatch(page.get_textbox(f.Rect(0, 0, view.width, 25)).strip()) if not page.rotation else None
        if known and not match:
            raise ValueError(f'第 {number} 页无法确认译文侧。可从「选择 PDF」手动选边。')
        if scope in {'left', 'right'} or known:
            if page.rotation:
                raise ValueError(f'第 {number} 页已旋转，无法可靠地按左右侧分离。')
            middle = view.width/2
            bounds = sorted((f.Rect(b) * page.transformation_matrix for _, _, invoker, b in page.get_xobjects()
                             if invoker == 0), key=lambda b: b.x0) if match else []
            if (len(bounds) >= 2 and abs(bounds[0].x0) < .1 and abs(bounds[1].x0-bounds[0].x1-20) < .1
                    and abs(bounds[1].x1-view.x1) < .1):
                clip = bounds[0 if scope == 'left' else 1] & view
            else:
                clip = f.Rect(0, 26 if match else 0, middle, view.y1) if scope == 'left' else f.Rect(
                    middle, 26 if match else 0, view.x1, view.y1)
            if match:
                number = int(match[1])
        yield number, page, clip


def _convert_pdf(path, identity, scope, kind, handle):
    import pymupdf as f

    pages, assets = [], set()
    with f.open(path) as doc, tempfile.TemporaryFile() as tmp:
        if doc.needs_pass:
            raise ValueError('PDF 已加密，无法直接提取文字。')
        with ZipFile(tmp, 'w', ZIP_DEFLATED) as archive:
            for number, page, clip in _pdf_pages(doc, path, identity, scope):
                selection = clip or page.rect * page.derotation_matrix
                has_image = any(f.Rect(image['bbox']).intersects(selection) for image in page.get_image_info())
                has_drawing = any(f.Rect(item['rect']).intersects(selection) for item in page.get_drawings())
                if not page.get_text('text', clip=selection).strip() and (has_image or has_drawing):
                    raise ValueError(f'第 {number} 页没有可编辑文本。扫描页和矢量字形需先核对 OCR，未生成不完整稿件。')
                items = _page(page, [], [], archive, assets, clip=clip)
                pages.append(dict(source_page=number, blocks=items))
        if not any(b.get('runs') for page in pages for b in page['blocks']):
            raise ValueError('没有可验证的可编辑正文。')
        with ZipFile(tmp) as archive:
            _write(archive, dict(pages=pages, issues={}), kind, handle, bool(assets and kind == 'markdown'))
        return bool(assets and kind == 'markdown')


def _md_text(t):
    return re.sub(r'([\\`*_{}\[\]<>#!|~])',r'\\\1',t)


def _write(archive, manifest, kind, handle, packed):
    if kind == 'docx':
        from linguafloat.document_word_export import write_word
        write_word(archive, manifest, handle)
        return
    lines=[]; assets=set()
    for page in manifest['pages']:
        for b in page['blocks']:
            if b['kind']=='table':
                rows=b['rows']
                fmt=lambda row:'| '+' | '.join(_md_text(c).replace('\n','<br>') for c in row)+' |'
                lines.append('\n'.join([fmt(rows[0]),fmt(['---']*len(rows[0])),*(fmt(r) for r in rows[1:])]))
                continue
            if b['kind']=='image':
                asset=b['asset']; assets.add(asset)
                lines.append(f"![图表，第 {page['source_page']} 页]({asset})")
                for uri in b.get('links',[]):
                    lines.append('<'+uri+'>')
                continue
            md=[]
            for s in b['runs']:
                text=s['text']; flags=s['flags']; uri=s['uri']
                if 'asset' in s:
                    asset=s['asset']; assets.add(asset)
                    md.append(f'![原文字形]({asset})')
                    continue
                val=_md_text(text)
                if flags&1: val='<sup>'+html.escape(text)+'</sup>'
                elif s.get('subscript'): val='<sub>'+html.escape(text)+'</sub>'
                if flags&2: val='*'+val+'*'
                if flags&16: val='**'+val+'**'
                if uri: val='['+val+']('+quote(uri,safe=':/@?&=#%+;,')+')'
                md.append(val)
            lines.append(('## ' if b['kind']=='heading' else '')+''.join(md))
        if manifest['issues'].get(str(page['source_page']-1)):
            note=f"原第 {page['source_page']} 页含未翻译区域，请对照此版本 PDF。"
            lines.append('> '+note)
    text=('\n\n'.join(lines)+'\n').encode('utf8')
    if packed:
        with ZipFile(handle,'w',ZIP_DEFLATED) as out:
            out.writestr('translation.md',text)
            for name in sorted(assets): out.writestr(name,archive.read(name))
    else:
        handle.write(text)


def export_file(identity, info, kind, destination):
    destination=Path(destination); selected=choices(info, identity).get(kind)
    if not selected or destination.suffix.lower()!=selected[1].lower(): raise ValueError('所选格式不可用或扩展名不符。')
    if destination.exists(): raise FileExistsError('目标文件已存在，请换一个文件名。')
    outputs=list(map(Path,info['outputs']))
    with tempfile.NamedTemporaryFile(dir=destination.parent,delete=False) as tmp:
        path=Path(tmp.name)
    notes = ''
    try:
        with path.open('wb') as out:
            if kind.startswith('output:'):
                with outputs[int(kind.split(':')[1])].open('rb') as src: shutil.copyfileobj(src,out)
            elif _matching_manuscript(identity, info, outputs):
                package=outputs[0].parent/MANUSCRIPT
                if digest(package)!=info['export_manuscript']['sha256']: raise ValueError('编辑底稿已变更。')
                with ZipFile(package) as z:
                    m = _read_manifest(z, identity)
                    if not all(_output_matches(m, p, digest) for p in outputs):
                        raise ValueError('所选结果与编辑底稿不一致。')
                    _write(z,m,kind,out,selected[1]=='.zip')
            else:
                source = _pdf_input(info)
                packed = _convert_pdf(source, identity, info.get('pdf_scope', 'history'), kind, out)
                if packed:
                    destination = destination.with_suffix('.zip')
                notes = '由 PDF 恢复，复杂公式、矢量图、表格结构和阅读顺序仍需对照原 PDF。'
        if os.name=='nt': os.rename(path,destination)  # Windows refuses an existing target.
        else: os.link(path,destination)
        return destination, notes
    finally: path.unlink(missing_ok=True)
