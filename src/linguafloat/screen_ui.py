from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizeGrip, QTextBrowser, QTextEdit, QToolButton, QVBoxLayout, QWidget
import qtawesome as qta

from linguafloat.theme import FLOAT_STYLESHEET, set_state
from linguafloat.resize import CURSORS, ResizeEdge, resized_rect, create_resize_edges, layout_resize_edges


def panel_geometry(region: QRect, available: QRect) -> QRect | None:
    """Place a readable panel outside the source, even near a screen edge."""
    gap = 12
    slots = (
        QRect(region.right() + gap + 1, available.top(), available.right() - region.right() - gap, available.height()),
        QRect(available.left(), available.top(), region.left() - available.left() - gap, available.height()),
        QRect(available.left(), region.bottom() + gap + 1, available.width(), available.bottom() - region.bottom() - gap),
        QRect(available.left(), available.top(), available.width(), region.top() - available.top() - gap),
    )
    for slot in slots:
        if slot.width() < 280 or slot.height() < 180:
            continue
        width, height = min(420, slot.width()), min(300, slot.height())
        x = max(slot.left(), min(region.left(), slot.right() - width + 1))
        y = max(slot.top(), min(region.top(), slot.bottom() - height + 1))
        return QRect(x, y, width, height)
    return None


def constrain_panel(position: QPoint, size: QSize, area: QRect, source: QRect) -> QPoint:
    x = min(max(position.x(), area.left()), area.right() - size.width() + 1)
    y = min(max(position.y(), area.top()), area.bottom() - size.height() + 1)
    desired = QRect(QPoint(x, y), size)
    if not desired.intersects(source):
        return desired.topLeft()
    candidates = [
        QRect(QPoint(source.right() + 5, y), size),
        QRect(QPoint(source.left() - size.width() - 4, y), size),
        QRect(QPoint(x, source.bottom() + 5), size),
        QRect(QPoint(x, source.top() - size.height() - 4), size),
    ]
    valid = [rect for rect in candidates if area.contains(rect) and not rect.intersects(source)]
    if valid:
        return min(valid, key=lambda rect: (rect.topLeft() - position).manhattanLength()).topLeft()
    return desired.topLeft()


