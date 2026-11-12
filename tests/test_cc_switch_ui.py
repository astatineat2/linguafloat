import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

import linguafloat.cc_switch_ui as module
from linguafloat.cc_switch import Candidate
from linguafloat.config import AppSettings, ProviderProfile
from linguafloat.ui import MainWindow


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def preview(application, monkeypatch):
    entries = [
        Candidate("codex", "a", ProviderProfile("cc-a", "已有名称", "https://example.test/v1", "model-a", env_key=""), "hidden-key"),
        Candidate("codex", "b", ProviderProfile("cc-b", "待补全", "", "", api_mode="", env_key="MISSING_CC_ENV")),
        Candidate("claude", "c", ProviderProfile("cc-c", "不支持", "", ""), blocked="暂不支持 Anthropic 协议"),
    ]
    monkeypatch.setattr(module, "discover_sources", lambda: [Path("source.db")])
    monkeypatch.setattr(module, "read_candidates", lambda _: entries)
    existing = [ProviderProfile("old", "已有名称", "https://old.example.test/v1", "old-model", env_key="")]
    dialog = module.CCSwitchPreview(existing)
    dialog.show()
    application.processEvents()
    yield dialog
    dialog.close()


def test_preview_masks_secret_defaults_skip_and_shows_update_fields(preview):
    assert all(c.action == "skip" for c in preview.operations())
    assert preview.actions[0].count() == 3
    assert preview.actions[2].count() == 1
    preview.actions[0].setCurrentIndex(2)
    assert "old-model" in preview.details.toPlainText()
    assert "model-a" in preview.details.toPlainText()
    texts = [preview.table.item(r, c).text() for r in range(3) for c in range(5)]
    assert "hidden-key" not in "\n".join(texts)
    assert "hidden-key" not in preview.details.toPlainText()


def test_missing_fields_must_be_completed_before_accept(preview):
    preview.table.setCurrentCell(1, 0)
    preview.actions[1].setCurrentIndex(1)
    preview._confirm()
    assert preview.result() != QDialog.DialogCode.Accepted
    assert not preview.env.model().item(1).isEnabled()
    preview.address.setText("https://example.test/custom")
    preview.model.setText("new-model")
    preview.mode.setCurrentIndex(2)
    preview._edit()
    preview._confirm()
    assert preview.result() == QDialog.DialogCode.Accepted
    choice = preview.operations()[1]
    assert choice.candidate.profile.api_mode == "chat_completions"


@pytest.mark.parametrize("width,height", [(860, 620), (1060, 840)])
def test_preview_fits_small_window_and_horizontal_scroll(preview, application, width, height):
    preview.resize(width, height)
    application.processEvents()
    assert preview.width() == width
    assert preview.height() == height
    assert preview.table.horizontalScrollBar().maximum() > 0
    ok = preview.buttons.button(module.QDialogButtonBox.StandardButton.Ok)
    assert preview.rect().contains(ok.mapTo(preview, ok.rect().bottomRight()))


def test_manual_selection_read_error_and_cancel(application, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "discover_sources", lambda: [])
    dialog = module.CCSwitchPreview([])
    assert "未找到" in dialog.status.text()
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *a: (str(tmp_path / "manual.db"), ""))
    def fail(_):
        raise module.CCSwitchError("不支持此来源")
    monkeypatch.setattr(module, "read_candidates", fail)
    dialog._browse()
    assert "不支持" in dialog.status.text()
    assert dialog.operations() == []
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_main_window_import_entry_emits_without_network(application):
    window = MainWindow(AppSettings(), "missing")
    calls = []
    window.dashboard.local_requested.connect(calls.append)
    button = next(b for b in window.findChildren(QPushButton) if b.text() == "从本机 CC Switch 读取")
    button.click()
    assert calls == ["cc"]
    window.allow_close()
    window.close()
