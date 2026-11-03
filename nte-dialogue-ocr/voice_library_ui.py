"""Prepare, audition, correct, and explicitly publish a batch of voice drafts."""
from pathlib import Path
import threading

from PySide6 import QtCore, QtGui, QtWidgets
from generator_ui_support import configure_dialog

from dialogue_generation import atomic_text
from generator_voices import VoiceImportTask, ImportCancelled
from voice_library import (VoiceLibraryDraft, BatchVoiceProfileBuilder, scan_character_audio_groups,
                           sha256, check_cancel, VIDEO_EXTENSIONS, validate_video_clip)

STATES = {'pending': '待准备', 'ready': '草稿待核对', 'imported': '已保存声线',
          'skipped': '已有声线，已跳过', 'failed': '准备失败，可重试'}


def clip_time(seconds):
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:05.2f}'


class VideoClipDialog(QtWidgets.QDialog):
    def __init__(self, row=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('从视频选取角色声线')
        self.resize(820, 340)
        self.result_values = None
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('选择视频，填写这段中说话的角色，并选取 3–30 秒的完整一句。\n'
            '可打开原视频查看时间；准备草稿后在批量窗口试听、核对文字，再保存声线。')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QtWidgets.QFormLayout()
        self.speaker = QtWidgets.QLineEdit(row['speaker'] if row else '')
        self.speaker.setPlaceholderText('填写这段声音对应的角色名')
        self.aliases = QtWidgets.QLineEdit('，'.join(row['aliases']) if row else '')
        self.aliases.setPlaceholderText('可选，多个别名用逗号分隔')
        form.addRow('角色名', self.speaker)
        form.addRow('别名', self.aliases)
        self.source = QtWidgets.QComboBox()
        self.source.setEditable(True)
        self.source.setMinimumWidth(360)
        self.source.addItems([p for p in row['files'] if Path(p).suffix.lower() in VIDEO_EXTENSIONS] if row else [])
        media = QtWidgets.QHBoxLayout()
        media.addWidget(self.source, 1)
        browse = QtWidgets.QPushButton('选择视频…')
        browse.clicked.connect(self.browse)
        media.addWidget(browse)
        original = QtWidgets.QPushButton('打开原视频')
        original.clicked.connect(self.open_original)
        media.addWidget(original)
        form.addRow('视频文件', media)
        self.start = QtWidgets.QDoubleSpinBox()
        self.start.setRange(0, 360000)
        self.start.setDecimals(2)
        self.start.setSuffix(' 秒')
        self.duration = QtWidgets.QDoubleSpinBox()
        self.duration.setRange(3, 30)
        self.duration.setDecimals(2)
        self.duration.setValue(10)
        self.duration.setSuffix(' 秒')
        clip = row.get('clip', {}) if row else {}
        if clip:
            self.source.setCurrentText(clip['source'])
            self.start.setValue(clip['start'])
            self.duration.setValue(clip['duration'])
        form.addRow('从哪里开始', self.start)
        form.addRow('截取时长', self.duration)
        layout.addLayout(form)
        self.range_hint = QtWidgets.QLabel()
        self.start.valueChanged.connect(self.show_range)
        self.duration.valueChanged.connect(self.show_range)
        layout.addWidget(self.range_hint)
        self.show_range()
        self.status = QtWidgets.QLabel('例如从 1 分 20 秒开始，填写 80 秒。片段需只有当前角色说话，尽量避开背景音乐。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText('取消')
        buttons.addButton('准备片段草稿', QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        configure_dialog(self, 'video')

    def show_range(self, *_):
        self.range_hint.setText(f'选取范围：{clip_time(self.start.value())} → '
                               f'{clip_time(self.start.value()+self.duration.value())}')

    def browse(self):
        patterns = ' '.join('*'+suffix for suffix in sorted(VIDEO_EXTENSIONS))
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, '选择角色视频或游戏录屏',
            self.source.currentText(), '视频文件 ('+patterns+')')
        if path:
            self.source.setCurrentText(path)

    def open_original(self):
        path = Path(self.source.currentText()).expanduser()
        if not path.is_file():
            self.status.setText('请先选择有效的视频文件。')
        elif not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.resolve()))):
            self.status.setText('系统未能打开视频，可在文件夹中用播放器查看。')

    def accept(self):
        try:
            source = Path(self.source.currentText().strip()).expanduser()
            if not source.is_file():
                raise ValueError('请先选择有效的视频文件')
            speaker = self.speaker.text().strip()
            if not speaker:
                raise ValueError('请填写这段视频中说话的角色名')
            clip = dict(source=str(source.resolve()), start=self.start.value(), duration=self.duration.value())
            validate_video_clip(clip)
            self.result_values = dict(speaker=speaker, clip=clip,
                aliases=[v.strip() for v in self.aliases.text().replace('，', ',').split(',') if v.strip()])
            super().accept()
        except ValueError as exc:
            self.status.setText(str(exc))


