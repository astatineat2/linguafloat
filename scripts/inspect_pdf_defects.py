"""Inspect supplied outputs and locate their cached inputs without API access."""
import ctypes
import json
import sqlite3
from collections import Counter
from pathlib import Path

ctypes.windll.kernel32.SetDllDirectoryW(None)
import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "verification-pdf-defects"
OUT.mkdir(exist_ok=True)
TEST = ROOT.parent / "test"

for prefix in ("s41467-024-52210-y.236e37dd96b9", "Hoburg - 1995 - Principles of quasistatic magnetic shielding.3c75b50ed581"):
    folder = TEST / prefix
    report = json.loads((folder / "layout-report.json").read_text(encoding="utf-8"))
    print(prefix, "untranslated", {p: dict(Counter(r["reason"] for r in rs)) for p, rs in report["untranslated_regions"].items()})
    with fitz.open(next(folder.glob("*.双语.pdf"))) as doc:
        pages = []
        for page in doc:
            text = page.get_text()
            pages.append(dict(page=page.number + 1, size=list(page.rect), text=text))
            if prefix.startswith("s41467") and ("References" in text or "Sample preparation" in text):
                page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4)).save(OUT / f"nature-before-{page.number+1}.png")
                print("Nature problem page", page.number + 1)
        (OUT / ("nature-text.json" if prefix.startswith("s41467") else "hoburg-text.json")).write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")

for path in ROOT.rglob("jobs.sqlite3"):
    if any(x in path.parts for x in ("site-packages", ".document-runtime")):
        continue
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT id,source FROM jobs WHERE source LIKE '%52210%' OR id LIKE '3c75b50ed581%'").fetchall()
        if rows:
            print("Located job database", str(path), rows)
            (OUT / "database-location.json").write_text(json.dumps(dict(database=str(path), jobs=rows), ensure_ascii=False), encoding="utf-8")
    except sqlite3.Error:
        pass
