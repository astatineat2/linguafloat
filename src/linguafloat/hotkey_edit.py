"""Explicit, cancellable shortcut recording; never accepts pasted key names."""
from PySide6.QtCore import Qt, QEvent, Signal
from PySide6.QtWidgets import QLineEdit
from linguafloat.hotkeys import parse_hotkey, HotkeyError


class HotkeyEdit(QLineEdit):
    recording_changed = Signal(bool)

    def __init__(self, value="", parent=None):
        self.recording = False
        super().__init__(value, parent)
        self.setReadOnly(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.recording = False
        self._previous = value
        self._candidate = None
        self.setToolTip("点击后直接按快捷键；Esc 取消。录入后点击底部“保存功能设置”统一保存。")

    def begin_recording(self):
        if self.recording:
            return
        self._previous = self.text()
        self._candidate = None
        self.recording = True
        self.setProperty("recording", True)
        self.setPlaceholderText("正在监听快捷键… Esc 取消")
        self.clear()
        self.style().unpolish(self); self.style().polish(self)
        self.recording_changed.emit(True)

    def end_recording(self, commit=False):
        if not self.recording:
            return
        self.setText(self._candidate if commit and self._candidate else self._previous)
        self.recording = False
        self.setProperty("recording", False)
        self.setPlaceholderText("")
        self.style().unpolish(self); self.style().polish(self)
        self.recording_changed.emit(False)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin_recording()

    def focusOutEvent(self, event):
        self.end_recording()
        super().focusOutEvent(event)

    def event(self, event):
        if self.recording and event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        if not self.recording:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Space, Qt.Key.Key_F2):
                self.begin_recording()
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.end_recording()
            return
        if event.isAutoRepeat():
            return
        names = {Qt.Key.Key_Left: "Left", Qt.Key.Key_Right: "Right", Qt.Key.Key_Up: "Up",
                 Qt.Key.Key_Down: "Down", Qt.Key.Key_Home: "Home", Qt.Key.Key_End: "End",
                 Qt.Key.Key_PageUp: "PageUp", Qt.Key.Key_PageDown: "PageDown",
                 Qt.Key.Key_Space: "Space", Qt.Key.Key_Insert: "Insert", Qt.Key.Key_Delete: "Delete"}
        # Windows VK remains reliable when an IME changes the event text.
        vk = event.nativeVirtualKey()
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5a:
            name = chr(vk)
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            name = chr(key)
        elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
            name = f"F{key - Qt.Key.Key_F1 + 1}"
        else:
            name = names.get(key)
        if not name:
            return
        mods = event.modifiers()
        parts = [n for m, n in ((Qt.KeyboardModifier.ControlModifier, "Ctrl"),
                 (Qt.KeyboardModifier.AltModifier, "Alt"), (Qt.KeyboardModifier.ShiftModifier, "Shift"),
                 (Qt.KeyboardModifier.MetaModifier, "Win")) if mods & m]
        try:
            self._candidate = parse_hotkey("+".join(parts + [name])).display
        except HotkeyError as exc:
            self.setPlaceholderText(str(exc))
            return
        self.setText(self._candidate)

    def keyReleaseEvent(self, event):
        # Restore global registrations only after all modifiers are released.
        if self.recording and self._candidate and not event.isAutoRepeat() and not event.modifiers():
            self.end_recording(commit=True)
        event.accept()
