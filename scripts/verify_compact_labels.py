"""Verify all short labels in the user's PDF stay on their corresponding page."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1]
evidence = root / "verification-continuation-marker"
folder = root / "verification-compact-labels"
folder.mkdir(exist_ok=True)
label = sys.argv[1] if len(sys.argv) > 1 else "compact"
record = json.loads((evidence / f"{label}.json").read_text(encoding="utf-8"))
result = next(e for e in record["events"] if e["type"] == "finished")
assert result["requests"] == 0
report = json.loads((Path(result["outputs"][0]).parent / "layout-report.json").read_text(encoding="utf-8"))
case = json.loads((evidence / "case.json").read_text(encoding="utf-8"))
shutil.copy2(result["outputs"][0], folder / "translated.pdf")
shutil.copy2(result["outputs"][1], folder / "bilingual.pdf")
normalize = lambda text: re.sub(r"[\s\x00-\x1f]+", "", text)
with fitz.open(case["source"]) as source, fitz.open(folder / "translated.pdf") as mono, fitz.open(folder / "bilingual.pdf") as dual:
    assert len(source) == len(mono) == len(dual) == 6
    assert all(info["inline"] for items in report["continuation_layout"].values() for info in items)
    for item in record["deferred"]:
        index = item["page"] - 1
        gutter = fitz.Rect(source[index].rect.width, 0, mono[index].rect.width, mono[index].rect.height)
        assert normalize(item["text"]) in normalize(mono[index].get_text(clip=gutter)), item
        assert normalize(item["text"]) in normalize(dual[index].get_text()), item
    for index, page in enumerate(mono):
        assert page.rect.height == source[index].rect.height
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["bbox"][0] > source[index].rect.width and span["size"] > 10:
                        assert span["size"] >= 10.9
    summary = dict(source_pages=6, translated_pages=len(mono), bilingual_pages=len(dual),
                   short_labels_verified=len(record["deferred"]), api_requests=0)
    (folder / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))

poppler = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe"
for name in ("translated", "bilingual"):
    pages = folder / (name + "-pages")
    pages.mkdir(exist_ok=True)
    subprocess.run([str(poppler), "-scale-to", "1400", "-png", str(folder / f"{name}.pdf"),
                    str(pages / "page")], check=True)
    sheet = Image.new("RGB", (1500, 1400), "#e5e5e5")
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(sorted(pages.glob("page-*.png"))):
        with Image.open(path) as image:
            image.thumbnail((490, 665))
            x, y = (index % 3) * 500, (index // 3) * 700
            sheet.paste(image, (x, y + 25))
            draw.text((x + 4, y + 4), path.stem, fill="black")
    sheet.save(folder / (name + "-contact.png"))
