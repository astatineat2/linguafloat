from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QRect, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from linguafloat.ocr import LocalOcr, OcrTask
from linguafloat.screen_ui import RegionOutline, RegionSelector, ScreenTranslationPanel, panel_geometry, constrain_panel
from linguafloat.selection import normalize_selection


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
        self.outline.move_started.connect(self._outline_move_started)
        self.outline.moved.connect(self._outline_moved)
        self.outline.move_finished.connect(self._outline_move_finished)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.engine = LocalOcr()
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
        self.outline_dragging = False

    def set_intervals(self, ocr_interval: float, translation_interval: float) -> None:
        self.timer.setInterval(round(1000 * ocr_interval))
        self.state.interval = translation_interval

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
        # Hide all other LinguaFloat surfaces before taking selector snapshots.
        for widget in QApplication.topLevelWidgets():
            if widget.isVisible():
                widget.hide()
        QTimer.singleShot(200, self._show_selectors)

    def _show_selectors(self) -> None:
        if not self.selecting:
            return
        screens = QApplication.screens()
        if self.moving_region:
            screens = [screen for screen in screens if screen is self.screen]
        snapshots = [(screen, screen.grabWindow(0)) for screen in screens]
        if not snapshots or any(snapshot.isNull() for _, snapshot in snapshots):
            self._cancel_selection()
            self._status("无法读取屏幕画面", error=True)
            return
        for screen, snapshot in snapshots:
            selector = RegionSelector(screen, snapshot, self.region if self.moving_region else None)
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
        self.timer.start()
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
        self.state.reset(False)
        self.frame_digest = None
        self._status("已暂停" if not self.region.isEmpty() else "未选择区域")

    def resume(self) -> None:
        if self.region.isEmpty():
            return
        self.state.reset(True)
        self.frame_digest = None
        self.active_scope = self.scope()
        self._status("正在读取区域")
        self.timer.start()

    @Slot()
    def stop(self) -> None:
        self._close_selectors()
        self.pause()
        self.region = QRect()
        self.panel.hide()
        self.panel.clear_translation()
        self.outline.hide()
        self._status("未选择区域")

    def _check_scope(self) -> None:
        current = self.scope()
        if current != self.active_scope:
            self.active_scope = current
            self.state.reset(self.state.running)
            self.frame_digest = None
            if self.state.running:
                self.panel.clear_translation()

    def request_is_current(self, token: int) -> bool:
        pending = self.state.pending
        return bool(self.state.running and pending and pending[0] == token
                    and pending[2] == self.state.generation and self.active_scope == self.scope())

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
        digest = hashlib.sha256(image.constBits()).digest()
        if digest == self.frame_digest:
            self._observe(self.frame_text)
            return
        self.job_sequence += 1
        job = self.job_sequence
        self.ocr_job = (job, self.state.generation)
        generation = self.state.generation
        task = OcrTask(self.engine, image, job, digest,
                       allowed=lambda: self.state.running and self.state.generation == generation)
        task.signals.succeeded.connect(self._recognized)
        task.signals.failed.connect(self._recognition_failed)
        try:
            self.pool.start(task)
        except Exception:
            self._recognition_failed(job, "无法启动文字识别")

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
        self._observe(text)

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
        if changed or not text:
            self.panel.clear_translation()
        if not text:
            self._status("未识别到文字")
            return
        request = self.state.next_request(time.monotonic())
        if request is not None:
            token, source = request
            self._status("翻译中")
            self.translation_requested.emit(source, token)
        elif self.state.pending:
            self._status("翻译中，等待最新内容")
        elif self.state.completed != text:
            self._status("等待文字稳定" if self.state.observations < 2 else "等待下次翻译")

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

    def shutdown(self) -> None:
        self.stop()
        self.pool.clear()
        self.pool.waitForDone()
