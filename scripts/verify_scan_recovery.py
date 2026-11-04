"""Offline recovery fixtures and real-paper formula-adjacent layout sample."""
import ctypes
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
ctypes.windll.kernel32.SetDllDirectoryW(None)
import pymupdf as fitz
from linguafloat.document_core import JobControl
from linguafloat.document_scan_layout import translate_scan_page
from linguafloat.document_scan_recovery import recover_regions
from test_document_scan_recovery import formula_scan

OUT = ROOT / "verification-scan-recovery"
OUT.mkdir(exist_ok=True)


def fixture():
    path, line, region = formula_scan(OUT)
    control = JobControl()
    analyzer = NS(translator=NS(control=control), emit=lambda _: None, ocr=NS(recognize=lambda *a, **kw: [line]))
    with fitz.open(path) as source:
        regions = recover_regions(source[0], [region], analyzer, "sample")
        t = NS(control=control, job=NS(target="zh-CN"), translate=lambda text: {"Since": "由于", "within the material": "在材料内部"}[text])
        output, retained, placed = translate_scan_page(source, 0, "scan_ocr", NS(regions=lambda *a: regions), "sample", t, lambda _: None)
        with output:
            output.save(OUT / "formula-translated.pdf")
            print(json.dumps(dict(regions=regions, placed=placed), ensure_ascii=True))
            source[0].get_pixmap(matrix=fitz.Matrix(4, 4), clip=fitz.Rect(20, 30, 210, 65)).save(OUT / "formula-source.png")
        with fitz.open(OUT / "formula-translated.pdf") as saved:
            saved[0].get_pixmap(matrix=fitz.Matrix(4, 4), clip=fitz.Rect(20, 30, 210, 65)).save(OUT / "formula-translated.png")


def paper():
    source_path = Path("C:/Users/Astatine/Zotero/storage/M3FJP6NJ/Hoburg - 1995 - Principles of quasistatic magnetic shielding with cylindrical and spherical shields.pdf")
    regions = json.loads((ROOT / "verification-scan-coverage/recovered.json").read_text(encoding="utf-8"))[1]["regions"]
    samples = {
        "Since": "由于",
        "within the material, while": "在材料中，而",
        "in the air, the": "在空气中，",
        "For the long cylindrical geometry, a dipole source consists of two long wires, coaxial with the cylinder, that carry currents in opposite directions.":
            "对于长圆柱形结构，偶极子场源由两根与圆柱轴线平行、通有反向电流的长导线构成。",
        "For the spherical geometry, a dipole source consists of a circular wire loop at the center of the sphere.":
            "对于球形结构，偶极子场源由位于球心的圆形导线环构成。",
        '1) With a uniform magnetic field imposed by a distant source outside the shield, the shielding factor is calculated in the region inside the shield. 2) With a dipole source at the center of the shield, the shielding factor is calculated in the region outside the shield. "Dipole source" here has different meanings for cylinders and spheres:':
            "1）当屏蔽体外部的远处场源施加均匀磁场时，计算屏蔽体内部区域的屏蔽因子。\n2）当屏蔽体中心存在偶极子场源时，计算屏蔽体外部区域的屏蔽因子。这里的偶极子场源，对于圆柱形和球形屏蔽体有不同含义：",
    }
    selected = [r for r in regions if r["text"] in samples]
    assert len(selected) == 6 and all(not r["reason"] for r in selected)
    # Include preserved formulas in the verification, so their pixels are checked.
    formulas = [r for r in regions if r.get("preserved") and 479 < r["box"][1] < 493]
    t = NS(control=JobControl(), job=NS(target="zh-CN"), translate=lambda text: samples[text])
    with fitz.open(source_path) as source:
        result, retained, placed = translate_scan_page(source, 1, "scan_ocr", NS(regions=lambda *a: selected + formulas), "paper", t, lambda _: None)
        with result:
            assert len(placed) == 6, retained
            result.save(OUT / "hoburg-recovered-regions-offline-sample.pdf", garbage=4, deflate=True)
    with fitz.open(source_path) as source, fitz.open(OUT / "hoburg-recovered-regions-offline-sample.pdf") as result:
        assert result[0].rect == source[1].rect and len(result) == 1
        for formula in formulas:
            rect = fitz.Rect(formula["box"]).irect
            assert result[0].get_pixmap(matrix=fitz.Matrix(3, 3), clip=rect).samples == source[1].get_pixmap(matrix=fitz.Matrix(3, 3), clip=rect).samples
        for name, rect in (("inline", fitz.Rect(70, 470, 315, 500)), ("lists", fitz.Rect(319, 540, 564, 676))):
            for label, page in (("source", source[1]), ("translated", result[0])):
                page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=rect).save(OUT / f"paper-{name}-{label}.png")
    (OUT / "paper-verification.json").write_text(json.dumps(dict(real_api_requests=0, manual_fixture_regions=6,
        formulas_pixel_identical=True, paragraphs=placed), ensure_ascii=False, indent=2), encoding="utf-8")
    print("Real-paper local sample: 6 recovered regions placed, formula pixels unchanged, zero paid requests")


if __name__ == "__main__":
    paper() if len(sys.argv) > 1 and sys.argv[1] == "paper" else fixture()
