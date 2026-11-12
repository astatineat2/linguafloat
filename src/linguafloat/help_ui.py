"""Local first-use guide and asynchronous public-release updates."""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
                              QProgressBar, QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from linguafloat import __version__
from linguafloat.workbench import AppDialog, MessageBox as QMessageBox
from linguafloat.update_core import (UpdateError, atomic_json, check_latest, download_release,
                                    load_channel, parse_manifest, update_directory)


GUIDE = """
<h2>开始使用 LinguaFloat</h2>
<p>解压完整便携 ZIP 后启动 <b>LinguaFloat.exe</b>。无需安装 Python；不要移动或删除
<code>_internal</code>、<code>document-runtime</code> 和更新助手。</p>
<h3>1 · 添加翻译服务</h3>
<p>首次启动可在“配置翻译服务”单页选择服务、填写 API Key 和模型，点击“完成并保存”后启用；
也可“跳过，稍后配置”，后续从“配置设置 → 添加供应商”继续添加。保存和跳过后不会再次自动弹出。</p>
<p>打开“配置设置 → 添加供应商 → 自定义”，填写供应商、接口地址、API Key、模型，并按供应商文档选择
Responses、Chat Completions、Anthropic Messages 或 Gemini generateContent。保存后，在卡片或“功能设置”明确启用该配置。</p>
<p>配置页直接显示全部供应商卡片，可搜索名称、地址和模型。右上方“数据管理”与“添加供应商”并排，数据管理页可返回全部配置。
点击“数据管理 → 选择 SQL 文件”，一次导入文件中全部类别的兼容配置，无需逐条勾选。
重复项跳过，缺失项在结果详情中点击“补全”。本机配置直接读取入口已移除，使用 CC Switch 导出的 SQL 文件交换。
导入不自动启用，不测试连接。网页登录、托管凭据或不支持的协议会明确说明限制。</p>
<p>“导出 SQL 备份”仅包含兼容供应商和必要结构，目标格式为 CC Switch SQL。
目标端会替换整库，请先备份。默认不包含密钥，主动勾选“包含 API Key”才写入明文密钥。
官方程序隔离恢复尚未实测。OpenCode 官方模型服务可从“添加供应商”菜单配置。</p>
<p>点击“测试连接”会发送一次真实短文本请求，可能产生 API 费用。首次启动不会自动测试、
翻译或上传文件。请确认供应商的隐私政策。</p>
<h3>2 · 选择翻译方式</h3>
<p>快捷键框点击后直接按组合键录入，Esc 取消，录入后点击底部“保存功能设置”。冲突会显示原因，不替换旧快捷键。
区域框四角与四边中点缩放，其余绿色边框拖动平移；点击浮窗“移动识别区域”可在框内拖动。
框选稳定约 1 秒后开始 OCR；译文分段显示。浮窗计时图标可调整识别与翻译最短间隔，设置同步到主界面。
首次 OCR 初始化和供应商响应仍需时间；分段请求可能比整段请求增加提示词开销。</p>
<p><b>选中翻译：</b>选中文字后按 Ctrl+Alt+T，或使用右键翻译入口。<br>
<b>输入翻译：</b>默认快捷键 Ctrl+Alt+Y，可在功能设置修改。<br>
<b>区域翻译：</b>选择屏幕区域，本地 OCR 后发送文字翻译；可暂停、继续、移动和缩放。
游戏模式仅保留区域翻译。<br>
<b>文件翻译：</b>添加 PDF、Word 或 PPTX，选择输出目录和输出选项，再开始。
文件翻译跟随当前供应商及模型；已启动任务保留原配置快照。</p>
<h3>3 · PDF 与 OCR 资源</h3>
<p>屏幕 OCR 模型随完整包提供。首次 PDF 处理可能需要联网下载缺失的布局模型、字体等资源，
具体大小取决于本机缺失项。资源下载与翻译 API 计费是两件事；下载资源本身不调用翻译模型。
遇到下载失败请检查网络或代理，不要删除原文件。PDF 默认保留版式；不会自动改成阅读版。</p>
<h3>设置、密钥与结果</h3>
<p>设置：<code>%APPDATA%\\LinguaFloat\\config.json</code><br>
任务与缓存：<code>%APPDATA%\\LinguaFloat\\documents</code><br>
PDF 资源：<code>%USERPROFILE%\\.cache\\babeldoc</code><br>
API Key 使用 Windows 凭据管理器或指定的环境变量，不放进软件发布包。
输出文件保存在你选择的目录。移动软件目录不会迁移密钥；换电脑通常需重新配置。</p>
<h3>常见问题</h3>
<p><b>快捷键无效：</b>检查是否被其他程序占用，并查看监听错误。<br>
<b>API 调用失败：</b>核对地址、接口模式、模型权限和账户余额；具体错误显示在界面中。<br>
<b>无法保存：</b>选择当前用户可写的输出目录。<br>
<b>OCR 或 PDF 资源缺失：</b>使用完整便携包，并检查资源下载网络。<br>
<b>系统提示未知发布者：</b>当前版本未做 Windows 程序签名，请从项目正式发布页下载并核对校验值；
不需要关闭安全软件。</p>
<h3>更新</h3>
<p>默认每天首次启动检查公开稳定版，可关闭。检查只访问 GitHub 发布信息，不上传文件、配置或密钥。
下载由你确认；安装等待任务安全结束。更新失败会尝试回退，旧包和恢复记录会保留。</p>
"""


