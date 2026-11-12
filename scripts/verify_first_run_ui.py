"""Isolated native Windows layout checks; sockets blocked, no real credentials."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import socket
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--scale", default="1")
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
os.environ.update(QT_QPA_PLATFORM="windows", QT_SCALE_FACTOR=args.scale,
                  APPDATA=str(args.output / "profile/roaming"),
                  LOCALAPPDATA=str(args.output / "profile/local"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def denied(*args, **kwargs):
    raise AssertionError("External network disabled during first-run verification")


socket.socket.connect = denied
socket.socket.connect_ex = denied
from PIL import Image, ImageStat
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from linguafloat import __version__
from linguafloat.config import AppSettings
from linguafloat.config_transfer_ui import FirstRunSetupDialog
from linguafloat.ui import MainWindow, apply_ui_font

app = QApplication([])
apply_ui_font(app, 14)
screen = app.primaryScreen()
report = dict(version=__version__, qt_platform=app.platformName(), process_scale=args.scale,
              system_dpi=ctypes.windll.user32.GetDpiForSystem(), real_requests=0,
              external_network_blocked=True, captures=[], checks=[])


def snapshot(widget, name):
    widget.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    widget.show()
    widget.raise_()
    widget.activateWindow()
    QTest.qWait(250)
    rect = widget.frameGeometry()
    capture = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
    target = args.output / (name + ".png")
    valid = False
    if not capture.isNull():
        capture.save(str(target))
        with Image.open(target) as image:
            valid = max(ImageStat.Stat(image.convert("RGB")).stddev) > 4
    report["captures"].append(dict(name=name, file=str(target), native_screen=True,
                                   nonblank=valid, logical_size=[widget.width(), widget.height()]))
    if not valid:
        widget.grab().save(str(args.output / (name + "-widget-render.png")))


window = MainWindow(AppSettings(update_auto_check=False, first_run_complete=True), "missing")
for width, height in ((860, 620), (1060, 840)):
    window.resize(width, height)
    window.move(screen.availableGeometry().topLeft() + QPoint(20, 24))
    snapshot(window, f"main-{width}")
    report["checks"].append(dict(name=f"main-{width}", actual=[window.width(), window.height()],
                                 passed=window.width() == width and window.height() == height))
window.hide()
d = FirstRunSetupDialog(AppSettings())
for name, size, advanced in (("first-run", (640, 610), False),
                            ("first-run-small", (480, 390), True)):
    d.resize(*size)
    d.move(screen.availableGeometry().topLeft() + QPoint(24, 30))
    d.advanced_toggle.setChecked(advanced)
    snapshot(d, name)
    assert all(d.rect().contains(QRect(control.mapTo(d, QPoint()), control.size()))
               for control in (d.save_button, d.skip_button))
    assert d.scroll.horizontalScrollBar().maximum() == 0
    report["checks"].append(dict(name=name, footer_visible=True,
        scroll_maximum=d.scroll.verticalScrollBar().maximum(), passed=True))
d.clear_sensitive()
d.done(QDialog.DialogCode.Rejected)
window.allow_close()
window.close()
report["status"] = "passed" if all(x["passed"] for x in report["checks"]) else "failed"
(args.output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True))
