"""Immutable body results used by explicit, bounded graphics-only follow-ups.

This artifact is local user data, not a translation cache or executable object.
Every follow-up starts from the clean body PDF and reuses validated label caches;
it never feeds a previously overlaid figure back into OCR or erasure.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from linguafloat.document_core import DocumentError, selected_pages

SCHEMA = 2
FOLDER = ".graphics-source"
SETTINGS = ("target", "source_language", "chinese_font", "latin_font", "mono", "bilingual",
            "bilingual_layout", "skip_references")
REQUEST_DEFAULTS = dict(retries=2, document_rpm=60, document_tpm=0, output_token_limit=0,
                        reasoning_effort="", thinking_mode="", instruction_role="system")


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def provider_identity(provider):
    return dict(id=provider.get("id"), model=provider.get("model"), api_mode=provider.get("api_mode"),
                endpoint_sha256=hashlib.sha256(provider.get("base_url", "").encode()).hexdigest())


def save_body_artifact(output, job, body, page_map, translator):
    folder = Path(output) / FOLDER
    folder.mkdir()
    path = folder / "body.pdf"
    body.save(path, garbage=2, deflate=True)
    manifest = dict(schema=SCHEMA, parent_job_id=job.job_id, source_sha256=digest(job.source),
        source=str(Path(job.source).resolve()), body_sha256=digest(path), pages=page_map,
        provider=provider_identity(job.provider), target=job.target, source_language=job.source_language,
        selected_pages=job.pages, trial=job.trial, reference_boundary=job.reference_boundary,
        skip_references=job.skip_references, chinese_font=job.chinese_font, latin_font=job.latin_font,
        mono=job.mono, bilingual=job.bilingual, bilingual_layout=job.bilingual_layout,
        table_policy="independent-tables-v1" if job.translate_tables else "preserve-tables-v1",
        graphic_context=getattr(getattr(translator, "graphic_context", None), "records", []),
        source_context=[dict(text=b.text, role=b.role, section=b.section)
                        for b in getattr(getattr(translator, "source_context", None), "blocks", [])])
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def load_body_artifact(folder, *, source=None, provider=None, verify_pdf=True):
    folder = Path(folder)
    try:
        manifest_path = folder / "manifest.json"
        if manifest_path.stat().st_size > 5 * 1024 ** 2:
            raise ValueError("manifest too large")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != SCHEMA:
            raise DocumentError("正文补译材料版本不兼容（旧版缺少 trial/任务绑定证据），请重新生成正文；不会自动重翻。")
        if not isinstance(data.get("parent_job_id"), str) or not data["parent_job_id"].strip():
            raise ValueError("parent job")
        if any(type(data.get(k)) is not bool for k in ("trial", "skip_references", "mono", "bilingual")):
            raise ValueError("boolean settings")
        if not (data["mono"] or data["bilingual"]):
            raise ValueError("outputs")
        original = Path(source or data["source"])
        if not original.is_file() or digest(original) != data["source_sha256"]:
            raise DocumentError("原 PDF 缺失或内容已改变，无法安全补译图片；未重新翻译正文。")
        if digest(folder / "body.pdf") != data["body_sha256"]:
            raise DocumentError("正文结果校验失败，无法安全补译图片；原结果未覆盖。")
        if provider is not None and provider_identity(provider) != data["provider"]:
            raise DocumentError("供应商、接口或模型已改变，请恢复原任务配置后再补译图片；未重新翻译正文。")
        pages = data["pages"]
        if (not isinstance(pages, list) or not pages or len(pages) > 100000
                or any(not isinstance(p, dict) or any(type(p.get(k)) is not int for k in ("source", "start", "count"))
                       or p["source"] < 0 or p["start"] < 0 or p["count"] < 1
                       or type(p.get("selected")) is not bool for p in pages)
                or len({p["source"] for p in pages}) != len(pages)):
            raise ValueError("pages")
        expected_start, previous = 0, -1
        for row in pages:
            if row["source"] <= previous or row["start"] != expected_start:
                raise DocumentError("正文页面映射顺序或分段不一致；未发送请求。")
            previous = row["source"]
            expected_start += row["count"]
        for key in ("target", "source_language", "selected_pages", "chinese_font", "latin_font", "bilingual_layout"):
            if not isinstance(data.get(key), str):
                raise ValueError(key)
        if not isinstance(data.get("reference_boundary"), dict):
            raise ValueError("reference boundary")
        blocks = data.get("source_context", [])
        if (not isinstance(blocks, list) or len(blocks) > 20000 or any(
                not isinstance(b, dict) or set(b) != {"text", "role", "section"}
                or not all(isinstance(v, str) for v in b.values()) for b in blocks)):
            raise ValueError("source context")
        if verify_pdf:
            _validate_page_map(data, original, folder / "body.pdf")
        return data
    except DocumentError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
        raise DocumentError("此任务没有完整、兼容的正文补译材料。请先生成正文版；不会自动重翻整篇文章。") from error


def _validate_page_map(data, original, body_path):
    """Validate exact selected source order, trial truncation and reference flags."""
    import math
    import pymupdf as fitz
    from linguafloat.document_pdf import open_document
    from linguafloat.document_references import reference_page_only
    with open_document(original) as source, fitz.open(body_path) as body:
        boundary = data["reference_boundary"]
        if boundary:
            boxes, whole = boundary.get("page_boxes"), boundary.get("whole_pages")
            if (not isinstance(boxes, dict) or not isinstance(whole, list)
                    or any(type(p) is not int or not 0 <= p < len(source) for p in whole)
                    or len(set(whole)) != len(whole)):
                raise DocumentError("参考文献页面映射无效；请重新生成正文，未发送请求。")
            for number, areas in boxes.items():
                if (not isinstance(number, str) or not number.isdigit() or str(int(number)) != number
                        or not 0 <= int(number) < len(source) or not isinstance(areas, list)):
                    raise DocumentError("参考文献页面映射无效；未发送请求。")
                for area in areas:
                    if (not isinstance(area, list) or len(area) != 4
                            or any(type(v) not in (float, int) or not math.isfinite(v) for v in area)
                            or fitz.Rect(area).is_empty or not source[int(number)].rect.contains(fitz.Rect(area))):
                        raise DocumentError("参考文献页面映射坐标无效；未发送请求。")
            for number in whole:
                if not any(fitz.Rect(a).contains(source[number].rect) for a in boxes.get(str(number), [])):
                    raise DocumentError("参考文献整页标记与保护区域不一致；未发送请求。")
        expected_pages = selected_pages(data["selected_pages"], len(source), data["trial"])
        if [r["source"] for r in data["pages"]] != expected_pages:
            raise DocumentError("正文页面映射与原页序/页码范围/试译设置不一致；未发送图片请求。")
        expected = 0
        for row in data["pages"]:
            number = row["source"]
            enabled = not (data["skip_references"] and reference_page_only(boundary, number))
            if (row["selected"] != enabled or row["start"] != expected
                    or row["start"] + row["count"] > len(body) or not enabled and row["count"] != 1):
                raise DocumentError("正文页面映射/参考文献 selected 标记不一致；未发送图片请求。")
            if body[row["start"]].rect != source[number].rect:
                raise DocumentError("正文页面映射尺寸不一致；未发送图片请求。")
            expected += row["count"]
        if expected != len(body):
            raise DocumentError("正文页面映射遗漏页面，未发送图片请求。")


def validate_graphics_source(store, parent_job_id, folder, *, source, provider, job=None, control=None,
                             verify_pdf=True):
    """Shared UI/worker gate: artifact, immutable DB binding and ancestry must agree.

    No legacy inference: completed body/child rows must pin the final manifest
    digest. Child copies keep that digest and can be followed repeatedly, even
    if an ancestor's output copy was removed; the persisted ancestor is required.
    The GUI passes verify_pdf=False: hashes/configuration/structure need no PDF
    runtime. Workers always retain the full PDF page/geometry check.
    """
    if store is None:
        raise DocumentError("缺少原任务数据库，无法验证图片补译归属；未发送请求。")
    try:
        if control:
            control.checkpoint()
        folder = Path(folder).resolve()
        fingerprint = digest(folder / "manifest.json")
        data = load_body_artifact(folder, source=source, provider=provider, verify_pdf=verify_pdf)
        if digest(folder / "manifest.json") != fingerprint:
            raise ValueError("manifest changed during validation")
        if Path(data["source"]).resolve() != Path(source).resolve():
            raise ValueError("source path")
        if job is not None:
            if (job.workflow != "graphics" or job.parent_job_id != parent_job_id
                    or Path(job.graphics_source).resolve() != folder or Path(job.source).resolve() != Path(source).resolve()
                    or job.pdf_mode != "layout" or type(job.trial) is not bool or job.trial != data["trial"]
                    or job.pages != data["selected_pages"] or job.reference_boundary != data["reference_boundary"]
                    or any(getattr(job, key) != data[key] for key in SETTINGS)):
                raise DocumentError("补译设置与正文快照不一致；未发送请求。")
        identity, visited = parent_job_id, set()
        while identity:
            if control:
                control.checkpoint()
            if not isinstance(identity, str) or identity in visited or len(visited) >= 256:
                raise ValueError("invalid ancestry")
            visited.add(identity)
            row = store.db.execute("SELECT source,status,details FROM jobs WHERE id=?", (identity,)).fetchone()
            if not row or row[1] not in {"completed", "partial"}:
                raise ValueError("missing or incomplete ancestor")
            info = json.loads(row[2])
            config = info["configuration"]
            if (Path(row[0]).resolve() != Path(source).resolve()
                    or info.get("graphics_manifest_sha256") != fingerprint
                    or type(info.get("trial")) is not bool or info["trial"] != data["trial"]
                    or info.get("reference_boundary") != data["reference_boundary"]
                    or config.get("pages") != data["selected_pages"] or config.get("pdf_mode") != "layout"
                    or any(config.get(key) != data[key] for key in SETTINGS)):
                raise ValueError("artifact/database settings")
            if identity == parent_job_id and (not info.get("graphics_source")
                    or Path(info["graphics_source"]).resolve() != folder):
                raise ValueError("selected task artifact path")
            stored_provider = dict(id=config.get("provider_id"), model=config.get("model"),
                api_mode=config.get("api_mode"), endpoint_sha256=config.get("endpoint_sha256"))
            timeout = job.request_timeout if job is not None else provider.get("document_timeout", 180)
            if (stored_provider != data["provider"] or config.get("request_policy") != {
                    key: provider.get(key, default) for key, default in REQUEST_DEFAULTS.items()}
                    or config.get("document_concurrency") != provider.get("document_concurrency", 2)
                    or config.get("request_timeout") != timeout):
                raise DocumentError("供应商请求配置与原任务不一致；请恢复原配置，未发送请求。")
            if identity == data["parent_job_id"]:
                if config.get("workflow") != "body" or config.get("parent_job_id"):
                    raise ValueError("root is not a body task")
                if data.get("table_policy", "preserve-tables-v1") != config.get(
                        "table_policy", "preserve-tables-v1"):
                    raise ValueError("body table policy")
                return data
            if config.get("workflow") != "graphics":
                raise ValueError("unrelated task")
            identity = config.get("parent_job_id")
        raise ValueError("missing body ancestor")
    except DocumentError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise DocumentError("图片补译材料与原任务/祖先记录不一致或缺少绑定证据；请重新生成正文，未发送请求。") from error


def translate_graphics_only(job, translator, output, emit, *, store=None):
    import pymupdf as fitz
    from linguafloat.document_context import SourceBlock
    from linguafloat.document_graphics import DocumentEnhancer, apply_graphics
    from linguafloat.document_failures import mark_pdf
    from linguafloat.document_pdf import append_bilingual, open_document, restore_bilingual_source_links
    from linguafloat.document_metrics import active_clock
    data = validate_graphics_source(store or getattr(translator, "store", None), job.parent_job_id,
        job.graphics_source, source=job.source, provider=job.provider, job=job, control=translator.control)
    translator.graphic_context.restore(data.get("graphic_context", []))
    translator.set_source_context(SourceBlock(**b) for b in data.get("source_context", []))
    enhancer = DocumentEnhancer(translator, emit)
    records = []
    label = "图片补译" if not job.translate_tables else "图表补译"
    started = active_clock(translator.control)
    with open_document(job.source) as source, fitz.open(Path(job.graphics_source) / "body.pdf") as body, fitz.open() as mono, fitz.open() as dual:
        bilingual_map = []
        for ordinal, row in enumerate(data["pages"], 1):
            translator.control.checkpoint()
            number = row["source"]
            emit(dict(type="progress", stage="翻译中", value=None, work_done=ordinal-1,
                      work_total=len(data["pages"]), message=f"{label} · 原始第 {number+1} 页"))
            with fitz.open() as fragment:
                fragment.insert_pdf(body, from_page=row["start"], to_page=row["start"]+row["count"]-1)
                if row.get("selected", True):
                    local = apply_graphics(source[number], fragment[0], translator, enhancer.analyzer,
                                           enhancer.digest, job.graphic_overrides.get(enhancer.digest, {}))
                    records.extend(local)
                    pending = [r for r in local if r.get("status") == "needs_review"]
                    for index, record in enumerate(pending, 1):
                        record.update(id=f"P{number+1}-G{index}", coordinates="top-left")
                    mark_pdf(fragment[0], pending, number, control=translator.control)
                    from linguafloat.document_publisher_footer import restore_printed_urls
                    restore_printed_urls(source[number], fragment[0])
                mono.insert_pdf(fragment)
                if job.bilingual:
                    start = len(dual)
                    append_bilingual(dual, source, number, fragment, job.bilingual_layout)
                    bilingual_map.append(dict(source=number, start=start, count=len(dual)-start))
        source_links = restore_bilingual_source_links(
            dual, source, bilingual_map, job.bilingual_layout, translator.control) if job.bilingual else {}
        paths = []
        for enabled, label, doc in ((job.mono, "译文", mono), (job.bilingual, "双语", dual)):
            if enabled:
                path = Path(output) / f"{Path(job.source).stem}.{label}.pdf"
                doc.save(path, garbage=4, deflate=True)
                paths.append(str(path))
    pending = [r for r in records if r.get("status") == "needs_review"]
    report = dict(mode="graphics_followup", parent_job_id=job.parent_job_id,
                  source_link_restoration=source_links,
                  reference_boundary=job.reference_boundary, graphics_regions=records,
                  untranslated_regions={}, body_translation_requests=0,
                  transport_partial=getattr(translator, "graphic_failure", None) is not None)
    for r in data.get("body_issues", []):
        if "page" in r:
            report["untranslated_regions"].setdefault(str(r["page"]-1), []).append(r)
    for r in pending:
        report["untranslated_regions"].setdefault(str(r["page"]-1), []).append(r)
    (Path(output) / "layout-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(dict(type="measurement", name=f"独立{label}与输出", seconds=active_clock(translator.control)-started))
    return paths, ([f"{label}有 {len(pending)} 处待复核；正文版与此前结果保留。"] if pending else [])