class HelpDialog(AppDialog):
    check_requested = Signal()
    download_requested = Signal()
    install_requested = Signal()
    cancel_download_requested = Signal()
    auto_changed = Signal(bool)
    guide_closed = Signal()
    configure_requested = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"LinguaFloat v{__version__} · 帮助与更新")
        self.setMinimumSize(620, 460)
        self.resize(740, 600)
        self.finished.connect(lambda _: self.guide_closed.emit())
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        updates = QWidget()
        body = QVBoxLayout(updates)
        body.addWidget(QLabel(f"当前版本：v{__version__} · Windows x64"))
        self.auto_check = QCheckBox("每天首次启动检查更新")
        self.auto_check.setChecked(settings.update_auto_check)
        self.auto_check.toggled.connect(self.auto_changed)
        body.addWidget(self.auto_check)
        self.status = QLabel("可手动检查公开稳定版；不会上传文档或 API Key。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        body.addWidget(self.status)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(False)
        body.addWidget(self.notes, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.hide()
        body.addWidget(self.progress)
        actions = QHBoxLayout()
        self.check_button = QPushButton("检查更新")
        self.check_button.setObjectName("primary")
        self.download_button = QPushButton("下载更新")
        self.download_button.setEnabled(False)
        self.install_button = QPushButton("安装并重启")
        self.install_button.setEnabled(False)
        self.cancel_button = QPushButton("暂停下载")
        self.cancel_button.setEnabled(False)
        self.check_button.clicked.connect(self.check_requested)
        self.download_button.clicked.connect(self.download_requested)
        self.install_button.clicked.connect(self.install_requested)
        self.cancel_button.clicked.connect(self.cancel_download_requested)
        for button in (self.check_button, self.download_button, self.install_button, self.cancel_button):
            actions.addWidget(button)
        body.addLayout(actions)
        self.tabs.addTab(updates, "软件更新")
        self.guide = QTextBrowser()
        notice_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
        notice_url = QUrl.fromLocalFile(str(notice_root / "THIRD_PARTY_NOTICES.md")).toString()
        license_url = QUrl.fromLocalFile(str(notice_root / "LICENSE")).toString()
        self.guide.setHtml(GUIDE + '<p style="color:#66717d;font-size:small">LinguaFloat：AGPL-3.0-only，可依许可再分发，无担保。'
                          f'<a href="{license_url}">许可证</a> · <a href="{notice_url}">开源致谢与第三方声明</a> · '
                          '<a href="https://github.com/astatineat2/linguafloat-releases/releases">公开版本对应源码</a></p>')
        self.guide.setOpenLinks(False)
        self.guide.anchorClicked.connect(QDesktopServices.openUrl)
        configure = QPushButton("打开配置设置")
        configure.clicked.connect(self.configure_requested)
        body_guide = QWidget()
        guide_layout = QVBoxLayout(body_guide)
        guide_layout.addWidget(self.guide, 1)
        guide_layout.addWidget(configure, alignment=Qt.AlignmentFlag.AlignLeft)
        self.tabs.addTab(body_guide, "首次使用指南")
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def closeEvent(self, event):
        super().closeEvent(event)


class TaskSignals(QObject):
    success = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)


class UpdateTask(QRunnable):
    def __init__(self, action):
        super().__init__()
        self.action = action
        self.signals = TaskSignals()

    def run(self):
        try:
            with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=True,
                              headers={"User-Agent": f"LinguaFloat/{__version__}", "Accept-Encoding": "identity"}) as client:
                self.signals.success.emit(self.action(client, self.signals.progress.emit))
        except Exception as exc:
            if isinstance(exc, UpdateError):
                message = str(exc)
            elif isinstance(exc, (httpx.HTTPError, OSError)):
                message = "无法连接更新服务或写入下载目录，请检查网络、代理和目录权限。"
            else:
                message = "更新信息无法处理，当前程序未更改；可稍后重试。"
            self.signals.failed.emit(message)


