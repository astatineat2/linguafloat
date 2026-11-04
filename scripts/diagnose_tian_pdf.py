"""Reproduce this user's layout failure in an isolated database and output folder."""
from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from dataclasses import asdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SOURCE = Path("C:/Users/Astatine/Zotero/storage/XTKPCD2U/Tian 等 - 2025 - Design and Optimization of Bi-Planar Coils for Active Magnetic Compensation System Inside the MINI-M.pdf")
FOLDER = ROOT / "verification-tian-layout"
if "--repair-keywords" in sys.argv:
    FOLDER = ROOT / "verification-tian-layout-repair"
if "--strict" in sys.argv:
    FOLDER = ROOT / "verification-tian-six-point"
if "--layout-policy" in sys.argv:
    FOLDER = ROOT / "verification-tian-layout-policy"


def child():
    import ctypes
    ctypes.windll.kernel32.SetDllDirectoryW(None)
    message = json.loads(sys.stdin.readline())
    import numpy
    import onnxruntime
    import scipy
    from linguafloat.document_core import DocumentError, DocumentJob, DocumentTranslator, JobControl, JobStore
    from linguafloat.document_worker import execute
    import linguafloat.document_pdf as pdf
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
    from babeldoc.format.pdf.document_il.backend.pdf_creater import PDFCreater
    from linguafloat.document_layout import LayoutPolicy
    prepare_page = LayoutPolicy.prepare_page
    def inspect_source(policy, page):
        if page.page_number in (0, 1):
            (FOLDER / f"translated-il-{page.page_number + 1}.json").write_text(
                json.dumps(asdict(page), ensure_ascii=False), encoding="utf-8")
        return prepare_page(policy, page)
    LayoutPolicy.prepare_page = inspect_source
    job = DocumentJob(**message["job"])
    store = JobStore(FOLDER / "jobs.sqlite3")
    records = {}
    misses = []
    protocol = sys.stdout
    last_progress = None

    def emit(event):
        nonlocal last_progress
        if event["type"] == "progress":
            progress = (event.get("stage"), event.get("value"))
            if progress == last_progress:
                return
            last_progress = progress
        if event["type"] in {"progress", "finished", "error"}:
            protocol.write(json.dumps(event, ensure_ascii=True) + "\n")
            protocol.flush()

    translator = DocumentTranslator(job, message.pop("key", "cache-only"), store, JobControl(), emit)
    original_request = translator.request
    def request(messages, **kwargs):
        if not message.get("network") and store.get(translator.cache_key(messages)) is None:
            misses.append(translator.cache_key(messages))
            raise DocumentError("Diagnostic cache miss; network disabled")
        return original_request(messages, **kwargs)
    translator.request = request

    optimal = Typesetting._get_optimal_scale
    def get_optimal(engine, paragraph, page, units):
        scale = optimal(engine, paragraph, page, units)
        records[id(paragraph)] = {"page": page.page_number + 1, "text": paragraph.unicode,
            "font_size": getattr(paragraph.pdf_style, "font_size", None), "individual_scale": scale,
            "initial_box": asdict(paragraph.box), "debug_id": paragraph.debug_id}
        return scale
    Typesetting._get_optimal_scale = get_optimal
    render = Typesetting.render_paragraph
    def render_paragraph(engine, paragraph, page, fonts):
        record = records.setdefault(id(paragraph), {"page": page.page_number + 1, "text": paragraph.unicode,
            "font_size": getattr(paragraph.pdf_style, "font_size", None), "initial_box": asdict(paragraph.box) if paragraph.box else None})
        record["assigned_scale"] = paragraph.optimal_scale
        if page.page_number == 0 and "关键词" in (paragraph.unicode or ""):
            import copy
            from babeldoc.format.pdf.document_il import il_version_1
            units = engine.create_typesetting_units(paragraph, fonts)
            record["unit_heights"] = sorted({round(u.height, 4) for u in units})
            record["plain_translation"] = "".join(u.try_get_unicode() or "" for u in units)
            record["probes"] = []
            chosen = None
            for size in (paragraph.pdf_style.font_size, 8.1, 8.0):
                for expand in (0, 2, 4, 6):
                    for spacing in (1.5, 1.3, 1.2):
                        box = copy.deepcopy(paragraph.box)
                        box.y -= expand
                        placed, fits = engine._layout_typesetting_units(units, box, size / paragraph.pdf_style.font_size,
                                                                       spacing, paragraph, True)
                        record["probes"].append(dict(size=size, expand=expand, spacing=spacing, fits=fits))
                        if fits and chosen is None:
                            chosen = (placed, box, size, expand, spacing)
            if message.get("repair") and chosen:
                placed, box, size, expand, spacing = chosen
                paragraph.box = box
                paragraph.scale = size / paragraph.pdf_style.font_size
                paragraph.pdf_paragraph_composition = []
                for unit in placed:
                    chars, curves, forms = unit.render()
                    paragraph.pdf_paragraph_composition.extend(il_version_1.PdfParagraphComposition(pdf_character=c) for c in chars)
                    page.pdf_curve.extend(curves)
                    page.pdf_form.extend(forms)
                engine._update_paragraph_render_order(paragraph)
                record.update(final_scale=paragraph.scale, final_box=asdict(box), repaired=dict(size=size, expand=expand, spacing=spacing))
                return
        result = render(engine, paragraph, page, fonts)
        record.update(final_scale=paragraph.scale, final_box=asdict(paragraph.box) if paragraph.box else None)
        return result
    Typesetting.render_paragraph = render_paragraph
    check = pdf.check_layout_paragraph
    def inspect(paragraph, characters, page_number=None):
        record = records.setdefault(id(paragraph), {"text": paragraph.unicode})
        record["character_count"] = len(characters)
        record["actual_sizes"] = sorted({round(c.pdf_style.font_size, 4) for c in characters if c.pdf_style and c.pdf_style.font_size})
        try:
            check(paragraph, characters, page_number)
        except DocumentError as exc:
            record["rejection"] = str(exc)
            if message.get("strict"):
                raise
        # Diagnostic output only: retain all failures and inspect the generated page.
    pdf.check_layout_paragraph = inspect
    logging.disable(logging.CRITICAL)
    result = {"network": message.get("network", False)}
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            execute(job, "unused", store, translator.control, emit, translator)
            result["rendered"] = True
        except Exception as exc:
            result["error"] = str(exc) if isinstance(exc, DocumentError) else type(exc).__name__
        finally:
            result.update(records=list(records.values()), cache_misses=len(misses), calls=translator.calls,
                          cache_hits=translator.hits, usage=translator.token_usage)
            (FOLDER / "diagnosis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            store.close()
    emit({"type": "error" if "error" in result else "finished", **{k: v for k, v in result.items() if k != "records"}})


def parent():
    from linguafloat.config import ConfigStore, CredentialStore
    from linguafloat.document_core import DocumentJob
    from uuid import uuid4
    FOLDER.mkdir(exist_ok=True)
    config = ConfigStore()
    settings = config.load()
    profile = settings.active_profile()
    live = sqlite3.connect(config.path.parent / "documents/jobs.sqlite3")
    copied = sqlite3.connect(FOLDER / "jobs.sqlite3")
    live.backup(copied)
    copied.close()
    live.close()
    job = DocumentJob(str(SOURCE), str(FOLDER / "diagnostic-only"), asdict(profile), pdf_mode="layout", job_id=uuid4().hex,
                      relayout_only="--network" not in sys.argv)
    network = "--network" in sys.argv
    payload = {"job": asdict(job), "network": network, "repair": "--repair-keywords" in sys.argv,
               "strict": "--strict" in sys.argv}
    if network:
        payload["key"] = CredentialStore().get_api_key(profile.id, profile.env_key)
        if not payload["key"]:
            raise SystemExit("No key configured")
    process = subprocess.Popen([str(ROOT / ".document-runtime/Scripts/python.exe"), str(Path(__file__).resolve()), "--child",
                                *(["--repair-keywords"] if payload["repair"] else []),
                                *(["--strict"] if payload["strict"] else []),
                                *(["--layout-policy"] if "--layout-policy" in sys.argv else [])],
        stdin=subprocess.PIPE, stdout=None, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    process.communicate((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    raise SystemExit(process.returncode)


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    child() if "--child" in sys.argv else parent()
