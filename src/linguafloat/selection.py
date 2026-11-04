from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass

from pynput import keyboard, mouse
from PySide6.QtCore import QObject, Signal, Slot


MAX_SELECTION_LENGTH = 5000
TEXT_PATTERN_ID = 10014
_TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*\b", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)
_CONTROL_KEYS = {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
_SHIFT_KEYS = {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r}
_SELECTION_NAVIGATION_KEYS = {
    keyboard.Key.left,
    keyboard.Key.right,
    keyboard.Key.up,
    keyboard.Key.down,
    keyboard.Key.home,
    keyboard.Key.end,
    keyboard.Key.page_up,
    keyboard.Key.page_down,
}


@dataclass(frozen=True, slots=True)
class SelectionCandidate:
    text: str
    anchor_x: int
    anchor_y: int
    left: int | None = None
    top: int | None = None
    right: int | None = None
    bottom: int | None = None

    def contains(self, x: int, y: int, padding: int = 12) -> bool:
        if None in (self.left, self.top, self.right, self.bottom):
            return True
        return (
            self.left - padding <= x <= self.right + padding
            and self.top - padding <= y <= self.bottom + padding
        )


def normalize_selection(text: str, max_length: int = MAX_SELECTION_LENGTH) -> str:
    normalized = text.replace("\x00", "").strip()
    if not normalized:
        return ""
    if len(normalized) > max_length:
        raise ValueError(f"所选文字超过 {max_length} 字，请缩短后重试。")
    if looks_like_secret(normalized):
        raise ValueError("所选内容看起来像密码或密钥，已阻止取词。")
    return normalized


def looks_like_secret(text: str) -> bool:
    if any(pattern.search(text) for pattern in _TOKEN_PATTERNS):
        return True
    compact = "".join(text.split())
    if len(compact) >= 40 and " " not in text and re.fullmatch(
        r"[A-Za-z0-9_./+=-]+", compact
    ):
        return True
    return False


def selected_text_from_control(
    control: object,
    click_x: int,
    click_y: int,
    *,
    own_process_id: int | None = None,
    max_length: int = MAX_SELECTION_LENGTH,
    allowed=None,
) -> SelectionCandidate | None:
    current = control
    visited: set[int] = set()
    for _ in range(12):
        if allowed is not None and not allowed():
            return None
        if current is None or id(current) in visited:
            break
        visited.add(id(current))
        try:
            if own_process_id is not None and current.ProcessId == own_process_id:
                return None
        except Exception:
            pass
        try:
            if current.IsPassword:
                return None
        except Exception:
            pass

        try:
            pattern = current.GetPattern(TEXT_PATTERN_ID)
        except Exception:
            pattern = None
        if pattern is not None:
            if allowed is not None and not allowed():
                return None
            try:
                ranges = pattern.GetSelection()
            except Exception:
                ranges = []
            for text_range in ranges or []:
                if allowed is not None and not allowed():
                    return None
                try:
                    text = normalize_selection(
                        text_range.GetText(max_length + 1), max_length
                    )
                except ValueError:
                    raise
                except Exception:
                    continue
                if not text:
                    continue
                anchor_x, anchor_y = click_x, click_y
                selection_bounds: tuple[int, int, int, int] | None = None
                try:
                    rectangles = text_range.GetBoundingRectangles()
                    valid = [
                        rect
                        for rect in rectangles
                        if getattr(rect, "right", 0) > getattr(rect, "left", 0)
                        and getattr(rect, "bottom", 0) > getattr(rect, "top", 0)
                    ]
                    if valid:
                        last = valid[-1]
                        anchor_x = int(last.right)
                        anchor_y = int((last.top + last.bottom) / 2)
                        selection_bounds = (
                            int(min(rect.left for rect in valid)),
                            int(min(rect.top for rect in valid)),
                            int(max(rect.right for rect in valid)),
                            int(max(rect.bottom for rect in valid)),
                        )
                except Exception:
                    pass
                return SelectionCandidate(
                    text,
                    anchor_x,
                    anchor_y,
                    *(selection_bounds or (None, None, None, None)),
                )

        try:
            current = current.GetParentControl()
        except Exception:
            break
    return None


def read_selection_at(
    x: int,
    y: int,
    *,
    own_process_id: int | None = None,
    max_length: int = MAX_SELECTION_LENGTH,
    allowed=None,
) -> SelectionCandidate | None:
    if sys.platform != "win32" or (allowed is not None and not allowed()):
        return None
    from uiautomation import uiautomation as auto

    with auto.UIAutomationInitializerInThread():
        if allowed is not None and not allowed():
            return None
        control = auto.ControlFromPoint(int(x), int(y))
        if control is not None:
            candidate = selected_text_from_control(
                control,
                int(x),
                int(y),
                own_process_id=own_process_id,
                max_length=max_length,
                allowed=allowed,
            )
            if candidate is not None:
                return candidate

        if allowed is not None and not allowed():
            return None
        focused = auto.GetFocusedControl()
        if focused is None:
            return None
        return selected_text_from_control(
            focused,
            int(x),
            int(y),
            own_process_id=own_process_id,
            max_length=max_length,
            allowed=allowed,
        )


def read_current_selection(
    x: int,
    y: int,
    *,
    own_process_id: int | None = None,
    max_length: int = MAX_SELECTION_LENGTH,
    allowed=None,
) -> SelectionCandidate | None:
    if sys.platform != "win32" or (allowed is not None and not allowed()):
        return None
    from uiautomation import uiautomation as auto

    with auto.UIAutomationInitializerInThread():
        if allowed is not None and not allowed():
            return None
        focused = auto.GetFocusedControl()
        if focused is not None:
            try:
                sensitive = focused.IsPassword or focused.ProcessId == own_process_id
            except Exception:
                sensitive = False
            if sensitive:
                raise ValueError("当前为敏感输入区域或浮译自身窗口，已阻止模拟复制。")
            candidate = selected_text_from_control(
                focused,
                int(x),
                int(y),
                own_process_id=own_process_id,
                max_length=max_length,
                allowed=allowed,
            )
            if candidate is not None:
                return candidate

        if allowed is not None and not allowed():
            return None
        control = auto.ControlFromPoint(int(x), int(y))
        if control is None:
            return None
        return selected_text_from_control(
            control,
            int(x),
            int(y),
            own_process_id=own_process_id,
            max_length=max_length,
            allowed=allowed,
        )


def ensure_uiautomation_available() -> None:
    if sys.platform != "win32":
        return
    try:
        from uiautomation import uiautomation as auto

        getattr(auto, "ControlFromPoint")
    except Exception as exc:
        raise RuntimeError("无法加载 Windows 右键取词组件。") from exc


class RightClickMonitor(QObject):
    selection_found = Signal(str, int, int)
    dismiss_requested = Signal()
    selection_rejected = Signal(str)
    _found = Signal(int, str, int, int)
    _rejected = Signal(int, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mouse_listener: mouse.Listener | None = None
        self._keyboard_listener: keyboard.Listener | None = None
        self._bounds: tuple[int, int, int, int] | None = None
        self._bounds_lock = threading.Lock()
        self._candidate: SelectionCandidate | None = None
        self._candidate_time = 0.0
        self._candidate_lock = threading.Lock()
        self._probe_generation = 0
        self._pressed_modifiers: set[keyboard.Key] = set()
        self._last_probe = 0.0
        self._session = 0
        self._active = False
        self.allowed = lambda: True
        self._found.connect(self._publish_found)
        self._rejected.connect(self._publish_rejected)

    def _current(self, session: int) -> bool:
        return self._active and session == self._session

    @Slot(int, str, int, int)
    def _publish_found(self, session: int, text: str, x: int, y: int) -> None:
        if self._current(session) and self.allowed():
            self.selection_found.emit(text, x, y)

    @Slot(int, str)
    def _publish_rejected(self, session: int, message: str) -> None:
        if self._current(session) and self.allowed():
            self.selection_rejected.emit(message)

    def start(self) -> None:
        if self._mouse_listener is not None:
            return
        ensure_uiautomation_available()
        self._session += 1
        self._active = True
        session = self._session
        mouse_listener = mouse.Listener(on_click=lambda *args: self._on_click(*args) if self._current(session) else None)
        keyboard_listener = keyboard.Listener(
            on_press=lambda key: self._on_key_press(key) if self._current(session) else None,
            on_release=lambda key: self._on_key_release(key) if self._current(session) else None,
        )
        try:
            mouse_listener.start()
            keyboard_listener.start()
        except Exception:
            self._active = False
            mouse_listener.stop()
            keyboard_listener.stop()
            raise
        self._mouse_listener = mouse_listener
        self._keyboard_listener = keyboard_listener

    def stop(self) -> None:
        self._active = False
        self._session += 1
        self._pressed_modifiers.clear()
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()
            self._keyboard_listener = None
        self.clear_action_bounds()
        self._clear_candidate()

    def set_action_bounds(self, left: int, top: int, right: int, bottom: int) -> None:
        with self._bounds_lock:
            self._bounds = (left, top, right, bottom)

    def clear_action_bounds(self) -> None:
        with self._bounds_lock:
            self._bounds = None

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool, injected: bool = False) -> None:
        session = self._session
        if not self._current(session) or not self.allowed():
            return
        mouse_x, mouse_y = int(x), int(y)
        if not pressed:
            if button == mouse.Button.left and not self._inside_action(
                mouse_x, mouse_y
            ):
                self._schedule_selection_probe(mouse_x, mouse_y)
            return
        if self._inside_action(mouse_x, mouse_y):
            return
        self.dismiss_requested.emit()
        if button == mouse.Button.left:
            self._clear_candidate()
            return
        if button != mouse.Button.right:
            self._clear_candidate()
            return

        candidate = self._candidate_for_click(mouse_x, mouse_y)
        if candidate is not None:
            self._found.emit(session, candidate.text, mouse_x, mouse_y)
            return

        now = time.monotonic()
        if now - self._last_probe < 0.35:
            return
        self._last_probe = now
        if not self._current(session) or not self.allowed():
            return
        try:
            candidate = read_selection_at(
                mouse_x, mouse_y, own_process_id=os.getpid(),
                allowed=lambda: self._current(session) and self.allowed(),
            )
        except ValueError as exc:
            self._rejected.emit(session, str(exc))
            return
        except Exception:
            return
        if candidate is not None and self._current(session) and self.allowed():
            self._store_candidate(candidate)
            self._found.emit(session, candidate.text, mouse_x, mouse_y)

    def _on_key_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if not self._current(self._session):
            return
        if key in _CONTROL_KEYS or key in _SHIFT_KEYS:
            self._pressed_modifiers.add(key)
        if key == keyboard.Key.esc:
            self._clear_candidate()
            self.dismiss_requested.emit()

    def _on_key_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if not self._current(self._session):
            return
        control_pressed = bool(self._pressed_modifiers & _CONTROL_KEYS)
        shift_pressed = bool(self._pressed_modifiers & _SHIFT_KEYS)
        is_select_all = (
            isinstance(key, keyboard.KeyCode)
            and (key.char or "").lower() == "a"
            and control_pressed
        )
        is_keyboard_selection = key in _SELECTION_NAVIGATION_KEYS and shift_pressed
        if is_select_all or is_keyboard_selection:
            x, y = mouse.Controller().position
            self._schedule_selection_probe(int(x), int(y))
        if key in _CONTROL_KEYS or key in _SHIFT_KEYS:
            self._pressed_modifiers.discard(key)

    def _schedule_selection_probe(self, x: int, y: int) -> None:
        session = self._session
        if not self._current(session) or not self.allowed():
            return
        with self._candidate_lock:
            self._probe_generation += 1
            generation = self._probe_generation

        def probe() -> None:
            time.sleep(0.08)
            if not self._current(session) or not self.allowed():
                return
            with self._candidate_lock:
                if generation != self._probe_generation:
                    return
            try:
                candidate = read_selection_at(x, y, own_process_id=os.getpid(),
                                               allowed=lambda: self._current(session) and self.allowed())
            except Exception:
                candidate = None
            with self._candidate_lock:
                if generation != self._probe_generation or session != self._session or not self._active:
                    return
                self._candidate = candidate
                self._candidate_time = time.monotonic() if candidate else 0.0

        threading.Thread(target=probe, daemon=True).start()

    def _store_candidate(self, candidate: SelectionCandidate) -> None:
        with self._candidate_lock:
            self._probe_generation += 1
            self._candidate = candidate
            self._candidate_time = time.monotonic()

    def _clear_candidate(self) -> None:
        with self._candidate_lock:
            self._probe_generation += 1
            self._candidate = None
            self._candidate_time = 0.0

    def _candidate_for_click(self, x: int, y: int) -> SelectionCandidate | None:
        with self._candidate_lock:
            candidate = self._candidate
            candidate_time = self._candidate_time
        if candidate is None or time.monotonic() - candidate_time > 30.0:
            return None
        if not candidate.contains(x, y):
            return None
        return candidate

    def _inside_action(self, x: int, y: int) -> bool:
        with self._bounds_lock:
            bounds = self._bounds
        if bounds is None:
            return False
        left, top, right, bottom = bounds
        return left <= x <= right and top <= y <= bottom
