"""One document per process. Secrets enter through stdin, never command arguments."""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import shutil
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path

from linguafloat.document_core import Cancelled, DocumentError, DocumentJob, DocumentTranslator, JobControl, JobStore
from linguafloat.document_metrics import PerformanceLog, active_clock


def failure_trace(exc):
    """Persist complete code-location stacks without text, paths or local values."""
    exceptions, seen = [], set()
    while exc is not None and id(exc) not in seen and len(exceptions) < 8:
        seen.add(id(exc))
        frames = []
        tb = exc.__traceback__
        while tb:
            code = tb.tb_frame.f_code
            frames.append(dict(file=Path(code.co_filename).name, line=tb.tb_lineno,
                               function=code.co_name, module=tb.tb_frame.f_globals.get("__name__", "")))
            tb = tb.tb_next
        exceptions.append(dict(type=type(exc).__name__, frames=frames))
        exc = exc.__cause__ if exc.__cause__ is not None else (
            None if exc.__suppress_context__ else exc.__context__)
    return dict(exceptions=exceptions, cause_chain_truncated=exc is not None)


def failure_location(exc):
    """Keep code locations, never exception bodies, source lines or frame locals."""
    frames = []
    tb = exc.__traceback__
    last = None
    while tb:
        code = tb.tb_frame.f_code
        module = tb.tb_frame.f_globals.get("__name__", "")
        if module.startswith(("linguafloat.", "babeldoc.", "pdf2zh_next.", "pymupdf.", "rapidocr.")):
            frames.append(f"{Path(code.co_filename).name}:{tb.tb_lineno} ({code.co_name})")
        last, tb = tb, tb.tb_next
    lines = ["代码位置：" + " → ".join(frames[-4:])] if frames else []
    if isinstance(exc, RecursionError):
        trace = failure_trace(exc)["exceptions"][0]["frames"]
        counts = Counter((f["file"], f["line"], f["function"]) for f in trace)
        repeated = [f"{name}:{number} ({function}) ×{count}"
                    for (name, number, function), count in counts.most_common(4) if count > 1]
        if repeated:
            lines.append("递归重复位置：" + "；".join(repeated))
    if isinstance(exc, AttributeError) and last:
        name = getattr(exc, "name", None)
        if isinstance(name, str) and name.isidentifier() and name in last.tb_frame.f_code.co_names:
            lines.append("无法读取字段：" + name)
            if exc.obj is None:
                lines.append("字段所属对象为空；需修复数据兼容或空值处理。")
    return "\n".join(lines)


def describe_failure(exc, job, stage):
    from zipfile import BadZipFile
    if isinstance(exc, DocumentError):
        detail = str(exc)
    elif type(exc).__name__ == "ScannedPDFError" and type(exc).__module__.startswith("babeldoc."):
        detail = ("[扫描 PDF 无法保留文字版式] 引擎检测到扫描图片或隐藏的 OCR 文字层，无法直接替换原页文字。\n"
                  "处理建议：保留版式模式未改变；按页试译定位问题，检查 OCR 坐标与图形保护区域。未自动切换阅读版。\n"
                  "仅重新排版需要匹配的译文缓存；普通翻译可能产生 API 费用。")
    elif isinstance(exc, PermissionError):
        detail = "[文件访问被拒绝] 无法读取文件或写入结果。\n处理建议：关闭占用结果的 PDF/Office 程序，并检查输入文件和保存目录的权限。"
    elif isinstance(exc, FileNotFoundError):
        detail = "[所需文件不存在] 输入文件、资源或保存目录中的某个文件未找到。\n处理建议：确认原文件仍在原位置，并使用包含完整文档运行环境的便携版。"
    elif isinstance(exc, BadZipFile):
        detail = "[Office 文件格式损坏] 文件不是可读取的 DOCX/PPTX 压缩包。\n处理建议：先用 WPS/Office 打开并另存为 DOCX/PPTX，不要只修改文件扩展名。"
    elif isinstance(exc, (ImportError, ModuleNotFoundError)):
        detail = "[运行组件缺失] 文档依赖未能加载。\n处理建议：确认 document-runtime 文件夹完整，并检查安全软件是否隔离了组件。"
    elif isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        detail = "[磁盘空间不足] 保存临时文件或结果时空间不足。\n处理建议：释放磁盘空间或选择其他保存位置。"
    elif isinstance(exc, AttributeError):
        detail = ("[文档数据处理错误] 程序读取了不存在的字段（AttributeError）。\n"
                  "这是程序内部的数据兼容问题；不能据此认定 API Key 无效或模型超时。\n"
                  "处理建议：保留以下发生阶段及代码位置供修复；重复提交同一任务通常不能解决此错误。")
    elif isinstance(exc, RecursionError):
        detail = ("[文档对象递归异常] 对象处理超过递归深度（RecursionError）。\n"
                  "这是程序内部的对象处理问题，不能据此认定密钥、额度或模型故障。\n"
                  "完整脱敏代码调用栈已随失败任务保存在本地历史中；未自动切换排版模式。")
    else:
        detail = f"[文档处理异常] {type(exc).__name__}\n目前未能确定具体原因；不代表一定是 API 或密钥故障。\n处理建议：先用少量页面试译并保留此详情；检查文档能否正常打开、运行环境是否完整。"
    location = failure_location(exc)
    if location:
        detail += "\n" + location
    name = Path(job.source).name if job else "任务尚未载入"
    context = f"文件：{name}\n发生阶段：{stage}"
    if job:
        context += f"\n模型：{job.provider.get('model', '未记录')}\n页码范围：{job.pages or '全部'}"
    return detail + "\n\n" + context + "\n已成功返回的译文缓存保留；同模式、同模型重试可复用匹配缓存。已发出的请求可能计费。"


