from __future__ import annotations

import copy
import os
import sys
import time
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot, Qt
from PySide6.QtGui import QAction, QActionGroup, QCursor
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from linguafloat import __version__
from linguafloat.assets import app_icon
from linguafloat.single_instance import SingleInstance, SingleInstanceError
from linguafloat.document_ui import DocumentController
from linguafloat.clipboard_io import read_clipboard_text
from linguafloat.provider_tools import ProviderTask
from linguafloat.import_ui import ImportPreview

from linguafloat.capture import SelectionCapture
from linguafloat.codex_config import (
    CodexConfigError,
    export_codex_config,
    import_codex_config,
)
from linguafloat.config import (
    AppSettings,
    ConfigStore,
    CredentialError,
    CredentialStore,
    ProviderProfile,
    create_provider_id,
    validate_provider,
    TARGET_LANGUAGES,
)
from linguafloat.hotkeys import GlobalHotkey, HotkeyError, parse_hotkey
from linguafloat.game_mode import GAME_MODES, GameModeMonitor, normalize_game_paths
from linguafloat.selection import (
    RightClickMonitor,
    normalize_selection,
    read_current_selection,
)
from linguafloat.translation import TranslationCache, TranslationTask, translation_scope
from linguafloat.input_capture import InputCaptureTask, foreground_process_id
from linguafloat.screen import ScreenReader
from linguafloat.ocr import LocalOcr, OcrTask
from linguafloat.help_ui import HelpController
from linguafloat.ui import (
    MainWindow,
    TranslateActionButton,
    TranslationOverlay,
    apply_ui_font,
)


