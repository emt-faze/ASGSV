"""Dual-game entry and spoiler-conscious WuWa preparation workbench."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading

from PySide6 import QtCore, QtGui, QtWidgets as W

from dialogue_generation import atomic_text
from dialogue_dubbing import voice_paths
from game_profiles import CODE_ROOT, WUWA_ROOT, initialize_wuwa
from wuwa_catalog import Catalog, resolve_text
from wuwa_resources import COMMIT, download_tables, inventory, probe_local
from wuwa_progress import PROGRESS_LABELS

STATUS = {'unknown': '未知 / 待核对', 'unvoiced': '确认无原配音', 'voiced': '有原配音证据'}


class Job(QtCore.QThread):
    message = QtCore.Signal(str)
    result = QtCore.Signal(object)
    error = QtCore.Signal(str)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn, self.stop = fn, threading.Event()

    def run(self):
        try:
            self.result.emit(self.fn(self.stop, self.message.emit))
        except Exception as exc:
            self.error.emit(str(exc))


class SettingsDialog(W.QDialog):
    def __init__(self, root, parent):
        super().__init__(parent)
        self.root = root
        self.config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
        self.setWindowTitle('鸣潮设置')
        self.resize(590, 340)
        form = W.QFormLayout(self)
        self.branch_rule = W.QCheckBox('普通支线按无原声处理')
        self.branch_rule.setChecked(parent.catalog.branch_rule_enabled())
        self.branch_rule.setToolTip('仅用于实时配音。已知原声、伴星任务和活动任务仍按逐句记录判断。')
        form.addRow('实时配音', self.branch_rule)
        import wuwa_breeze as breeze
        self.cloud_only = W.QCheckBox('默认使用 BreezeBlue，已指定原声参考的角色在本地合成')
        self.cloud_only.setChecked(parent.catalog.cloud_only())
        form.addRow('合成方式', self.cloud_only)
        self.cloud_budget = W.QSpinBox()
        self.cloud_budget.setRange(0,100000)
        self.cloud_budget.setValue(int(parent.catalog.meta('breeze_daily_credits','0')))
        self.cloud_budget.setSpecialValueText('暂停云端付费生成')
        self.cloud_budget.setSuffix(' 积分 / 天')
        self.cloud_budget.setToolTip('先复用缓存。达到本工具的预算后暂停新的云端请求。')
        form.addRow('BreezeBlue 每日预算', self.cloud_budget)
        self.cloud_key = W.QLineEdit()
        self.cloud_key.setEchoMode(W.QLineEdit.Password)
        try:
            key_hint = '已配置，留空保留' if breeze.api_key() else 'API Key，保存到 Windows 凭据管理器'
        except ValueError as exc:
            key_hint = str(exc)
        self.cloud_key.setPlaceholderText(key_hint)
        form.addRow('BreezeBlue 密钥', self.cloud_key)
        self.install = W.QLineEdit(self.config['install_root'])
        self.nickname = W.QLineEdit(self.config.get('nickname', '漂泊者'))
        self.gender = W.QComboBox()
        for label, value in (('女漂泊者', 'female'), ('男漂泊者', 'male')):
            self.gender.addItem(label, value)
        self.gender.setCurrentIndex(max(0, self.gender.findData(self.config.get('traveler_gender'))))
        self.narrator = W.QComboBox()
        from system_voices import PRESETS
        for key, (label, *_) in PRESETS.items():
            self.narrator.addItem(label, 'system:' + key)
        self.narrator.setCurrentIndex(max(0, self.narrator.findData(self.config.get('narrator_voice'))))
        for title, widget in (('游戏目录', self.install), ('昵称', self.nickname), ('漂泊者', self.gender), ('默认旁白', self.narrator)):
            form.addRow(title, widget)
        self.keys = {}
        for action, title, default in (('pause', '暂停 / 恢复', 'F8'), ('replay', '重播', 'F9'), ('skip', '跳过', 'F10')):
            combo = W.QComboBox()
            combo.addItems(['F' + str(n) for n in range(1, 25)])
            combo.setCurrentText(self.config.get('hotkeys', {}).get(action, default))
            self.keys[action] = combo
            form.addRow(title, combo)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.Save | W.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self):
        keys = {key: widget.currentText() for key, widget in self.keys.items()}
        if len(set(keys.values())) != len(keys) or not self.nickname.text().strip():
            W.QMessageBox.warning(self, '设置未保存', '昵称不能为空，快捷键不可重复。')
            return
        try:
            inventory(self.install.text().strip(), self.root)
            if self.cloud_key.text().strip():
                from wuwa_breeze import save_key
                save_key(self.cloud_key.text())
            if self.cloud_only.isChecked() and not self.parent().catalog.db.execute('SELECT 1 FROM breeze_presets LIMIT 1').fetchone():
                from wuwa_breeze import refresh_presets
                refresh_presets(self.parent().catalog)
        except Exception as exc:
            W.QMessageBox.warning(self, '设置未保存', str(exc))
            return
        self.config.update(install_root=self.install.text().strip(), nickname=self.nickname.text().strip(),
                           traveler_gender=self.gender.currentData(), narrator_voice=self.narrator.currentData(), hotkeys=keys,
                           setup_complete=True)
        atomic_text(self.root / 'config.json', json.dumps(self.config, ensure_ascii=False, indent=2))
        catalog = Catalog(self.root)
        try:
            with catalog.db:
                catalog.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                                   ('breeze_daily_credits',str(self.cloud_budget.value())))
                catalog.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                                   ('wuwa_tts_provider','breezeblue' if self.cloud_only.isChecked() else 'hybrid'))
            catalog.set_branch_rule(self.branch_rule.isChecked())
            if catalog.cloud_only():
                traveler_profile = '漂泊者（女）' if self.gender.currentData()=='female' else '漂泊者（男）'
                with catalog.db:
                    catalog.db.execute("UPDATE original_reference_cast SET profile=? WHERE speaker_id='750088'",(traveler_profile,))
                super().accept()
                return
            _, data = voice_paths(self.config['dubbing'])
            name = '漂泊者（女）' if self.gender.currentData() == 'female' else '漂泊者（男）'
            exists = any(json.loads(p.read_text(encoding='utf-8-sig')).get('speaker') == name
                         for p in (data / 'profiles').glob('*/profile.json'))
            for row in catalog.db.execute("SELECT DISTINCT speaker_id,speaker FROM lines WHERE speaker IN ('漂泊者','旁白')").fetchall():
                old = catalog.binding(row['speaker_id'])
                voice = 'character:' + name if row['speaker'] == '漂泊者' and exists else self.narrator.currentData()
                reviewed = voice.startswith('system:') or bool(old and old['voice'] == voice and old['transcript_reviewed'])
                catalog.set_binding(row['speaker_id'], row['speaker'], voice, reviewed)
        finally:
            catalog.close()
        super().accept()


class VoiceBindingDialog(W.QDialog):
    def __init__(self, catalog, row, config, parent):
        super().__init__(parent)
        self.catalog, self.row = catalog, row
        self.variant_job = None
        self.variant_result = self.variant_error = None
        self.cancel_requested = False
        self.setWindowTitle('角色声线 · ' + row['speaker'])
        self.resize(700, 510)
        layout = W.QVBoxLayout(self)
        self.voices = W.QComboBox()
        from system_voices import PRESETS
        for key, (label, *_) in PRESETS.items():
            self.voices.addItem(label + '（通用，非角色克隆）', 'system:' + key)
        _, data = voice_paths(config['dubbing'])
        self.profile_root = data / 'profiles'
        self.profiles = {}
        for path in sorted((data / 'profiles').glob('*/profile.json')):
            p = json.loads(path.read_text(encoding='utf-8-sig'))
            if p.get('enabled', True):
                self.voices.addItem(p['speaker'] + '（参考声线）', 'character:' + p['speaker'])
                self.profiles['character:' + p['speaker']] = p
        from wuwa_npc_voices import choices
        for label, voice, profile in choices(catalog.root, speaker_id=row['speaker_id']):
            self.voices.addItem(label, voice)
            self.profiles[voice] = profile.to_dict()
        self.adapt = W.QCheckBox('创建 NPC 改编声线')
        self.pitch = W.QDoubleSpinBox()
        self.pitch.setRange(-2, 2)
        self.pitch.setSingleStep(.25)
        self.pitch.setDecimals(2)
        self.pitch.setSuffix(' 半音')
        self.pitch.setValue(-.75)
        self.speed = W.QDoubleSpinBox()
        self.speed.setRange(.9, 1.1)
        self.speed.setSingleStep(.01)
        self.speed.setDecimals(2)
        self.speed.setSuffix('x')
        self.speed.setValue(1.03)
        self.adapt.toggled.connect(self.update_variant_controls)
        self.transcript = W.QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.reviewed = W.QCheckBox('参考台词已核对 / 通用系统声线已确认')
        self.cloud_voice = W.QLineEdit()
        self.cloud_voice.setPlaceholderText('可选：填入为该角色试听选定的 BreezeBlue Voice ID')
        self.voices.currentIndexChanged.connect(self.changed)
        binding = catalog.binding(row['speaker_id'])
        self.voices.setCurrentIndex(max(0, self.voices.findData(binding['voice'] if binding else 'system:default')))
        self.changed()
        self.reviewed.setChecked(bool(binding and binding['transcript_reviewed']))
        layout.addWidget(self.voices)
        layout.addWidget(W.QLabel('本地失败时使用的云端音色'))
        layout.addWidget(self.cloud_voice)
        layout.addWidget(self.adapt)
        variant_form = W.QFormLayout()
        variant_form.addRow('参考音高', self.pitch)
        variant_form.addRow('合成语速', self.speed)
        layout.addLayout(variant_form)
        layout.addWidget(self.transcript)
        reference = W.QPushButton('播放参考音频')
        reference.setIcon(self.style().standardIcon(W.QStyle.SP_MediaPlay))
        reference.clicked.connect(self.play_reference)
        layout.addWidget(reference)
        layout.addWidget(self.reviewed)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.Save | W.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.buttons = buttons
        layout.addWidget(buttons)

    def changed(self):
        from wuwa_breeze import pairing
        self.cloud_voice.setText(pairing(self.catalog,self.row['speaker_id'],self.voices.currentData()))
        profile = self.profiles.get(self.voices.currentData(), {})
        self.transcript.setPlainText(profile.get('prompt_text', '') or ('参考台词缺失' if profile else '通用系统中文声线'))
        self.reviewed.setChecked(not profile)
        can_adapt = str(self.voices.currentData()).startswith('character:')
        self.adapt.setEnabled(can_adapt)
        if not can_adapt:
            self.adapt.setChecked(False)
        self.update_variant_controls()

    def update_variant_controls(self):
        enabled = self.adapt.isEnabled() and self.adapt.isChecked()
        self.pitch.setEnabled(enabled)
        self.speed.setEnabled(enabled)

    def accept(self):
        if self.variant_job:
            return
        try:
            from wuwa_breeze import voice_id
            voice_id(self.cloud_voice.text())
            if self.adapt.isChecked():
                from wuwa_dubber.models import VoiceProfile
                from wuwa_npc_voices import create_variant
                profile = VoiceProfile.from_dict(self.profiles[self.voices.currentData()])
                profile.reference_audio = str(profile.resolve_reference(self.profile_root).resolve())
                pitch, speed = self.pitch.value(), self.speed.value()
                self.variant_result = self.variant_error = None
                self.cancel_requested = False
                self.variant_job = Job(lambda stop, emit: create_variant(
                    self.catalog.root, self.row['speaker_id'], self.row['speaker'], profile,
                    pitch=pitch, speed=speed, stop=stop), self)
                self.variant_job.result.connect(lambda result: setattr(self, 'variant_result', result))
                self.variant_job.error.connect(lambda error: setattr(self, 'variant_error', error))
                self.variant_job.finished.connect(self.finish_variant)
                for widget in (self.voices, self.adapt, self.pitch, self.speed, self.reviewed, self.cloud_voice):
                    widget.setEnabled(False)
                self.buttons.button(W.QDialogButtonBox.Save).setEnabled(False)
                self.variant_job.start()
                return
            self.save_binding(self.voices.currentData())
            super().accept()
        except Exception as exc:
            W.QMessageBox.warning(self, '声线未保存', str(exc))

    def finish_variant(self):
        self.variant_job.deleteLater()
        self.variant_job = None
        if self.cancel_requested:
            super().reject()
            return
        for widget in (self.voices, self.adapt, self.reviewed, self.cloud_voice):
            widget.setEnabled(True)
        self.update_variant_controls()
        self.buttons.button(W.QDialogButtonBox.Save).setEnabled(True)
        try:
            if self.variant_error:
                raise ValueError(self.variant_error)
            self.save_binding(self.variant_result)
            super().accept()
        except Exception as exc:
            W.QMessageBox.warning(self, '声线未保存', str(exc))

    def reject(self):
        if self.variant_job:
            self.cancel_requested = True
            self.variant_job.stop.set()
            return
        super().reject()

    def save_binding(self,voice):
        from wuwa_breeze import set_pairing
        self.catalog.set_binding(self.row['speaker_id'],self.row['speaker'],voice,self.reviewed.isChecked())
        set_pairing(self.catalog,self.row['speaker_id'],voice,self.cloud_voice.text())

    def closeEvent(self, event):
        if self.variant_job:
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)

    def play_reference(self):
        profile = self.profiles.get(self.voices.currentData())
        if not profile:
            return
        try:
            from wuwa_dubber.models import VoiceProfile
            path = VoiceProfile.from_dict(profile).resolve_reference(self.profile_root)
            if not path.is_file():
                raise ValueError('参考音频不存在')
            os.startfile(path)
        except Exception as exc:
            W.QMessageBox.warning(self, '参考音频未打开', str(exc))


class GameHome(W.QDialog):
    def __init__(self, *, root=None, preference=None):
        super().__init__()
        self.root = Path(root) if root is not None else initialize_wuwa()
        fresh_catalog = not (self.root/'dialogue.sqlite').exists()
        self.catalog = Catalog(self.root)
        if fresh_catalog and root is None:
            with self.catalog.db:
                self.catalog.db.execute("INSERT OR IGNORE INTO meta VALUES('require_quest_cast_review','1')")
        self.worker = None
        self.closing = False
        self.child = None
        self.rows = []
        self.page_offset = 0
        self.preference = Path(preference) if preference is not None else CODE_ROOT.parent / 'game-data/entry.json'
        self.lock = QtCore.QLockFile(str(self.preference.with_suffix('.lock')))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            raise ValueError('双游戏工具已打开，请使用已有窗口')
        self.setWindowTitle('ASGSV · 鸣潮 / 异环')
        self.resize(1240, 840)
        self.setMinimumSize(960, 680)
        self.setWindowFlag(QtCore.Qt.WindowMinimizeButtonHint, True)
        self.setWindowFlag(QtCore.Qt.WindowMaximizeButtonHint, True)
        layout = W.QVBoxLayout(self)
        layout.setSpacing(12)
        top = W.QHBoxLayout()
        title = W.QLabel('ASGSV 配音工作台')
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        top.addWidget(title)
        self.game = W.QComboBox()
        self.game.addItem('鸣潮', 'wuwa')
        self.game.addItem('异环', 'nte')
        top.addWidget(self.game)
        top.addStretch()
        self.settings = self.button(top, '设置', self.open_settings, W.QStyle.SP_FileDialogDetailedView)
        self.button(top, '打开项目', self.open_existing, W.QStyle.SP_DirOpenIcon)
        self.button(top, '继续上次项目', self.resume_project, W.QStyle.SP_MediaPlay)
        self.advanced_toggle = W.QToolButton()
        self.advanced_toggle.setText('高级')
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.advanced_toggle.setIcon(self.style().standardIcon(W.QStyle.SP_FileDialogDetailedView))
        self.advanced_toggle.setToolTip('资源版本、台词索引、采集导入与任务进度核对')
        top.addWidget(self.advanced_toggle)
        layout.addLayout(top)
        self.wuwa = W.QWidget()
        wuwa_layout = W.QVBoxLayout(self.wuwa)
        wuwa_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_tabs = W.QTabWidget()
        from wuwa_live_ui import LivePanel
        self.live = LivePanel(self.root, self)
        self.live.task_selected.connect(self.show_live_task)
        self.live.stopped.connect(self.live_stopped)
        self.workspace_tabs.addTab(self.live, '实时配音')
        self.task_library = W.QWidget()
        self.workspace_tabs.addTab(self.task_library, '任务库与配音准备')
        wuwa_layout.addWidget(self.workspace_tabs)
        body = W.QVBoxLayout(self.task_library)
        body.setContentsMargins(0, 0, 0, 0)
        self.advanced = W.QWidget()
        advanced_layout = W.QVBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.source = W.QLabel()
        self.source.setWordWrap(True)
        advanced_layout.addWidget(self.source)
        actions = W.QHBoxLayout()
        self.import_button = self.button(actions, '更新台词索引', self.start_import, W.QStyle.SP_BrowserReload)
        self.probe_button = self.button(actions, '本机资源探测', self.start_probe, W.QStyle.SP_ComputerIcon)
        self.capture_import = self.button(actions, '导入采集', self.import_capture, W.QStyle.SP_DialogOpenButton)
        actions.addStretch()
        self.stop = self.button(top, '停止', self.stop_job, W.QStyle.SP_MediaStop)
        self.stop.setEnabled(False)
        advanced_layout.addLayout(actions)
        progress_actions = W.QHBoxLayout()
        self.progress_button = self.button(progress_actions, '读取任务进度', self.refresh_progress, W.QStyle.SP_BrowserReload)
        self.progress_review = self.button(progress_actions, '核对任务进度', self.review_progress, W.QStyle.SP_DialogApplyButton)
        self.hide_completed = W.QCheckBox('隐藏已完成')
        self.hide_completed.setChecked(True)
        progress_actions.addWidget(self.hide_completed)
        self.allow_unknown = W.QCheckBox('本次允许进度未知任务')
        self.allow_unknown.setToolTip('仅允许当前选择范围；已完成任务仍跳过，未知配音状态仍不可生成。')
        progress_actions.addWidget(self.allow_unknown)
        progress_actions.addStretch()
        advanced_layout.addLayout(progress_actions)
        body.addWidget(self.advanced)
        self.advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.progress_summary = W.QLabel('任务进度未读取')
        self.progress_summary.setWordWrap(True)
        body.addWidget(self.progress_summary)
        filters = W.QHBoxLayout()
        self.search = W.QLineEdit()
        self.search.setPlaceholderText('任务 / 流程名称')
        self.search.setClearButtonEnabled(True)
        self.kind = W.QComboBox()
        for label, value in (('任务与流程', ''), ('任务', 'quest'), ('NPC / 剧情流程', 'flow'), ('实机采集', 'capture')):
            self.kind.addItem(label, value)
        self.kind.setCurrentIndex(self.kind.findData('quest'))
        self.progress_filter = W.QComboBox()
        for label, value in (('我的待办', 'pending'), ('全部进度', ''), ('进度待核对', 'unknown'), ('已完成', 'completed')):
            self.progress_filter.addItem(label, value)
        self.region = W.QComboBox()
        self.region.addItem('全部区域', '')
        filters.addWidget(self.search, 1)
        filters.addWidget(self.progress_filter)
        filters.addWidget(self.kind)
        filters.addWidget(self.region)
        self.page = W.QLabel()
        filters.addWidget(self.page)
        self.previous_page = self.button(filters, '', lambda:self.change_page(-1), W.QStyle.SP_ArrowLeft)
        self.next_page = self.button(filters, '', lambda:self.change_page(1), W.QStyle.SP_ArrowRight)
        self.previous_page.setToolTip('上一页')
        self.next_page.setToolTip('下一页')
        body.addLayout(filters)
        split = W.QSplitter()
        self.scopes = W.QTableWidget(0, 5)
        self.scopes.setMinimumWidth(430)
        self.scopes.setHorizontalHeaderLabels(['任务 / 流程', '区域', '句数', '任务进度', '原配音'])
        self.scopes.setAlternatingRowColors(True)
        self.scopes.verticalHeader().setVisible(False)
        self.scopes.verticalHeader().setDefaultSectionSize(34)
        self.scopes.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        self.scopes.setSelectionMode(W.QAbstractItemView.ExtendedSelection)
        self.scopes.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
        self.scopes.horizontalHeader().setSectionResizeMode(0, W.QHeaderView.Stretch)
        self.scopes.setColumnWidth(1, 80)
        self.scopes.setColumnWidth(2, 45)
        self.scopes.setColumnWidth(3, 105)
        self.scopes.setColumnWidth(4, 105)
        self.scopes.setColumnHidden(1, True)
        split.addWidget(self.scopes)
        right = W.QWidget()
        r = W.QVBoxLayout(right)
        r.setContentsMargins(8, 0, 0, 0)
        line_filters = W.QHBoxLayout()
        self.status_filter = W.QComboBox()
        self.status_filter.addItem('全部配音状态', '')
        for key, label in STATUS.items():
            self.status_filter.addItem(label, key)
        self.speaker_filter = W.QLineEdit()
        self.speaker_filter.setPlaceholderText('角色')
        self.spoilers = W.QCheckBox('显示正文')
        line_filters.addWidget(self.status_filter)
        line_filters.addWidget(self.speaker_filter, 1)
        line_filters.addWidget(self.spoilers)
        r.addLayout(line_filters)
        self.lines = W.QTableWidget(0, 4)
        self.lines.setAlternatingRowColors(True)
        self.lines.verticalHeader().setVisible(False)
        self.lines.verticalHeader().setDefaultSectionSize(34)
        self.lines.setHorizontalHeaderLabels(['角色', '正文', '配音状态', '声线'])
        self.lines.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        self.lines.setSelectionMode(W.QAbstractItemView.ExtendedSelection)
        self.lines.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
        self.lines.horizontalHeader().setSectionResizeMode(1, W.QHeaderView.Stretch)
        self.lines.setColumnWidth(0, 80)
        self.lines.setColumnWidth(2, 125)
        self.lines.setColumnWidth(3, 120)
        r.addWidget(self.lines, 1)
        self.detail = W.QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(125)
        self.detail.setVisible(False)
        r.addWidget(self.detail)
        review = W.QHBoxLayout()
        self.review_button = self.button(review, '核对配音状态', self.review_lines, W.QStyle.SP_DialogApplyButton)
        self.voice_button = self.button(review, '角色声线', self.bind_voice, W.QStyle.SP_MediaVolume)
        self.button(review, '确认主要人物声线', self.review_quest_cast, W.QStyle.SP_DialogApplyButton)
        review.addStretch()
        r.addLayout(review)
        self.summary = W.QLabel()
        self.summary.setWordWrap(True)
        r.addWidget(self.summary)
        self.prepare = W.QPushButton('生成与试听')
        self.prepare.setObjectName('primary')
        self.prepare.setMinimumHeight(42)
        self.prepare.setToolTip('打开所选任务的生成项目；继续生成会复用已有有效音频。未知或已有原声的对白不会生成。')
        self.prepare.setIcon(self.style().standardIcon(W.QStyle.SP_FileIcon))
        self.prepare.clicked.connect(self.prepare_project)
        r.addWidget(self.prepare)
        split.addWidget(right)
        split.setSizes([500, 680])
        body.addWidget(split, 1)
        playback_actions = W.QHBoxLayout()
        self.launch_button = self.button(playback_actions, '启动鸣潮', self.launch_game, W.QStyle.SP_MediaPlay)
        self.launch_button.setToolTip('通过官方启动器进入游戏；不会接取任务或推进剧情。')
        self.calibrate_button = self.button(playback_actions, '字幕校准', self.calibrate, W.QStyle.SP_DesktopIcon)
        self.calibrate_button.setToolTip('检查当前分辨率的字幕区与角色名区。')
        playback_actions.addStretch()
        self.ready_note = W.QLabel('没有已准备任务')
        self.ready_note.setWordWrap(True)
        playback_actions.addWidget(self.ready_note, 1)
        self.audition_prepared = self.button(playback_actions, '试听配音', self.preview_prepared, W.QStyle.SP_MediaVolume)
        self.play_prepared = self.button(playback_actions, '开始配音', self.open_prepared, W.QStyle.SP_MediaPlay)
        self.play_prepared.setToolTip('打开最近准备完成的任务，识别游戏字幕并播放已有音频；不会推进剧情。')
        body.addLayout(playback_actions)
        layout.addWidget(self.wuwa, 1)
        self.nte = W.QPushButton('打开异环台词生成工具')
        self.nte.clicked.connect(self.open_nte)
        layout.addWidget(self.nte)
        self.status = W.QLabel('就绪')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh_scopes)
        self.search.textChanged.connect(lambda: self.timer.start(250))
        self.kind.currentIndexChanged.connect(self.refresh_scopes)
        self.progress_filter.currentIndexChanged.connect(self.change_progress_filter)
        self.region.currentIndexChanged.connect(self.refresh_scopes)
        self.hide_completed.toggled.connect(self.refresh_scopes)
        self.allow_unknown.toggled.connect(self.estimate)
        self.scopes.itemSelectionChanged.connect(self.refresh_lines)
        self.status_filter.currentIndexChanged.connect(self.refresh_lines)
        self.speaker_filter.textChanged.connect(self.refresh_lines)
        self.spoilers.toggled.connect(self.refresh_lines)
        self.lines.itemSelectionChanged.connect(self.estimate)
        self.game.currentIndexChanged.connect(self.switch_game)
        selected = 'wuwa'
        if self.preference.exists():
            try:
                selected = json.loads(self.preference.read_text(encoding='utf-8')).get('game_id', selected)
            except ValueError:
                pass
        self.game.setCurrentIndex(max(0, self.game.findData(selected)))
        self.refresh_all()
        self.switch_game()
        self.refresh_progress()
        from generator_ui_support import configure_dialog
        configure_dialog(self, 'generator')

    def button(self, row, title, callback, icon):
        button = W.QPushButton(title)
        button.setIcon(self.style().standardIcon(icon))
        button.clicked.connect(callback)
        row.addWidget(button)
        return button

    def config(self):
        return json.loads((self.root / 'config.json').read_text(encoding='utf-8'))

    def switch_game(self):
        wuwa = self.game.currentData() == 'wuwa'
        if not wuwa and self.live.running():
            self.live.stop_live()
        self.wuwa.setVisible(wuwa)
        self.nte.setVisible(not wuwa)
        self.settings.setEnabled(wuwa)
        self.advanced_toggle.setVisible(wuwa)
        atomic_text(self.preference, json.dumps({'game_id': self.game.currentData()}))

    def show_live_task(self, scope):
        row = self.catalog.db.execute('SELECT name FROM scopes WHERE id=?', (scope,)).fetchone()
        if row is None:
            return
        self.workspace_tabs.setCurrentWidget(self.task_library)
        self.kind.setCurrentIndex(self.kind.findData('quest'))
        self.progress_filter.setCurrentIndex(self.progress_filter.findData(''))
        self.hide_completed.setChecked(False)
        self.search.setText(row['name'])
        self.refresh_scopes()
        for index in range(self.scopes.rowCount()):
            if self.scopes.item(index,0).data(QtCore.Qt.UserRole)==scope:
                self.scopes.selectRow(index)
                break

    def live_stopped(self):
        if self.closing:
            self.close()

    def open_settings(self):
        SettingsDialog(self.root, self).exec()
        self.refresh_lines()

    def refresh_progress(self):
        try:
            report = self.catalog.progress.refresh(self.config()['install_root'])
            states = self.catalog.progress.states()
            completed = sum(state == 'completed' for state in states.values())
            pending = sum(state == 'pending' for state in states.values())
            self.progress_summary.setText(f'我的待办 {pending} 项  ·  已完成排除 {completed} 项  ·  本地模型配音')
            self.progress_summary.setToolTip('来源：本机 UserFinishedQuests；缓存不保证完整或实时，不推断未完成。')
        except Exception as exc:
            self.progress_summary.setText('进度刷新未完成：' + str(exc))
        self.refresh_scopes(reset=False)

    def review_progress(self):
        scopes = self.selected_scopes()
        if not scopes or any(not scope.startswith('quest:') for scope in scopes):
            self.status.setText('请选择需要核对的任务，不包括 NPC 流程。')
            return
        dialog = W.QDialog(self)
        dialog.setWindowTitle(f'任务列表核对 · {len(scopes)} 个任务')
        form = W.QFormLayout(dialog)
        state = W.QComboBox()
        for key, title in PROGRESS_LABELS.items():
            state.addItem(title, key)
        state.setCurrentIndex(state.findData('unknown'))
        basis = W.QLineEdit()
        basis.setPlaceholderText('任务列表 / 已完成记录、核对日期')
        confirm = W.QCheckBox('已核对当前账号的任务列表；未以列表缺失推断已完成')
        form.addRow('任务进度', state)
        form.addRow('依据', basis)
        form.addRow(confirm)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.Save | W.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != W.QDialog.Accepted:
            return
        try:
            if not confirm.isChecked():
                raise ValueError('尚未确认任务列表核对')
            self.catalog.progress.review(scopes, state.currentData(), basis.text())
            self.refresh_progress()
        except Exception as exc:
            self.status.setText(str(exc))

    def refresh_all(self):
        config = self.config()
        _, data = voice_paths(config['dubbing'])
        count = self.catalog.sync_voices(data)
        local = self.root / 'sources/local-inventory.json'
        versions = json.loads(local.read_text(encoding='utf-8')).get('resource_versions', []) if local.exists() else []
        self.source.setText(f"本机资源 {' / '.join(versions) or '未读取'} · 公开数据 Global 3.7.8 · {COMMIT[:12]} · 本机覆盖未验证 · 共享参考声线 {count} 条")
        self.region.blockSignals(True)
        selected = self.region.currentData()
        self.region.clear()
        self.region.addItem('全部区域', '')
        for row in self.catalog.db.execute('SELECT DISTINCT region FROM scopes ORDER BY region'):
            self.region.addItem(row[0], row[0])
        self.region.setCurrentIndex(max(0, self.region.findData(selected)))
        self.region.blockSignals(False)
        self.refresh_scopes()
        self.refresh_prepared()

    def refresh_prepared(self):
        from dialogue_generation import GenerationProject
        from wuwa_workflow import project_quality, quality_label
        self.ready_project = None
        pointer = self.root / 'generation-projects/last-project.txt'
        try:
            if not pointer.is_file():
                raise ValueError('没有已准备任务')
            project = GenerationProject.load(Path(pointer.read_text(encoding='utf-8').strip()))
            if project.game_id != 'wuwa' or not project.rows:
                raise ValueError('没有已准备任务')
            if not all(row.status == 'done' and project.audio_path(row) and not row.review_reason() for row in project.rows):
                raise ValueError('上次项目尚未生成完成')
            self.catalog.validate_project(project)
            self.catalog.progress.validate_generation(project)
            selection = json.loads((project.directory/'selection.json').read_text(encoding='utf-8'))
            names = [self.catalog.db.execute('SELECT name FROM scopes WHERE id=?', (scope,)).fetchone()[0]
                     for scope in selection['scopes']]
            self.ready_project = project
            self.ready_quality = project_quality(project)
            self.ready_note.setText('已生成：' + '、'.join(names) + f' · {len(project.rows)} 句\n' + quality_label(self.ready_quality))
            self.ready_note.setToolTip('音频已生成；实机匹配和主观自然度以实际试听为准。')
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            self.ready_note.setText('暂无可直接播放的任务')
            self.ready_note.setToolTip(str(exc))
        self.play_prepared.setEnabled(self.ready_project is not None and not (self.worker and self.worker.isRunning()))
        self.audition_prepared.setEnabled(self.play_prepared.isEnabled())

    def preview_prepared(self):
        self.refresh_prepared()
        if self.ready_project is not None:
            try:
                self.open_generator(self.ready_project.directory)
            except Exception as exc:
                self.status.setText('未能打开试听：' + str(exc))

    def open_prepared(self):
        # The live page now owns capture and playback; no second modal/worker.
        self.workspace_tabs.setCurrentWidget(self.live)
        self.live.start_live()

    def change_page(self, direction):
        self.page_offset=max(0,self.page_offset+1000*direction)
        self.refresh_scopes(reset=False)

    def change_progress_filter(self):
        completed = self.progress_filter.currentData() == 'completed'
        self.hide_completed.blockSignals(True)
        self.hide_completed.setChecked(not completed)
        self.hide_completed.blockSignals(False)
        self.refresh_scopes()

    def refresh_scopes(self, *_, reset=True):
        if reset:
            self.page_offset=0
        selected = set(self.selected_scopes())
        rows = self.catalog.scopes(self.search.text(), self.kind.currentData(), self.region.currentData(), self.page_offset,
                                   hide_completed=self.hide_completed.isChecked(), progress_status=self.progress_filter.currentData())
        count = self.catalog.scope_count(self.search.text(), self.kind.currentData(), self.region.currentData(),
                                         hide_completed=self.hide_completed.isChecked(), progress_status=self.progress_filter.currentData())
        self.page.setText(f'{self.page_offset+1 if count else 0}–{self.page_offset+len(rows)} / {count}')
        self.previous_page.setEnabled(self.page_offset>0)
        self.next_page.setEnabled(self.page_offset+len(rows)<count)
        self.scopes.blockSignals(True)
        self.scopes.setRowCount(len(rows))
        for index, row in enumerate(rows):
            from wuwa_workflow import scope_voice_label
            voice_state = scope_voice_label(row)
            for col, value in enumerate((row['name'], row['region'], row['total'], PROGRESS_LABELS[row['progress_status']], voice_state)):
                item = W.QTableWidgetItem(str(value))
                item.setData(QtCore.Qt.UserRole, row['id'])
                item.setToolTip(row['name'] + '\n' + row['id'])
                self.scopes.setItem(index, col, item)
                if row['id'] in selected:
                    item.setSelected(True)
        self.scopes.blockSignals(False)
        self.refresh_lines()

    def selected_scopes(self):
        return [self.scopes.item(i.row(), 0).data(QtCore.Qt.UserRole) for i in self.scopes.selectionModel().selectedRows()]

    def selected_rows(self):
        return [self.rows[i.row()] for i in self.lines.selectionModel().selectedRows()]

    def refresh_lines(self):
        self.rows = self.catalog.lines(self.selected_scopes(), self.status_filter.currentData(), self.speaker_filter.text())
        self.lines.blockSignals(True)
        self.lines.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            binding = self.catalog.binding(row['speaker_id'])
            from wuwa_workflow import voice_label
            voice = '首次对白自动选云端音色' if not binding and self.catalog.cloud_only() else voice_label(binding)
            for col, value in enumerate((row['speaker'], row['raw_text'] if self.spoilers.isChecked() else '正文已隐藏', STATUS[row['voice_status']], voice)):
                item = W.QTableWidgetItem(value)
                if col == 2:
                    item.setToolTip(row['basis'])
                if col == 3 and binding:
                    item.setToolTip(binding['voice'])
                self.lines.setItem(index, col, item)
        self.lines.blockSignals(False)
        self.estimate()

    def estimate(self):
        chosen = self.selected_rows()
        self.detail.setVisible(self.spoilers.isChecked() and bool(chosen))
        self.detail.setPlainText('\n\n'.join(row['speaker'] + '\n' + row['raw_text'] + '\n' + row['state_id']
                                            + ' · ' + row['branch'] + '\n' + row['basis'] for row in chosen[:5])
                                 if self.spoilers.isChecked() else '')
        rows = self.selected_rows() or self.rows
        version, config = self.catalog.local_version(), self.config()
        ready, held, chars, clones = 0, 0, 0, 0
        progress_states = self.catalog.progress.line_states(self.selected_scopes())
        allowed = self.catalog.progress.allowed(self.selected_scopes(), self.allow_unknown.isChecked())
        skipped = sum(progress_states.get(row['id']) == 'completed' for row in rows)
        unknown = sum(progress_states.get(row['id'], 'unknown') == 'unknown' for row in rows)
        missing = set()
        auto_cloud = self.catalog.cloud_only()
        for row in rows:
            binding = self.catalog.binding(row['speaker_id'])
            needs_voice = not auto_cloud and (not binding or not binding['transcript_reviewed'])
            if needs_voice:
                missing.add(row['speaker_id'])
            text, unresolved = resolve_text(row['raw_text'], config.get('nickname', '漂泊者'))
            if row['id'] not in allowed or row['voice_status'] != 'unvoiced' or row['review_version'] != version or unresolved or needs_voice:
                held += 1
            else:
                ready += 1
                chars += len(text)
                clones += bool(binding and binding['voice'].startswith(('character:', 'npc:')))
        seconds = chars / 4.5
        busy = bool(self.worker and self.worker.isRunning())
        self.prepare.setEnabled(ready > 0 and not busy)
        self.voice_button.setEnabled(bool(rows) and not busy)
        self.review_button.setEnabled(bool(chosen) and self.spoilers.isChecked() and not busy)
        if not rows:
            self.summary.setText('未选择任务' if not self.selected_scopes() else '没有符合条件的对白')
        else:
            self.summary.setText(f'已选 {len(rows)} 句  ·  可准备 {ready}  ·  暂不生成 {held}\n'
                f'已完成跳过 {skipped}  ·  进度待核对 {unknown}  ·  声线待检查 {len(missing)}\n'
                f'预计音频 {seconds/60:.1f} 分钟 / {seconds*64000/1024**2:.1f} MB')
        self.prepare.setToolTip('已有有效音频会复用；生成期间可以停止，之后继续。' if ready else
            '尚无可生成对白：需要未完成任务、当前版本确认无原声、有效且核对过的声线，以及可解析的文字。')

    def review_lines(self):
        rows = self.selected_rows()
        if not rows or not self.spoilers.isChecked():
            self.status.setText('请显示正文，并选中已经实际核对的台词。')
            return
        dialog = W.QDialog(self)
        dialog.setWindowTitle(f'配音核对 · {len(rows)} 句')
        form = W.QFormLayout(dialog)
        status = W.QComboBox()
        for value, title in STATUS.items():
            status.addItem(title, value)
        basis = W.QPlainTextEdit()
        basis.setPlaceholderText('实机任务、核对日期、对应台词、是否关闭了原声及判定依据')
        confirm = W.QCheckBox('已在当前本机版本核对；未以缺失文件或空字段作为无配音依据')
        form.addRow('判定', status)
        form.addRow('依据', basis)
        form.addRow(confirm)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.Save | W.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != W.QDialog.Accepted:
            return
        try:
            if not confirm.isChecked():
                raise ValueError('尚未确认本机核对')
            inventory(self.config()['install_root'], self.root)
            self.catalog.review([r['id'] for r in rows], status.currentData(), basis.toPlainText())
            self.refresh_lines()
        except Exception as exc:
            self.status.setText(str(exc))

    def bind_voice(self):
        rows = self.selected_rows() or self.rows
        if not rows:
            self.status.setText('先选中一条角色台词。')
            return
        if self.catalog.cloud_only():
            row = rows[0]
            voices = self.catalog.db.execute('SELECT id,name FROM breeze_presets ORDER BY name,id').fetchall()
            labels = [v['name']+' · '+v['id'] for v in voices]
            if not labels:
                self.status.setText('请在鸣潮设置保存云端配置，同步中文音色库')
                return
            binding = self.catalog.binding(row['speaker_id'])
            index = next((i for i,v in enumerate(voices) if binding and binding['voice']=='breeze:'+v['id']),0)
            choice, ok = W.QInputDialog.getItem(self, 'BreezeBlue · '+row['speaker'], '中文音色', labels, index, True)
            if ok and choice in labels:
                with self.catalog.db:
                    self.catalog.db.execute('INSERT OR REPLACE INTO breeze_cast VALUES(?,?)',
                                            (row['speaker_id'],voices[labels.index(choice)]['id']))
            elif ok:
                self.status.setText('请从中文音色列表中选择一项')
        else:
            VoiceBindingDialog(self.catalog, rows[0], self.config(), self).exec()
        self.refresh_lines()

    def review_quest_cast(self):
        from collections import Counter
        from wuwa_cast_review import reviewed_roles, save_review
        scopes = self.selected_scopes()
        if len(scopes)!=1 or not scopes[0].startswith('quest:'):
            self.status.setText('请先选中一个任务，再确认主要人物')
            return
        quest = scopes[0]
        saved = reviewed_roles(self.catalog,quest) or {}
        counts = Counter((r['speaker_id'],r['speaker']) for r in self.catalog.lines([quest]))
        dialog = W.QDialog(self)
        dialog.setWindowTitle('确认主要人物声线')
        layout = W.QVBoxLayout(dialog)
        layout.addWidget(W.QLabel('为主要人物选择声线。需要稍后决定的角色可选“待确认”。'))
        table = W.QTableWidget(len(counts),4)
        table.setHorizontalHeaderLabels(['说话人','句数','配音要求','上下文建议'])
        layout.addWidget(table)
        choices = {}
        opts = [('配角 / 拟声',''),('待确认','pending'),('女声','female'),('男声','male'),
                ('不限音色性别','any'),('使用角色原声','original')]
        from wuwa_cast_context import quest_suggestion
        lines = self.catalog.lines([quest])
        labels = {'female':'女声','male':'男声','old':'老年','child':'儿童','young':'年轻'}
        for index,((sid,name),count) in enumerate(counts.most_common()):
            table.setItem(index,0,W.QTableWidgetItem('漂泊者' if name=='{PlayerName}' else name))
            table.setItem(index,1,W.QTableWidgetItem(str(count)))
            gender, age, cues = quest_suggestion(lines, sid, name)
            hint = ' · '.join(labels[v] for v in (age, gender) if v) or '待判断'
            item = W.QTableWidgetItem(hint)
            item.setToolTip('、'.join(cues[:8]))
            table.setItem(index,3,item)
            combo = W.QComboBox()
            for text,value in opts:
                combo.addItem(text,value)
            original = self.catalog.db.execute('SELECT 1 FROM original_voice_roles WHERE speaker_id=?',(sid,)).fetchone()
            combo.setCurrentIndex(combo.findData('original' if original else saved.get(sid,'')))
            combo.setEnabled(not original)
            choices[sid] = combo
            table.setCellWidget(index,2,combo)
        table.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
        table.resizeColumnsToContents()
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.Save | W.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(710,440)
        if dialog.exec()==W.QDialog.Accepted:
            try:
                save_review(self.catalog,quest,{sid:w.currentData() for sid,w in choices.items() if w.currentData()})
                self.refresh_lines()
                self.status.setText('主要人物声线已保存')
            except ValueError as exc:
                self.status.setText(str(exc))

    def import_capture(self):
        path, _ = W.QFileDialog.getOpenFileName(self, '鸣潮采集记录', str(self.root/'records'), '识别记录 (*.jsonl)')
        if not path:
            return
        name, okay = W.QInputDialog.getText(self, '采集范围', '任务或 NPC 名称')
        if okay:
            try:
                count = self.catalog.import_capture(path,name)
                self.status.setText(f'已导入 {count} 句，配音状态仍待人工核对')
                self.refresh_all()
            except Exception as exc:
                self.status.setText(str(exc))

    def prepare_project(self):
        try:
            if not self.config().get('setup_complete'):
                if SettingsDialog(self.root, self).exec() != W.QDialog.Accepted:
                    return
            inventory(self.config()['install_root'], self.root)
            self.catalog.progress.refresh(self.config()['install_root'])
            chosen = self.selected_rows() or self.rows
            options = dict(self.config(), allow_unknown_progress=self.allow_unknown.isChecked())
            project, held = self.catalog.prepare_project(self.selected_scopes(), options, {r['id'] for r in chosen})
            self.status.setText(f'项目已准备：{len(project.rows)} 句，跳过或保留 {held} 句；相同项目自动复用')
            self.open_generator(project.directory)
        except Exception as exc:
            self.status.setText(str(exc))

    def open_generator(self, path=None):
        from dialogue_generator_ui import GeneratorWindow
        config = self.config()
        self.child = GeneratorWindow(config['dubbing'], self, root=self.root, project_path=path)
        try:
            self.child.exec()
        finally:
            self.child = None
            self.refresh_prepared()

    def open_nte(self):
        try:
            from dialogue_generator_ui import GeneratorWindow
            config = json.loads((CODE_ROOT / 'config.json').read_text(encoding='utf-8-sig'))
            self.child = GeneratorWindow(config.get('dubbing', {}), self)
            self.child.exec()
        except Exception as exc:
            self.status.setText(str(exc))
        finally:
            self.child = None

    def resume_project(self):
        try:
            if self.game.currentData() == 'nte':
                self.open_nte()
            else:
                self.open_generator()
        except Exception as exc:
            self.status.setText(str(exc))

    def open_existing(self):
        if self.game.currentData() == 'nte':
            self.open_nte()
            return
        path, _ = W.QFileDialog.getOpenFileName(self, '打开鸣潮项目', str(self.root / 'generation-projects'), '项目 (project.json)')
        if path:
            try:
                self.open_generator(Path(path))
            except Exception as exc:
                self.status.setText(str(exc))

    def start_job(self, fn):
        if self.worker and self.worker.isRunning():
            return
        self.worker = Job(fn, self)
        self.worker.message.connect(self.status.setText)
        self.worker.error.connect(lambda text: self.status.setText('未完成：' + text))
        self.worker.result.connect(lambda result: self.status.setText('完成：' + str(result)))
        self.worker.finished.connect(self.job_finished)
        for widget in (self.game, self.settings, self.import_button, self.probe_button, self.launch_button,
                       self.prepare, self.review_button, self.voice_button, self.calibrate_button, self.capture_import,
                       self.progress_button, self.progress_review, self.play_prepared, self.audition_prepared):
            widget.setEnabled(False)
        self.stop.setEnabled(True)
        self.worker.start()

    def start_import(self):
        root = self.root
        def work(stop, progress):
            folder = download_tables(root, stop, progress)
            catalog = Catalog(root)
            try:
                return catalog.import_public(folder, stop, progress)
            finally:
                catalog.close()
        self.start_job(work)

    def start_probe(self):
        root, install = self.root, self.config()['install_root']
        self.start_job(lambda stop, progress: probe_local(root, install, progress)['parser_status'])

    def launch_game(self):
        from wuwa_launch import launch_and_monitor
        config = self.config()
        self.start_job(lambda stop, progress: launch_and_monitor(config, self.root, stop, progress))

    def calibrate(self):
        if self.live.running():
            self.workspace_tabs.setCurrentWidget(self.live)
            self.status.setText('正在自动配置字幕区域；如需手动校准，请先停止实时配音。')
            return
        try:
            from generator_ocr import OcrCollectionDialog
            self.child = OcrCollectionDialog(self.root, self)
            self.child.exec()
        except Exception as exc:
            self.status.setText(str(exc))
        finally:
            self.child = None

    def stop_job(self):
        if self.worker:
            self.worker.stop.set()
            self.status.setText('正在停止…')

    def job_finished(self):
        for widget in (self.game, self.settings, self.import_button, self.probe_button, self.launch_button,
                       self.prepare, self.review_button, self.voice_button, self.calibrate_button, self.capture_import,
                       self.progress_button, self.progress_review):
            widget.setEnabled(True)
        self.stop.setEnabled(False)
        self.refresh_all()
        if self.closing:
            self.close()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.live.running():
            self.closing = True
            self.live.stop_live()
            event.ignore()
            return
        if self.worker and self.worker.isRunning():
            self.closing = True
            self.stop_job()
            event.ignore()
            return
        self.live.jobs.close()
        self.catalog.close()
        self.live.timer.stop()
        self.lock.unlock()
        event.accept()
