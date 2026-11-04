"""Render representative Qt views with synthetic settings, without hooks or API calls."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from linguafloat.config import AppSettings, ProviderProfile
from linguafloat.ui import MainWindow, TranslationOverlay, apply_ui_font
from linguafloat.screen_ui import ScreenTranslationPanel


def main():
    app = QApplication([])
    for font_file in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font_file))
    apply_ui_font(app, 14)
    folder = Path(__file__).resolve().parents[1] / "artifacts" / "ui"
    folder.mkdir(parents=True, exist_ok=True)
    settings = AppSettings(active_provider="demo", providers=[
        ProviderProfile("demo", "日常翻译", "https://example.com/v1", "translation-model", env_key="", favorite=True),
        ProviderProfile("backup", "备用站点", "https://example.org/api", "custom-model", env_key=""),
    ])
    window = MainWindow(settings, "missing")
    for width, height in [(1060, 840), (860, 620)]:
        window.resize(width, height)
        window.show()
        for page, name in [(0, "functions"), (1, "providers")]:
            window.pages.setCurrentIndex(page)
            app.processEvents()
            window.grab().save(str(folder / f"{name}-{width}.png"))
    window.resize(1060, 840)
    window.pages.setCurrentIndex(0)
    window.set_game_settings("on", [])
    window.set_game_state(True, "游戏模式：仅区域翻译")
    window.set_screen_status("已暂停", True, False)
    app.processEvents()
    window.grab().save(str(folder / "game-paused.png"))
    window.set_self_test_result("示例测试报告\n右键取词：因游戏模式停用\n快捷键翻译：因游戏模式停用\nOCR：通过\nAPI：失败，HTTP 401", error=True)
    window.diagnostics_toggle.setChecked(True)
    window.capture_diagnostics.setPlainText("取词诊断：当前因游戏模式停用")
    app.processEvents()
    window.function_scroll.verticalScrollBar().setValue(window.function_scroll.verticalScrollBar().maximum())
    app.processEvents()
    window.grab().save(str(folder / "diagnostics-example.png"))
    window.pages.setCurrentIndex(1)
    window.advanced_toggle.setChecked(True)
    app.processEvents()
    window.config_scroll.verticalScrollBar().setValue(window.config_scroll.verticalScrollBar().maximum())
    app.processEvents()
    window.grab().save(str(folder / "providers-advanced.png"))
    apply_ui_font(app, 16)
    window.resize(860, 620)
    app.processEvents()
    window.grab().save(str(folder / "providers-large-font.png"))
    apply_ui_font(app, 14)
    panel = ScreenTranslationPanel()
    panel.set_paused(True)
    panel.show_translation("Line one\nLine two", "这一行很长，会根据窗口宽度自动换行显示，直到全部显示完毕。\n这是原文的下一行。")
    panel.resize(280, 230)
    panel.show()
    app.processEvents()
    panel.grab().save(str(folder / "region-minimum.png"))
    overlay = TranslationOverlay()
    overlay.resize(320, 220)
    overlay.show_translation("测试输入", "Test input", "en", origin="input")
    app.processEvents()
    overlay.grab().save(str(folder / "input-minimum.png"))
    window.allow_close()
    window.close()
    panel.close()
    overlay.close()
    print(folder)


if __name__ == "__main__":
    main()
