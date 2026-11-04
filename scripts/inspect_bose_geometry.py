"""Inspect source geometry and translated snapshot without network access."""
import json
import sys
import zipfile
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
with zipfile.ZipFile(ROOT / "verification-bose-live/layout-cache/661b2299222dcc5eca3c075fabbadfe0beee50bc9f2735289cfc35b1c9904b2f.zip") as archive:
    docs = json.loads(archive.read("translated.json"))
    source = fitz.open(stream=archive.read("input.pdf"), filetype="pdf")

for number, indices in ((0, [6]), (2, [i for i,p in enumerate(docs['page'][2]['pdf_paragraph']) if any(s in (p.get('unicode') or '') for s in ['为研究', '提取'])]), (3, [i for i,p in enumerate(docs['page'][3]['pdf_paragraph']) if '样品在磁场' in (p.get('unicode') or '')])):
    page = docs['page'][number]
    print('PAGE', number+1, 'indices', indices)
    for index in indices:
        p = page['pdf_paragraph'][index]
        print('PARAGRAPH', index, p['box'], p['unicode'])
        for part in p['pdf_paragraph_composition']:
            f = part.get('pdf_formula')
            if not f:
                print('TEXT', (part.get('pdf_same_style_unicode_characters') or {}).get('unicode'))
                continue
            print('FORMULA', ''.join(c.get('char_unicode','') for c in f['pdf_character']), 'box',f['box'],'offset',f.get('y_offset'),'curves',len(f['pdf_curve']))
            for c in f['pdf_character']:
                print('  CHAR',repr(c['char_unicode']),c['box'],c.get('visual_bbox'),c['pdf_style']['font_size'])
            for c in f['pdf_curve']:
                print('  CURVE',c['box'],c.get('relocation_transform'),c.get('ctm'))
    if number==3:
        print('PAGE CURVES',[(c['box'],c.get('relocation_transform')) for c in page['pdf_curve'] if c.get('box') and 300<c['box']['y']<600])

out=ROOT/'verification-formula-geometry'
out.mkdir(exist_ok=True)
before=fitz.open(ROOT/'verification-bose-live/repaired/bose2022.译文.pdf')
for number,clip in ((0,(299,539,558,601)),(2,(299,538,558,657)),(3,(39,239,298,452))):
    for name, pdf in [('source',source),('before',before)]:
        pdf[number].get_pixmap(matrix=fitz.Matrix(3,3),clip=fitz.Rect(clip)).save(out/f'{name}-{number+1}.png')
