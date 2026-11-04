from pathlib import Path
from PIL import Image, ImageOps
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
import qtawesome as qta

root = Path(__file__).resolve().parents[1] / "photo"
if not (root / "1.png").exists():
    image = Image.open(root / "1.jpg").convert("RGBA")
    ImageOps.fit(image, (512, 512), method=Image.Resampling.LANCZOS).save(root / "1.png")
if not (root / "backg.png").exists():
    Image.open(root / "backg.webp").convert("RGB").save(root / "backg.png")
Image.open(root / "1.png").save(root / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

app = QApplication.instance() or QApplication([])
for name, icon, color in (
    ("chevron-down", "mdi6.chevron-down", "#66717d"),
    ("chevron-up", "mdi6.chevron-up", "#66717d"),
    ("check", "mdi6.check", "#ffffff"),
):
    qta.icon(icon, color=color).pixmap(32, 32).save(str(root / f"{name}.png"))