class DragHandle(QLabel):
    started = Signal(object)
    moved = Signal(object)
    finished = Signal()

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.dragging = False
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("拖动译文窗口")
        self.setAccessibleName("拖动译文窗口")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.started.emit(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging:
            self.moved.emit(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.dragging:
            self.moved.emit(event.globalPosition().toPoint())
            self.dragging = False
            self.finished.emit()


class RegionSelector(QWidget):
    selected = Signal(object, object)
    cancelled = Signal()

    def __init__(self, screen, snapshot: QPixmap, move_region: QRect | None = None) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.target_screen = screen
        self.snapshot = snapshot
        self.origin: QPoint | None = None
        self.move_mode = move_region is not None
        self.selection = move_region.translated(-screen.geometry().topLeft()) if self.move_mode else QRect()
        self.initial_position = self.selection.topLeft()
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.SizeAllCursor if self.move_mode else Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setWindowTitle("移动识别区域" if self.move_mode else "选择翻译区域")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.snapshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 95))
        if not self.selection.isEmpty():
            painter.save()
            painter.setClipRect(self.selection)
            painter.drawPixmap(self.rect(), self.snapshot)
            painter.restore()
            painter.setPen(QPen(QColor("#26c6a0"), 2))
            painter.drawRect(self.selection.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
        elif event.button() == Qt.MouseButton.LeftButton:
            if self.move_mode and not self.selection.contains(event.position().toPoint()):
                return
            self.activateWindow()
            self.origin = event.position().toPoint()
            self.initial_position = self.selection.topLeft()
            if not self.move_mode:
                self.selection = QRect(self.origin, QSize())

    def _update_selection(self, position: QPoint) -> None:
        if self.move_mode:
            desired = self.initial_position + position - self.origin
            self.selection.moveTopLeft(QPoint(
                max(0, min(desired.x(), self.width() - self.selection.width())),
                max(0, min(desired.y(), self.height() - self.selection.height())),
            ))
        else:
            self.selection = QRect(self.origin, position).normalized().intersected(self.rect())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.origin is not None:
            self._update_selection(event.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.origin is None:
            return
        self._update_selection(event.position().toPoint())
        self.origin = None
        if self.selection.width() >= 32 and self.selection.height() >= 24:
            self.selected.emit(self.target_screen, self.selection.translated(self.geometry().topLeft()))
        else:
            self.selection = QRect()
            self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)


class RegionOutline(QWidget):
    PADDING = 6
    moved = Signal(object)
    move_started = Signal()
    move_finished = Signal(object)

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("识别区域边框 - LinguaFloat")
        self.setMouseTracking(True)
        self._drag_origin: QPoint | None = None
        self._drag_region = QRect()
        self._resize_corner = ""
        self._minimum_size = QSize(48, 32)
        self._drag_bounds = QRect()

    def source_region(self) -> QRect:
        p = self.PADDING
        return self.geometry().adjusted(p, p, -p, -p)

    def show_region(self, region: QRect) -> None:
        p = self.PADDING
        self.setGeometry(region.adjusted(-p, -p, p, p))
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.show()

    def resizeEvent(self, event) -> None:
        p = self.PADDING
        self.setMask(QRegion(self.rect()).subtracted(QRegion(self.rect().adjusted(p, p, -p, -p))))
        super().resizeEvent(event)

    def _corner_at(self, point: QPoint) -> str:
        if self.move_handle_rect().contains(point):
            return ""
        margin = 14
        x, y = point.x(), point.y()
        left, top = 0, 0
        right, bottom = self.width() - 1, self.height() - 1
        horizontal = "left" if x <= left + margin else "right" if x >= right - margin else ""
        vertical = "top" if y <= top + margin else "bottom" if y >= bottom - margin else ""
        return vertical + horizontal

    def move_handle_rect(self) -> QRect:
        return QRect(self.width() // 2 - 18, 0, 36, self.PADDING)

    @staticmethod
    def _cursor_for_corner(corner: str) -> Qt.CursorShape:
        return CURSORS.get(corner, Qt.CursorShape.SizeAllCursor)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#16845b"), 1))
        painter.drawRect(self.rect().adjusted(3, 3, -3, -3))
        painter.fillRect(self.move_handle_rect(), QColor("#16845b"))
        for x in (0, self.width() - 6):
            for y in (0, self.height() - 6):
                painter.fillRect(QRect(x, y, 6, 6), QColor("#16845b"))
        for rect in (QRect(0, self.height() // 2 - 8, 3, 16),
                     QRect(self.width() - 3, self.height() // 2 - 8, 3, 16),
                     QRect(self.width() // 2 - 8, self.height() - 3, 16, 3)):
            painter.fillRect(rect, QColor("#16845b"))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_origin = event.globalPosition().toPoint()
        self._drag_region = self.source_region()
        screen = QGuiApplication.screenAt(self._drag_region.center()) or self.screen()
        self._drag_bounds = QRect(screen.geometry())
        self._resize_corner = self._corner_at(event.position().toPoint())
        self.setCursor(self._cursor_for_corner(self._resize_corner))
        self.move_started.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_origin is not None:
            region = self.source_region()
            self._drag_origin = None
            self._resize_corner = ""
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            self.move_finished.emit(region)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is None:
            self.setCursor(self._cursor_for_corner(self._corner_at(event.position().toPoint())))
            return
        self._resize_or_move(event)

    def _resize_or_move(self, event: QMouseEvent) -> None:
        # Keep the actual geometry update in one path for corner resize and pan.
        delta = event.globalPosition().toPoint() - self._drag_origin
        bounds = self._drag_bounds
        region = QRect(self._drag_region)
        if self._resize_corner:
            region = resized_rect(region, self._resize_corner, delta, bounds, self._minimum_size)
        else:
            region.translate(delta)
            region.moveLeft(max(bounds.left(), min(region.left(), bounds.right() - region.width() + 1)))
            region.moveTop(max(bounds.top(), min(region.top(), bounds.bottom() - region.height() + 1)))
        if region.width() >= self._minimum_size.width() and region.height() >= self._minimum_size.height():
            p = self.PADDING
            self.setGeometry(region.adjusted(-p, -p, p, p))
            self.moved.emit(region)


def tool_button(icon: str, label: str, callback) -> QToolButton:
    button = QToolButton()
    button.setIcon(qta.icon(icon, color="#303841"))
    button.setToolTip(label)
    button.setAccessibleName(label)
    button.setFixedSize(32, 32)
    button.clicked.connect(callback)
    return button


class ScreenTranslationPanel(QWidget):
    pause_requested = Signal()
    select_requested = Signal()
    stop_requested = Signal()
    move_region_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("区域翻译 - LinguaFloat")
        self.setObjectName("screenPanel")
        create_resize_edges(self)
        self.source_region = QRect()
        self.user_moved = False
        self.drag_offset = QPoint()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(FLOAT_STYLESHEET)
        self._paused = False
        self._status_message = "等待文字"
        self._status_error = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self.title = DragHandle("区域翻译")
        self.title.setObjectName("floatingTitle")
        self.title.started.connect(self._begin_drag)
        self.title.moved.connect(self._drag)
        top.addWidget(self.title, 1)
        self.pause_button = tool_button("mdi6.pause", "暂停区域翻译", self.pause_requested.emit)
        self.select_button = tool_button("mdi6.crop-free", "重新选择区域", self.select_requested.emit)
        self.move_button = tool_button("mdi6.cursor-move", "移动识别区域", self.move_region_requested.emit)
        self.copy_button = tool_button("mdi6.content-copy", "复制译文", self._copy)
        self.stop_button = tool_button("mdi6.close", "停止区域翻译", self.stop_requested.emit)
        for button in (self.pause_button, self.stop_button):
            top.addWidget(button)
        layout.addLayout(top)
        tools = QHBoxLayout()
        self.status = QLabel("等待文字")
        self.status.setStyleSheet("font-size: 13px;")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        tools.addWidget(self.status, 1)
        for button in (self.move_button, self.select_button, self.copy_button):
            tools.addWidget(button)
        layout.addLayout(tools)
        self.translation = QTextBrowser()
        self.translation.setObjectName("translation")
        self.translation.setAccessibleName("区域翻译结果")
        self.translation.setOpenExternalLinks(False)
        self.setMinimumSize(280, 180)
        self.setMaximumSize(800, 650)
        self.resize(420, 300)
        self.translation.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.translation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.translation, 1)
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        self.size_grip = ResizeEdge(self, "bottomright")
        self.size_grip.setFixedSize(22, 22)
        self.size_grip.setToolTip("调整译文窗口大小")
        self.size_grip.setAccessibleName("调整区域译文窗口大小")
        grip_row.addWidget(self.size_grip)
        layout.addLayout(grip_row)
        self.copy_button.setEnabled(False)
        layout_resize_edges(self)

    def resizeEvent(self, event) -> None:
        layout_resize_edges(self)
        if self.isVisible():
            self.user_moved = True
        super().resizeEvent(event)

    def _begin_drag(self, point: QPoint) -> None:
        self.drag_offset = point - self.pos()

    def _drag(self, point: QPoint) -> None:
        screen = QGuiApplication.screenAt(point) or self.screen()
        position = constrain_panel(point - self.drag_offset, self.size(), screen.availableGeometry(), self.source_region)
        candidate = QRect(position, self.size())
        if not candidate.intersects(self.source_region):
            self.move(position)
            self.user_moved = True

    def set_status(self, message: str, *, error: bool = False) -> None:
        self._status_message = message
        self._status_error = error
        self._refresh_status()

    def _refresh_status(self) -> None:
        self.status.setText("已暂停" if self._paused and not self._status_error else self._status_message)
        set_state(self.status, "error" if self._status_error else "paused" if self._paused else "idle")

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._refresh_status()
        label = "继续区域翻译" if paused else "暂停区域翻译"
        self.pause_button.setIcon(qta.icon("mdi6.play" if paused else "mdi6.pause", color="#303841"))
        self.pause_button.setToolTip(label)
        self.pause_button.setAccessibleName(label)
        self.pause_button.setText("继续" if paused else "暂停")
        self.pause_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.pause_button.setFixedWidth(76)

    def show_translation(self, source: str, translated: str) -> None:
        self.translation.setPlainText(translated)
        self.translation.setToolTip(source)
        self.copy_button.setEnabled(bool(translated))
        self.set_status("翻译完成")

    def clear_translation(self) -> None:
        self.translation.clear()
        self.translation.setToolTip("")
        self.copy_button.setEnabled(False)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.translation.toPlainText())

    def closeEvent(self, event) -> None:
        self.stop_requested.emit()
        event.accept()
