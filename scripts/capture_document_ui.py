"""Render actual Qt widgets at supported sizes without changing user settings."""
import os
import sys
import json
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--native", action="store_true")
parser.add_argument("--folder", default="verification-speed/screenshots")
args = parser.parse_args()
os.environ["QT_QPA_PLATFORM"] = "windows" if args.native else "offscreen"
project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "src"))
from PySide6.QtWidgets import QApplication, QToolTip
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from linguafloat.config import AppSettings
from linguafloat.ui import MainWindow, apply_ui_font

app = QApplication([])
apply_ui_font(app, 14)
window = MainWindow(AppSettings(), "missing")
window.documents.add_files([str(project / "verification" / "samples" / name)
                           for name in ("sample.pdf", "sample.docx", "sample.pptx")])
scale = os.environ.get("QT_SCALE_FACTOR", "1")
folder = project / args.folder / scale
folder.mkdir(parents=True, exist_ok=True)
for width, height in ((860, 620), (1060, 840)):
    window.resize(width, height)
    window.show()
    for index, name in enumerate(("functions", "configuration", "documents")):
        window.pages.setCurrentIndex(index)
        app.processEvents()
        window.grab().save(str(folder / f"{name}-{width}x{height}.png"))
        scroll = (window.function_scroll, window.config_scroll, window.documents.scroll)[index]
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        app.processEvents()
        window.grab().save(str(folder / f"{name}-{width}x{height}-bottom.png"))
        scroll.verticalScrollBar().setValue(0)
        if index == 1:
            window.advanced_toggle.setChecked(False)
            scroll.ensureWidgetVisible(window.document_concurrency_spin)
            app.processEvents()
            window.grab().save(str(folder / f"configuration-{width}x{height}-document-settings.png"))
            scroll.verticalScrollBar().setValue(0)
    window.pages.setCurrentIndex(2)
    window.documents.scroll.ensureWidgetVisible(window.documents.relayout_only, 0, 80)
    app.processEvents()
    window.grab().save(str(folder / f"documents-{width}x{height}-relayout.png"))
    window.documents.scroll.verticalScrollBar().setValue(0)
    identities = list(window.documents.rows)
    window.documents.update_row(identities[0], "已完成", 100)
    window.documents.rows[identities[0]]["item"].setText(4, "用时 1 分 20 秒")
    window.documents.update_row(identities[1], "翻译中", 45)
    for count in range(4):
        window.documents.update_estimate(identities[1], dict(stage="翻译中", stage_key="sample",
            work_done=count * 100, work_total=1000, phase_elapsed=count * 20))
    window.documents.update_row(identities[2], "待处理", 0)
    window.documents.status.setText("sample.docx · Word 段落 9/20")
    window.documents.batch_status.setText("本批 3 个 · 完成 1 · 失败 0 · 取消 0")
    window.documents.progress.setValue(45)
    window.documents.update_billing(identities[1], {"input_tokens": 18200, "output_tokens": 12400,
        "reported_requests": 12, "pricing": {"input": 2, "output": 8, "multiplier": .1}})
    app.processEvents()
    window.grab().save(str(folder / f"documents-{width}x{height}-progress.png"))
    window.documents.files.horizontalScrollBar().setValue(window.documents.files.horizontalScrollBar().maximum())
    app.processEvents()
    window.grab().save(str(folder / f"billing-{width}x{height}.png"))
    QToolTip.showText(window.mapToGlobal(QPoint(70, 240)), window.documents.rows[identities[1]]["bar"].toolTip(), window)
    QTest.qWait(120)
    for tooltip in app.topLevelWidgets():
        if tooltip.metaObject().className() == "QTipLabel" and tooltip.isVisible():
            tooltip.grab().save(str(folder / f"usage-tooltip-{width}x{height}.png"))
    app.primaryScreen().grabWindow(0, window.x(), window.y(), window.width(), window.height()).save(str(folder / f"billing-tooltip-{width}x{height}.png"))
    QToolTip.hideText()
    window.documents.files.horizontalScrollBar().setValue(0)
    for identity in identities:
        window.documents.update_row(identity, "待处理", 0)
    window.documents.status.setText("等待添加文件")
    window.documents.batch_status.setText("共 3 个文件")
    window.documents.progress.setValue(0)
    window.documents.set_history([
        ("example1", "C:/Examples/Research paper.pdf", "completed", 1780000000, json.dumps({
            "file_type": "PDF", "file_size": 5700000, "started_at": 1780000000,
            "elapsed_seconds": 168.5, "input_tokens": 18200, "output_tokens": 12400,
            "total_tokens": 30600, "reported_requests": 12, "unreported_requests": 0,
            "pricing": {"input": 2, "output": 8, "multiplier": .1}})),
        ("example2", "C:/Examples/Notes.docx", "failed", 1779999900, json.dumps({
            "file_type": "DOCX", "file_size": 48500, "elapsed_seconds": 181.2,
            "input_tokens": 800, "output_tokens": 400, "total_tokens": 1200,
            "reported_requests": 1, "unreported_requests": 1})),
    ])
    window.documents.file_tabs.setCurrentIndex(1)
    app.processEvents()
    window.grab().save(str(folder / f"history-{width}x{height}.png"))
    window.documents.history.horizontalScrollBar().setValue(window.documents.history.horizontalScrollBar().maximum())
    app.processEvents()
    window.grab().save(str(folder / f"history-billing-{width}x{height}.png"))
    window.documents.history.horizontalScrollBar().setValue(0)
    window.documents.file_tabs.setCurrentIndex(0)
window.allow_close()
window.close()
print(folder)
