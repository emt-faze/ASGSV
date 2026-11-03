"""Choose export scope/format and save without blocking the generator window."""
from datetime import datetime
import os
from pathlib import Path

from PySide6 import QtCore, QtWidgets
from dialogue_export import DialogueExportTask, ExportCancelled, FORMATS
from generator_ui_support import configure_dialog, set_help


class ExportWorker(QtCore.QThread):
    message = QtCore.Signal(str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task, self.report, self.error = task, None, ''

    def run(self):
        try:
            self.report = self.task.run()
        except ExportCancelled as exc:
            self.error = str(exc)
        except Exception as exc:
            self.error = '导出未完成：'+str(exc)


class DialogueExportDialog(QtWidgets.QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project, self.worker, self.result_report = project, None, None
        self.closing = False
        self.setWindowTitle('导出台词')
        self.resize(650, 380)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('按当前列表顺序导出台词，保留角色名和换行。\n'
                               '选择 ZIP 可同时带走已有配音；未生成的台词也会保留文字。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.form_widget = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.form_widget)
        selected = sum(row.selected for row in project.rows)
        self.scope = QtWidgets.QComboBox()
        self.scope.addItem(f'全部台词（{len(project.rows)} 条）', False)
        self.scope.addItem(f'仅左侧勾选项（{selected} 条）', True)
        self.format = QtWidgets.QComboBox()
        for key, label in FORMATS.items():
            self.format.addItem(label, key)
        form.addRow('导出范围', self.scope)
        form.addRow('文件格式', self.format)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)
        layout.addWidget(self.form_widget)
        self.scope.currentIndexChanged.connect(self.refresh)
        self.format.currentIndexChanged.connect(self.refresh)
        self.status = QtWidgets.QLabel('点击“选择位置并导出…”保存到你指定的文件夹。')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.status, 1)
        buttons = QtWidgets.QHBoxLayout()
        self.save_button = QtWidgets.QPushButton('选择位置并导出…')
        self.save_button.clicked.connect(self.choose_destination)
        self.stop_button = QtWidgets.QPushButton('取消导出')
        self.stop_button.clicked.connect(self.cancel)
        self.stop_button.setEnabled(False)
        self.open_button = QtWidgets.QPushButton('打开导出文件夹')
        self.open_button.clicked.connect(self.open_folder)
        self.open_button.setEnabled(False)
        close = QtWidgets.QPushButton('关闭')
        close.clicked.connect(self.close)
        for button in (self.save_button, self.stop_button, self.open_button, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.refresh()
        configure_dialog(self, 'export')
        set_help(self.scope, '选择导出整个列表，或只导出主窗口左侧打勾的台词。\n高亮的当前行不决定导出范围。', '导出范围')
        set_help(self.format, 'TXT 适合阅读；CSV 可用 Excel 打开；ZIP 同时包含文字清单和已有有效配音。\n需要把配音一起带到另一台电脑时请选择 ZIP。', '文件格式')

    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def refresh(self, *_):
        rows = [row for row in self.project.rows if not self.scope.currentData() or row.selected]
        pending = sum(bool(row.review_reason()) for row in rows)
        hints = {'txt': 'TXT 适合阅读、复制文字。',
                 'csv': 'CSV 可用 Excel 打开；配音文件列是原项目内的相对路径。',
                 'zip': 'ZIP 包含台词、清单和可用的 WAV 配音；缺少或过期的音频会在清单中标明。'}
        self.summary.setText(f'将导出 {len(rows)} 条，其中 {pending} 条文字待核对。\n'+hints[self.format.currentData()])
        self.save_button.setEnabled(bool(rows) and not self.busy())

    def choose_destination(self):
        if self.busy():
            return
        format = self.format.currentData()
        scope = '勾选项' if self.scope.currentData() else '全部'
        folder = self.project.directory/'exports'
        try:
            folder.mkdir(exist_ok=True)
            default = folder/f'台词_{scope}_{datetime.now():%Y%m%d-%H%M%S}.{format}'
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, '保存导出台词', str(default),
                FORMATS[format]+f' (*.{format})')
            if path:
                destination = Path(path)
                if not destination.suffix:
                    destination = destination.with_suffix('.'+format)
                self.start_export(destination)
        except (OSError, ValueError) as exc:
            self.status.setText('无法保存到这个位置：'+str(exc))

    def start_export(self, path):
        if self.busy():
            return
        try:
            task = DialogueExportTask(self.project, path, self.format.currentData(), selected_only=self.scope.currentData())
            task.validate_target()
            self.worker = ExportWorker(task, self)
            task.progress = self.worker.message.emit
            self.worker.message.connect(self.status.setText)
            self.worker.finished.connect(self.finished_task)
            self.form_widget.setEnabled(False)
            self.save_button.setEnabled(False)
            self.open_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.status.setText('正在导出台词…')
            self.worker.start()
        except (OSError, ValueError) as exc:
            self.status.setText('无法开始导出：'+str(exc))

    def finished_task(self):
        report = self.worker.report
        if report:
            self.result_report = report
            extra = f"，包含 {report['audio']} 条配音，{report['missing_audio']} 条暂无可用配音" if report['format']=='zip' else ''
            self.status.setText(f"已导出 {report['rows']} 条台词{extra}。\n保存位置：{report['path']}")
        else:
            self.status.setText(self.worker.error)
        self.form_widget.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.open_button.setEnabled(bool(self.result_report))
        self.refresh()
        if self.closing:
            self.close()

    def cancel(self):
        if self.busy():
            self.worker.task.cancel()
            self.stop_button.setEnabled(False)
            self.status.setText('正在取消导出…')

    def open_folder(self):
        if self.result_report:
            os.startfile(Path(self.result_report['path']).parent)

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.busy():
            self.closing = True
            self.cancel()
            event.ignore()
        else:
            event.accept()
