"""Run the application's real OCR/API self-test on synthetic text and exit."""
import json
import time
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from linguafloat.app import AppController


app = QApplication([])
app.setQuitOnLastWindowClosed(False)
controller = AppController(app)
controller.start()
controller.window.self_test_input.setText("Hello from LinguaFloat.")
started = time.monotonic()
folder = Path(__file__).resolve().parents[1] / "artifacts"
folder.mkdir(exist_ok=True)


def start_test():
    controller._run_self_test()
    timer.start(250)


def poll():
    if controller._self_test_pending and time.monotonic() - started < 80:
        return
    timer.stop()
    report = controller.window.self_test_result.toPlainText()
    result = {"completed": not controller._self_test_pending, "report": report}
    (folder / "native-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    controller.window.grab().save(str(folder / "native-smoke.png"))
    print(json.dumps(result, ensure_ascii=True), flush=True)
    controller.quit()


timer = QTimer()
timer.timeout.connect(poll)
QTimer.singleShot(500, start_test)
app.exec()
