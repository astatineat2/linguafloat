"""Render all pages and compare confirmed problem areas using real cached text."""
import json
import shutil
import sys
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
work = ROOT / "verification-formula-geometry"
out = ROOT / "output/pdf/formula-correction"
out.mkdir(parents=True, exist_ok=True)
before = fitz.open(ROOT / "verification-bose-live/repaired/bose2022.译文.pdf")
result = work / (sys.argv[1] if len(sys.argv) > 1 else "final")
after = fitz.open(result / "bose2022.译文.pdf")
font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)
for i, page in enumerate(after):
    assert page.rect == before[i].rect
    page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6)).save(work / f"page-{i+1}.png")
for n in range(0, len(after), 2):
    images = [Image.open(work / f"page-{i+1}.png") for i in (n, n+1)]
    canvas = Image.new("RGB", (sum(im.width for im in images), max(im.height for im in images)+25), "white")
    canvas.paste(images[0], (0,25))
    canvas.paste(images[1], (images[0].width,25))
    ImageDraw.Draw(canvas).text((10,2), f"Pages {n+1}, {n+2}", fill="black")
    canvas.save(work / f"pair-{n+1}.png")
for n, clip in ((0,(299,539,558,601)), (2,(299,538,558,657)), (3,(39,239,298,428))):
    images=[]
    for pdf in (before,after):
        pix=pdf[n].get_pixmap(matrix=fitz.Matrix(3,3),clip=fitz.Rect(clip))
        images.append(Image.frombytes("RGB",(pix.width,pix.height),pix.samples))
    canvas=Image.new("RGB",(images[0].width,sum(im.height for im in images)+80),"white")
    draw=ImageDraw.Draw(canvas)
    y=0
    for label,image in zip((f"第 {n+1} 页：修复前", "修复后：真实缓存重排，API 请求 0 次"),images):
        draw.text((8,y+6),label,font=font,fill="#263343")
        canvas.paste(image,(0,y+36))
        y+=image.height+40
    canvas.save(out / f"第{n+1}页-修复对照.png")
for suffix in ("译文","双语"):
    shutil.copy2(result/f"bose2022.{suffix}.pdf",out/f"bose2022-公式排版修复.{suffix}.pdf")
report=json.loads((result/'layout-report.json').read_text(encoding='utf-8'))
print('Geometry repairs:',report['geometry_repairs'])
print('Retained regions:',report['untranslated_regions'])
print('Output:',out)