class LibraryWorker(QtCore.QThread):
    message = QtCore.Signal(str)
    result = QtCore.Signal(object, object)

    def __init__(self, options, root, draft, operation, source='', row_id=None, parent=None):
        super().__init__(parent)
        self.options, self.root, self.draft = options, Path(root), draft
        self.operation, self.source, self.row_id = operation, source, row_id
        self.stop = threading.Event()

    def cancel(self):
        self.stop.set()

    def run(self):
        before = sum(r['state'] == 'imported' for r in self.draft.rows) if self.draft else 0
        summary = {}
        try:
            if self.operation == 'scan':
                self.message.emit('正在扫描角色素材…')
                groups = scan_character_audio_groups(self.source, cancel_event=self.stop)
                if not groups:
                    raise ValueError('没有找到音频或视频；请选择角色目录或包含多个角色目录的文件夹')
                check_cancel(self.stop)
                self.draft = VoiceLibraryDraft.create(self.root/'voice-library-drafts', self.source, groups)
            builder = BatchVoiceProfileBuilder(self.options, self.draft, stop=self.stop, progress=self.message.emit)
            if self.operation in ('scan', 'prepare'):
                builder.prepare()
                summary['message'] = '素材草稿已准备。请选择片段、试听并核对参考台词，再保存声线。'
            elif self.operation == 'prepare_row':
                builder.prepare(row_id=self.row_id)
                summary['message'] = '视频处理已结束。请查看当前角色的状态，再试听、核对并保存。'
            elif self.operation == 'transcribe':
                row = next(r for r in self.draft.rows if r['id'] == self.row_id)
                builder.transcribe_row(row)
                summary['message'] = '转写任务已结束，请查看所选片段的文字或提示，并试听核对。'
            elif self.operation == 'publish':
                summary.update(builder.publish())
                summary['message'] = f"保存完成：新增 {summary['imported']} 个声线，待核对 {summary['held']} 个，失败 {summary['failed']} 个。"
        except ImportCancelled:
            summary['message'] = '已停止；已完成的草稿和声线保留，可继续准备。'
        except Exception as exc:
            summary['message'] = '未完成：'+str(exc)
        finally:
            if self.draft:
                try:
                    self.draft.save()
                except OSError as exc:
                    summary['message'] = '草稿未能保存：'+str(exc)
            summary['imported'] = (sum(r['state'] == 'imported' for r in self.draft.rows) if self.draft else 0)-before
            self.result.emit(self.draft, summary)


class ReferencePreview(QtCore.QThread):
    message = QtCore.Signal(str)

    def __init__(self, path, checksum, config, parent=None):
        super().__init__(parent)
        self.path, self.checksum, self.config = path, checksum, config
        self.stop = threading.Event()

    def cancel(self):
        self.stop.set()

    def run(self):
        try:
            from wuwa_dubber.audio.player import AudioPlayer
            if sha256(self.path) != self.checksum:
                raise ValueError('参考片段已变化，请重新准备草稿')
            AudioPlayer(self.config.output_device, volume=self.config.output_volume).play(self.path, cancel_event=self.stop)
            self.message.emit('试听已停止' if self.stop.is_set() else '试听完成，请核对角色与参考台词。')
        except Exception as exc:
            self.message.emit('试听未完成：'+str(exc))