class AppController(QObject):
    hotkey_activated = Signal(int)
    input_hotkey_activated = Signal(int)

    def __init__(self, application: QApplication) -> None:
        super().__init__()
        self.application = application
        self._update_waiting = False
        self.config_store = ConfigStore()
        self.credentials = CredentialStore()
        self.settings = self.config_store.load()
        self.settings.font_size = 14
        apply_ui_font(application, 14)
        self.hotkey = GlobalHotkey(native=True)
        self.input_hotkey = GlobalHotkey(native=True)
        self._input_sequence = 0
        self._input_pending: tuple[int, str, str] | None = None
        self._input_capture_token: int | None = None
        self._input_capture_timed_out = False
        self.right_click = RightClickMonitor(self)
        self.cache = TranslationCache(capacity=100)
        self._inflight_requests: set[tuple[str, str]] = set()
        self._request_targets: dict[tuple[str, str], list[tuple[str, int]]] = {}
        self._connection_test_profile: ProviderProfile | None = None
        self._provider_tool_token = 0
        self._provider_tool_form = None
        self._connection_test_form = None
        self._connection_test_started = 0.0
        self._self_test_pending = False
        self._self_test_checks: list[str] = []
        self._self_test_source = ""
        self._self_test_started = 0.0
        self._diagnostic_ocr = LocalOcr()
        self.thread_pool = QThreadPool.globalInstance()
        self.paused = False
        self.game_active = False
        self._trigger_error = ""
        self._ordinary_generation = 0
        self._capture_cancelled = threading.Event()
        self.game_monitor = GameModeMonitor(self)

        profile = self.settings.active_profile()
        self.window = MainWindow(self.settings, self._credential_source(profile))
        self.documents = DocumentController(self.window.documents, self.settings, self.credentials,
                                            self.config_store.path.parent / "documents" / "jobs.sqlite3", self)
        self.window.documents.output_directory_changed.connect(self._save_document_output)
        self.window.documents.preferences_changed.connect(self._save_document_preferences)
        self.overlay = TranslationOverlay()
        self.action_button = TranslateActionButton()
        self.capture = SelectionCapture(application.clipboard(), self)
        self.capture.allowed = self._capture_allowed
        self.right_click.allowed = self._capture_allowed
        self.tray = self._create_tray()
        self._refresh_key(profile)
        self.screen_reader = ScreenReader(self.window, lambda: self.settings.active_profile().cache_scope, self)
        self.screen_reader.set_intervals(self.settings.ocr_interval, self.settings.translation_interval)
        self.screen_reader.translation_requested.connect(self._translate_screen)
        self.screen_reader.status_changed.connect(self.window.set_screen_status)
        self.window.screen_select_requested.connect(lambda: self.screen_reader.select_region() if not self._update_waiting else None)
        self.window.screen_pause_requested.connect(self.screen_reader.toggle_pause)
        self.window.screen_stop_requested.connect(self.screen_reader.stop)
        self.window.screen_move_requested.connect(self.screen_reader.move_region)
        self.window.screen_intervals_changed.connect(self._save_screen_intervals)
        self.window.input_language_requested.connect(self._save_input_language)
        self.window.input_hotkey_requested.connect(self._save_input_hotkey)
        self.window.self_test_requested.connect(self._run_self_test)
        self.window.game_mode_requested.connect(self._save_game_mode)
        self.window.game_paths_requested.connect(self._save_game_paths)
        self.input_hotkey_activated.connect(self._on_input_hotkey)
        self.overlay.language_requested.connect(self._retranslate_overlay)

        self.hotkey_activated.connect(self._on_hotkey)
        self.capture.captured.connect(self._translate)
        self.capture.failed.connect(self._capture_failed)
        self.capture.diagnostic.connect(self.window.capture_diagnostics.setPlainText)
        self.right_click.selection_found.connect(self._show_right_click_action)
        self.right_click.selection_rejected.connect(self._selection_rejected)
        self.right_click.dismiss_requested.connect(self.action_button.dismiss)
        self.action_button.translate_requested.connect(self._translate)
        self.action_button.dismissed.connect(self.right_click.clear_action_bounds)
        self.window.save_requested.connect(self._save_settings)
        self.window.delete_key_requested.connect(self._delete_key)
        self.window.pause_requested.connect(self._set_paused)
        self.window.provider_selected.connect(self._edit_provider)
        self.window.active_provider_requested.connect(self._select_provider)
        self.window.active_model_requested.connect(self._select_model)
        self.window.duplicate_provider_requested.connect(self._duplicate_provider)
        self.window.provider_tools_requested.connect(self._provider_tools)
        self.window.clipboard_requested.connect(self._translate_clipboard)
        self.window.function_save_requested.connect(self._save_function_settings)
        self.window.add_provider_requested.connect(self._add_provider)
        self.window.delete_provider_requested.connect(self._delete_provider)
        self.window.import_codex_requested.connect(self._import_codex)
        self.window.export_codex_requested.connect(self._export_codex)
        self.window.connection_test_requested.connect(self._test_connection)

        self.game_monitor.configure(self.settings.game_mode, self.settings.game_paths)
        self.game_active = self.game_monitor.active
        self.game_monitor.changed.connect(self._game_changed)
        self._reconcile_triggers()
        self.help = HelpController(self)
        self.window.help_requested.connect(self.help.show)

    def _save_document_preferences(self,values):
        allowed={"document_source_language","document_target_language","document_pdf_mode",
                 "document_translate_graphics","document_skip_references","document_mono","document_bilingual","document_bilingual_layout",
                 "document_column_widths"}
        for name,value in values.items():
            if name in allowed:setattr(self.settings,name,value)
        self.config_store.save(self.settings)

    def _save_document_output(self, directory):
        directory = directory.strip()
        if directory and directory != self.settings.document_output_dir:
            self.settings.document_output_dir = directory
            self.config_store.save(self.settings)

    def start(self) -> None:
        self.game_monitor.start()
        self.tray.show()
        self.window.show()
        self.window.active_provider_combo.setFocus()
        QTimer.singleShot(0, lambda: self.window.function_scroll.verticalScrollBar().setValue(0))
        self.help.start()

    def _ordinary_allowed(self) -> bool:
        return not (self.paused or self.game_active or self._trigger_error)

    def _capture_allowed(self) -> bool:
        return (not getattr(self, "_update_waiting", False) and self._ordinary_allowed()
                and self.game_monitor.allows_capture())

    def _emit_hotkey(self, generation: int, *, input_translation: bool = False) -> None:
        if generation == self._ordinary_generation and self._capture_allowed():
            signal = self.input_hotkey_activated if input_translation else self.hotkey_activated
            signal.emit(generation)

    def _stop_ordinary(self) -> None:
        self._ordinary_generation += 1
        self._capture_cancelled.set()
        self._capture_cancelled = threading.Event()
        self._input_pending = None
        self._input_capture_token = None
        self.capture.cancel()
        self.hotkey.stop()
        self.input_hotkey.stop()
        self.right_click.stop()
        self.action_button.dismiss()
        self.overlay.hide()
        # A shared HTTP request can still serve the screen reader.
        for key, recipients in self._request_targets.items():
            self._request_targets[key] = [(target, token) for target, token in recipients
                                          if target not in {"selection", "input", "overlay_input"}]

    def _refresh_game_ui(self) -> None:
        self.window.set_game_state(self.game_active, self.game_monitor.reason, self._trigger_error)
        for mode, action in self.game_actions.items():
            action.setChecked(mode == self.settings.game_mode)
        self.tray.setToolTip(f"浮译 LinguaFloat v{__version__} | " + self.game_monitor.reason)
        self.game_tray_status.setText("仅区域翻译" if self.game_active else "普通监听已暂停" if self.paused else
                                     "监听恢复失败" if self._trigger_error else "普通模式")

    def _reconcile_triggers(self) -> None:
        self._trigger_error = ""
        self._stop_ordinary()
        try:
            if not self.paused and not self.game_active:
                self._configure_triggers(self.settings.hotkey, self.settings.trigger_mode)
        except (HotkeyError, RuntimeError) as exc:
            self._trigger_error = str(exc)
            self.window.set_activity("监听恢复失败：" + str(exc), error=True)
        self.window.set_paused(self.paused)
        self._refresh_game_ui()

    @Slot(bool, str)
    def _game_changed(self, active: bool, reason: str) -> None:
        changed = active != self.game_active
        self.game_active = active
        if changed:
            self._reconcile_triggers()
        else:
            self._refresh_game_ui()

    @Slot(str)
    def _save_game_mode(self, mode: str) -> None:
        if mode not in GAME_MODES:
            return
        self._save_game_settings(mode, self.settings.game_paths)

    @Slot(list)
    def _save_game_paths(self, paths: list[str]) -> None:
        self._save_game_settings(self.settings.game_mode, normalize_game_paths(paths))

    def _save_game_settings(self, mode: str, paths: list[str]) -> None:
        previous = self.settings.game_mode, self.settings.game_paths
        self.settings.game_mode, self.settings.game_paths = mode, list(paths)
        try:
            self.config_store.save(self.settings)
        except OSError:
            self.settings.game_mode, self.settings.game_paths = previous
            self.window.set_activity("游戏模式设置保存失败。", error=True)
        self.window.set_game_settings(self.settings.game_mode, self.settings.game_paths)
        self.game_monitor.configure(self.settings.game_mode, self.settings.game_paths)
        self._refresh_game_ui()

    def _credential_source(self, profile: ProviderProfile) -> str:
        try:
            return self.credentials.source(profile.id, profile.env_key)
        except CredentialError:
            return "missing"

    def _refresh_key(self, profile: ProviderProfile) -> None:
        try:
            api_key = self.credentials.get_api_key(profile.id, profile.env_key)
            source = self.credentials.source(profile.id, profile.env_key)
        except CredentialError as exc:
            self.window.set_key_source("missing")
            self.window.set_activity(str(exc), error=True)
            return
        self.window.set_key_source(source, api_key)

    @Slot(str, str, str, str, str, str, str)
    def _test_connection(
        self, provider_id: str, name: str, base_url: str, model: str,
        api_mode: str, env_key: str, api_key: str,
    ) -> None:
        if self._update_waiting:
            self.window.set_activity("正在等待安装更新，本次未发起新的连接测试。")
            return
        if self._connection_test_profile is not None:
            return
        profile = ProviderProfile(
            id=provider_id, name=name, base_url=base_url.rstrip("/"),
            model=model, api_mode=api_mode, env_key=env_key,
            **self.window.api_options(),
        )
        try:
            validate_provider(profile)
            if not api_key:
                raise ValueError("请先填写 API key。")
        except ValueError as exc:
            self.window.set_connection_status(str(exc), error=True)
            return
        self._connection_test_profile = profile
        self._connection_test_form = (provider_id, self.window._form_values())
        self._connection_test_started = time.monotonic()
        self.window.set_connection_status(
            f"正在测试“{name}”的 {model}…", pending=True
        )
        task = TranslationTask(
            api_key, profile, "Hello.", timeout=12.0, max_retries=0
        )
        task.signals.succeeded.connect(self._connection_test_succeeded)
        task.signals.failed.connect(self._connection_test_failed)
        try:
            self.thread_pool.start(task)
        except Exception:
            self._connection_test_failed(
                profile.cache_scope, "Hello.", "无法启动连接测试，请重试。"
            )

    @Slot(str, str, str)
    def _connection_test_succeeded(
        self, cache_scope: str, source_text: str, translated_text: str
    ) -> None:
        profile = self._connection_test_profile
        self._connection_test_profile = None
        if profile is not None and self._connection_form_current():
            elapsed = time.monotonic() - self._connection_test_started
            self.window.set_connection_status(
                f"“{profile.name}”连接成功（{elapsed:.1f} 秒，{time.strftime('%H:%M:%S')}）\n样例：{translated_text[:200]}"
            )
            if profile.cache_scope == self.settings.active_profile().cache_scope and not self.window.is_dirty():
                self.window.active_connection_status.setText(f"测试通过 · {time.strftime('%H:%M:%S')}")
        else:
            self.window.test_connection_button.setEnabled(True)
            self.window.test_connection_button.setText("测试连接")

    @Slot(str, str, str)
    def _connection_test_failed(
        self, cache_scope: str, source_text: str, message: str
    ) -> None:
        self._connection_test_profile = None
        if self._connection_form_current():
            self.window.set_connection_status(message, error=True)
        else:
            self.window.test_connection_button.setEnabled(True)
            self.window.test_connection_button.setText("测试连接")

    def _connection_form_current(self) -> bool:
        return self._connection_test_form == (self.window._editing_id, self.window._form_values())

    def _create_tray(self) -> QSystemTrayIcon:
        icon = app_icon()
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip(f"浮译 LinguaFloat v{__version__}")

        menu = QMenu()
        show_action = QAction("打开设置", menu)
        show_action.triggered.connect(self.window.bring_to_front)
        quit_action = QAction("退出", menu)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.window.addAction(quit_action)
        quit_action.triggered.connect(self.quit)
        menu.addAction(show_action)
        region_pause = menu.addAction("暂停 / 继续区域翻译")
        region_pause.triggered.connect(lambda: self.screen_reader.toggle_pause())
        game_menu = menu.addMenu("游戏模式")
        self.game_tray_status = game_menu.addAction("普通模式")
        self.game_tray_status.setEnabled(False)
        group = QActionGroup(game_menu)
        group.setExclusive(True)
        self.game_actions = {}
        for mode, label in GAME_MODES.items():
            action = game_menu.addAction(label)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, value=mode: self._save_game_mode(value))
            self.game_actions[mode] = action
        menu.addSeparator()
        menu.addAction("帮助与更新", lambda: self.help.show())
        menu.addAction("首次使用指南", lambda: self.help.show(guide=True))
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        return tray

    @Slot(QSystemTrayIcon.ActivationReason)
    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.window.bring_to_front()

    @Slot(int)
    def _on_hotkey(self, generation: int | None = None) -> None:
        self.game_monitor.refresh()
        if generation is not None and generation != self._ordinary_generation:
            return
        if not self._capture_allowed() or self.screen_reader.selecting:
            return
        cursor = QCursor.pos()
        try:
            candidate = read_current_selection(
                cursor.x(), cursor.y(), own_process_id=os.getpid(), allowed=self._capture_allowed
            )
        except ValueError as exc:
            self._capture_failed(str(exc))
            return
        except Exception:
            candidate = None
        if candidate is not None:
            self.capture.note("快捷键：UI Automation 读取成功")
            self._translate(candidate.text)
            return
        self.capture.note("快捷键：UI Automation 未返回文字，尝试一次复制")
        self.capture.begin()

    @Slot()
    def _translate_clipboard(self) -> None:
        self.game_monitor.refresh()
        if not self._ordinary_allowed():
            self.window.set_activity("游戏模式或普通监听暂停时，剪贴板翻译已停用。")
            return
        try:
            text, format_name = read_clipboard_text(self.application.clipboard())
            text = normalize_selection(text)
            if not text:
                raise ValueError("剪贴板没有可翻译的文字，请先手动复制或使用区域翻译。")
        except (ValueError, OSError) as exc:
            self._capture_failed(str(exc))
            return
        self._translate(text)

    @Slot(str, int, int)
    def _show_right_click_action(
        self, source_text: str, mouse_x: int, mouse_y: int
    ) -> None:
        self.game_monitor.refresh()
        if not self._ordinary_allowed() or self.screen_reader.selecting or self.settings.trigger_mode not in {"both", "right_click"}:
            return
        self.action_button.show_for(source_text, mouse_x, mouse_y)
        self.right_click.set_action_bounds(*self.action_button.global_bounds())

    @Slot(str)
    def _selection_rejected(self, message: str) -> None:
        if self._ordinary_allowed():
            self.window.set_activity(message, error=True)

    @Slot(str)
    def _translate(self, source_text: str) -> None:
        self._submit_translation(source_text)

    @Slot(str, int)
    def _translate_screen(self, source_text: str, token: int) -> None:
        self._submit_translation(source_text, "screen", token)

    @Slot(str)
    def _retranslate_overlay(self, target_language: str) -> None:
        source = self.overlay.source_text
        if not source or target_language not in {"auto", *TARGET_LANGUAGES}:
            return
        target = "overlay_input" if self.overlay.origin == "input" else "selection"
        self._submit_translation(source, target, target_language=target_language)

    def _listener_checks(self) -> list[str]:
        checks = []
        listeners = (
            ("选中快捷键监听", self.hotkey._listener, self.settings.trigger_mode in {"both", "hotkey"}),
            ("输入快捷键监听", self.input_hotkey._listener, True),
            ("右键取词监听", self.right_click._mouse_listener, self.settings.trigger_mode in {"both", "right_click"}),
        )
        for label, running, enabled in listeners:
            status = ("因游戏模式停用" if self.game_active else "因用户暂停停用" if self.paused else
                      "因触发方式设置停用" if not enabled else "恢复失败，" + self._trigger_error if self._trigger_error else
                      "已启用" if running else "未启用")
            checks.append(label + "：" + status)
        return checks

    @Slot()
    def _run_self_test(self) -> None:
        if getattr(self, "_update_waiting", False):
            self.window.set_activity("正在等待安装更新，本次未发起新的功能测试。")
            return
        if self._self_test_pending:
            return
        try:
            self._self_test_source = normalize_selection(self.window.self_test_input.text())
        except ValueError as exc:
            self.window.set_self_test_result(str(exc), error=True)
            return
        if not self._self_test_source:
            self.window.set_self_test_result("未通过：请先填写测试文字。", error=True)
            return
        self._self_test_pending = True
        self._self_test_started = time.monotonic()
        self.window.self_test_button.setEnabled(False)
        self.window.self_test_button.setText("测试中…")
        profile = self.settings.active_profile()
        try:
            has_key = bool(self.credentials.get_api_key(profile.id, profile.env_key))
        except CredentialError:
            has_key = False
        self._self_test_checks = [
            "API key：已配置" if has_key else "API key：未配置，请填写并保存",
            *self._listener_checks(),
        ]
        self.window.set_self_test_result("正在测试…\n" + "\n".join(self._self_test_checks) + "\nOCR：正在识别测试输入栏…")
        image = self.window.self_test_input.grab().toImage()
        task = OcrTask(self._diagnostic_ocr, image, 0, b"")
        task.signals.succeeded.connect(self._self_test_ocr_succeeded)
        task.signals.failed.connect(self._self_test_ocr_failed)
        try:
            self.thread_pool.start(task)
        except Exception:
            self._self_test_ocr_failed(0, "无法启动 OCR 测试")

    @Slot(int, bytes, str)
    def _self_test_ocr_succeeded(self, job: int, digest: bytes, text: str) -> None:
        self._self_test_checks.append("OCR：通过\n识别文字：" + text if text.strip() else "OCR：未识别到文字")
        self._self_test_request()

    @Slot(int, str)
    def _self_test_ocr_failed(self, job: int, message: str) -> None:
        self._self_test_checks.append("OCR：未通过，" + message)
        self._self_test_request()

    def _self_test_request(self) -> None:
        self.window.set_self_test_result("正在测试…\n" + "\n".join(self._self_test_checks) + "\nAPI：正在请求译文…")
        self._submit_translation(self._self_test_source, "selftest")

    @Slot(str)
    def _save_input_language(self, language: str) -> None:
        if language not in TARGET_LANGUAGES:
            return
        previous = self.settings.input_language
        self.settings.input_language = language
        try:
            self.config_store.save(self.settings)
        except OSError:
            self.settings.input_language = previous
            self.window.input_language_combo.blockSignals(True)
            self.window.input_language_combo.setCurrentIndex(self.window.input_language_combo.findData(previous))
            self.window.input_language_combo.blockSignals(False)
            self.window.set_activity("无法保存输入翻译语言。", error=True)

    @Slot(str)
    def _save_input_hotkey(self, value: str) -> None:
        previous = self.settings.input_hotkey
        try:
            spec = parse_hotkey(value)
            if spec.display == parse_hotkey(self.settings.hotkey).display:
                raise HotkeyError("输入翻译和取词翻译不能使用相同快捷键。")
            if self._ordinary_allowed():
                generation = self._ordinary_generation
                self.input_hotkey.start(spec.display, lambda: self._emit_hotkey(generation, input_translation=True))
            self.settings.input_hotkey = spec.display
            self.config_store.save(self.settings)
        except (HotkeyError, OSError) as exc:
            self.settings.input_hotkey = previous
            if self._ordinary_allowed():
                try:
                    generation = self._ordinary_generation
                    self.input_hotkey.start(previous, lambda: self._emit_hotkey(generation, input_translation=True))
                except HotkeyError:
                    self._set_paused(True)
            self.window.set_activity(str(exc), error=True)
            return
        self.window.input_hotkey_edit.setText(spec.display)
        self.window.set_activity("输入翻译快捷键已保存。")

    @Slot(int)
    def _on_input_hotkey(self, generation: int | None = None) -> None:
        if getattr(self, "_update_waiting", False):
            return
        self.game_monitor.refresh()
        if generation is not None and generation != self._ordinary_generation:
            return
        if not self._capture_allowed() or self.screen_reader.selecting:
            return
        if self._input_pending is not None or self._input_capture_token is not None:
            self.window.set_activity("输入翻译正在处理，请稍候。")
            return
        self._input_sequence += 1
        token = self._input_sequence
        self._input_pending = (token, self.settings.active_profile().cache_scope, self.settings.input_language)
        self._input_capture_token = token
        self._input_capture_timed_out = False
        cancelled = self._capture_cancelled
        task = InputCaptureTask(token, foreground_process_id(), os.getpid(),
                                allowed=lambda: not cancelled.is_set() and self._capture_allowed())
        task.signals.succeeded.connect(self._input_captured)
        task.signals.failed.connect(self._input_capture_failed)
        self.window.set_activity("正在读取当前输入内容…")
        try:
            self.thread_pool.start(task)
        except Exception:
            self._input_capture_failed(token, "无法启动输入取词。")
            return
        QTimer.singleShot(6000, lambda: self._input_capture_timeout(token))

    def _input_capture_timeout(self, token: int) -> None:
        if self._input_capture_token == token:
            self._input_capture_timed_out = True
            self._input_pending = None
            self._capture_failed("当前软件的输入框响应超时。")

    def _input_is_current(self, token: int) -> bool:
        pending = self._input_pending
        return bool(pending and pending[0] == token and self._ordinary_allowed() and not self.screen_reader.selecting
                    and pending[1] == self.settings.active_profile().cache_scope
                    and pending[2] == self.settings.input_language)

    @Slot(int, str)
    def _input_captured(self, token: int, text: str) -> None:
        if self._input_capture_token != token:
            return
        self._input_capture_token = None
        if self._input_capture_timed_out or not self._input_is_current(token):
            self._input_pending = None
            return
        self._submit_translation(text, "input", token, self._input_pending[2])

    @Slot(int, str)
    def _input_capture_failed(self, token: int, message: str) -> None:
        if self._input_capture_token != token:
            return
        self._input_capture_token = None
        current = self._input_is_current(token)
        self._input_pending = None
        if current and not self._input_capture_timed_out:
            self._capture_failed(message)

    @Slot(float, float)
    def _save_screen_intervals(self, ocr_interval: float, translation_interval: float) -> None:
        previous = (self.settings.ocr_interval, self.settings.translation_interval)
        self.settings.ocr_interval = ocr_interval
        self.settings.translation_interval = translation_interval
        try:
            self.config_store.save(self.settings)
        except OSError:
            self.settings.ocr_interval, self.settings.translation_interval = previous
            self.window.set_activity("无法保存区域刷新间隔。", error=True)
            return
        self.screen_reader.set_intervals(ocr_interval, translation_interval)

    def _deliver_translation(self, target: str, token: int, source: str, result: str, *, error: bool = False) -> None:
        if target in {"selection", "input", "overlay_input"}:
            self.game_monitor.refresh()
            if not self._ordinary_allowed():
                return
            if target != "input" and token != self._ordinary_generation:
                return
        if target == "screen":
            self.screen_reader.translated(token, source, result, error=error)
        elif target in {"input", "overlay_input"}:
            current = self._input_is_current(token)
            if target == "overlay_input":
                current = self._ordinary_allowed() and self.overlay.source_text == source
            if self._input_pending and self._input_pending[0] == token:
                self._input_pending = None
            if not current:
                return
            if error:
                self.overlay.show_error(result)
            else:
                self.application.clipboard().setText(result)
                self.overlay.show_translation(
                    source, result,
                    target_language=(self.settings.input_language if target == "input" else self.overlay.target_language()),
                    origin="input",
                )
        elif target == "selftest":
            self._self_test_checks[1:4] = self._listener_checks()
            self._self_test_pending = False
            self.window.self_test_button.setEnabled(True)
            self.window.self_test_button.setText("一键测试")
            elapsed = time.monotonic() - self._self_test_started
            report = f"测试完成（{elapsed:.1f} 秒）\n" + "\n".join(self._self_test_checks)
            report += f"\nAPI：未通过，{result}" if error else f"\nAPI：通过\n原文：{source}\n译文：{result}"
            report += "\n外部软件取词兼容性需在对应软件验证。"
            self.window.set_self_test_result(report, error=error)
        elif error:
            self.overlay.show_error(result)
        else:
            self.overlay.show_translation(source, result, target_language="auto", origin="selection")

    def _submit_translation(self, source_text: str, target: str = "selection", token: int = 0, target_language: str = "auto") -> None:
        # Drain existing results without invalidating their generation or input token.
        if getattr(self, "_update_waiting", False) and target in {"selection", "overlay_input"}:
            return
        if target in {"selection", "input", "overlay_input"}:
            self.game_monitor.refresh()
            if not self._ordinary_allowed():
                return
            if target != "input":
                token = self._ordinary_generation
        try:
            source_text = normalize_selection(source_text)
        except ValueError as exc:
            self._deliver_translation(target, token, source_text, str(exc), error=True)
            return
        if not source_text:
            return

        profile = self.settings.active_profile()
        request_key = (translation_scope(profile, target_language), source_text)
        cached = self.cache.get(*request_key)
        if cached is not None and target != "selftest":
            self._deliver_translation(target, token, source_text, cached)
            self.window.set_activity("已从本地缓存读取翻译。")
            return

        if request_key in self._inflight_requests:
            if (target, token) not in self._request_targets[request_key]:
                self._request_targets[request_key].append((target, token))
            self.window.set_activity("相同内容正在翻译，请稍候。")
            return

        try:
            api_key = self.credentials.get_api_key(profile.id, profile.env_key)
        except CredentialError as exc:
            self._deliver_translation(target, token, source_text, str(exc), error=True)
            return

        if not api_key:
            self._deliver_translation(
                target, token, source_text, f"请先为“{profile.name}”填写并保存 API key。", error=True
            )
            return

        if target in {"selection", "overlay_input"}:
            self.overlay.show_loading(
                source_text,
                target_language=target_language,
                origin="input" if target == "overlay_input" else "selection",
            )
        self.window.set_activity(f"正在通过“{profile.name}”请求翻译…")
        task = TranslationTask(api_key, copy.copy(profile), source_text,
                               timeout=12 if target == "selftest" else profile.timeout,
                               max_retries=0 if target in {"screen", "input", "selftest"} else profile.retries,
                               target_language=target_language,
                               allowed=lambda: self._request_needed(request_key))
        task.signals.succeeded.connect(self._translation_succeeded)
        task.signals.failed.connect(self._translation_failed)
        task.signals.cancelled.connect(self._translation_cancelled)
        self._inflight_requests.add(request_key)
        self._request_targets[request_key] = [(target, token)]
        try:
            self.thread_pool.start(task)
        except Exception as exc:
            self._inflight_requests.discard(request_key)
            self._translation_failed(
                request_key[0], source_text, "无法启动翻译任务。"
            )

    def _request_needed(self, key: tuple[str, str]) -> bool:
        return any(self._recipient_current(target, token)
                   for target, token in self._request_targets.get(key, []))

    def _recipient_current(self, target: str, token: int) -> bool:
        if target == "screen":
            return self.screen_reader.request_is_current(token)
        if target == "selftest":
            return True
        if target == "input":
            return self._input_is_current(token)
        return self._ordinary_allowed() and token == self._ordinary_generation

    @Slot(str, str, str)
    def _translation_cancelled(self, scope: str, source: str, language: str) -> None:
        key = (scope, source)
        self._inflight_requests.discard(key)
        targets = self._request_targets.pop(key, [])
        # A screen recipient may have arrived after the worker chose to cancel.
        for target, token in targets:
            if self._recipient_current(target, token):
                self._submit_translation(source, target, token, language)
            elif target == "screen":
                self.screen_reader.state.finish(token)

    @Slot(str, str, str)
    def _translation_succeeded(
        self, cache_scope: str, source_text: str, translated_text: str
    ) -> None:
        self._inflight_requests.discard((cache_scope, source_text))
        self.cache.put(cache_scope, source_text, translated_text)
        targets = self._request_targets.pop((cache_scope, source_text), [])
        visible = any(self._recipient_current(target, token) for target, token in targets)
        for target, token in targets:
            self._deliver_translation(target, token, source_text, translated_text)
        if visible:
            self.window.set_activity("翻译完成。")

    @Slot(str, str, str)
    def _translation_failed(
        self, cache_scope: str, source_text: str, message: str
    ) -> None:
        self._inflight_requests.discard((cache_scope, source_text))
        targets = self._request_targets.pop((cache_scope, source_text), [])
        visible = any(self._recipient_current(target, token) for target, token in targets)
        for target, token in targets:
            self._deliver_translation(target, token, source_text, message, error=True)
        if visible:
            self.window.set_activity(message, error=True)

    @Slot(str)
    def _capture_failed(self, message: str) -> None:
        if not self._ordinary_allowed():
            return
        self.overlay.show_error(message)
        self.window.set_activity(message, error=True)

    def _show_configuration_error(self, message: str) -> None:
        self.overlay.show_error(message)
        self.window.set_activity(message, error=True)
        self.window.bring_to_front()

    @Slot(str, str, str, str, str, str, str, str, str, int)
    def _save_settings(
        self,
        hotkey_value: str,
        provider_id: str,
        provider_name: str,
        base_url: str,
        model: str,
        api_mode: str,
        env_key: str,
        api_key: str,
        trigger_mode: str,
        font_size: int,
    ) -> None:
        try:
            spec = parse_hotkey(hotkey_value)
            if spec.display == parse_hotkey(self.settings.input_hotkey).display:
                raise HotkeyError("输入翻译和取词翻译不能使用相同快捷键。")
            if trigger_mode not in {"both", "hotkey", "right_click"}:
                raise ValueError("不支持的翻译触发方式。")
            updated_profile = ProviderProfile(
                id=provider_id,
                name=provider_name,
                base_url=base_url.rstrip("/"),
                model=model,
                api_mode=api_mode,
                # Explicitly replacing a key must take effect even if an old
                # environment variable previously supplied this provider's key.
                env_key="" if api_key else env_key,
                favorite=self.window.favorite_check.isChecked(),
                models=list(self.window._model_choices),
                favorite_models=list(self.window._favorite_models),
                timeout=self.window.timeout_spin.value(),
                document_timeout=self.window.document_timeout_spin.value(),
                retries=self.window.retries_spin.value(),
                document_concurrency=self.window.document_concurrency_spin.value(),
                document_rpm=self.window.document_rpm_spin.value(),
                document_tpm=self.window.document_tpm_spin.value(),
                **self.window.api_options(),
            )
            validate_provider(updated_profile)
        except (HotkeyError, ValueError) as exc:
            self.window.set_activity(str(exc), error=True)
            return

        if self._find_provider(provider_id) is None:
            self.window.set_activity("当前供应商不存在，请重新选择。", error=True)
            return

        previous = copy.deepcopy(self.settings)
        was_paused = self.paused
        if not was_paused:
            try:
                self._configure_triggers(spec.display, trigger_mode)
            except (HotkeyError, RuntimeError) as exc:
                self._restore_triggers(previous.hotkey, previous.trigger_mode)
                self.window.set_activity(str(exc), error=True)
                return

        for index, profile in enumerate(self.settings.providers):
            if profile.id == provider_id:
                self.settings.providers[index] = updated_profile
                break
        self.settings.hotkey = spec.display
        self.settings.trigger_mode = trigger_mode
        self.settings.font_size = 14

        previous_key = None
        try:
            if api_key:
                previous_key = self.credentials.get_api_key(provider_id, "")
                self.credentials.save_api_key(provider_id, api_key)
            self.config_store.save(self.settings)
        except (CredentialError, OSError) as exc:
            self.settings = previous
            if previous_key is not None:
                try:
                    if previous_key:
                        self.credentials.save_api_key(provider_id, previous_key)
                    else:
                        self.credentials.delete_api_key(provider_id)
                except CredentialError:
                    self.window.set_activity("配置保存失败，密钥回滚也失败，请重新检查当前密钥。", error=True)
                    if not was_paused:
                        self._restore_triggers(previous.hotkey, previous.trigger_mode)
                    return
            if not was_paused:
                self._restore_triggers(previous.hotkey, previous.trigger_mode)
            self.window.set_activity(f"保存失败：{exc}", error=True)
            return

        self.window.hotkey_edit.setText(self.settings.hotkey)
        apply_ui_font(self.application, self.settings.font_size)
        self.window.settings_saved(
            self.settings.providers,
            self.settings.active_provider,
        )
        self._refresh_key(updated_profile)
        self.screen_reader._check_scope()
        self.window.active_connection_status.setText("配置已更新 · 待测试")
        self._refresh_game_ui()

    def _configure_triggers(self, hotkey_value: str, trigger_mode: str) -> None:
        self._stop_ordinary()
        if self.paused or self.game_active:
            return
        generation = self._ordinary_generation
        try:
            if parse_hotkey(hotkey_value).display == parse_hotkey(self.settings.input_hotkey).display:
                raise HotkeyError("输入翻译和取词翻译不能使用相同快捷键。")
            self.input_hotkey.start(self.settings.input_hotkey, lambda: self._emit_hotkey(generation, input_translation=True))
            if trigger_mode in {"both", "right_click"}:
                self.right_click.start()
            if trigger_mode in {"both", "hotkey"}:
                spec = self.hotkey.start(hotkey_value, lambda: self._emit_hotkey(generation))
                self.settings.hotkey = spec.display
            self._trigger_error = ""
        except HotkeyError:
            self.hotkey.stop()
            self.input_hotkey.stop()
            self.right_click.stop()
            raise
        except Exception as exc:
            self.hotkey.stop()
            self.input_hotkey.stop()
            self.right_click.stop()
            raise RuntimeError("无法启动右键翻译监听。") from exc

    def _restore_triggers(self, hotkey_value: str, trigger_mode: str) -> None:
        try:
            self._configure_triggers(hotkey_value, trigger_mode)
        except (HotkeyError, RuntimeError):
            self._set_paused(True)

    @Slot(str)
    def _edit_provider(self, provider_id: str) -> None:
        profile = self._find_provider(provider_id)
        if profile:
            self._refresh_key(profile)

    def _active_configuration_changed(self) -> None:
        self._reconcile_triggers()
        self.screen_reader._check_scope()
        self.window._profiles = {p.id: p for p in self.settings.providers}
        self.window.set_active_profile(self.settings.active_provider)
        self.window.active_connection_status.setText("当前配置尚未测试")

    @Slot(str)
    def _select_provider(self, provider_id: str) -> None:
        profile = self._find_provider(provider_id)
        if profile is None:
            return
        previous = self.settings.active_provider
        self.settings.active_provider = provider_id
        try:
            self.config_store.save(self.settings)
        except OSError as exc:
            self.settings.active_provider = previous
            self.window.set_active_profile(previous)
            self.window.set_activity(f"切换失败：{exc}", error=True)
            return
        self._active_configuration_changed()
        self.window.set_activity(f"已切换到“{profile.name}”。")

    @Slot(str)
    def _select_model(self, model: str) -> None:
        profile = self.settings.active_profile()
        previous = profile.model
        previous_models = list(profile.models)
        profile.model = model.strip()
        try:
            validate_provider(profile)
            profile.models = list(dict.fromkeys([*profile.models, previous, profile.model]))
            self.config_store.save(self.settings)
        except (ValueError, OSError) as exc:
            profile.model = previous
            profile.models = previous_models
            self.window.set_active_profile(profile.id)
            self.window.set_activity(f"模型切换失败：{exc}", error=True)
            return
        if self.window._editing_id == profile.id:
            self.window._load_profile(profile.id)
            self._refresh_key(profile)
        self._active_configuration_changed()

    @Slot()
    def _save_function_settings(self) -> None:
        previous = copy.deepcopy(self.settings)
        try:
            spec = parse_hotkey(self.window.hotkey_edit.text())
            if spec.display == parse_hotkey(self.settings.input_hotkey).display:
                raise HotkeyError("输入翻译和取词翻译不能使用相同快捷键。")
            self.settings.hotkey = spec.display
            self.settings.trigger_mode = self.window.trigger_mode_combo.currentData()
            self.settings.font_size = 14
            self.config_store.save(self.settings)
        except (HotkeyError, OSError) as exc:
            self.settings = previous
            self.window.set_activity(str(exc), error=True)
            return
        apply_ui_font(self.application, self.settings.font_size)
        self._reconcile_triggers()
        self.window.set_activity("功能设置已保存。")

    @Slot(str)
    def _provider_tools(self, operation: str) -> None:
        window = self.window
        if self._provider_tool_form is not None:
            return
        profile = ProviderProfile(window._editing_id, window.provider_name_edit.text().strip(),
                                  window.base_url_edit.text().strip(), window.model_edit.text().strip(),
                                  api_mode=window.api_mode_combo.currentData(), env_key=window.env_key_edit.text().strip(),
                                  **window.api_options())
        if operation == "models" and not profile.model:
            profile.model = "model-list"
        key = window.api_key_edit.text().strip()
        try:
            validate_provider(profile)
            if not key:
                raise ValueError("请先填写 API key。")
        except ValueError as exc:
            window.set_connection_status(str(exc), error=True)
            return
        if operation == "detect" and QMessageBox.question(window, "检测接口", "将分别测试 Responses 和 Chat Completions，共发送两次请求。继续？") != QMessageBox.StandardButton.Yes:
            return
        self._provider_tool_token += 1
        self._provider_tool_form = (profile.id, window._form_values(), operation)
        window.fetch_models_button.setEnabled(False)
        window.detect_api_button.setEnabled(False)
        window.set_activity("正在获取模型列表…" if operation == "models" else "正在检测接口…")
        task = ProviderTask(self._provider_tool_token, profile, key, operation)
        task.signals.finished.connect(self._provider_tools_finished)
        try:
            self.thread_pool.start(task)
        except Exception:
            self._provider_tools_finished(self._provider_tool_token, None, "无法启动请求，请重试。")

    @Slot(int, object, str)
    def _provider_tools_finished(self, token, result, error) -> None:
        if token != self._provider_tool_token or self._provider_tool_form is None:
            return
        provider_id, form, operation = self._provider_tool_form
        self._provider_tool_form = None
        window = self.window
        window.fetch_models_button.setEnabled(True)
        window.detect_api_button.setEnabled(True)
        if (provider_id, form) != (window._editing_id, window._form_values()):
            window.set_activity("配置已变更，已忽略旧检测结果。")
            return
        if error:
            window.set_connection_status(error, error=True)
        elif operation == "models":
            window.set_models(result)
            window.set_activity(f"已获取 {len(result)} 个模型，保存配置后保留列表。")
        elif result:
            modes = [item[0] for item in result]
            current = window.api_mode_combo.currentData()
            proposed = current if current in modes else modes[0]
            label = "Responses" if proposed == "responses" else "Chat Completions"
            if QMessageBox.question(window, "接口检测完成", f"可用接口：{', '.join(modes)}。\n是否在表单中选择 {label}？") == QMessageBox.StandardButton.Yes:
                window.api_mode_combo.setCurrentIndex(window.api_mode_combo.findData(proposed))

    @Slot(str)
    def _duplicate_provider(self, provider_id: str) -> None:
        if not self.window.confirm_discard():
            return
        original = self._find_provider(provider_id)
        if original is None:
            return
        profile = copy.deepcopy(original)
        profile.id = create_provider_id("copy")
        profile.name += " 副本"
        try:
            key = self.credentials.get_api_key(original.id, original.env_key)
            if key and not original.env_key:
                self.credentials.save_api_key(profile.id, key)
            self.settings.providers.append(profile)
            self.config_store.save(self.settings)
        except (OSError, CredentialError):
            self.settings.providers = [p for p in self.settings.providers if p.id != profile.id]
            self.window.set_activity("复制配置失败。", error=True)
            return
        self.window.set_profiles(self.settings.providers, self.settings.active_provider, profile.id)
        self._refresh_key(profile)

    @Slot()
    def _add_provider(self) -> None:
        if not self.window.confirm_discard():
            return
        current = self.settings.active_profile()
        profile = ProviderProfile(
            id=create_provider_id("custom"),
            name="新供应商",
            base_url="https://",
            model=current.model,
            api_mode="responses",
            env_key="",
        )
        self.settings.providers.append(profile)
        try:
            self.config_store.save(self.settings)
        except OSError as exc:
            self.settings.providers.pop()
            self.settings.active_provider = current.id
            self.window.set_activity(f"新增失败：{exc}", error=True)
            return
        self.window.set_profiles(self.settings.providers, self.settings.active_provider, profile.id)
        self.window.set_key_source("missing")
        self.window.set_activity("已新增供应商，请填写连接信息后保存。")

    @Slot(str)
    def _delete_provider(self, provider_id: str) -> None:
        if len(self.settings.providers) <= 1:
            self.window.set_activity("至少需要保留一个供应商。", error=True)
            return
        remaining = [item for item in self.settings.providers if item.id != provider_id]
        if len(remaining) == len(self.settings.providers):
            return
        previous = copy.deepcopy(self.settings)
        try:
            self.settings.providers = remaining
            if self.settings.active_provider == provider_id:
                self.settings.active_provider = remaining[0].id
            self.config_store.save(self.settings)
        except (CredentialError, OSError) as exc:
            self.settings = previous
            self.window.set_activity(f"删除失败：{exc}", error=True)
            return
        try:
            self.credentials.delete_api_key(provider_id)
        except CredentialError:
            self.window.set_activity("配置已删除，但系统凭据清理失败。", error=True)
        profile = self.settings.active_profile()
        self.window.set_profiles(self.settings.providers, profile.id, profile.id)
        self._edit_provider(self.window._editing_id)
        if previous.active_provider == provider_id:
            self._active_configuration_changed()
        self.window.set_activity("供应商已删除。")

    @Slot(str)
    def _delete_key(self, provider_id: str) -> None:
        profile = self._find_provider(provider_id)
        if profile is None:
            return
        if self._credential_source(profile) == "environment":
            self.window.set_activity(
                f"当前密钥来自环境变量 {profile.env_key}，请在环境变量中删除。",
                error=True,
            )
            return
        try:
            self.credentials.delete_api_key(provider_id)
        except CredentialError as exc:
            self.window.set_activity(str(exc), error=True)
            return
        self._refresh_key(profile)
        self.window.set_activity("已清除当前供应商的 API key。")

    @Slot(str)
    def _import_codex(self, selected_path: str) -> None:
        previous = copy.deepcopy(self.settings)
        imported_count = 0
        secrets_before = {}
        try:
            imported = import_codex_config(Path(selected_path))
            profiles_by_id = {item.id: item for item in self.settings.providers}
            dialog = ImportPreview(imported.profiles, profiles_by_id, self.window)
            if not dialog.exec():
                return
            for incoming, operation in dialog.operations():
                if operation == "skip":
                    continue
                profile = copy.deepcopy(incoming)
                if profile.id in profiles_by_id:
                    if operation == "add":
                        profile.id = create_provider_id("import")
                    else:
                        old = profiles_by_id[profile.id]
                        for field in ("favorite", "models", "favorite_models", "timeout", "retries",
                                      "document_timeout", "document_concurrency", "document_rpm", "document_tpm"):
                            setattr(profile, field, copy.deepcopy(getattr(old, field)))
                secret = imported.secrets.get(incoming.id)
                if secret:
                    secrets_before[profile.id] = self.credentials.get_api_key(profile.id, "")
                    self.credentials.save_api_key(profile.id, secret)
                    profile.env_key = ""
                profiles_by_id[profile.id] = profile
                imported_count += 1
            self.settings.providers = list(profiles_by_id.values())
            self.config_store.save(self.settings)
        except (CodexConfigError, CredentialError, OSError) as exc:
            self.settings = previous
            for provider_id, secret in secrets_before.items():
                try:
                    if secret:
                        self.credentials.save_api_key(provider_id, secret)
                    else:
                        self.credentials.delete_api_key(provider_id)
                except CredentialError:
                    pass
            self.window.set_activity(f"导入失败：{exc}", error=True)
            return
        profile = self.settings.active_profile()
        self.window.set_profiles(self.settings.providers, profile.id)
        self._edit_provider(self.window._editing_id)
        self._active_configuration_changed()
        secret_note = "，其中的明文令牌已转存到 Windows 凭据管理器" if imported.secrets else ""
        self.window.set_activity(
            f"已从 Codex 导入 {imported_count} 个供应商{secret_note}。"
        )

    @Slot(str, str, bool)
    def _export_codex(
        self, provider_id: str, selected_path: str, include_key: bool
    ) -> None:
        profile = self._find_provider(provider_id)
        if profile is None:
            self.window.set_activity("没有找到要导出的供应商。", error=True)
            return
        api_key = ""
        if include_key:
            try:
                api_key = self.credentials.get_api_key(profile.id, profile.env_key)
            except CredentialError as exc:
                self.window.set_activity(f"导出失败：{exc}", error=True)
                return
            if not api_key:
                self.window.set_activity("当前供应商没有可导出的 API key。", error=True)
                return
        try:
            export_codex_config(Path(selected_path), profile, api_key=api_key)
        except CodexConfigError as exc:
            self.window.set_activity(f"导出失败：{exc}", error=True)
            return
        key_note = "（包含明文密钥）" if include_key else "（未包含密钥）"
        self.window.set_activity(f"Codex 配置已导出到 {selected_path} {key_note}。")

    def _find_provider(self, provider_id: str) -> ProviderProfile | None:
        return next(
            (item for item in self.settings.providers if item.id == provider_id),
            None,
        )

    @Slot(bool)
    def _set_paused(self, paused: bool) -> None:
        self.paused = paused
        self._reconcile_triggers()
        if not self._trigger_error:
            self.window.set_activity("普通翻译监听已暂停。" if paused else
                                     "游戏模式仍生效，仅保留区域翻译。" if self.game_active else "翻译监听已恢复。")

    @Slot()
    def quit(self, *, for_update=False) -> None:
        if not for_update and not self.window.confirm_discard():
            return
        self.documents.shutdown()
        self.game_monitor.stop()
        self._stop_ordinary()
        self.screen_reader.shutdown()
        self.input_hotkey.stop()
        self.hotkey.stop()
        self.right_click.stop()
        self.tray.hide()
        self.window.allow_close()
        self.window.close()
        self.overlay.close()
        self.action_button.close()
        self.application.quit()