class HelpController(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.dialog = HelpDialog(app.settings, app.window)
        self.release = None
        self.busy = False
        self.install_pending = False
        self.cancelled = threading.Event()
        self.job = None
        self.last_manual = 0.0
        self.started = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._try_install)
        self.dialog.check_requested.connect(lambda: self.check(manual=True))
        self.dialog.download_requested.connect(self.download)
        self.dialog.install_requested.connect(self.install)
        self.dialog.cancel_download_requested.connect(self.cancel_action)
        self.dialog.auto_changed.connect(self._auto_changed)
        self.dialog.configure_requested.connect(self._configure)

    def _configure(self):
        self.dialog.close()
        self.app.window.pages.setCurrentIndex(1)
        self.app.window.bring_to_front()

    def cancel_action(self):
        if not self.install_pending:
            self.cancelled.set()
            return
        self.timer.stop()
        self.install_pending = False
        self.app._update_waiting = False
        self.app.documents.update_pending = False
        self.app.window.documents.set_busy(self.app.window.documents.busy)
        self.dialog.cancel_button.setText("暂停下载")
        self.dialog.cancel_button.setEnabled(False)
        self.dialog.check_button.setEnabled(True)
        self.dialog.install_button.setEnabled(True)
        self.dialog.status.setText("已取消安装等待；当前翻译任务继续运行。")

    def start(self):
        if not self.app.settings.first_run_complete:
            self.app._show_first_run()
            return
        if self.app.settings.update_auto_check:
            QTimer.singleShot(4000, self.check)

    def show(self, *, guide=False):
        self.dialog.tabs.setCurrentIndex(1 if guide else 0)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _auto_changed(self, enabled):
        self.app.settings.update_auto_check = enabled
        self.app.config_store.save(self.app.settings)

    def check(self, *, manual=False):
        if self.busy or self.install_pending:
            return
        state_file = update_directory() / "last-check.json"
        today = dt.date.today().isoformat()
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if not manual and (not self.app.settings.update_auto_check or state.get("date") == today):
            return
        if manual and time.monotonic() - self.last_manual < 30:
            self.dialog.status.setText("刚刚已检查，请稍后再试。")
            return
        try:
            channel = load_channel()
            atomic_json(state_file, {"date": today})
        except (UpdateError, OSError) as exc:
            self.dialog.status.setText(str(exc))
            return
        self.last_manual = time.monotonic()
        self.release = None
        self.dialog.install_button.setEnabled(False)
        self.dialog.download_button.setEnabled(False)
        self.dialog.status.setText("正在检查公开稳定版……")
        self._task(lambda client, progress: check_latest(client, channel, __version__, update_directory() / "release-cache.json"), self._checked)

    def _task(self, action, success):
        self.busy = True
        self.dialog.check_button.setEnabled(False)
        task = UpdateTask(action)
        task.signals.success.connect(lambda value: self._finished(success, value))
        task.signals.failed.connect(self._failed)
        task.signals.progress.connect(self._progress)
        self._active_task = task
        QThreadPool.globalInstance().start(task)

    def _finished(self, callback, value):
        self.busy = False
        self.dialog.check_button.setEnabled(True)
        self.dialog.cancel_button.setEnabled(False)
        callback(value)

    def _failed(self, message):
        self.busy = False
        self.dialog.check_button.setEnabled(True)
        self.dialog.cancel_button.setEnabled(False)
        self.dialog.download_button.setEnabled(self.release is not None)
        self.dialog.status.setText(message)

    def _checked(self, release):
        self.release = release
        if release is None:
            self.dialog.status.setText("当前已是最新稳定版。")
            self.dialog.notes.clear()
            return
        self.dialog.status.setText(f"发现新版 v{release.version} · 完整包约 {release.size / 1024**2:.1f} MiB")
        self.dialog.notes.setPlainText(release.notes)
        self.dialog.download_button.setEnabled(True)
        self.app.window.help_button.setText("帮助 · 有更新")

    def download(self):
        if self.busy or self.release is None:
            return
        self.cancelled.clear()
        self.started = time.monotonic()
        self.job = update_directory() / ("v" + self.release.version + "-" + self.release.sha256[:12])
        self.dialog.download_button.setEnabled(False)
        self.dialog.cancel_button.setEnabled(True)
        self.dialog.progress.show()
        release = self.release
        self._task(lambda client, progress: download_release(client, release, self.job, progress, self.cancelled), self._downloaded)

    def _progress(self, done, total):
        self.dialog.progress.setValue(round(1000 * done / total))
        self.dialog.status.setText(f"已下载 {done / 1024**2:.1f} / {total / 1024**2:.1f} MiB；下载完成后校验完整包。")

    def _downloaded(self, path):
        self.dialog.progress.setValue(1000)
        self.dialog.status.setText("下载与校验完成。安装会等待当前任务安全结束，并保留旧版备份。")
        self.dialog.install_button.setEnabled(True)

    def install(self):
        if not self.job or self.busy or self.install_pending:
            return
        if not getattr(sys, "frozen", False):
            self.dialog.status.setText("源码运行模式不能替换程序，请在完整便携版中安装更新。")
            return
        if not self.app.window.confirm_discard():
            return
        if QMessageBox.question(self.dialog, "安装更新", "安装时会关闭并重启 LinguaFloat。正在运行的任务会继续完成；暂停中的任务需先继续或自行取消。是否等待并安装？",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.install_pending = True
        self.dialog.install_button.setEnabled(False)
        self.dialog.check_button.setEnabled(False)
        self.app._update_waiting = True
        self.app.documents.update_pending = True
        self.app.window.documents.start_button.setEnabled(False)
        self.app.window.documents.trial_button.setEnabled(False)
        self.dialog.cancel_button.setText("取消安装等待")
        self.dialog.cancel_button.setEnabled(True)
        self.dialog.status.setText("等待当前任务安全结束；请结束持续区域翻译。关闭此窗口不取消等待。")
        self.timer.start()
        self._try_install()

    def _try_install(self):
        reader = self.app.screen_reader
        if (self.app.documents.process is not None or self.app.documents.queue or self.app._inflight_requests
                or reader.state.running or reader.ocr_job is not None or reader.state.pending
                or self.app.thread_pool.activeThreadCount() or self.app.capture._pending or self.app._input_pending):
            return
        self.timer.stop()
        try:
            target = Path(sys.executable).resolve().parent
            helper = target / "LinguaFloatUpdater.exe"
            if not helper.is_file():
                raise UpdateError("更新助手缺失，请从发布页下载完整便携包。")
            cache = json.loads((update_directory() / "release-cache.json").read_text(encoding="utf-8"))
            payload, signature = base64.b64decode(cache["payload"]), cache["signature"].encode("ascii")
            release = parse_manifest(payload, signature, load_channel(), __version__)
            if release != self.release:
                raise UpdateError("更新信息已变化，请重新检查。")
            (self.job / "update.json").write_bytes(payload)
            (self.job / "update.json.sig").write_bytes(signature)
            atomic_json(self.job / "request.json", {"target": str(target), "current": __version__,
                        "parent_pid": os.getpid(), "nonce": uuid.uuid4().hex})
            atomic_json(self.job / "state.json", {"phase": "prepared", "target": str(target)})
            outside_helper = self.job / "LinguaFloatUpdater.exe"
            shutil.copy2(helper, outside_helper)
            subprocess.Popen([str(outside_helper), "--job", str(self.job)], cwd=self.job, creationflags=0x08000000)
        except Exception as exc:
            self.install_pending = False
            self.app._update_waiting = False
            self.app.documents.update_pending = False
            self.app.window.documents.set_busy(False)
            self.dialog.check_button.setEnabled(True)
            self.dialog.install_button.setEnabled(True)
            self.dialog.status.setText(str(exc) if isinstance(exc, (UpdateError, OSError)) else "无法启动更新助手，当前程序未更改。")
            return
        self.app.quit(for_update=True)
