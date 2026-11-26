"""Independent review queue: translation output is already saved when this starts."""
import json
from pathlib import Path

from PySide6.QtCore import QProcess, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (QCheckBox, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSizePolicy, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
import qtawesome as qta

from linguafloat.document_review import AI_REQUESTS, AI_OUTPUT_LIMIT
from linguafloat.theme import section_heading
from linguafloat.workbench import AppDialog


ISSUES = dict(retained='整段保留原文', missing='疑似缺段或移位', comparison='比较符变化',
              negation='否定', condition='条件', numbers='数值或单位', terminology='术语',
              claim='结论强度', omission='语义遗漏', undrawn='译后疑似未落版')
ISSUES.update({'layout-overlap': '文字重叠或重复', 'layout-clip': '疑似文字裁切',
               'layout-reference': '参考文献复制异常', 'layout-link': '参考文献链接异常'})
STATES = dict(confirmed='已证实', suspected='疑似')


def review_target(info):
    config = info.get('configuration', {})
    if info.get('workflow', config.get('workflow')) == 'graphics':
        return None
    if config.get('source_language', 'en') != 'en' or config.get('target', 'zh-Hans') != 'zh-Hans':
        return None
    return next((p for p in map(Path, info.get('outputs', ()))
                 if p.name.endswith('.双语.pdf') and p.is_file()), None)


class ReviewPreview(QLabel):
    def __init__(self, document):
        super().__init__('未能预览此 PDF')
        self.document, self.current_page = document, -1
        self.image = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(200, 150)
        self.setStyleSheet('background: #e5e7ee;')

    def show_page(self, index):
        if index == self.current_page or not 0 <= index < self.document.pageCount():
            return
        self.current_page = index
        size = self.document.pagePointSize(index).toSize().scaled(QSize(1480, 1100), Qt.AspectRatioMode.KeepAspectRatio)
        self.image = QPixmap.fromImage(self.document.render(index, size))
        self._fit()

    def _fit(self):
        if self.image.isNull():
            self.setText('未能预览此 PDF')
        else:
            self.setPixmap(self.image.scaled(self.size() - QSize(12, 12), Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class ReviewDialog(AppDialog):
    def __init__(self, parent, filename, dual, report, ai):
        super().__init__(parent)
        self.setWindowTitle('译后校对')
        self.resize(1000, 670)
        layout = QVBoxLayout(self)
        title = QLabel(filename)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        summary = QLabel(self.summary(report, ai))
        summary.setWordWrap(True)
        layout.addWidget(summary)
        split = QSplitter()
        left = QWidget()
        controls = QVBoxLayout(left)
        controls.setContentsMargins(0, 0, 0, 0)
        self.issues = QTreeWidget()
        self.issues.setHeaderLabels(['原页', '问题', '状态'])
        self.issues.setRootIsDecorated(False)
        self.issues.setAlternatingRowColors(True)
        self.issues.setColumnWidth(0, 56)
        self.issues.setColumnWidth(1, 154)
        controls.addWidget(self.issues, 2)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText('没有已报告问题。未检查的页和段落不计为通过。')
        controls.addWidget(self.detail, 3)
        split.addWidget(left)
        self.pdf = QPdfDocument(self)
        self.pdf.load(str(dual))
        self.preview = ReviewPreview(self.pdf)
        self.preview.show_page(0)
        split.addWidget(self.preview)
        split.setSizes([380, 570])
        layout.addWidget(split, 1)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        actions.rejected.connect(self.reject)
        open_file = actions.addButton('打开双语 PDF', QDialogButtonBox.ButtonRole.ActionRole)
        open_file.setIcon(qta.icon('mdi6.open-in-new'))
        open_file.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(dual))))
        layout.addWidget(actions)
        for row in sorted([*report.get('issues', []), *ai.get('issues', [])], key=lambda r: r['source_page']):
            item = QTreeWidgetItem([str(row['source_page']), ISSUES.get(row['kind'], row['kind']), STATES[row['status']]])
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            self.issues.addTopLevelItem(item)
        self.issues.currentItemChanged.connect(self.show_issue)
        self.issues.setCurrentItem(self.issues.topLevelItem(0))

    @staticmethod
    def summary(report, ai):
        skipped = report.get('unchecked_pages', [])
        text = (f"本地：已扫描 {len(report.get('checked_pages', []))} 页；未检查 {len(skipped)} 页；"
                f"按范围排除 {len(report.get('excluded_pages', []))} 页。")
        if skipped:
            text += ' 未检查原页：' + '、'.join(map(str, skipped[:30])) + ('…' if len(skipped) > 30 else '') + '。'
        labels = dict(disabled='未启用', unavailable='需要可靠的任务记录、配对底稿和 HTTPS API', pending='正在运行',
                      no_candidates='没有可靠配对的候选段', cancelled='已取消，已发请求仍可能计费', error='未完成')
        if 'layout_checked_pages' in report:
            not_checked = report['layout_unchecked_pages']
            text += (f"\n本地版面：检查 {len(report['layout_checked_pages'])} 页；未检查 {len(not_checked)} 页；"
                     f"参考文献原位文字与链接对照 {len(report['layout_reference_checked_pages'])} 页。")
            if not_checked:
                text += ' 未检查原页：' + '、'.join(map(str, not_checked[:30])) + '。'
            text += ' 检查文字重叠、重复和边界，不替代人工看图。'
        text += '\nAI 语义：' + labels.get(ai.get('state', 'disabled'), '已返回') + '。'
        if ai.get('requests'):
            text += (f" 已检查 {ai['reviewed']} 段；候选中未检查 {ai['unchecked']} 段；"
                     f"请求 {ai['requests']} 次，已报告 Token {ai['input_tokens']} 输入 / {ai['output_tokens']} 输出。")
            if ai.get('usage_unreported'):
                text += f" {ai['usage_unreported']} 次用量未知。"
        return text + ' 未送审段落未经过 AI 校对。'

    def show_issue(self, item, _previous):
        if item is None:
            return
        row = item.data(0, Qt.ItemDataRole.UserRole)
        self.detail.setPlainText(f"原始第 {row['source_page']} 页 · 双语 PDF 第 {row['pdf_page']} 页\n\n"
                                 f"原文\n{row['source']}\n\n译文或保留状态\n{row['target'] or '未找到对应文字'}\n\n"
                                 f"证据\n{row['evidence']}\n\n建议\n"
                                 f"{row.get('suggestion') or '对照右侧 PDF 的译文面，复核这一段。'}")
        self.preview.show_page(row['pdf_page'] - 1)


