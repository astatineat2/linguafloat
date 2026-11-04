"""Render model menus using demonstration data, without API calls or hooks."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from linguafloat.config import AppSettings, ProviderProfile
from linguafloat.ui import MainWindow, apply_ui_font


app = QApplication([])
apply_ui_font(app, 14)
folder = Path(__file__).resolve().parents[1] / "artifacts" / "ui"
folder.mkdir(parents=True, exist_ok=True)
settings = AppSettings(active_provider="demo", providers=[ProviderProfile(
    "demo", "示例供应商", "https://example.com/v1", "translation-pro", env_key="",
    models=["translation-pro", "translation-fast", "multilingual-large", "custom/model"],
    favorite_models=["translation-pro", "translation-fast"],
)])
window = MainWindow(settings, "missing")
for width, height in [(1060, 840), (860, 620)]:
    window.resize(width, height)
    window.show()
    app.processEvents()
    picker = window.active_model_combo
    picker.showPopup()
    app.processEvents()
    picker.menu.grab().save(str(folder / f"model-menu-{width}.png"))
    screenshot = window.grab()
    from PySide6.QtGui import QPainter
    painter = QPainter(screenshot)
    painter.drawPixmap(picker.menu.pos() - window.mapToGlobal(window.rect().topLeft()), picker.menu.grab())
    painter.end()
    screenshot.save(str(folder / f"model-selector-{width}.png"))
    picker.menu.search.setText("custom-new-model")
    app.processEvents()
    picker.menu.grab().save(str(folder / "model-menu-custom.png"))
    picker.hidePopup()
window.allow_close()
window.close()
print("Model menu screenshots saved.")
