"""Manual native integration probe. Never sends captured content to an API."""
import json
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from linguafloat.app import AppController


class ProbeController(AppController):
    def _translate(self, text):
        self.record("selected", text)

    def _submit_translation(self, source_text, *args, **kwargs):
        self.record("submitted", source_text)

    def record(self, stage, text):
        expected = ["Hello from LinguaFloat.", "This is a two-line selection test."]
        print(json.dumps({"stage": stage, "sample_matches": all(part in text for part in expected),
                          "length": len(text)}, ensure_ascii=True), flush=True)
        self.window.set_activity("本地取词测试已完成；没有发送 API 请求。")


app = QApplication([])
app.setQuitOnLastWindowClosed(False)
controller = ProbeController(app)
controller.settings.game_mode = "off"
controller.settings.trigger_mode = "both"
controller.settings.hotkey = "Ctrl+Alt+T"
controller.game_monitor.configure("off", [])
controller._reconcile_triggers()
controller.capture.diagnostic.connect(lambda message: print(message.encode("ascii", errors="backslashreplace").decode(), flush=True))
controller.start()
print("PROBE_READY", flush=True)
QTimer.singleShot(180000, controller.quit)
app.exec()
