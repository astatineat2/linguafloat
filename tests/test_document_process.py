"""Qt controller/worker boundary checks using synthetic fixtures and local HTTP."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zipfile import ZipFile

from test_credentials_ui import application, controller


def pump(application, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("Timed out waiting for document worker")


def test_qprocess_translates_pauses_resumes_and_cancels(controller, application, tmp_path):
    c, _, _ = controller
    source = tmp_path / "sample.docx"
    with ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Hello </w:t></w:r><w:r><w:t>document translation.</w:t></w:r></w:p></w:body></w:document>')
    received = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.set()
            release.wait(8)
            text = "<text><r id='0'>你好 </r><r id='1'>文档翻译。</r></text>"
            payload = json.dumps({"id": "r", "usage": {"input_tokens": 120, "output_tokens": 80, "total_tokens": 200}, "output": [{"type": "message", "id": "m", "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}]}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    c.settings.active_profile().base_url = f"http://127.0.0.1:{server.server_port}/v1"
    page = c.window.documents
    page.add_files([str(source)])
    page.output_dir.setText(str(tmp_path / "results"))
    try:
        c.documents.start()
        pump(application, received.is_set)
        c.documents.pause()
        assert c.documents.paused
        release.set()
        pump(application, lambda: "缓存" in page.status.text())
        for _ in range(20):
            application.processEvents()
            time.sleep(.02)
        assert not list((tmp_path / "results").glob("*/*.docx"))
        c.documents.pause()
        pump(application, lambda: c.documents.process is None)
        assert page.files.topLevelItemCount() == 0 and not page.rows
        assert page.file_tabs.currentIndex() == 1
        assert len(list((tmp_path / "results").glob("*/*.docx"))) == 2
        assert page.history.topLevelItem(0).text(4) == "120"
        assert page.history.topLevelItem(0).text(5) == "80"
        assert page.history.topLevelItem(0).text(3) != "未记录"

        # Cache must serve a second task without contacting the server.
        received.clear()
        page.add_files([str(source)])
        assert page.file_tabs.currentIndex() == 0
        page.relayout_only.setChecked(True)
        assert page.relayout_only.isChecked()
        c.documents.start()
        pump(application, lambda: c.documents.process is None)
        assert not received.is_set()
        assert c.documents.total_hits == 1
        assert page.history.topLevelItemCount() == 2
        assert page.history.topLevelItem(0).text(4) == "0"
        page.relayout_only.setChecked(False)

        # A different target creates a request, which can be cancelled safely.
        second = tmp_path / "second.docx"
        second.write_bytes(source.read_bytes())
        page.add_files([str(source), str(second)])
        page.target.setCurrentIndex(page.target.findData("ja"))
        release.clear()
        c.documents.start()
        pump(application, received.is_set)
        c.documents.cancel()
        release.set()
        pump(application, lambda: c.documents.process is None)
        assert page.files.topLevelItem(0).text(3) == "已取消"
        assert page.files.topLevelItem(1).text(3) == "已取消"
        assert len(list((tmp_path / "results").glob("*/*.docx"))) == 4
        assert page.start_button.isEnabled()

        c.documents.start()
        pump(application, lambda: not page.busy)
        assert not page.rows and page.files.topLevelItemCount() == 0
        assert "完成 2" in page.batch_status.text()
        assert len(list((tmp_path / "results").glob("*/*.docx"))) == 8
        assert page.history.topLevelItemCount() == 5

        # Failed jobs remain available for retry while successful ones move to history.
        broken = tmp_path / "broken.docx"
        broken.write_bytes(b"not a Word file")
        page.add_files([str(source), str(broken)])
        c.documents.start()
        pump(application, lambda: not page.busy)
        assert page.files.topLevelItemCount() == 1
        assert page.files.topLevelItem(0).text(0) == "broken.docx"
        assert page.files.topLevelItem(0).text(3) == "失败"
        assert page.history.topLevelItemCount() == 7
        for row in page.rows.values():
            row["item"].setSelected(True)
        c.documents.remove_selected()
        assert page.files.topLevelItemCount() == 0
        assert len(list((tmp_path / "results").glob("*/*.docx"))) == 10
    finally:
        release.set()
        c.documents.shutdown()
        server.shutdown()
