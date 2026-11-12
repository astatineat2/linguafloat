import os
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton, QDialog
import linguafloat.config_transfer_ui as module
from linguafloat.cc_switch import Candidate
from linguafloat.config import AppSettings, ProviderProfile
from linguafloat.ui import MainWindow


@pytest.fixture
def dialog(monkeypatch):
    app = QApplication.instance() or QApplication([])
    entries = [Candidate(source, str(i), ProviderProfile(str(i), name, "https://example.test", "model",
                         "anthropic" if source == "claude" else "responses", env_key=""), "synthetic-private")
               for i, (source, name) in enumerate([("codex", "A"), ("codex", "B"), ("claude", "C")])]
    monkeypatch.setattr(module, "discover_sources", lambda: [Path("test.sql")])
    monkeypatch.setattr(module, "read_candidates", lambda _: entries)
    instance = module.BatchImportDialog()
    yield app, instance
    instance.close()


def test_category_selects_all_without_per_item_confirmation(dialog):
    app, d = dialog
    assert not d.selected_candidates()
    d.category.setCurrentIndex(d.category.findData("codex"))
    assert len(d.selected_candidates()) == 2
    d.table.setCurrentCell(1, 0)
    assert len(d.selected_candidates()) == 2
    for row in range(2):
        for col in range(4):
            cell = d.table.item(row, col)
            assert cell.data(Qt.ItemDataRole.CheckStateRole) is None
            assert "synthetic-private" not in cell.text()
    assert d.key.text() == ""
    d.category.setCurrentIndex(d.category.findData("claude"))
    assert len(d.selected_candidates()) == 1


@pytest.mark.parametrize("size", [(860, 620), (1060, 840)])
def test_batch_controls_fit_and_scroll(dialog, size):
    app, d = dialog
    d.category.setCurrentIndex(1)
    d.resize(*size); d.show(); app.processEvents()
    assert (d.width(), d.height()) == size
    for control in (d.path, d.category, d.key, d.buttons):
        assert d.rect().contains(QRect(control.mapTo(d, QPoint()), control.size()))
    if size[0] == 860:
        assert d.table.horizontalScrollBar().maximum() > 0


def test_manual_source_and_error_then_recovery(dialog, monkeypatch):
    _, d = dialog
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *a: ("manual.sql", ""))
    d._browse()
    assert d.path.currentText() == "manual.sql"
    def fail(_):
        raise module.CCSwitchError("文件格式损坏")
    monkeypatch.setattr(module, "read_candidates", fail)
    d._load()
    assert "损坏" in d.status.text()
    assert not d.selected_candidates()
    assert not d.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_main_entries_are_outside_provider_editor(dialog):
    app, _ = dialog
    w = MainWindow(AppSettings(), "missing")
    w.resize(860, 620); w.pages.setCurrentIndex(1); w.show(); app.processEvents()
    w.dashboard.show_data(); app.processEvents()
    try:
        for name in ("选择 SQL 文件", "导出 SQL 备份"):
            button = next(b for b in w.findChildren(QPushButton) if b.text() == name)
            assert not w.config_scroll.isAncestorOf(button)
            assert w.rect().contains(QRect(button.mapTo(w, QPoint()), button.size()))
            assert button.width() >= button.minimumSizeHint().width()
        assert [a.text() for a in w.dashboard.add_button.menu().actions()] == ["OpenCode Go", "Now Coding", "自定义"]
    finally:
        w.allow_close(); w.close()


def test_official_service_requires_explicit_plan_and_does_not_change_it(dialog):
    d = module.OpenCodeServiceDialog()
    try:
        d._confirm()
        assert d.result() != QDialog.DialogCode.Accepted
        d.plan.setCurrentIndex(d.plan.findData("go"))
        d.mode.setCurrentIndex(d.mode.findData("gemini"))
        d.model.setText("explicit-model"); d.key.setText("synthetic")
        d._confirm()
        assert d.result() != QDialog.DialogCode.Accepted and d.plan.currentData() == "go"
        d.plan.setCurrentIndex(d.plan.findData("paid"))
        d._confirm()
        assert d.result() == QDialog.DialogCode.Accepted
        c = d.candidate()
        assert c.profile.base_url == "https://opencode.ai/inference/google/v1beta"
        assert c.profile.native_auth == "bearer"
    finally:
        d.close()
