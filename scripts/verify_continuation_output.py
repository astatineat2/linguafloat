"""Verify the cached user reproduction and render every output page for review."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1] / "verification-continuation-marker"
record = json.loads((root / "portable.json").read_text(encoding="utf-8"))
result = next(e for e in record["events"] if e["type"] == "finished")
assert result["requests"] == 0
report = json.loads((Path(result["outputs"][0]).parent / "layout-report.json").read_text(encoding="utf-8"))
shutil.copy2(result["outputs"][0], root / "repaired-mono.pdf")
shutil.copy2(result["outputs"][1], root / "repaired-bilingual.pdf")
counts = {}
with fitz.open(root / "repaired-mono.pdf") as mono, fitz.open(root / "repaired-bilingual.pdf") as dual:
    assert len(mono) == len(dual) == 6 + sum(map(len, report["continuations"].values()))
    for item in record["deferred"]:
        number = item["page"]
        counts[number] = counts.get(number, 0) + 1
        label = f"Continuation P{number}-{counts[number]}"
        matching = [p for p in mono if label in p.get_text()]
        assert len(matching) == 1
        # Original embedded equation fonts sometimes extract a space as ETX.
        normalize = lambda text: re.sub(r"[\s\x00-\x1f]+", "", text)
        assert normalize(item["text"]) in normalize(matching[0].get_text()), (label, item["text"], matching[0].get_text())
    for refs in report["margin_references"].values():
        for reference in refs:
            assert any(reference["marker"] in p.get_text() for p in mono)
            assert any(reference["marker"] in p.get_text() for p in dual)
    print(json.dumps(dict(mono_pages=len(mono), bilingual_pages=len(dual),
                          relocated_labels_verified=sum(counts.values()), api_requests=0)))

poppler = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe"
for name in ("mono", "bilingual"):
    folder = root / (name + "-pages")
    folder.mkdir(exist_ok=True)
    subprocess.run([str(poppler), "-scale-to", "700", "-png", str(root / f"repaired-{name}.pdf"),
                    str(folder / "page")], check=True, stdout=subprocess.DEVNULL)
    paths = sorted(folder.glob("page-*.png"))
    sheet = Image.new("RGB", (5 * 360, ((len(paths) + 4) // 5) * 430), "#e5e5e5")
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        with Image.open(path) as image:
            image.thumbnail((350, 400))
            x, y = (index % 5) * 360, (index // 5) * 430
            sheet.paste(image, (x, y + 22))
            draw.text((x + 4, y + 4), path.stem, fill="black")
    sheet.save(root / (name + "-contact.png"))
