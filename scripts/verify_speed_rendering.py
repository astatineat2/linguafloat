"""Compare cached re-layout against the baseline and render review pages."""
import json
import argparse
import shutil
import subprocess
from pathlib import Path

import pymupdf

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--folder", default="verification-speed")
args = parser.parse_args()
folder = root / args.folder
records = json.loads((folder / "tian/results.json").read_text(encoding="utf-8"))
before = next((folder / "tian/before").glob("*/*.译文.pdf"))
after = Path(records[-1]["outputs"][0])
result = []
with pymupdf.open(before) as old, pymupdf.open(after) as new:
    assert len(old) == len(new)
    for index in range(len(old)):
        a, b = old[index].get_pixmap(), new[index].get_pixmap()
        same = a.width == b.width and a.height == b.height and a.samples == b.samples
        result.append(dict(page=index + 1, pixels_identical=same))
        assert same, f"Rendered page {index + 1} differs from baseline"
render = folder / "pdf-review"
render.mkdir(exist_ok=True)
poppler = shutil.which("pdftoppm")
assert poppler, "Poppler is required for visual verification"
shutil.copy2(after, render / "tian-review.pdf")
subprocess.run([poppler, "-f", "1", "-l", "2", "-scale-to", "1400", "-png", str(render / "tian-review.pdf"), str(render / "tian")], check=True)
report = folder / "long-layout/layout-report.json"
if report.is_file():
    long_pdf = json.loads(report.read_text(encoding="utf-8"))[0]["outputs"][1]
    shutil.copy2(long_pdf, render / "long-review.pdf")
    subprocess.run([poppler, "-f", "1", "-l", "3", "-scale-to", "1400", "-png", str(render / "long-review.pdf"), str(render / "long")], check=True)
(render / "pixel-comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(f"PASS: {len(result)} Tian pages render identically to the baseline; review images: {render}")