class ReviewPanel(QWidget):
    def __init__(self, page, worker_command):
        super().__init__(page)
        self.page, self.worker_command = page, worker_command
        self.process, self.latest, self.active = None, None, None
        self.queue, self.reports = [], {}
        self.ai_key = lambda: None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_heading('译后校对', 'mdi6.clipboard-check-outline'))
        row = QHBoxLayout()
        self.ai = QCheckBox('AI 校对')
        self.ai.setToolTip(f'使用当前翻译 API 发送必要的原译片段。每篇最多 {AI_REQUESTS} 次请求，'
                           f'每次最多 {AI_OUTPUT_LIMIT} 输出 Token；可能产生费用。不发送 PDF。')
        row.addWidget(self.ai)
        row.addStretch(1)
        self.open = QPushButton('查看校对')
        self.open.setIcon(qta.icon('mdi6.clipboard-text-search-outline'))
        self.open.clicked.connect(self.open_selected)
        row.addWidget(self.open)
        self.run_ai = QPushButton('补做 AI 校对')
        self.run_ai.clicked.connect(self.review_selected_with_ai)
        self.ai.toggled.connect(self.refresh)
        row.addWidget(self.run_ai)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    def refresh(self):
        identity = self.page._export_id or self.latest
        report = self.reports.get(identity)
        self.run_ai.setVisible(bool(self.ai.isChecked() and report and report.get('local') is not None
                                    and report['ai']['state'] in {'disabled', 'error', 'cancelled', 'unavailable'}))
        self.run_ai.setEnabled(self.process is None and identity in self.page._export_records)
        self.open.setEnabled(bool(report) or (identity in self.page._export_records and self.process is None))
        if report and report.get('local') is not None:
            self.open.setText('查看校对')
            rows = report['local']['issues']
            layout = sum(i['kind'].startswith('layout-') for i in rows)
            ai = report['ai']
            states = {'pending': '校对中', 'disabled': '未启用', 'unavailable': '未检查', 'no_candidates': '未送审',
                      'error': '未完成', 'cancelled': '已取消'}
            ai_status = states.get(ai['state'], f"已检查 {ai.get('reviewed', 0)} 段")
            self.status.setText(f"本地 · 正文覆盖 {len(rows)-layout} 项，版面 {layout} 项 ｜ AI 语义 · {ai_status}")
        else:
            self.open.setText('校对选中结果')
            self.status.setText('正在检查已保存的 PDF…' if self.process else
                                (report.get('error', '') if report else ''))

    def enqueue(self, identity, source, info, *, allow_ai=True):
        dual = review_target(info)
        if dual is None or not Path(source).is_file():
            return False
        self.latest = identity
        profile = getattr(self.page, '_review_profile', None) if allow_ai and self.ai.isChecked() else None
        self.queue.append((identity, source, dual, info.get('graphics_manifest_sha256', ''),
                           info.get('review_layout_sha256', ''), profile))
        if self.process is None:
            self.start_next()
        return True

    def open_selected(self):
        identity = self.page._export_id or self.latest
        report = self.reports.get(identity)
        if report and report.get('local') is not None:
            ReviewDialog(self, report['name'], report['dual'], report['local'], report['ai']).exec()
            return
        source = self.page._review_sources.get(identity, '')
        if not source or not self.enqueue(identity, source, self.page.export_result_info(identity)):
            self.status.setText('无法校对：需要原 PDF 和可用的英译简中双语 PDF。')

    def review_selected_with_ai(self):
        identity = self.page._export_id or self.latest
        if self.process is None and self.ai.isChecked() and (source := self.page._review_sources.get(identity)):
            if not self.enqueue(identity, source, self.page.export_result_info(identity)):
                self.status.setText('无法校对：需要原 PDF 和可用的英译简中双语 PDF。')

    def start_next(self):
        if not self.queue:
            self.refresh()
            return
        identity, source, dual, manifest_hash, layout_hash, profile = self.queue.pop(0)
        self.active = identity
        report = dict(name=Path(source).name, dual=dual, local=None, ai={'state': 'disabled'})
        self.reports[identity] = report
        self.process = process = QProcess(self)
        self.refresh()
        request = dict(operation='review_result', source=source, dual=str(dual), identity=identity,
                       manifest_hash=manifest_hash, layout_hash=layout_hash)
        if profile and profile == getattr(self.page, '_review_profile', None) and self.ai.isChecked():
            if credentials := self.ai_key():
                request['provider'], request['api_key'] = credentials
            else:
                request['ai_without_key'] = True
        python, entry = self.worker_command()
        process.setProgram(str(python))
        process.setArguments(['-X', 'utf8', str(entry)])
        pending = bytearray()

        def read():
            pending.extend(process.readAllStandardOutput().data())
            while b'\n' in pending:
                line, _, rest = pending.partition(b'\n')
                pending[:] = rest
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if event.get('type') == 'review_local':
                    report['local'] = event['report']
                    report['ai'] = {'state': event['ai']}
                elif event.get('type') == 'review_ai':
                    report['ai'] = event['report']
                elif event.get('type') == 'review_error':
                    report['error'] = event.get('message', '校对未完成。')
                self.refresh()

        def done(*_):
            if self.process is not process:
                return
            read()
            if report['ai']['state'] == 'pending':
                report['ai']['state'] = 'cancelled' if report.get('cancelled') else 'error'
            if report['local'] is None:
                report.setdefault('error', '校对已取消。' if report.get('cancelled') else '校对未完成，PDF 未改动。')
            self.process, self.active = None, None
            process.deleteLater()
            self.start_next()

        def started():
            process.write((json.dumps(request) + '\n').encode())
            process.closeWriteChannel()
            request.pop('api_key', None)

        process.started.connect(started)
        process.readyReadStandardOutput.connect(read)
        process.finished.connect(done)
        process.errorOccurred.connect(lambda err: done() if err == QProcess.ProcessError.FailedToStart else None)
        process.start()

    def cancel_active(self):
        if self.process:
            self.reports[self.active]['cancelled'] = True
            self.process.kill()

    def shutdown(self):
        self.queue.clear()
        if self.process:
            process = self.process
            self.cancel_active()
            process.waitForFinished(2000)