def execute(job, key, store, control, emit, translator=None):
    emit = emit if isinstance(emit, PerformanceLog) else PerformanceLog(control, emit)
    job.validate()
    control.checkpoint()
    output_root = Path(job.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    prefix = "试译版_" if job.trial else ""
    supplement = ""
    if job.workflow == "graphics":
        from linguafloat.document_postprocess import validate_graphics_source
        validate_graphics_source(store, job.parent_job_id, job.graphics_source,
            source=job.source, provider=job.provider, job=job, control=control)
        index = 1
        label = "图片补译" if job.translate_figures and not job.translate_tables else "图表补译"
        while any(output_root.glob(Path(job.source).stem[:60] + f".{label}-{index}.*")):
            index += 1
        supplement = f".{label}-{index}"
    target = output_root / (prefix + Path(job.source).stem[:60] + supplement + "." + job.job_id[:12])
    if target.exists():
        raise DocumentError("输出任务目录已存在，请重新开始任务。")
    store.status(job, "running")
    store.record_usage(job, {"runtime": {"executable": sys.executable, "worker": str(Path(__file__).resolve()),
        "database": str(store.path.resolve()), "layout_cache": str(store.path.parent / "layout-cache")}})
    emit.persist = lambda summary: store.record_usage(job, {"performance": summary})
    translator = translator or DocumentTranslator(job, key, store, control, emit)
    try:
        with tempfile.TemporaryDirectory(prefix=".linguafloat-", dir=output_root) as temporary:
            work = Path(temporary)
            emit({"type": "progress", "stage": "解析中", "value": None, "message": "解析文档"})
            extension = Path(job.source).suffix.lower()
            if job.skip_references and job.workflow != "graphics" and extension not in {".docx", ".pptx"}:
                from linguafloat.document_pdf import open_document
                from linguafloat.document_references import pdf_boundary
                with open_document(job.source) as document:
                    job.reference_boundary = pdf_boundary(document, control, emit) or {}
                store.record_usage(job, {"reference_boundary": job.reference_boundary})
                emit({"type": "progress", "stage": "解析中", "value": None,
                      "message": (f"已定位第 {job.reference_boundary['page'] + 1} 页起的参考文献范围；后续章节继续翻译"
                                  if job.reference_boundary else "未确认参考文献区域；继续处理可识别内容")})
            if job.workflow == "graphics":
                from linguafloat.document_postprocess import translate_graphics_only
                outputs, warnings = translate_graphics_only(job, translator, work, emit, store=store)
            elif extension == ".docx":
                from linguafloat.document_office import translate_docx
                outputs, warnings = translate_docx(job, translator, work, emit)
            elif extension == ".pptx":
                from linguafloat.document_office import translate_pptx
                outputs, warnings = translate_pptx(job, translator, work, emit)
            else:
                from linguafloat.document_pdf import translate_layout_pdf, translate_reading_pdf
                processor = translate_layout_pdf if job.pdf_mode == "layout" else translate_reading_pdf
                outputs, warnings = processor(job, translator, work, emit)
            if job.skip_references and job.reference_boundary:
                boundary = job.reference_boundary
                location = (f"原始第 {boundary['page'] + 1} 页" + ("右栏" if boundary.get("column")=="right" else "") if "page" in boundary
                            else boundary.get("section", "Word 正文") + "第 " + str(boundary.get("paragraph", "?")) + " 段")
                warnings.append(f"已保护参考文献：从{location}“{boundary.get('title', '参考文献')}”起的已确认参考文献区域保留原文；后续独立章节继续翻译。")
                if boundary.get("uncertain_end"):
                    warnings.append(f"原始第 {boundary['uncertain_end']['page'] + 1} 页起未确认参考文献续页范围，"
                                    "未自动保护后续正文；请核对参考文献与附录。")
                (work / "reference-skip.json").write_text(json.dumps(dict(
                    enabled=True, boundary=boundary, requests_for_skipped_content=0), ensure_ascii=False, indent=2), encoding="utf-8")
            elif job.skip_references:
                warnings.append("未可靠确认参考文献范围；这不代表文档没有参考文献，请核对输出中的相关内容。")
            control.checkpoint()
            emit({"type": "progress", "stage": "保存中", "value": None, "message": "校验并保存输出文件"})
            if len(outputs) != int(job.mono) + int(job.bilingual):
                raise DocumentError("输出文件不完整，未发布结果。")
            for path in outputs:
                if not Path(path).is_file() or not Path(path).stat().st_size:
                    raise DocumentError("输出文件为空，未发布结果。")
                if Path(path).suffix.lower() == ".pdf":
                    import pymupdf
                    with pymupdf.open(path) as check:
                        if not len(check):
                            raise DocumentError("输出 PDF 没有页面。")
            # Publish the complete set together; never modify the original file.
            publish = work / "publish"
            publish.mkdir()
            names = [prefix + (Path(path).stem + supplement + Path(path).suffix if supplement else Path(path).name)
                     for path in outputs]
            for path, name in zip(outputs, names):
                shutil.copy2(path, publish / name)
            if (work / "reference-skip.json").is_file():
                shutil.copy2(work / "reference-skip.json", publish / "reference-skip.json")
            local_issues = list(getattr(translator, "local_issues", []))
            if (work / "layout-report.json").is_file():
                layout = json.loads((work / "layout-report.json").read_text(encoding="utf-8"))
                def metadata(value):
                    if isinstance(value, dict):
                        return {k: metadata(v) for k, v in value.items()
                                if k not in {"source", "text", "translation", "unicode"}}
                    if isinstance(value, list):
                        return [metadata(v) for v in value]
                    return value
                layout = metadata(layout)
                (publish / "layout-report.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
                explanation = ["保留版式处理说明", "未自动切换阅读版；保留原文的区域不代表已完成翻译。", ""]
                for number, regions in layout.get("untranslated_regions", {}).items():
                    for index, region in enumerate(regions, 1):
                        origin = "左上角" if region.get("coordinates") == "top-left" else "左下角"
                        # Navigation failures retain one issue spanning several labels.
                        boxes = [region["box"]] if "box" in region else region.get("boxes", [])
                        if (not isinstance(boxes, (list, tuple)) or not boxes or any(
                                not isinstance(rect, (list, tuple)) or len(rect) != 4
                                or any(type(v) not in (int, float) or not math.isfinite(v) for v in rect)
                                or rect[0] >= rect[2] or rect[1] >= rect[3] for rect in boxes)):
                            raise DocumentError(f"[未翻译区域坐标无效] 原始第 {int(number) + 1} 页，"
                                                f"区域 {index} 无法安全记录位置，未发布结果。")
                        box = "；".join(", ".join(f"{v:.1f}" for v in rect) for rect in boxes)
                        local_issues.append(dict(region, page=int(number) + 1,
                            id=region.get("id", f"P{int(number)+1}-U{index}")))
                        explanation.extend([f"原始第 {int(number) + 1} 页，区域 {index}",
                            f"坐标：{box} pt（以页面{origin}为原点）",
                            "处理结果：保留原文", "原因：" + region["reason"],
                            "内容：请对照输出文件的未翻译标记及原文。", ""])
                if len(explanation) == 3:
                    explanation.append("没有检测到需要保留原文的排版冲突；公式和图表仍建议对照原文核对。")
                (publish / "排版说明.txt").write_text("\n".join(explanation), encoding="utf-8-sig")
            for index, issue in enumerate(local_issues, 1):
                issue.setdefault("id", f"U{index}")
                issue.setdefault("stage", "识别/排版")
                issue.setdefault("fallback", "保留原文")
                issue.setdefault("retry_result", "未追加请求")
            from linguafloat.document_postprocess import FOLDER
            artifact = Path(job.graphics_source) if job.workflow == "graphics" else work / FOLDER
            if artifact.is_dir() and (artifact / "manifest.json").is_file():
                shutil.copytree(artifact, publish / FOLDER)
                if job.workflow == "body":
                    manifest_path = publish / FOLDER / "manifest.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["body_issues"] = local_issues
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            if local_issues:
                try:
                    (publish / "未翻译问题.jsonl").write_text("\n".join(json.dumps(dict(
                        job_id=job.job_id, source_id=hashlib.sha256(str(job.source).encode()).hexdigest(),
                        **issue), ensure_ascii=False) for issue in local_issues), encoding="utf-8")
                    lines = [f"{r['id']} · " + (f"第 {r['page']} 页" if "page" in r else
                        f"{r.get('location', '文档')} 第 {r.get('paragraph', '?')} 段") + " · " + r["reason"] for r in local_issues]
                    (publish / "未翻译说明.txt").write_text("\n".join(lines), encoding="utf-8-sig")
                except OSError:
                    warnings.append("未翻译问题日志写入失败；位置及原因仍保留在任务历史中。")
            (publish / "performance.json").write_text(json.dumps(emit.summary(), ensure_ascii=False, indent=2), encoding="utf-8")
            evidence = {}
            for name in ("source_context", "graphic_context"):
                context = getattr(translator, name, None)
                if callable(getattr(context, "export_evidence", None)):
                    evidence[name] = context.export_evidence()
            if evidence:
                (publish / "terminology-evidence.json").write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            from linguafloat.document_postprocess import digest as artifact_digest
            artifact_manifest = publish / FOLDER / "manifest.json"
            export_record = None
            export_reason = ''
            if all(Path(p).suffix.lower()=='.pdf' for p in outputs) and (artifact_manifest.is_file() or (work/'export-pages.json').is_file()):
                from linguafloat.document_export import MANUSCRIPT, create_manuscript
                try:
                    report_path = publish / 'layout-report.json'
                    report = json.loads(report_path.read_bytes()) if report_path.exists() else {}
                    artifact_data = json.loads(artifact_manifest.read_bytes()) if artifact_manifest.exists() else {}
                    if job.workflow == 'body' and artifact_data:
                        anchors = {k:report.get(k,{}) for k in ('normal_preserved_regions','export_formulas')}
                        anchors['paragraphs'] = [{k:r[k] for k in ('page','box','role') if k in r} for r in report.get('paragraphs',[])]
                        anchor_file = artifact_manifest.with_name('export-layout.json')
                        anchor_file.write_text(json.dumps(anchors,ensure_ascii=False),'utf8')
                        artifact_data['export_layout_sha256'] = artifact_digest(anchor_file)
                        artifact_manifest.write_text(json.dumps(artifact_data,ensure_ascii=False,indent=2),'utf8')
                    if job.workflow == 'graphics':
                        anchor_file = artifact_manifest.with_name('export-layout.json')
                        if not anchor_file.is_file() or artifact_digest(anchor_file)!=artifact_data.get('export_layout_sha256'):
                            raise ValueError('旧正文缺少经过核验的图表边界。')
                        report.update(json.loads(anchor_file.read_bytes()))
                    mapping = artifact_data.get('pages') or json.loads((work/'export-pages.json').read_bytes())
                    mono = Path(outputs[0]) if job.mono else ((work/FOLDER/'body.pdf') if job.workflow=='body' and artifact_data else work/'export-panel.pdf')
                    export_record = create_manuscript(mono,publish,job.job_id,report,mapping,
                        outputs=[publish/name for name in names],checkpoint=control.checkpoint)
                except Cancelled:
                    raise
                except Exception as exc:
                    (publish/MANUSCRIPT).unlink(missing_ok=True)
                    export_reason = f'可编辑导出底稿未生成，PDF 已保留：{exc}'
            graphics_manifest_sha256 = artifact_digest(artifact_manifest) if artifact_manifest.is_file() else ""
            review_layout = publish / 'layout-report.json'
            review_layout_sha256 = (artifact_digest(review_layout) if job.workflow == 'body' and review_layout.is_file() else '')
            control.checkpoint()
            publish.rename(target)
            paths = [str(target / name) for name in names]
        unresolved = len(local_issues)
        transport_partial = getattr(translator, 'graphic_failure', None) is not None
        if transport_partial:
            warnings.append('部分完成：图表连接重试已耗尽，已停止剩余图表请求。正文及成功图文已保存，未完成区域保留原文并标记；请主动重试补译。')
        issues = [f"{r['id']} · " + (f"第 {r['page']} 页" if "page" in r else
                  f"{r.get('location', '文档')} 第 {r.get('paragraph', '?')} 段") + " · " + r["reason"] for r in local_issues]
        result = {"outputs": paths, "warnings": warnings, "untranslated_regions": unresolved,
                  "export_manuscript": export_record,
                  "export_unavailable": export_reason,
                  "parent_job_id": job.parent_job_id,
                  "graphics_source": str(target / FOLDER) if (target / FOLDER).is_dir() else "",
                  "graphics_manifest_sha256": graphics_manifest_sha256,
                  "review_layout_sha256": review_layout_sha256,
                  "workflow": job.workflow,
                  "transport_partial": transport_partial,
                  "issues": issues,
                  "local_failures": local_issues,
                  "reference_boundary": job.reference_boundary,
                  "requests": translator.calls, "cache_hits": translator.hits,
                  "performance": emit.summary(), "usage": getattr(translator, "token_usage", {})}
        store.status(job, "partial" if transport_partial else "completed", result)
        row = store.db.execute("SELECT details FROM jobs WHERE id=?", (job.job_id,)).fetchone()
        result["elapsed_seconds"] = json.loads(row[0]).get("elapsed_seconds")
        emit({"type": "finished", **result})
    finally:
        store.record_usage(job, {"performance": emit.summary()})
        if hasattr(translator, "client"):
            translator.client.close()


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    protocol = sys.stdout
    lock = threading.Lock()
    stage = "载入任务"

    def emit(event):
        nonlocal stage
        if event.get("stage"):
            stage = event["stage"]
        with lock:
            if job is not None:
                event = {**event, "job_id": job.job_id}
            protocol.write(json.dumps(event, ensure_ascii=False) + "\n")
            protocol.flush()

    # Libraries may print progress or HTTP details. Keep them off the IPC channel.
    logging.disable(logging.CRITICAL)
    control = JobControl()
    emit = PerformanceLog(control, emit)
    store = job = None
    with open(os.devnull, "w", encoding="utf-8") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            message = json.loads(sys.stdin.readline())
            if message.get('operation')=='export_result':
                from linguafloat.document_export import export_file
                try:
                    path, note = export_file(message['identity'],message['info'],message['format'],message['destination'])
                    emit({'type':'exported','path':str(path),'note':note})
                except Exception as error:
                    emit({'type':'error','message':str(error)})
                return
            if message.get('operation') == 'review_result':
                from linguafloat.document_review import run_review
                try:
                    run_review(message, emit)
                except Exception:
                    emit({'type': 'review_error', 'message': '校对未完成，请核对原 PDF、任务记录及校对接口。PDF 未改动。'})
                return
            if message.get("operation")=="graphics_preview":
                from linguafloat.document_graphics import preview_page
                preview_page(message,emit)
                return
            job = DocumentJob(**message["job"])
            store = JobStore(Path(message["database"]))
            # Load numerical DLLs before another thread blocks on Windows stdin.
            # Some BLAS initializers acquire CRT stream locks during DLL loading.
            if Path(job.source).suffix.lower() not in {".docx", ".pptx"}:
                started = active_clock(control)
                emit({"type": "progress", "stage": "解析中", "value": None, "message": "加载文档识别引擎"})
                import numpy
                import onnxruntime
                if job.pdf_mode == "layout":
                    import scipy
                    import pdf2zh_next.high_level
                emit({"type": "measurement", "name": "文档引擎导入", "seconds": active_clock(control) - started})

            def commands():
                for line in sys.stdin:
                    try:
                        command = json.loads(line).get("command")
                        if command == "pause":
                            control.paused.set()
                        elif command == "resume":
                            control.paused.clear()
                        elif command == "cancel":
                            control.cancelled.set()
                    except (ValueError, TypeError):
                        continue
                control.cancelled.set()

            threading.Thread(target=commands, daemon=True).start()
            execute(job, message.pop("api_key"), store, control, emit)
        except Cancelled as exc:
            if store and job:
                store.status(job, "cancelled", {"performance": emit.summary()})
            emit({"type": "cancelled", "message": str(exc)})
        except Exception as exc:
            # Only locally authored messages are displayed, never raw upstream bodies.
            reason = describe_failure(exc, job, stage)
            if store and job:
                store.status(job, "failed", {"message": reason, "performance": emit.summary(),
                                             "diagnostic": failure_trace(exc)})
            emit({"type": "error", "message": reason})
        finally:
            if store:
                store.close()


if __name__ == "__main__":
    main()
