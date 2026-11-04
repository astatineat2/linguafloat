"""Offline scan diagnostics; no credentials or external translation requests."""
import ctypes
import hashlib
import json
import sys
from uuid import uuid4
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
ctypes.windll.kernel32.SetDllDirectoryW(None)
import pymupdf as fitz
from linguafloat.document_core import DocumentJob, DocumentTranslator, JobControl, JobStore
from linguafloat.document_scan_layout import ScanAnalyzer, classify_page, ink_patch, translate_scan_page

SOURCE = Path("C:/Users/Astatine/Zotero/storage/M3FJP6NJ/Hoburg - 1995 - Principles of quasistatic magnetic shielding with cylindrical and spherical shields.pdf")
FOLDER = ROOT / "verification-scan-coverage"


def diagnose(label):
    FOLDER.mkdir(exist_ok=True)
    store = JobStore(FOLDER / "analysis.sqlite")
    analyzer = ScanAnalyzer(SimpleNamespace(control=JobControl(), store=store), lambda _: None)
    original = analyzer.ocr.recognize
    raw = {}
    def recognize(page, **kwargs):
        result = original(page, **kwargs)
        raw[page.number] = result
        return result
    analyzer.ocr.recognize = recognize
    with SOURCE.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    results = []
    try:
        with fitz.open(SOURCE) as doc:
            for page in doc:
                regions = analyzer.regions(page, classify_page(page), digest)
                for r in regions:
                    if not r.get("reason"):
                        patch, reason = ink_patch(page, r)
                        r["mask_reason"] = reason
                        r["mask_ok"] = patch is not None
                results.append(dict(page=page.number + 1, regions=regions))
                reasons = Counter(r.get("reason") or r.get("mask_reason") or "eligible" for r in regions)
                print(json.dumps(dict(page=page.number + 1, reasons=reasons,
                                     eligible_chars=sum(len(r["text"]) for r in regions if r.get("mask_ok"))), ensure_ascii=True), flush=True)
        (FOLDER / f"{label}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        if raw:
            (FOLDER / f"{label}-ocr.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        store.close()


def sample():
    # These local human-readable fixtures verify layout, not live API quality.
    regions = json.loads((FOLDER / "after.json").read_text(encoding="utf-8"))[0]["regions"]
    samples = {
        0: "用于定量衡量磁屏蔽体降低某点磁场强度效果的指标是屏蔽因子 s，其定义为安装屏蔽体时该点的磁场强度与未安装屏蔽体时同一点磁场强度之比。当场源和磁场随时间作正弦变化时，屏蔽因子是一个复数，定义为有、无屏蔽体时磁场复振幅之比。屏蔽因子的模越小，屏蔽效果越好。屏蔽因子为 1.0 表示没有屏蔽，屏蔽因子为 0.0 则表示完全屏蔽。",
        1: "降低特定区域内准静态磁场的一种方法，是利用材料的性质来改变给定场源所产生磁场的空间分布。当屏蔽材料将作为磁场源的载流导体与需要降低磁场强度的区域隔开时，屏蔽体会改变磁场的结构，使磁通线偏离被屏蔽区域。",
        2: "作者感谢审稿人提出的意见，这些意见显著改善了本文的表述；同时感谢 M. Kanda 博士的支持，使本文得以发表。",
        3: "一般而言，屏蔽因子是测量位置的函数。如果屏蔽体的性质与磁场强度无关，则屏蔽因子也与激励振幅无关。但是，如果屏蔽材料的磁导率显著依赖于材料内部的磁通密度，屏蔽因子就会随激励振幅变化。",
        4: "摘要：本文阐述了利用材料实现准静态磁屏蔽的基本原理。通过长圆柱形和球形屏蔽体的具体示例，辨识并说明不同的屏蔽机制。针对包围被屏蔽区域以及包围场源的屏蔽体，均给出了解析结果。这两种配置具有互易性：对于相同的屏蔽体，外部施加均匀磁场与屏蔽体内部存在偶极子场源时，所得屏蔽因子相同。\n\n首先，在只有一种机制起作用的屏蔽情形下，对磁通分流机制和感应电流屏蔽机制分别作定量和定性描述。随后，分析两种机制同时起作用的情况，并说明仅涉及单一机制的描述如何作为一般描述中的特定适用区间。",
        5: "图 1 展示了两种不同的几何结构，两者的屏蔽体都包围一个空间区域。在图 1(a) 中，远处外部导体产生磁场，圆柱形或球形屏蔽材料包围需要屏蔽的区域。在图 1(b) 中，同样呈圆柱形或球形的屏蔽材料包围导体，从而降低屏蔽体外部区域的磁场强度。",
        6: "圆柱形与球形屏蔽体的准静态磁屏蔽原理",
        7: "一、引言", 8: "致谢", 9: "参考文献", 10: "J. F. Hoburg，IEEE 高级会员",
    }
    assert regions[4]["text"].startswith("Abstract")
    assert all(regions[i].get("mask_ok") for i in samples)
    translations = {regions[i]["text"]: text for i, text in samples.items()}
    job = DocumentJob(str(SOURCE), str(FOLDER), dict(id="offline-coverage", model="manual-fixture",
                      base_url="https://example.invalid", api_mode="responses"), pdf_mode="layout")
    def create(**kwargs):
        prompt = kwargs["input"][-1]["content"]
        matches = [v for k, v in translations.items() if k in prompt]
        assert len(matches) == 1
        return SimpleNamespace(output_text=matches[0])
    store = JobStore(FOLDER / f"manual-sample-{uuid4().hex}.sqlite")
    records = []
    try:
        with fitz.open(SOURCE) as source:
            for cached in (False, True):
                job.relayout_only = cached
                translator = DocumentTranslator(job, "", store, JobControl(), lambda _: None,
                    SimpleNamespace(responses=SimpleNamespace(create=create)))
                result, retained, placed = translate_scan_page(source, 0, "scan_ocr",
                    SimpleNamespace(regions=lambda *args: regions), "offline", translator, lambda _: None)
                with result:
                    assert len(result) == 1 and result[0].rect == source[0].rect
                    assert len(placed) == len(samples), retained
                    for rect in (fitz.Rect(0, 310, 303, 720), fitz.Rect(0, 710, 610, 790)):
                        assert result[0].get_pixmap(clip=rect).samples == source[0].get_pixmap(clip=rect).samples
                    result.save(FOLDER / "hoburg-page1-offline-layout-sample.pdf", garbage=4, deflate=True)
                    with fitz.open(FOLDER / "hoburg-page1-offline-layout-sample.pdf") as saved:
                        saved[0].get_pixmap(matrix=fitz.Matrix(1.8, 1.8)).save(FOLDER / "hoburg-page1-offline-layout-sample.png")
                if cached:
                    assert translator.calls == 0
                records.append(dict(real_api_requests=0, local_fixture_requests=translator.calls,
                                    cached=cached, placed=placed, retained=retained))
        (FOLDER / "sample-verification.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps([dict(cached=r["cached"], placed=len(r["placed"]), retained=len(r["retained"]),
                              real_api_requests=0, local_fixture_requests=r["local_fixture_requests"]) for r in records]))
    finally:
        store.close()


if __name__ == "__main__":
    sample() if sys.argv[1] == "sample" else diagnose(sys.argv[1])