class VoiceLibraryDialog(QtWidgets.QDialog):
    def __init__(self, options, root, parent=None):
        super().__init__(parent)
        self.options, self.root = dict(options), Path(root)
        self.draft = self.editor_row = self.worker = self.preview_worker = self.clip_dialog = None
        self.imported_count = 0
        self.rendering = self.closing = False
        self.setWindowTitle('批量建立角色声线')
        self.resize(1080, 780)
        self.setMinimumSize(920, 690)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('选择音频 / 视频素材文件夹，或点击“从视频添加…” → 转写 → 试听 / 核对 → 保存声线。\n'
            '每个角色一个文件夹；自动推荐完整的 3–30 秒短片，优先 6–12 秒。长视频可指定截取范围。\n'
            '请选只有当前角色说话的片段，并试听确认；已有角色和别名会跳过。')
        note.setWordWrap(True)
        layout.addWidget(note)
        source_row = QtWidgets.QHBoxLayout()
        self.source = QtWidgets.QLineEdit()
        self.source.setPlaceholderText('选择包含多个角色文件夹的目录，或单个角色素材目录')
        source_row.addWidget(self.source, 1)
        self.controls = []
        self.add_button(source_row, '选择文件夹…', self.choose_folder)
        layout.addLayout(source_row)
        actions = QtWidgets.QHBoxLayout()
        self.add_button(actions, '扫描新素材', lambda: self.start_task('scan'))
        self.add_button(actions, '从视频添加…', self.add_video)
        self.add_button(actions, '继续准备草稿', lambda: self.start_task('prepare'))
        self.add_button(actions, '打开草稿…', self.open_draft)
        actions.addStretch()
        self.stop_button = QtWidgets.QPushButton('停止当前操作')
        self.stop_button.clicked.connect(self.cancel)
        self.stop_button.setEnabled(False)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['保存', '角色', '素材数量', '参考片段', '参考台词草稿', '状态'])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        for col, width in ((0, 45), (1, 120), (2, 75), (3, 110), (5, 165)):
            self.table.setColumnWidth(col, width)
        self.table.currentCellChanged.connect(self.change_row)
        self.table.itemChanged.connect(self.check_row)
        layout.addWidget(self.table, 1)
        self.count = QtWidgets.QLabel()
        layout.addWidget(self.count)
        self.editor = QtWidgets.QGroupBox('当前角色 · 修改自动保存在草稿中')
        edit = QtWidgets.QVBoxLayout(self.editor)
        names = QtWidgets.QHBoxLayout()
        names.addWidget(QtWidgets.QLabel('角色名'))
        self.speaker = QtWidgets.QLineEdit()
        names.addWidget(self.speaker, 1)
        names.addWidget(QtWidgets.QLabel('别名'))
        self.aliases = QtWidgets.QLineEdit()
        self.aliases.setPlaceholderText('可选，用逗号分隔')
        names.addWidget(self.aliases, 1)
        edit.addLayout(names)
        clips = QtWidgets.QHBoxLayout()
        clips.addWidget(QtWidgets.QLabel('候选片段'))
        self.candidates = QtWidgets.QComboBox()
        self.candidates.currentIndexChanged.connect(self.change_candidate)
        clips.addWidget(self.candidates, 1)
        self.preview_button = self.add_button(clips, '试听片段', self.preview)
        self.transcribe_button = self.add_button(clips, '转写此片段', lambda: self.start_task('transcribe'))
        edit.addLayout(clips)
        video_row = QtWidgets.QHBoxLayout()
        self.clip_button = self.add_button(video_row, '选取视频片段…', self.adjust_video)
        self.clip_label = QtWidgets.QLabel()
        self.clip_label.setWordWrap(True)
        video_row.addWidget(self.clip_label, 1)
        edit.addLayout(video_row)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setPlaceholderText('填写片段中实际说出的完整内容；转写结果可以修改。不要补写音频未说出的字。')
        self.text.setFixedHeight(85)
        edit.addWidget(self.text)
        self.confirmed = QtWidgets.QCheckBox('我已试听，并核对了角色、参考片段和台词文字')
        self.confirmed.toggled.connect(self.save_editor)
        edit.addWidget(self.confirmed)
        self.detail = QtWidgets.QLabel()
        self.detail.setWordWrap(True)
        edit.addWidget(self.detail)
        layout.addWidget(self.editor)
        bottom = QtWidgets.QHBoxLayout()
        self.publish_button = self.add_button(bottom, '保存已核对声线', lambda: self.start_task('publish'))
        bottom.addStretch()
        close = QtWidgets.QPushButton('关闭')
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.status = QtWidgets.QLabel('草稿会自动保存；只保存你勾选且已试听核对的角色。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.save_editor)
        for signal in (self.speaker.textEdited, self.aliases.textEdited, self.text.textChanged):
            signal.connect(self.edited)
        last = self.root/'voice-library-drafts/last-draft.txt'
        if last.is_file():
            try:
                self.draft = VoiceLibraryDraft.load(last.read_text(encoding='utf-8').strip())
                self.source.setText(self.draft.root)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.status.setText('上次草稿暂不可用，可重新扫描：'+str(exc))
        self.render()
        if self.draft and self.draft.rows:
            self.table.setCurrentCell(0, 1)
        configure_dialog(self, 'library')

    def add_button(self, layout, text, callback):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(lambda _=False: callback())
        self.controls.append(button)
        layout.addWidget(button)
        return button

    def busy(self):
        return bool((self.worker and self.worker.isRunning()) or
                    (self.preview_worker and self.preview_worker.isRunning()))

    def set_busy(self):
        busy = self.busy()
        for control in [*self.controls, self.table, self.source]:
            control.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.editor.setEnabled(not busy and self.editor_row is not None and
                               self.editor_row['state'] not in ('imported', 'skipped'))
        self.clip_button.setEnabled(not busy and self.editor_row is not None and
            any(Path(p).suffix.lower() in VIDEO_EXTENSIONS for p in self.editor_row['files']))

    def render(self):
        self.rendering = True
        self.table.blockSignals(True)
        rows = self.draft.rows if self.draft else []
        self.table.setRowCount(len(rows))
        ready = 0
        for index, row in enumerate(rows):
            selected = self.draft.chosen(row)
            key = self.draft.review_key(row)
            reviewed = bool(key and row['reviewed'] == key)
            ready += bool(row['include'] and reviewed and row['state'] != 'imported')
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsUserCheckable)
            check.setCheckState(QtCore.Qt.Checked if row['include'] else QtCore.Qt.Unchecked)
            self.table.setItem(index, 0, check)
            state = '已核对，待保存' if reviewed and row['state'] == 'ready' else STATES.get(row['state'], row['state'])
            if row['state'] == 'ready' and row['error']:
                state = '保存未完成，请检查'
            values = [row['speaker'], str(len(row['files'])), f"{selected['seconds']:.2f} 秒" if selected else '—',
                      selected.get('text', '') if selected else '', state]
            for column, value in enumerate(values, 1):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(row['error'] if column == 5 and row['error'] else value)
                self.table.setItem(index, column, item)
        self.count.setText(f"共 {len(rows)} 个角色 · 已核对待保存 {ready} 个 · 已保存 {sum(r['state']=='imported' for r in rows)} 个")
        self.table.blockSignals(False)
        self.rendering = False
        self.set_busy()

    def load_editor(self, index):
        self.rendering = True
        self.editor_row = self.draft.rows[index] if self.draft and 0 <= index < len(self.draft.rows) else None
        row = self.editor_row
        self.speaker.setText(row['speaker'] if row else '')
        self.aliases.setText('，'.join(row['aliases']) if row else '')
        self.candidates.clear()
        for candidate in row['candidates'] if row else []:
            ratio = candidate['quality'].get('speech_ratio')
            label = f"{Path(candidate['source']).name} · {candidate['seconds']:.2f} 秒"
            if ratio is not None:
                label += f" · 语音占比约 {ratio:.0%}"
            self.candidates.addItem(label)
        if row:
            self.candidates.setCurrentIndex(row['selected'])
        selected = self.draft.chosen(row) if row else None
        clip = row.get('clip') if row else None
        video = selected and Path(selected['source']).suffix.lower() in VIDEO_EXTENSIONS
        start = selected.get('start', 0) if video else clip['start'] if clip else 0
        duration = selected['seconds'] if video else clip['duration'] if clip else 0
        self.clip_label.setText(f'视频范围：{clip_time(start)} → {clip_time(start+duration)}' if video or clip else '')
        self.text.setPlainText(selected.get('text', '') if selected else '')
        key = self.draft.review_key(row) if row else ''
        self.confirmed.setChecked(bool(key and row['reviewed'] == key))
        self.detail.setText((row.get('error') or selected.get('asr_error') or '转写是草稿，请逐字核对；语音占比是自动估计。') if selected else
                            row.get('error', '') if row else '')
        self.rendering = False
        self.set_busy()

    def save_editor(self, *_):
        if self.rendering or self.busy() or not self.editor_row:
            return
        self.timer.stop()
        row = self.editor_row
        if row['state'] in ('imported', 'skipped'):
            return
        row['speaker'] = self.speaker.text().strip()
        row['aliases'] = [n.strip() for n in self.aliases.text().replace('，', ',').split(',') if n.strip()]
        selected = self.draft.chosen(row)
        if selected:
            selected['text'] = self.text.toPlainText().strip()
        row['reviewed'] = self.draft.review_key(row) if self.confirmed.isChecked() else ''
        self.draft.save()
        self.render()

    def edited(self, *_):
        if not self.rendering and not self.busy():
            self.confirmed.blockSignals(True)
            self.confirmed.setChecked(False)
            self.confirmed.blockSignals(False)
            self.timer.start(350)

    def change_row(self, row, *_):
        if not self.rendering:
            self.save_editor()
            self.load_editor(row)

    def change_candidate(self, index):
        if self.rendering or not self.editor_row or index < 0:
            return
        self.save_editor()
        self.editor_row['selected'], self.editor_row['reviewed'] = index, ''
        self.draft.save()
        self.render()
        self.load_editor(self.table.currentRow())

    def check_row(self, item):
        if not self.rendering and item.column() == 0 and self.draft:
            self.draft.rows[item.row()]['include'] = item.checkState() == QtCore.Qt.Checked
            self.draft.save()
            self.render()

    def choose_folder(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, '选择角色素材文件夹', self.source.text())
        if path:
            self.source.setText(path)

    def add_video(self):
        self.choose_video_clip()

    def adjust_video(self):
        if self.editor_row:
            self.choose_video_clip(self.editor_row)

    def choose_video_clip(self, row=None):
        if self.busy():
            return
        self.save_editor()
        try:
            self.clip_dialog = VideoClipDialog(row, self)
            if self.clip_dialog.exec() != QtWidgets.QDialog.Accepted:
                return
            values = self.clip_dialog.result_values
            if self.draft is None:
                self.draft = VoiceLibraryDraft.create(self.root/'voice-library-drafts',
                    Path(values['clip']['source']).parent, [])
                self.source.setText(self.draft.root)
            selected = self.draft.set_video_clip(**values, row=row)
            self.remember_draft()
            self.editor_row = None
            self.render()
            index = self.draft.rows.index(selected)
            self.table.setCurrentCell(index, 1)
            self.load_editor(index)
            self.start_task('prepare_row')
        except (OSError, ValueError, TypeError) as exc:
            self.status.setText('视频草稿未能准备：'+str(exc))
        finally:
            self.clip_dialog = None

    def open_draft(self):
        self.save_editor()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, '打开声线草稿', str(self.root/'voice-library-drafts'), '声线草稿 (draft.json)')
        if path:
            try:
                self.draft = VoiceLibraryDraft.load(path)
                self.editor_row = None
                self.source.setText(self.draft.root)
                self.remember_draft()
                self.render()
                self.table.setCurrentCell(0, 1)
                self.load_editor(0)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.status.setText('无法打开草稿：'+str(exc))

    def remember_draft(self):
        if self.draft:
            atomic_text(self.root/'voice-library-drafts/last-draft.txt', str(self.draft.directory))

    def start_task(self, operation):
        if self.busy():
            return
        self.save_editor()
        if operation == 'scan' and (not self.source.text().strip() or not Path(self.source.text()).is_dir()):
            self.status.setText('请先选择有效的素材文件夹。')
            return
        if operation != 'scan' and not self.draft:
            self.status.setText('请先扫描素材或打开草稿。')
            return
        if operation == 'transcribe' and (not self.editor_row or not self.draft.chosen(self.editor_row)):
            return
        if operation == 'prepare_row' and not self.editor_row:
            return
        self.worker = LibraryWorker(self.options, self.root, None if operation == 'scan' else self.draft,
            operation, self.source.text(), self.editor_row['id'] if self.editor_row else None, self)
        self.worker.message.connect(self.status.setText)
        self.worker.result.connect(self.task_result)
        self.worker.finished.connect(self.finished_task)
        self.worker.start()
        self.set_busy()

    def task_result(self, draft, summary):
        if draft is not None:
            self.draft = draft
            self.remember_draft()
        self.imported_count += summary['imported']
        self.status.setText(summary['message'])

    def finished_task(self):
        self.timer.stop()
        self.editor_row = None
        self.render()
        if self.draft and self.draft.rows:
            index = max(0, min(self.table.currentRow(), len(self.draft.rows)-1))
            self.table.setCurrentCell(index, 1)
            self.load_editor(index)
        if self.closing:
            self.close()

    def preview(self):
        self.save_editor()
        candidate = self.draft.chosen(self.editor_row) if self.draft and self.editor_row else None
        if not candidate or self.busy():
            return
        config = VoiceImportTask(self.options, {}).config
        self.preview_worker = ReferencePreview(self.draft.reference(candidate), candidate['sha256'], config, self)
        self.preview_worker.message.connect(self.status.setText)
        self.preview_worker.finished.connect(self.finished_task)
        self.preview_worker.start()
        self.set_busy()

    def cancel(self):
        for worker in (self.worker, self.preview_worker):
            if worker and worker.isRunning():
                worker.cancel()
        self.status.setText('正在停止，已完成的草稿和声线会保留…')

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.clip_dialog:
            self.clip_dialog.reject()
        if self.busy():
            self.closing = True
            self.cancel()
            event.ignore()
        else:
            self.save_editor()
            event.accept()
