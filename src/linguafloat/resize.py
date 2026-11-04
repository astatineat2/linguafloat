from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QWidget


CURSORS = {"left": Qt.CursorShape.SizeHorCursor, "right": Qt.CursorShape.SizeHorCursor,
           "top": Qt.CursorShape.SizeVerCursor, "bottom": Qt.CursorShape.SizeVerCursor,
           "topleft": Qt.CursorShape.SizeFDiagCursor, "bottomright": Qt.CursorShape.SizeFDiagCursor,
           "topright": Qt.CursorShape.SizeBDiagCursor, "bottomleft": Qt.CursorShape.SizeBDiagCursor}


def resized_rect(rect, edge, delta, bounds, minimum, maximum=None):
    result = QRect(rect)
    if "left" in edge:
        result.setLeft(max(bounds.left(), min(rect.left() + delta.x(), rect.right() - minimum.width() + 1)))
    if "right" in edge:
        result.setRight(min(bounds.right(), max(rect.right() + delta.x(), rect.left() + minimum.width() - 1)))
    if "top" in edge:
        result.setTop(max(bounds.top(), min(rect.top() + delta.y(), rect.bottom() - minimum.height() + 1)))
    if "bottom" in edge:
        result.setBottom(min(bounds.bottom(), max(rect.bottom() + delta.y(), rect.top() + minimum.height() - 1)))
    if maximum:
        if result.width() > maximum.width():
            result.setLeft(result.right() - maximum.width() + 1) if "left" in edge else result.setWidth(maximum.width())
        if result.height() > maximum.height():
            result.setTop(result.bottom() - maximum.height() + 1) if "top" in edge else result.setHeight(maximum.height())
    return result


class ResizeEdge(QWidget):
    def __init__(self, parent, edge):
        super().__init__(parent)
        self.edge = edge
        self.origin = None
        self.setCursor(CURSORS[edge])
        self.setToolTip("调整窗口大小")
        self.setStyleSheet("background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.globalPosition().toPoint()
            self.initial = self.parentWidget().geometry()
            self.bounds = self.parentWidget().screen().availableGeometry()

    def mouseMoveEvent(self, event):
        if self.origin is None:
            return
        parent = self.parentWidget()
        rect = resized_rect(self.initial, self.edge, event.globalPosition().toPoint() - self.origin,
                            self.bounds, parent.minimumSize(), parent.maximumSize())
        source = getattr(parent, "source_region", QRect())
        if not rect.intersects(source):
            parent.setGeometry(rect)
            parent.user_moved = True
            if hasattr(parent, "user_positioned"):
                parent.user_positioned = True

    def mouseReleaseEvent(self, event):
        self.origin = None


def create_resize_edges(widget):
    widget.resize_edges = {edge: ResizeEdge(widget, edge) for edge in CURSORS}


def layout_resize_edges(widget):
    if not hasattr(widget, "resize_edges"):
        return
    w, h, p, c = widget.width(), widget.height(), 6, 12
    rects = {"left": QRect(0, c, p, h-2*c), "right": QRect(w-p, c, p, h-2*c),
             "top": QRect(c, 0, w-2*c, p), "bottom": QRect(c, h-p, w-2*c, p),
             "topleft": QRect(0, 0, c, c), "topright": QRect(w-c, 0, c, c),
             "bottomleft": QRect(0, h-c, c, c), "bottomright": QRect(w-c, h-c, c, c)}
    for edge, child in widget.resize_edges.items():
        child.setGeometry(rects[edge])
        child.raise_()
        child.show()