def run() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("LinguaFloat")
    application.setOrganizationName("LinguaFloat")
    application.setWindowIcon(app_icon())
    application.setQuitOnLastWindowClosed(False)
    instance = None
    try:
        health_directory = None
        if "--update-health" in sys.argv:
            health_directory = Path(sys.argv[sys.argv.index("--update-health") + 1])
        if getattr(sys, "frozen", False) and health_directory is None:
            from linguafloat.update_install import pending_recovery
            import subprocess
            recovery = pending_recovery(Path(sys.executable).resolve().parent)
            if recovery and (recovery / "LinguaFloatUpdater.exe").is_file():
                subprocess.Popen([str(recovery / "LinguaFloatUpdater.exe"), "--job", str(recovery), "--recover"],
                                 cwd=recovery, creationflags=0x08000000)
                return 0
        instance = SingleInstance(parent=application)
        if not instance.start():
            return 0
        controller = AppController(application)
        instance.activation_requested.connect(controller.window.bring_to_front)
        controller.start()
        if health_directory is not None:
            from linguafloat.update_install import mark_healthy
            QTimer.singleShot(2000, lambda: mark_healthy(health_directory, __version__, Path(sys.executable).resolve().parent))
        application.aboutToQuit.connect(controller.hotkey.stop)
        application.aboutToQuit.connect(controller.input_hotkey.stop)
        application.aboutToQuit.connect(controller.right_click.stop)
        application.aboutToQuit.connect(controller.game_monitor.stop)
        application.aboutToQuit.connect(controller.documents.shutdown)
        return application.exec()
    except (SingleInstanceError, OSError) as exc:
        QMessageBox.warning(None, "LinguaFloat 启动失败", str(exc))
        return 1
    finally:
        if instance is not None:
            instance.close()
