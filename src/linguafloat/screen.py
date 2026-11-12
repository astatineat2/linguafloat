from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QRect, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from linguafloat.ocr import LocalOcr, OcrTask, ReleaseOcrTask
from linguafloat.screen_ui import RegionOutline, RegionSelector, ScreenTranslationPanel, panel_geometry, constrain_panel
from linguafloat.selection import normalize_selection
from linguafloat.screen_layout import translation_chunks


@dataclass
class ScreenTextState:
    interval: float = 3.0
    running: bool = False
    generation: int = 0
    text: str = ""
    observations: int = 0
    completed: str = ""
    sequence: int = 0
    pending: tuple[int, str, int] | None = None
    last_request: float = float("-inf")

    def reset(self, running: bool) -> None:
        # An uncancellable HTTP request still occupies its slot until it returns.
        self.generation += 1
        self.running = running
        self.text = self.completed = ""
        self.observations = 0

    def observe(self, text: str) -> bool:
        changed = text != self.text
        self.observations = 1 if changed else self.observations + 1
        self.text = text
        if changed:
            self.completed = ""
        return changed

    def next_request(self, now: float) -> tuple[int, str] | None:
        if (not self.running or self.pending or not self.text or self.observations < 2
                or self.completed == self.text or now - self.last_request < self.interval):
            return None
        self.sequence += 1
        self.pending = (self.sequence, self.text, self.generation)
        self.last_request = now
        return self.sequence, self.text

    def finish(self, token: int) -> bool:
        if self.pending is None or self.pending[0] != token:
            return False
        _, text, generation = self.pending
        self.pending = None
        current = (self.running and generation == self.generation
                   and text == self.text and self.observations >= 2)
        if current:
            self.completed = text
        return current


