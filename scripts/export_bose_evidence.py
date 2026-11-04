"""Export focused real-cache layout evidence; never call a translation API."""
from pathlib import Path
import json
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
output = ROOT / "output/bose-repair"
output.mkdir(parents=True, exist_ok=True)
before = ROOT.parent / "test/bose2022.f0bf63c462dc/bose2022.双语.pdf"
after = ROOT / "verification-bose-live/repaired/bose2022.译文.pdf"
with fitz.open(before) as old, fitz.open(after) as new:
    assert len(old) == len(new) == 8
    p = old[1]
    offset = (p.rect.width + 20) / 2
    a = p.get_pixmap(matrix=fitz.Matrix(2, 2),
        clip=fitz.Rect(offset+35, 26+302, offset+558, 26+480))
    b = new[1].get_pixmap(matrix=fitz.Matrix(2, 2), clip=fitz.Rect(35,302,558,480))
    images = [Image.frombytes("RGB", (pix.width,pix.height), pix.samples) for pix in (a,b)]
    canvas = Image.new("RGB",(max(im.width for im in images),sum(im.height for im in images)+90),"white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc",20)
    y = 0
    for label, image in zip(("修复前：真实双语输出", "修复后：复用真实译文，仅重新排版，API 请求 0 次"),images):
        draw.text((12,y+8),label,font=font,fill="#263343")
        canvas.paste(image,(0,y+40))
        y += image.height+45
    canvas.save(output / "第2页-修复前后.png")
    with fitz.open() as sample:
        sample.insert_pdf(new,from_page=1,to_page=1)
        sample.save(output / "bose2022-第2页-排版修复验证.pdf")
    for i in range(len(new)):
        new[i].get_pixmap(matrix=fitz.Matrix(1,1)).save(output/f"page-{i+1}.png")
report=json.loads((ROOT/"verification-bose-live/repaired/layout-report.json").read_text(encoding="utf-8"))
(output/"remaining-regions.json").write_text(json.dumps(report["untranslated_regions"],ensure_ascii=False,indent=2),encoding="utf-8")
print(output)
