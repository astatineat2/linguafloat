"""Monotonic, pause-aware measurements and conservative work-based estimates."""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict


class PauseEvent(threading.Event):
    def __init__(self):
        super().__init__()
        self._clock_lock = threading.RLock()
        self._since = None
        self._elapsed = 0.0

    def set(self):
        with self._clock_lock:
            if self._since is None:
                self._since = time.monotonic()
            super().set()

    def clear(self):
        with self._clock_lock:
            if self._since is not None:
                self._elapsed += time.monotonic() - self._since
                self._since = None
            super().clear()

    def elapsed(self):
        with self._clock_lock:
            return self._elapsed + (time.monotonic() - self._since if self._since is not None else 0)


def active_clock(control):
    return time.monotonic() - control.paused.elapsed()


class PerformanceLog:
    """Main pipeline phase wall times; request metrics may overlap these phases."""
    def __init__(self, control, sink):
        self.control, self.sink = control, sink
        self.lock = threading.RLock()
        self.started = self.last = active_clock(control)
        self.phase = "启动中"
        self.stage_key = None
        self.stage_started = self.started
        self.phases = defaultdict(float)
        self.operations = defaultdict(float)
        self.counts = defaultdict(int)
        self.persist = None
        self.persisted_at = self.started

    def __call__(self, event):
        with self.lock:
            now = active_clock(self.control)
            event = dict(event)
            if event.get("type") == "measurement":
                name = event["name"]
                self.operations[name] += max(0, event["seconds"])
                self.counts[name] += event.get("amount", 1)
                return
            if event.get("type") == "progress" or event.get("type") == "activity" and event.get("stage"):
                phase = event.get("stage", self.phase)
                if phase != self.phase:
                    self.phases[self.phase] += max(0, now - self.last)
                    self.last, self.phase = now, phase
                key = event.get("stage_key", phase)
                if key != self.stage_key:
                    self.stage_key, self.stage_started = key, now
                event.update(stage_key=key, phase_elapsed=max(0, now - self.stage_started))
            self.sink(event)
            if self.persist and now - self.persisted_at >= 1:
                self.persist(self.summary())
                self.persisted_at = now

    def summary(self):
        with self.lock:
            now = active_clock(self.control)
            phases = dict(self.phases)
            phases[self.phase] = phases.get(self.phase, 0) + max(0, now - self.last)
            return {"active_seconds": max(0, now - self.started),
                    "paused_seconds": self.control.paused.elapsed(),
                    "phases": phases, "operations": dict(self.operations),
                    "operation_counts": dict(self.counts)}


def duration_text(seconds):
    seconds = max(0, int(math.ceil(seconds)))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分 {seconds} 秒" if seconds else f"{minutes} 分"


class WorkEstimate:
    def __init__(self):
        self.key = None
        self.samples = 0
        self.last_done = self.last_elapsed = 0
        self.rate = None
        self.remaining = None
        self.stage = ""

    def observe(self, event):
        key = event.get("stage_key", event.get("stage"))
        done, total = event.get("work_done"), event.get("work_total")
        elapsed = event.get("phase_elapsed")
        if key != self.key:
            self.__init__()
            self.key = key
        self.stage = event.get("stage", "处理").removesuffix("中").strip()
        if not all(isinstance(n, (int, float)) and math.isfinite(n) for n in (done, total, elapsed)):
            self.remaining = None
            return
        if total <= 0 or done < self.last_done or elapsed < self.last_elapsed or done > total:
            self.remaining = None
            return
        delta, seconds = done - self.last_done, elapsed - self.last_elapsed
        if delta > 0 and seconds >= .2:
            rate = delta / seconds
            self.rate = rate if self.rate is None else .35 * rate + .65 * self.rate
            self.samples += 1
            self.last_done, self.last_elapsed = done, elapsed
        self.remaining = ((total - done) / self.rate
                          if self.samples >= 3 and self.rate and done < total else None)

    def display(self):
        if self.remaining is None:
            return "估算中", "样本不足或剩余阶段不可可靠估算；不按文件大小或百分比倒推。"
        rounded = max(5, math.ceil(self.remaining / 5) * 5)
        return (f"{self.stage}约" + duration_text(rounded).replace(" ", ""),
                f"仅当前{self.stage}阶段约剩 {duration_text(rounded)}；后续阶段待估算。按已完成工作量和有效运行时间更新，排除暂停。")
