from pathlib import Path
import sys
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QWidget


def asset_path(name: str) -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return root / "photo" / name


def app_icon() -> QIcon:
    return QIcon(str(asset_path("app.ico")))


class Background(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("appBackground")
        self.picture = QPixmap(str(asset_path("backg.png")))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f6f8fb"))
        if not self.picture.isNull():
            band = QRect(0, 0, self.width(), 116)
            painter.setClipRect(band)
            painter.setOpacity(0.045)
            scaled = self.picture.scaled(band.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                         Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((band.width()-scaled.width())//2, (band.height()-scaled.height())//2, scaled)
