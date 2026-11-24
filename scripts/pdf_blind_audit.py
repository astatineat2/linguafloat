"""Inspect delivered side-by-side PDFs independently of worker paragraph detection."""
from collections import Counter
import json
import re

import pymupdf as pdf

from check_pdf_sample import compare

HEADER = re.compile(r'Original (\d+)\s*\|\s*Translation (\d+)/(\d+)')
PROSE = re.compile(r'\b(?:we (?:found|used|observed)|were (?:used|measured|determined)|results (?:showed|suggest))\b', re.I)


def compact(text):
    return ' '.join(text.split())


def narrative(text):
    words = re.findall(r"[A-Za-z]+",text.lower())
    if len(words)<15: return False
    if PROSE.search(text): return True
    if re.search(r'https?://|\bdoi:|\bISBN\b|©|all rights reserved',text,re.I): return False
    # Evidence of a sentence, not just a long list of authors, entities or symbols.
    connectors = {'the','that','than','which','these','those','from','with','after','before','when','between'}
    return len(connectors.intersection(words))>=3 and bool(re.search(
        r'\b(?:was|were|had|has|is|are|can|could|showed|contained|remained|increased|decreased)\b',text,re.I))


def inspect(source, mono, dual):
    findings = []
    markers = set()
    def flag(rule, page, evidence, severity='fail'):
        findings.append(dict(rule=rule, page=page, evidence=evidence, severity=severity))

    with pdf.open(source) as a, pdf.open(mono) as b, pdf.open(dual) as c:
        if min(len(a), len(b), len(c)) == 0 or len(b) != len(c):
            flag('page-count', 0, f'原文 {len(a)}，单语 {len(b)}，双语 {len(c)}')
        expected_page = expected_part = 1
        expected_total = None
        for k in range(min(len(b), len(c))):
            sheet, target = c[k], b[k]
            match = HEADER.fullmatch(sheet.get_text(clip=pdf.Rect(0, 0, sheet.rect.width, 25)).strip())
            if not match:
                flag('page-map', 0, f'双语第 {k+1} 页没有可识别的左右页标'); continue
            page, part, total = map(int, match.groups())
            if not (1 <= page <= len(a) and 1 <= part <= total):
                flag('page-map', 0, f'双语第 {k+1} 页标签越界'); continue
            if (page, part) != (expected_page, expected_part) or (part > 1 and total != expected_total):
                flag('page-map', page, f'双语第 {k+1} 页页序或续页编号不连续')
            expected_page, expected_part = (page+1, 1) if part == total else (page, part+1)
            expected_total = total
            original = a[page-1]
            for marker in re.findall(r'未\s*翻\s*译\s*(P\s*\d+\s*-\s*[A-Z]\s*\d+)\b',target.get_text()):
                markers.add((page, re.sub(r'\s+','',marker)))
            gap, header = 20, 26  # side-by-side contract in document_pdf.py
            if abs(sheet.rect.width-original.rect.width-gap-target.rect.width) > .1 or abs(sheet.rect.height-header-max(original.rect.height,target.rect.height)) > .1:
                flag('panel-geometry', page, '双语页与原文、译文实际尺寸不符'); continue
            for label, panel, offset in [('原文',original,0), ('译文',target,original.rect.width+gap)]:
                clip = pdf.Rect(offset,header,offset+panel.rect.width,header+panel.rect.height)
                if compact(panel.get_text()) != compact(sheet.get_text(clip=clip)):
                    flag('panel-copy',page,f'双语第 {k+1} 页{label}侧与独立 PDF 复制结果不一致','review')
                uris = Counter(link['uri'] for link in panel.get_links() if link.get('uri'))
                sheet_uris = Counter(link['uri'] for link in sheet.get_links() if link.get('uri') and clip.contains(link['from']))
                for uri in (uris.keys() - sheet_uris.keys()):
                    flag('panel-link',page,f'{label}侧缺少链接：{uri}')
                pix = sheet.get_pixmap(matrix=pdf.Matrix(.55,.55),clip=clip,colorspace=pdf.csGRAY)
                if pix.samples.count(255) == pix.width*pix.height:
                    ref = panel.get_pixmap(matrix=pdf.Matrix(.55,.55),colorspace=pdf.csGRAY)
                    if ref.samples.count(255) != ref.width*ref.height:
                        flag('blank-panel',page,f'双语第 {k+1} 页{label}侧渲染为空白')
            if total != 1:
                if part == 1: flag('split-page', page,'续页不能按原坐标逐格核对','review')
                continue
            target_uris = {link.get('uri') for link in target.get_links()}
            for issue in compare(original,target):
                if issue['rule'] == 'missing-link-target' and issue['evidence'] in target_uris:
                    issue.update(rule='link-hotspot-count',severity='review')
                if issue.get('box') and issue['severity']=='review':
                    issue['target']=compact(target.get_textbox(pdf.Rect(issue['box'])))[:220]
                findings.append(dict(issue,page=page))
            blocks = original.get_text('blocks')
            if not compact(original.get_text()) or not compact(target.get_text()):
                flag('no-copy-layer',page,'未提取到文字：需区分空页、图片和丢失文字层','review')
            for block in blocks:
                text = compact(block[4])
                if block[6] or not narrative(text): continue
                box = pdf.Rect(block[:4]); translated = compact(target.get_textbox(box))
                if not translated or text in translated:
                    findings.append(dict(rule='prose-needs-review',page=page,box=list(box),evidence=text[:220],
                                         target=translated[:220],severity='review'))
        if (expected_page,expected_part) != (len(a)+1,1):
            flag('page-map',0,'原始页或最后一页的续页未完整覆盖')
        pages = len(a)
    # A copied reading edition may not include the job sidecar. Do not invent a zero count.
    issues = mono.with_name('未翻译问题.jsonl')
    if issues.is_file():
        for raw in issues.read_text('utf-8-sig').splitlines():
            if raw.strip():
                row = json.loads(raw)
                markers.discard((row.get('page',0),row.get('id','')))
                flag('worker-retained',row.get('page',0),str(row.get('id',''))+' '+str(row.get('reason','未记录原因')),'hold')
    for page, marker in sorted(markers):
        flag('visible-retained',page,marker+' 成品标注保留原文；不能据此判定保留原因','hold')
    return pages, findings
