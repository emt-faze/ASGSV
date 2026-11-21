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
from wuwa_catalog import Catalog
from wuwa_resources import inventory, probe_local
from quest_library import QuestLibrary, generate_missing
from wuwa_surface import AccentButton, GameSelector
from generator_ui_support import studio_font
from wuwa_identity import HOME_SANS, configure_home
from asgsv_identity import application_icon
from ui.icons import bind_button_icon, get_icon
from ui.workbench import RoleVoices, Sidebar

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
        self.catalog = parent.catalog
        self.config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
        self.setWindowTitle('鸣潮设置')
        self.resize(610, 345)
        layout = W.QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        self.settings_tabs = W.QTabWidget()
        layout.addWidget(self.settings_tabs, 1)

        def form_page(title):
            page = W.QWidget()
            form = W.QFormLayout(page)
            form.setContentsMargins(12, 18, 12, 12)
            form.setVerticalSpacing(12)
            form.setFieldGrowthPolicy(W.QFormLayout.AllNonFixedFieldsGrow)
            self.settings_tabs.addTab(page, title)
            return form

        game_form = form_page('游戏与角色')
        keys_form = form_page('播放快捷键')
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
        for title, widget in (('昵称', self.nickname), ('漂泊者', self.gender), ('默认旁白', self.narrator)):
            game_form.addRow(title, widget)
        path_row=W.QHBoxLayout()
        path_row.addWidget(self.install,1)
        self.browse=W.QPushButton('手动选择')
        self.browse.clicked.connect(self.choose_install)
        path_row.addWidget(self.browse)
        game_form.insertRow(0,'游戏目录',path_row)
        self.probe_note=W.QLabel()
        self.probe_note.setWordWrap(True)
        game_form.addRow('探测状态',self.probe_note)
        self.probe=W.QPushButton('本机资源探测')
        self.probe.clicked.connect(parent.start_probe)
        data_actions = W.QHBoxLayout()
        data_actions.addWidget(self.probe)
        for label, action in (('更新台词索引', parent.start_import),
                              ('导入采集', parent.import_capture)):
            button = W.QPushButton(label)
            button.clicked.connect(action)
            data_actions.addWidget(button)
        game_form.addRow('台词数据', data_actions)
        self.keys = {}
        for action, title, default in (('pause', '暂停 / 恢复', 'F8'), ('replay', '重播', 'F9'), ('skip', '跳过', 'F10')):
            combo = W.QComboBox()
            combo.addItems(['F' + str(n) for n in range(1, 25)])
            combo.setCurrentText(self.config.get('hotkeys', {}).get(action, default))
            self.keys[action] = combo
            keys_form.addRow(title, combo)
        from wuwa_pronunciation import DEFAULT_TERMS
        self.lexicon = W.QFrame()
        self.lexicon.setObjectName('lexiconCard')
        self.lexicon.setStyleSheet('QFrame#lexiconCard {background:#141D21; border:1px solid #23333A; border-radius:10px;}')
        lex_layout = W.QVBoxLayout(self.lexicon)
        lex_layout.setContentsMargins(14, 12, 14, 12)
        lex_layout.addWidget(W.QLabel('专有名词发音词典'))
        self.terms = W.QTableWidget(0, 2)
        self.terms.setHorizontalHeaderLabels(['原文词汇', 'TTS 读音替换'])
        self.terms.horizontalHeader().setSectionResizeMode(W.QHeaderView.Stretch)
        self.terms.verticalHeader().hide()
        self.terms.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        self.terms.setSelectionMode(W.QAbstractItemView.SingleSelection)
        self.terms.setAccessibleName('专有名词发音词典')
        lex_layout.addWidget(self.terms, 1)
        for term, replacement in self.config.get('tts_pronunciations', DEFAULT_TERMS).items():
            self.add_term(term, replacement)
        actions = W.QHBoxLayout()
        add = W.QPushButton('新增词条')
        add.clicked.connect(self.new_term)
        remove = W.QPushButton('删除')
        remove.setEnabled(False)
        remove.clicked.connect(lambda: self.terms.removeRow(self.terms.currentRow()))
        self.terms.itemSelectionChanged.connect(lambda: remove.setEnabled(bool(self.terms.selectedItems())))
        actions.addWidget(add)
        actions.addWidget(remove)
        actions.addStretch()
        lex_layout.addLayout(actions)
        self.settings_tabs.addTab(self.lexicon, '发音词典')
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.Save | W.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(W.QDialogButtonBox.Save).setText('保存')
        buttons.button(W.QDialogButtonBox.Save).setObjectName('primary')
        buttons.button(W.QDialogButtonBox.Cancel).setText('取消')
        layout.addWidget(buttons)

    def choose_install(self):
        folder=W.QFileDialog.getExistingDirectory(self,'选择鸣潮安装目录',self.install.text())
        if folder:
            self.install.setText(folder)

    def add_term(self, term, replacement):
        row = self.terms.rowCount()
        self.terms.insertRow(row)
        for column, value in enumerate((term, replacement)):
            self.terms.setItem(row, column, W.QTableWidgetItem(value))

    def new_term(self):
        self.add_term('', '')
        item = self.terms.item(self.terms.rowCount() - 1, 0)
        self.terms.setCurrentItem(item)
        self.terms.editItem(item)

    def accept(self):
        keys = {key: widget.currentText() for key, widget in self.keys.items()}
        if len(set(keys.values())) != len(keys) or not self.nickname.text().strip():
            self.settings_tabs.setCurrentIndex(0 if not self.nickname.text().strip() else 1)
            W.QMessageBox.warning(self, '设置未保存', '昵称不能为空，快捷键不可重复。')
            return
        terms = {}
        for row in range(self.terms.rowCount()):
            term, replacement = (self.terms.item(row, col).text().strip() for col in range(2))
            if not term or not replacement or term in terms:
                self.settings_tabs.setCurrentWidget(self.lexicon)
                self.terms.setCurrentCell(row, 0 if not term or term in terms else 1)
                W.QMessageBox.warning(self, '词典未保存', f'请检查第 {row + 1} 行。两列都需要填写，原文词汇不能重复。')
                return
            terms[term] = replacement
        try:
            inventory(self.install.text().strip(), self.root)
        except Exception as exc:
            W.QMessageBox.warning(self, '设置未保存', str(exc))
            return
        self.config.update(install_root=self.install.text().strip(), nickname=self.nickname.text().strip(),
                           traveler_gender=self.gender.currentData(), narrator_voice=self.narrator.currentData(), hotkeys=keys,
                           setup_complete=True, tts_pronunciations=terms)
        atomic_text(self.root / 'config.json', json.dumps(self.config, ensure_ascii=False, indent=2))
        catalog = Catalog(self.root)
        try:
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
        self.voice_search=W.QLineEdit()
        self.voice_search.setPlaceholderText('搜索声线、角色或关键词')
        layout.addWidget(self.voice_search)
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
        def filter_voices(text):
            matches=[]
            for i in range(self.voices.count()):
                hit=text.strip().casefold() in (self.voices.itemText(i)+' '+str(self.voices.itemData(i))).casefold()
                self.voices.view().setRowHidden(i,not hit)
                if hit:
                    matches.append(i)
            if self.voices.currentIndex() not in matches:
                self.voices.setCurrentIndex(matches[0] if matches else -1)
        self.voice_search.textChanged.connect(filter_voices)
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
        bind_button_icon(reference, 'play')
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
    def __init__(self, *, root=None, preference=None, game_id=None):
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
        self.preference = Path(preference) if preference is not None else CODE_ROOT.parent / 'game-data/entry.json'
        self.lock = QtCore.QLockFile(str(self.preference.with_suffix('.lock')))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            raise ValueError('双游戏工具已打开，请使用已有窗口')
        self.setWindowTitle('ASGSV · 配音工作台')
        self.setWindowIcon(application_icon())
        self.resize(1180, 700)
        self.setMinimumSize(960, 680)
        self.setWindowFlag(QtCore.Qt.WindowMinimizeButtonHint, True)
        self.setWindowFlag(QtCore.Qt.WindowMaximizeButtonHint, True)
        layout = W.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = W.QFrame()
        header.setObjectName('workbenchHeader')
        header.setFixedHeight(54)
        top = W.QHBoxLayout(header)
        top.setContentsMargins(17, 0, 14, 0)
        top.setSpacing(8)
        mark = W.QLabel()
        mark.setPixmap(get_icon('asgsv_logo', '#D8B467', 26).pixmap(QtCore.QSize(26, 26), self.devicePixelRatioF()))
        top.addWidget(mark)
        self.workbench_title = W.QLabel()
        self.workbench_title.setObjectName('workbenchTitle')
        top.addWidget(self.workbench_title)
        version = Path(__file__).with_name('GENERATOR_VERSION').read_text(encoding='utf-8').strip()
        badge = W.QLabel('v' + version)
        badge.setObjectName('versionBadge')
        top.addWidget(badge, 0, QtCore.Qt.AlignVCenter)
        top.addStretch()
        self.game = GameSelector()
        self.game.setAccessibleName('选择游戏')
        self.game.setToolTip('选择游戏')
        self.game.addItem('鸣潮', 'wuwa')
        self.game.addItem('异环', 'nte')
        self.game.setMinimumWidth(84)
        top.addWidget(self.game)
        for title, icon, tip, slot in (
            ('打开项目', 'folder', '选择并打开已有配音项目', self.open_existing),
            ('继续上次项目', 'history', '继续上次打开的配音项目', self.resume_project),
            ('设置', 'settings', '打开鸣潮设置，调整游戏目录、角色和快捷键', self.open_settings),
        ):
            button = W.QToolButton()
            button.setObjectName('headerTool')
            button.setAccessibleName(title)
            button.setToolTip(tip)
            button.setFixedSize(32, 32)
            bind_button_icon(button, icon, size=16)
            button.clicked.connect(slot)
            top.addWidget(button)
            if icon == 'settings':
                self.settings = button
        self.update_button=W.QPushButton('更新')
        self.update_button.setToolTip('检查在线升级、更新配音资源或导入网盘更新包')
        self.update_button.clicked.connect(self.open_update_center)
        top.addWidget(self.update_button)
        self.advanced_toggle = W.QToolButton()
        self.advanced_toggle.setText('高级')
        self.advanced_toggle.setToolTip('异环采集与字幕校准选项')
        self.advanced_toggle.setCheckable(True)
        top.addWidget(self.advanced_toggle)
        layout.addWidget(header)
        self.wuwa = W.QWidget()
        wuwa_layout = W.QHBoxLayout(self.wuwa)
        wuwa_layout.setContentsMargins(0, 0, 0, 0)
        wuwa_layout.setSpacing(0)
        self.sidebar = Sidebar()
        wuwa_layout.addWidget(self.sidebar)
        content = W.QVBoxLayout()
        content.setContentsMargins(14, 6, 14, 4)
        wuwa_layout.addLayout(content, 1)
        self.workspace_tabs = W.QStackedWidget()
        content.addWidget(self.workspace_tabs, 1)
        from wuwa_live_ui import LivePanel
        self.live = LivePanel(self.root, self)
        self.launch_button = self.live.launch_button
        self.launch_button.clicked.connect(self.launch_game)
        self.launch_button.clicked.connect(self.live.start_live)
        self.live.task_selected.connect(self.show_live_task)
        self.live.stopped.connect(self.live_stopped)
        top.addWidget(self.live.start)
        self.task_library = QuestLibrary(self.catalog)
        self.task_library.voice_requested.connect(self.edit_voice)
        self.task_library.generate_requested.connect(self.generate_quest)
        self.task_library.notice.connect(lambda text: self.status.setText(text))
        self.roles = RoleVoices(self.catalog)
        self.roles.edit_requested.connect(self.edit_voice)
        self.settings_page = W.QWidget()
        settings_layout = W.QVBoxLayout(self.settings_page)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_editor = None
        self.pages = {'实时配音': self.live, '任务库': self.task_library, '角色音色': self.roles,
                      '运行日志': self.live.diagnostic, '设置': self.settings_page}
        for page in self.pages.values():
            self.workspace_tabs.addWidget(page)
        self.live.log.setMinimumHeight(86)
        self.live.log.setMaximumHeight(16777215)
        self.live.diagnostic.layout().setStretchFactor(self.live.log, 1)
        self.sidebar.requested.connect(self.navigate)
        self.sidebar.buttons['实时配音'].setChecked(True)
        self.live.library.clicked.connect(lambda: self.navigate('任务库'))
        layout.addWidget(self.wuwa, 1)
        from nte_home import NteHomePanel
        self.nte = NteHomePanel(CODE_ROOT, self)
        self.nte.project_requested.connect(self.open_nte)
        self.nte.stopped.connect(self.live_stopped)
        self.nte.busy_changed.connect(self.update_stop_button)
        self.advanced_toggle.toggled.connect(self.nte.advanced.setVisible)
        layout.addWidget(self.nte, 1)
        # The shared footer keeps background-job messages visible on either tab.
        status_row = self.live.footer.layout()
        self.status = W.QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName('subtle')
        status_row.insertWidget(1, self.status, 1)
        self.stop = self.button(status_row, '停止后台任务', self.stop_job, glyph='stop')
        self.stop.setObjectName('danger')
        self.stop.hide()
        self.content_layout = content
        self.home_layout = layout
        content.addWidget(self.live.footer)
        self.workspace_tabs.currentChanged.connect(self.update_footer)
        self.game.currentIndexChanged.connect(self.switch_game)
        selected = 'wuwa'
        if self.preference.exists():
            try:
                selected = json.loads(self.preference.read_text(encoding='utf-8')).get('game_id', selected)
            except ValueError:
                pass
        if game_id is not None:
            from game_profiles import validate_game
            selected = validate_game(game_id)
        self.game.setCurrentIndex(max(0, self.game.findData(selected)))
        self.refresh_all()
        self.switch_game()
        configure_home(self)
        from workbench_ipc import WorkbenchRequests
        self.requests = WorkbenchRequests(self.preference, self)
        self.requests.requested.connect(self.request_workspace)
        from wuwa_update import notify_healthy
        QtCore.QTimer.singleShot(0,lambda:notify_healthy(CODE_ROOT.parent))

    def open_update_center(self):
        from wuwa_update_ui import UpdateCenter
        dialog=UpdateCenter(CODE_ROOT.parent,self)
        dialog.exec()
        dialog.deleteLater()

    def queue_program_update(self,archive):
        from wuwa_update_ui import launch_apply
        self.update_button.setEnabled(False)
        def restart_when_idle():
            if ((self.worker and self.worker.isRunning()) or self.nte.busy()
                    or self.live.progress_start is not None or self.live.jobs.activity[0]=='busy'
                    or (self.child and self.child.isVisible())):
                self.status.setText('新版已就绪，将在当前播放、编辑或生成结束后升级')
                QtCore.QTimer.singleShot(700,restart_when_idle)
                return
            try:
                launch_apply(CODE_ROOT.parent,archive)
            except OSError as exc:
                self.status.setText('启动更新器失败 · '+str(exc))
                self.update_button.setEnabled(True)
                return
            self.close()
        QtCore.QTimer.singleShot(0,restart_when_idle)

    def showEvent(self, event):
        super().showEvent(event)
        if os.name == 'nt' and QtGui.QGuiApplication.platformName() == 'windows':
            import ctypes
            from ctypes import wintypes
            # COLORREF uses 0x00BBGGRR. Only the non-client colors change.
            set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
            set_attr.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            set_attr.restype = ctypes.c_long
            for attribute, color in ((35, 0x16130D), (36, 0xF6F6F0)):
                value = wintypes.COLORREF(color)
                set_attr(int(self.winId()), attribute, ctypes.byref(value), ctypes.sizeof(value))

    def button(self, row, title, callback, *, glyph=None):
        button = AccentButton(title, glyph)
        button.clicked.connect(callback)
        row.addWidget(button)
        return button

    def config(self):
        return json.loads((self.root / 'config.json').read_text(encoding='utf-8'))

    def switch_game(self):
        wuwa = self.game.currentData() == 'wuwa'
        if not wuwa and self.live.running():
            self.live.stop_live()
        if wuwa and self.nte.live.running():
            self.nte.live.stop_live()
        footer_target = self.content_layout if wuwa else self.home_layout
        if footer_target.indexOf(self.live.footer) < 0:
            footer_target.addWidget(self.live.footer)
        self.wuwa.setVisible(wuwa)
        self.nte.setVisible(not wuwa)
        self.settings.setToolTip('打开鸣潮设置，调整游戏目录、角色和快捷键' if wuwa else '打开异环连接与字幕校准')
        self.settings.setEnabled(True)
        self.live.start.setVisible(wuwa)
        self.advanced_toggle.setVisible(not wuwa)
        if not wuwa:
            self.nte.refresh()
            if not self.nte.live.running():
                self.nte.live.start.setEnabled(not self.live.running() and not self.nte.busy())
        self.update_stop_button()
        self.update_footer()
        self.workbench_title.setText(f'ASGSV · {self.game.currentText()}配音工作台')
        self.setWindowTitle('ASGSV · ' + self.game.currentText() + '配音工作台')
        atomic_text(self.preference, json.dumps({'game_id': self.game.currentData()}))

    def update_stop_button(self, *_):
        active = self.nte.busy() if self.game.currentData() == 'nte' else bool(self.worker and self.worker.isRunning())
        self.stop.setVisible(active)

    def request_workspace(self, game_id, library=False):
        if self.closing:
            return
        index = self.game.findData(game_id)
        if index < 0:
            return
        self.game.setCurrentIndex(index)
        if library:
            if game_id == 'nte':
                self.nte.workspace_tabs.setCurrentWidget(self.nte.task_library)
            else:
                self.navigate('任务库')
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if self.child:
            self.child.raise_()
            self.child.activateWindow()

    def update_footer(self):
        wuwa = self.game.currentData() == 'wuwa'
        self.live.library.setVisible(wuwa)
        self.live.details.hide()
        page = self.workspace_tabs.currentWidget()
        for name, widget in self.pages.items():
            if widget is page:
                if page is self.settings_page and self.settings_editor and self.settings_editor.settings_tabs.currentIndex() == 1:
                    name = '快捷键'
                self.sidebar.buttons[name].setChecked(True)
                break

    def show_live_task(self, scope):
        self.navigate('任务库')
        self.task_library.focus_scope(scope)

    def live_stopped(self):
        if self.closing:
            self.close()
        elif self.game.currentData() == 'nte' and not self.nte.live.running():
            self.nte.live.start.setEnabled(not self.live.running() and not self.nte.busy())

    def navigate(self, name):
        if name in ('设置', '快捷键'):
            if self.settings_editor is None:
                editor = SettingsDialog(self.root, self)
                editor.setWindowFlags(QtCore.Qt.Widget)
                self.settings_page.layout().addWidget(editor)
                self.settings_editor = editor
                editor.finished.connect(self.finish_settings)
                editor.settings_tabs.currentChanged.connect(self.update_footer)
            self.settings_editor.settings_tabs.setCurrentIndex(1 if name == '快捷键' else 0)
            self.workspace_tabs.setCurrentWidget(self.settings_page)
        else:
            if name == '角色音色':
                self.roles.refresh()
            self.workspace_tabs.setCurrentWidget(self.pages[name])

    def finish_settings(self, result):
        editor = self.settings_editor
        self.settings_editor = None
        self.navigate('实时配音')
        editor.deleteLater()
        if result == W.QDialog.Accepted:
            self.task_library.reload()

    def open_settings(self):
        if self.game.currentData() == 'nte':
            self.nte.open_live(dubbing=False)
            return
        self.navigate('设置')

    def refresh_all(self):
        if not self.catalog.cloud_only():
            _, data = voice_paths(self.config()['dubbing'])
            self.catalog.sync_voices(data)
        self.task_library.reload()

    def generate_quest(self, scope, ids):
        if self.worker and self.worker.isRunning():
            self.task_library.set_work_status('已有后台任务正在运行，请在完成后重试')
            return
        self.generating_scope = scope
        self.task_library.set_work_status('正在核对已有录音、声线和当日预算…')
        selection = self.catalog.progress.selection
        discovered_only = self.task_library.discovered_only.isChecked()
        self.start_job(lambda stop, progress: generate_missing(self.root, scope, ids, stop, progress,
            self.task_library.line_ready.emit, selection=selection, discovered_only=discovered_only))

    def edit_voice(self, row):
        if self.catalog.cloud_only():
            from wuwa_voice_select import CloudVoiceDialog
            dialog=CloudVoiceDialog(self.catalog,row,self)
            if dialog.exec()!=W.QDialog.Accepted:
                return
        else:
            VoiceBindingDialog(self.catalog, row, self.config(), self).exec()
        if self.task_library.isVisible():
            self.task_library.requested_scope = self.task_library.scope
        self.task_library.reload()
        self.roles.refresh()

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

    def open_generator(self, path=None):
        from dialogue_generator_ui import GeneratorWindow
        config = self.config()
        self.child = GeneratorWindow(config['dubbing'], self, root=self.root, project_path=path)
        try:
            self.child.exec()
        finally:
            self.child = None
            self.task_library.reload()

    def open_nte(self, path=None, action='prepare'):
        if self.nte.busy():
            self.nte.defer_project(path,action)
            return
        selected_path = path
        try:
            from dialogue_generator_ui import GeneratorWindow
            config = json.loads((CODE_ROOT / 'config.json').read_text(encoding='utf-8-sig'))
            self.child = GeneratorWindow(dict(config.get('dubbing', {}), game_id='nte'), self,
                                         root=CODE_ROOT, project_path=path)
            if action == 'collect':
                QtCore.QTimer.singleShot(0, self.child.open_ocr)
            elif action == 'playback':
                QtCore.QTimer.singleShot(0, self.child.open_playback)
            self.child.exec()
            selected_path = self.child.project.directory
        except Exception as exc:
            self.status.setText(str(exc))
        finally:
            self.child = None
            self.nte.refresh(project_path=selected_path)

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
            path, _ = W.QFileDialog.getOpenFileName(self, '打开异环项目',
                str(CODE_ROOT / 'generation-projects'), '项目 (project.json)')
            if path:
                self.open_nte(Path(path))
            return
        path, _ = W.QFileDialog.getOpenFileName(self, '打开鸣潮项目', str(self.root / 'generation-projects'), '项目 (project.json)')
        if path:
            try:
                self.open_generator(Path(path))
            except Exception as exc:
                self.status.setText(str(exc))

    def start_job(self, fn):
        if self.worker and self.worker.isRunning():
            self.status.setText('已有后台任务正在运行')
            return
        self.live.launch_busy = True
        self.live.update_launch_button()
        self.worker = Job(fn, self)
        def report(text):
            self.status.setText(text)
            if getattr(self,'generating_scope',None):
                self.task_library.set_work_status(text)
            if getattr(self,'probing',False) and self.settings_editor:
                self.settings_editor.probe_note.setText(text)
        self.worker.message.connect(report)
        self.worker.error.connect(lambda text: report('未完成 · '+text))
        self.worker.result.connect(lambda result: report(str(result)))
        self.worker.finished.connect(self.job_finished)
        self.game.setEnabled(False)
        self.settings.setEnabled(False)
        self.task_library.busy = True
        self.task_library.update_actions()
        self.stop.show()
        self.worker.start()

    def start_import(self):
        from wuwa_update import update_index
        self.start_job(lambda stop, progress: update_index(CODE_ROOT.parent, self.root, stop, progress))

    def start_probe(self):
        from wuwa_game_discovery import detect_install
        if self.worker and self.worker.isRunning():
            self.status.setText('请等待当前后台任务结束')
            return
        self.probing=True
        editor=self.settings_editor
        preferred=editor.install.text().strip() if editor else self.config().get('install_root','')
        if editor:
            editor.probe.setEnabled(False)
            editor.probe_note.setText('正在探测本机游戏位置…')
        def work(stop,progress):
            path,source,details=detect_install(preferred,stop,progress)
            inventory(path,self.root)
            return path,source,details
        def found(result):
            path,source,details=result
            cfg=self.config()
            cfg['install_root']=path
            try:
                atomic_text(self.root/'config.json',json.dumps(cfg,ensure_ascii=False,indent=2))
            except OSError as exc:
                msg='目录已识别，保存失败 · '+str(exc)
            else:
                msg=f'已锁定 {path} · {source} · 已校验 {details}'
                if self.settings_editor:
                    self.settings_editor.install.setText(path)
                    self.settings_editor.config['install_root']=path
            self.status.setText(msg)
            if self.settings_editor:
                self.settings_editor.probe_note.setText(msg)
        self.start_job(work)
        self.worker.result.connect(found)

    def launch_game(self):
        from wuwa_launch import launch_and_monitor
        config = self.config()
        self.start_job(lambda stop, progress: launch_and_monitor(config, self.root, stop, progress))

    def stop_job(self):
        if self.game.currentData() == 'nte':
            self.nte.stop()
            return
        if self.worker:
            self.worker.stop.set()
            self.status.setText('正在停止…')

    def job_finished(self):
        if getattr(self,"probing",False):
            self.probing=False
            if self.settings_editor:
                self.settings_editor.probe.setEnabled(True)
        self.live.launch_busy = False
        self.live.update_launch_button()
        self.game.setEnabled(True)
        self.settings.setEnabled(True)
        self.task_library.busy = False
        generated = getattr(self, 'generating_scope', None)
        if generated and self.task_library.scope == generated:
            self.task_library.requested_scope = generated
        self.generating_scope = None
        self.stop.hide()
        self.refresh_all()
        if self.closing:
            self.close()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.nte.busy():
            self.closing = True
            self.nte.stop()
            event.ignore()
            return
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
        if not self.nte.shutdown():
            event.ignore()
            return
        self.requests.close()
        if self.live.hud:
            self.live.hud.hide()
        self.task_library.shutdown()
        self.live.jobs.close()
        self.catalog.close()
        self.live.timer.stop()
        self.lock.unlock()
        event.accept()
