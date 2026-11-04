"""Export clearly scoped verification PDFs and source/output comparison images."""
import ctypes
import json
import shutil
from pathlib import Path

ctypes.windll.kernel32.SetDllDirectoryW(None)
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/pdf-repair-verification"
LIVE = ROOT / "verification-pdf-defects/live"
OUT.mkdir(parents=True, exist_ok=True)
font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 24)


def render(page, clip=None):
    pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), clip=clip, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def compare(before, after, name):
    before.thumbnail((900, 1150))
    after.thumbnail((900, 1150))
    canvas = Image.new("RGB", (before.width + after.width + 24, max(before.height, after.height) + 48), "#f6f8fb")
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 8), "修复前：用户提供的译文", font=font, fill="#222222")
    draw.text((before.width + 24, 8), "修复后：真实 API 验证样例", font=font, fill="#222222")
    canvas.paste(before, (0, 48))
    canvas.paste(after, (before.width+24, 48))
    canvas.save(OUT / name)


old_nature = next((ROOT.parent / "test/s41467-024-52210-y.236e37dd96b9").glob("*.双语.pdf"))
with fitz.open(old_nature) as before, fitz.open(next((LIVE / "nature").glob("*.译文.pdf"))) as after:
    for original, index, name in ((5, 0, "Nature-公式-前后对比.png"), (6, 1, "Nature-参考文献-前后对比.png")):
        page = before[original]
        right = fitz.Rect((page.rect.width + 20)/2, 26, page.rect.width, page.rect.height)
        compare(render(page, right), render(after[index]), name)
old_hoburg = next((ROOT.parent / "test/Hoburg - 1995 - Principles of quasistatic magnetic shielding.3c75b50ed581").glob("*.译文.pdf"))
with fitz.open(old_hoburg) as before, fitz.open(next((LIVE / "hoburg").glob("*.译文.pdf"))) as after:
    compare(render(before[1]), render(after[0]), "Hoburg-原第2页-前后对比.png")
for name, label in (("nature", "Nature-原第6至7页"), ("hoburg", "Hoburg-原第2页")):
    for kind in ("译文", "双语"):
        source = next((LIVE / name).glob(f"*.{kind}.pdf"))
        shutil.copy2(source, OUT / f"{label}-修复验证样例-{kind}.pdf")
    shutil.copy2(LIVE / name / "layout-report.json", OUT / f"{name}-layout-report.json")
print("Exported scoped verification PDFs and three comparison images")