class ScreenReader(QObject):
    translation_requested = Signal(str, int)
    status_changed = Signal(str, bool, bool)
    intervals_changed = Signal(float, float)

    def __init__(self, window, scope: Callable[[], str], parent=None) -> None:
        super().__init__(parent)
        self.window = window
        self.scope = scope
        self.state = ScreenTextState()
        self.panel = ScreenTranslationPanel()
        self.outline = RegionOutline()
        self.panel.pause_requested.connect(self.toggle_pause)
        self.panel.select_requested.connect(self.select_region)
        self.panel.move_region_requested.connect(self.move_region)
        self.panel.stop_requested.connect(self.stop)
        self.panel.intervals_changed.connect(self.intervals_changed.emit)
        self.outline.move_started.connect(self._outline_move_started)
        self.outline.moved.connect(self._outline_moved)
        self.outline.move_finished.connect(self._outline_move_finished)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.settle_timer = QTimer(self)
        self.settle_timer.setSingleShot(True)
        self.settle_timer.setInterval(1000)
        self.settle_timer.timeout.connect(self._settled)
        self.dispatch_timer = QTimer(self)
        self.dispatch_timer.setInterval(100)
        self.dispatch_timer.timeout.connect(self._pump)
        self._batch = None
        self._block_jobs = {}
        self._block_sequence = 0
        self._last_wave = float("-inf")
        self._pumping = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.engine = LocalOcr()
        self._ocr_cache = None  # One digest/text/boxes only, never an image.
        self.idle_release_timer = QTimer(self)
        self.idle_release_timer.setSingleShot(True)
        self.idle_release_timer.setInterval(120_000)
        self.idle_release_timer.timeout.connect(self._release_idle_ocr)
        self.region = QRect()
        self.screen = None
        self.screen_bounds = QRect()
        self.screen_scale = 1.0
        self.active_scope = ""
        self.selectors: list[RegionSelector] = []
        self.selecting = False
        self.moving_region = False
        self.resume_after_cancel = False
        self.ocr_job: tuple[int, int] | None = None
        self.job_sequence = 0
        self.frame_digest: bytes | None = None
        self.frame_text = ""
        self.frame_regions = []
        self.outline_dragging = False

    def set_intervals(self, ocr_interval: float, translation_interval: float) -> None:
        self.timer.setInterval(round(1000 * ocr_interval))
        self.state.interval = translation_interval
        self.panel.set_intervals(ocr_interval, translation_interval)

    def _status(self, message: str, *, error: bool = False) -> None:
        self.panel.set_status(message, error=error)
        self.panel.set_paused(not self.state.running)
        self.status_changed.emit(message, not self.region.isEmpty(), self.state.running)

    @Slot()
    def select_region(self) -> None:
        self._begin_selection(False)

    @Slot()
    def move_region(self) -> None:
        if not self.region.isEmpty():
            self._begin_selection(True)

    def _begin_selection(self, moving: bool) -> None:
        if self.selecting:
            return
        self.moving_region = moving
        self.selecting = True
        self.resume_after_cancel = self.state.running
        self.pause()
        self.panel.hide()
        self.outline.hide()
        # Hide other LinguaFloat surfaces; selection remains live and stores no screen images.
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible():
                widget.hide()
        QTimer.singleShot(0, self._show_selectors)

    def _show_selectors(self) -> None:
        if not self.selecting:
            return
        screens = QApplication.screens()
        if self.moving_region:
            screens = [screen for screen in screens if screen is self.screen]
        if not screens:
            self._cancel_selection()
            self._status("未找到可用显示器", error=True)
            return
        for screen in screens:
            selector = RegionSelector(screen, move_region=self.region if self.moving_region else None)
            if not selector.glass_ready:
                selector.deleteLater()
                self._cancel_selection()
                self._status("无法建立实时透明选区，请检查 Windows 桌面合成是否可用。", error=True)
                return
            selector.selected.connect(self._selected)
            selector.cancelled.connect(self._cancel_selection)
            self.selectors.append(selector)
            selector.show()
            selector.raise_()
        self.selectors[0].activateWindow()

    def _close_selectors(self) -> None:
        self.selecting = False
        for selector in self.selectors:
            selector.close()
            selector.deleteLater()
        self.selectors.clear()

    def _cancel_selection(self) -> None:
        self._close_selectors()
        if not self.region.isEmpty():
            self.panel.show()
            self.outline.show_region(self.region)
            if self.resume_after_cancel:
                self.resume()
        self.window.bring_to_front()

    def _selected(self, screen, region: QRect) -> None:
        self._close_selectors()
        if not self.start_region(screen, region):
            self.window.bring_to_front()
        elif self.moving_region and not self.resume_after_cancel:
            self.pause()

    def start_region(self, screen, region: QRect) -> bool:
        self.timer.stop()
        if not self.outline.glass_ready:
            self._status("无法建立透明识别框，请检查 Windows 桌面合成是否可用。", error=True)
            return False
        self._batch = None
        self.state.pending = None
        if region.width() < 48 or region.height() < 32:
            self.stop()
            self._status("识别区域至少需要 48 × 32，请扩大后重新框选。", error=True)
            return False
        geometry = panel_geometry(region, screen.availableGeometry())
        if geometry is None or not screen.geometry().contains(region):
            self.stop()
            self._status("区域周围没有足够空间显示译文，请缩小范围后重新框选。", error=True)
            return False
        self.screen = screen
        self.screen_bounds = QRect(screen.geometry())
        self.screen_scale = screen.devicePixelRatio()
        self.region = QRect(region)
        self.active_scope = self.scope()
        self.idle_release_timer.stop()
        self.state.reset(True)
        self.frame_digest = None
        self.panel.clear_translation()
        self.panel.source_region = QRect(region)
        if self.panel.user_moved:
            available = screen.availableGeometry()
            position = constrain_panel(self.panel.pos(), self.panel.size(), available, region)
            candidate = QRect(position, self.panel.size())
            self.panel.setGeometry(candidate if available.contains(candidate) and not candidate.intersects(region) else geometry)
        else:
            self.panel.setGeometry(geometry)
        self.panel.show()
        self.outline.show_region(region)
        self._status("正在读取区域")
        self.settle_timer.start()
        self.dispatch_timer.start()
        return True

    @Slot()
    def _outline_move_started(self) -> None:
        if self.region.isEmpty() or self.selecting:
            return
        self.outline_dragging = True
        self.resume_after_cancel = self.state.running
        self.pause()

    @Slot(object)
    def _outline_moved(self, region: QRect) -> None:
        if self.outline_dragging:
            self.panel.source_region = QRect(region)

    @Slot(object)
    def _outline_move_finished(self, region: QRect) -> None:
        if not self.outline_dragging:
            return
        self.outline_dragging = False
        if self.screen is None or not self.screen.geometry().contains(region):
            self.outline.show_region(self.region)
            if self.resume_after_cancel:
                self.resume()
            return
        previous_text = self.panel.translation.toPlainText()
        previous_source = self.panel.translation.toolTip()
        if self.start_region(self.screen, region) and not self.resume_after_cancel:
            self.panel.show_translation(previous_source, previous_text)
            self.pause()

    @Slot()
    def toggle_pause(self) -> None:
        if self.state.running:
            self.pause()
        else:
            self.resume()

    def pause(self) -> None:
        self.timer.stop()
        self.settle_timer.stop()
        self.dispatch_timer.stop()
        self._batch = None
        self.state.pending = None
        self.state.reset(False)
        self.frame_digest = None
        if not self.idle_release_timer.isActive():
            self.idle_release_timer.start(120_000)
        self._status("已暂停" if not self.region.isEmpty() else "未选择区域")

    def resume(self) -> None:
        if self.region.isEmpty():
            return
        self.idle_release_timer.stop()
        self.state.reset(True)
        self.frame_digest = None
        self.active_scope = self.scope()
        self._status("正在读取区域")
        self.settle_timer.start()
        self.dispatch_timer.start()

    @Slot()
    def stop(self) -> None:
        self._close_selectors()
        self.pause()
        self.region = QRect()
        self.panel.hide()
        self.panel.clear_translation()
        self.outline.hide()
        self.frame_text = ""
        self.frame_regions = []
        self._status("未选择区域")

    def _check_scope(self) -> None:
        current = self.scope()
        if current != self.active_scope:
            self.active_scope = current
            self._batch = None
            self.state.pending = None
            self.state.reset(self.state.running)
            self.frame_digest = None
            if self.state.running:
                self.panel.clear_translation()

    def request_is_current(self, token: int) -> bool:
        if token in self._block_jobs:
            batch, _ = self._block_jobs[token]
            return bool(self.state.running and batch is self._batch
                        and batch["generation"] == self.state.generation
                        and batch["text"] == self.state.text and self.active_scope == self.scope())
        pending = self.state.pending
        return bool(self.state.running and pending and pending[0] == token
                    and pending[2] == self.state.generation and self.active_scope == self.scope())

    def _settled(self):
        if not self.state.running:
            return
        self.tick()
        if self.state.running:
            self.timer.start()

    @Slot()
    def tick(self) -> None:
        if not self.state.running or self.selecting:
            return
        if (self.screen not in QApplication.screens() or self.screen.geometry() != self.screen_bounds
                or self.screen.devicePixelRatio() != self.screen_scale):
            self.stop()
            self._status("显示器布局已改变，请重新选择区域。", error=True)
            return
        self._check_scope()
        if self.ocr_job is not None:
            return
        for widget in QApplication.topLevelWidgets():
            if widget is not self.outline and widget.isVisible() and widget.frameGeometry().intersects(self.region):
                self.state.observe("")
                self.frame_digest = None
                self.panel.clear_translation()
                self._status("区域被浮译窗口遮挡")
                return
        local = self.region.translated(-self.screen_bounds.topLeft())
        image = self.screen.grabWindow(0, local.x(), local.y(), local.width(), local.height()).toImage()
        if image.isNull():
            self.pause()
            self._status("无法读取画面，区域翻译已暂停。", error=True)
            return
        fingerprint = hashlib.sha256(f"{image.width()}:{image.height()}:{image.format().value}:".encode())
        fingerprint.update(image.constBits())
        digest = fingerprint.digest()
        if self._ocr_cache is not None and self._ocr_cache[0] == digest:
            self.frame_digest, self.frame_text, self.frame_regions = self._ocr_cache
            self._observe(self.frame_text)
            return
        if digest == self.frame_digest:
            self._observe(self.frame_text)
            return
        self.job_sequence += 1
        job = self.job_sequence
        self.ocr_job = (job, self.state.generation)
        generation = self.state.generation
        task = OcrTask(self.engine, image, job, digest,
                       allowed=lambda: self.state.running and self.state.generation == generation)
        self._status("正在加载本地 OCR 模型" if self.engine.engine is None else "正在识别文字")
        task.signals.phase.connect(self._recognition_phase)
        task.signals.succeeded.connect(self._recognized)
        task.signals.regions.connect(self._recognized_regions)
        task.signals.failed.connect(self._recognition_failed)
        try:
            self.pool.start(task)
        except Exception:
            self._recognition_failed(job, "无法启动文字识别")

    @Slot(int, str)
    def _recognition_phase(self, job, message):
        if self.ocr_job == (job, self.state.generation) and self.state.running:
            self._status(message)

    @Slot(int, object)
    def _recognized_regions(self, job, regions):
        if self.ocr_job == (job, self.state.generation) and self.state.running:
            self.frame_regions = list(regions)

    @Slot(int, bytes, str)
    def _recognized(self, job: int, digest: bytes, text: str) -> None:
        current = self.ocr_job
        if current is None or current[0] != job:
            return
        self.ocr_job = None
        self._check_scope()
        if current[1] != self.state.generation or not self.state.running:
            return
        self.frame_digest = digest
        self.frame_text = text
        self._ocr_cache = (digest, text, list(self.frame_regions))
        self._observe(text)
        if self._batch is not None:
            self._render_batch()

    def _observe(self, text: str) -> None:
        if not self.state.running:
            return
        try:
            text = normalize_selection(text)
        except ValueError as exc:
            self.pause()
            self.panel.clear_translation()
            self._status(str(exc), error=True)
            return
        changed = self.state.observe(text)
        # The region has already settled for one second before the first OCR.
        # Subsequent OCR snapshots are authoritative; no second full timer wait.
        self.state.observations = max(2, self.state.observations)
        if changed:
            self._batch = None
            self.state.pending = None
        if changed or not text:
            self.panel.clear_translation()
        if not text:
            self._status("未识别到文字")
            return
        self._pump()

    def _pump(self):
        if self._pumping or not self.state.running or self.settle_timer.isActive():
            return
        self._pumping = True
        try:
            self._check_scope()
            now = time.monotonic()
            if self._batch is None:
                request = self.state.next_request(now)
                if request is None:
                    return
                token, text = request
                chunks = translation_chunks(text)
                self._batch = dict(token=token, generation=self.state.generation, text=text,
                                   blocks=[[part,"","queued"] for part in chunks],
                                   regions=self._chunk_regions(chunks))
                self.panel.show_blocks(self._batch["blocks"])
            batch = self._batch
            if batch is None or now-self._last_wave < self.state.interval:
                return
            capacity = max(0, 2-len(self._block_jobs))
            queued = [i for i,b in enumerate(batch["blocks"]) if b[2]=="queued"][:capacity]
            if queued:
                self._last_wave = now
            for index in queued:
                if batch is not self._batch or not self.state.running:
                    break
                block = batch["blocks"][index]
                self._block_sequence -= 1
                token = self._block_sequence
                block[2] = "pending"
                self._block_jobs[token] = (batch,index)
                self.translation_requested.emit(block[0],token)
            if queued and batch is self._batch:
                self._render_batch()
        finally:
            self._pumping = False

    def _render_batch(self):
        batch=self._batch
        if batch is None or not self.state.running:
            return
        blocks=batch["blocks"]
        self.panel.show_blocks(blocks)
        done=sum(b[2]=="done" for b in blocks)
        errors=sum(b[2]=="error" for b in blocks)
        if done+errors==len(blocks):
            self.state.finish(batch["token"])
            self._status(f"部分完成 · {errors} 段失败，暂停后继续可重试" if errors else "翻译完成", error=bool(errors))
        else:
            self._status(f"已完成 {done}/{len(blocks)} 段" + (f" · {errors} 段失败" if errors else " · 翻译中"))

    def _chunk_regions(self, chunks):
        # Match normalized character offsets, preserving original OCR row bounds.
        # Multiple sentences on one row deliberately reference the same row.
        rows=[]; cursor=0
        for row in self.frame_regions:
            length=len("".join(row["text"].split()))
            rows.append((cursor,cursor+length,row["box"])); cursor+=length
        mapped=[]; cursor=0
        for chunk in chunks:
            end=cursor+len("".join(chunk.split()))
            mapped.append([box for start,stop,box in rows if start<end and stop>cursor])
            cursor=end
        return mapped

    def cancel_request(self, token):
        self._block_jobs.pop(token, None)
        self.state.finish(token)
        self._pump()

    @Slot(int, str)
    def _recognition_failed(self, job: int, message: str) -> None:
        current = self.ocr_job
        if current is None or current[0] != job:
            return
        self.ocr_job = None
        if current[1] == self.state.generation and self.state.running:
            self.pause()
            self._status(message, error=True)

    def translated(self, token: int, source: str, result: str, *, error: bool = False) -> None:
        self._check_scope()
        if token in self._block_jobs:
            current = self.request_is_current(token)
            batch,index = self._block_jobs.pop(token)
            if current:
                batch["blocks"][index][1:] = [result,"error" if error else "done"]
                self._render_batch()
            self._pump()
            return
        self._check_scope()
        pending = self.state.pending
        same_session = pending is not None and pending[0] == token and pending[2] == self.state.generation
        accepted = self.state.finish(token)
        if error and same_session and self.state.running:
            # Stop after a failed request, including a failure for obsolete text.
            self.pause()
            self._status(result, error=True)
        elif accepted:
            self.panel.show_translation(source, result)
            self._status("翻译完成")

    def _release_idle_ocr(self):
        if self.state.running:
            return
        if self.pool.activeThreadCount():
            self.idle_release_timer.start(1000)
            return
        generation = self.state.generation
        self._ocr_cache = None
        self.pool.start(ReleaseOcrTask(self.engine, lambda: not self.state.running
                                     and self.state.generation == generation))

    def shutdown(self) -> bool:
        self.stop()
        self.idle_release_timer.stop()
        # Active inference cannot safely be killed. Caller polls without blocking
        # the GUI; no late callback can restart the stopped reader.
        return self.pool.activeThreadCount() == 0
