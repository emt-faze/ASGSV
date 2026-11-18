"""Visible live dashboard; no capture, OCR or synthesis runs on the GUI thread."""
from __future__ import annotations

from collections import deque
from contextlib import closing
import json
import os
import sqlite3
from pathlib import Path
import time

import psutil

from PySide6 import QtCore, QtGui, QtWidgets as W

from dialogue_generation import atomic_text
from game_profiles import WUWA_ROOT
from generator_ui_support import studio_font
from wuwa_identity import HOME_SANS, DialogueStage
from wuwa_log_live import LogWorker
from wuwa_live_hud import LiveHud
from ui.icons import bind_button_icon, get_pixmap, get_icon
from ui.stage_background import BackgroundMenu, show_at
from wuwa_surface import AccentButton, CapsuleText, LibraryLink, PulseLamp, ResonanceWaveWidget, ToggleSwitch, TransportButton, TelemetryBadge


GAME_EXECUTABLE = 'Client-Win64-Shipping.exe'
DEMO_CLIP = Path(__file__).with_name('assets') / 'dialogue-preview.flac'
DEMO_DURATION_MS = 4000
DEMO_TIP = '试听鲙娘的本地配音样例，使用系统当前音频输出'


class GameProcessProbe(QtCore.QObject):
    sampled = QtCore.Signal(str, str)

    def __init__(self, root):
        super().__init__()
        self.root = root

    def sample(self):
        # Some other Unreal games use this same filename. Verify the configured install.
        try:
            config = json.loads((self.root / 'config.json').read_text(encoding='utf-8'))
            expected = Path(config['install_root']) / 'Wuthering Waves Game/Client/Binaries/Win64' / GAME_EXECUTABLE
            state, detail = 'off', '未检测到鸣潮进程'
            for process in psutil.process_iter(['name']):
                if (process.info['name'] or '').casefold() != GAME_EXECUTABLE.casefold():
                    continue
                try:
                    path = process.exe()
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied:
                    state, detail = 'warning', '检测到同名进程，无法核对安装路径'
                    continue
                if Path(path) == expected:
                    state, detail = 'ready', f'鸣潮进程运行中 · PID {process.pid}'
                    break
        except (OSError, ValueError, KeyError, psutil.Error) as exc:
            state, detail = 'warning', '进程状态未能读取：' + str(exc)
        self.sampled.emit(state, detail)


class SpeakerBadge(W.QPushButton):
    def __init__(self):
        super().__init__('等待对白')
        self.setObjectName('liveSpeaker')
        self.setAutoDefault(False)
        self.setEnabled(False)
        self.arrow = get_icon('chevron_down', '#E4C684', 12)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isEnabled() and (self.underMouse() or self.hasFocus()):
            with QtGui.QPainter(self) as painter:
                self.arrow.paint(painter,self.width()-18,(self.height()-12)//2,12,12)


class DialogueBody(W.QTextEdit):
    """Give dialogue the available reading space and let long subtitles scroll."""

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(64)
        self.setSizePolicy(W.QSizePolicy.Expanding, W.QSizePolicy.Expanding)
        self.document().setDocumentMargin(1)

    def setPlainText(self, text):
        super().setPlainText(text)
        cursor = self.textCursor()
        cursor.select(QtGui.QTextCursor.Document)
        paragraph = QtGui.QTextBlockFormat()
        paragraph.setLineHeight(126, QtGui.QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(paragraph)
        self.verticalScrollBar().setValue(0)


class LivePanel(W.QWidget):
    generation_notice = QtCore.Signal(str)
    task_selected = QtCore.Signal(str)
    stopped = QtCore.Signal()

    def __init__(self, root, parent=None, *, auto_start=None):
        super().__init__(parent)
        self.root = Path(root)
        from wuwa_log_generation import GenerationService
        self.generation_notice.connect(self.generation_status)
        self.jobs = GenerationService(self.root,self.generation_notice.emit)
        self.worker = None
        self.hotkeys = None
        self.active_run = None
        self.last_sequence = 0
        self.last_frame_at = 0.
        self.played = 0
        self.task_id = ''
        self.task_counts = {}
        self.recognized = {}
        self.last_playback = {}
        self.link_active = False
        self.rois = None
        self.scene = None
        self.events = deque(maxlen=80)
        self.hud = None
        self.hud_history_line = None
        self.progress_start = None
        self.progress_duration = 0
        self._last_log = ''
        self._last_health = ''
        self._last_health_at = 0.
        self.last_publish = 0.
        self.demo_player = None
        self.demo_output = None
        self.demo_snapshot = None
        self.demo_clock = QtCore.QElapsedTimer()
        self.demo_silent = False
        self.setObjectName('livePanel')
        shell = W.QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        scroll = W.QScrollArea()
        scroll.setObjectName('liveScroll')
        scroll.setFrameShape(W.QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        page = W.QWidget()
        page.setObjectName('livePage')
        scroll.setWidget(page)
        shell.addWidget(scroll)
        layout = W.QVBoxLayout(page)
        layout.setContentsMargins(3, 4, 3, 3)
        layout.setSpacing(8)

        def label(text, name):
            widget = W.QLabel(text)
            widget.setObjectName(name)
            widget.setTextFormat(QtCore.Qt.PlainText)
            widget.setWordWrap(True)
            return widget

        def caption(text):
            widget = label(text, 'hudCaption')
            widget.setWordWrap(False)
            widget.setFont(studio_font(HOME_SANS, 10, tracking=1.5))
            return widget

        def statistic(text):
            widget = label(text, 'liveHint')
            widget.setProperty('liveStat', True)
            widget.setFont(studio_font(('Consolas', *HOME_SANS), 12, tracking=.45))
            return widget

        def card(name='liveCard'):
            frame = W.QFrame()
            frame.setObjectName(name)
            items = W.QVBoxLayout(frame)
            items.setContentsMargins(18, 14, 18, 14)
            items.setSpacing(8)
            return frame, items

        control_card, control_layout = card('liveToolbar')
        control_layout.setContentsMargins(0, 3, 0, 10)
        heading = W.QHBoxLayout()
        state_header = W.QHBoxLayout()
        state_header.setSpacing(6)
        state_header.addWidget(label('工作状态', 'liveCaption'))
        self.state_capsule = W.QFrame()
        self.state_capsule.setObjectName('stateCapsule')
        self.state_capsule.setSizePolicy(W.QSizePolicy.Maximum, W.QSizePolicy.Preferred)
        capsule = W.QHBoxLayout(self.state_capsule)
        capsule.setContentsMargins(3, 2, 9, 2)
        capsule.setSpacing(1)
        self.link_light = PulseLamp()
        capsule.addWidget(self.link_light)
        self.state = CapsuleText('尚未连接')
        capsule.addWidget(self.state)
        state_header.addWidget(self.state_capsule, 0, QtCore.Qt.AlignVCenter)
        self.game_detected = None
        self.launch_busy = False
        self.launch_button = AccentButton('启动鸣潮')
        self.launch_button.setObjectName('primary')
        self.launch_button.setAutoDefault(False)
        bind_button_icon(self.launch_button, 'play', '#111618', '#111618', '#A6B8BE')
        self.update_launch_button()
        state_header.addWidget(self.launch_button, 0, QtCore.Qt.AlignVCenter)
        self.telemetry = W.QWidget()
        badges = W.QHBoxLayout(self.telemetry)
        badges.setContentsMargins(0, 0, 0, 0)
        badges.setSpacing(5)
        self.process_badge = TelemetryBadge(GAME_EXECUTABLE)
        self.log_badge = TelemetryBadge('日志监听')
        self.tts_badge = TelemetryBadge('TTS 引擎')
        for badge in (self.process_badge, self.log_badge, self.tts_badge):
            badges.addWidget(badge)
        self.telemetry.setSizePolicy(W.QSizePolicy.Fixed, W.QSizePolicy.Fixed)
        self.state_header = state_header
        self.control_layout = control_layout
        self.telemetry_below = False
        state_header.addWidget(self.telemetry)
        state_header.addStretch()
        heading.addLayout(state_header, 1)
        self.auto = ToggleSwitch('启动时自动连接')
        config = json.loads((self.root/'config.json').read_text(encoding='utf-8'))
        self.auto.setChecked(config.get('live_auto_start', True))
        self.auto.toggled.connect(self.save_auto)
        heading.addWidget(self.auto)
        self.hud_button = AccentButton('悬浮窗模式', 'pip')
        self.hud_button.clicked.connect(self.show_hud)
        heading.addWidget(self.hud_button)
        heading.addSpacing(8)
        self.start = TransportButton('一键配置并开始', None)
        bind_button_icon(self.start, 'play', '#111618', '#111618', '#A6B8BE')
        self.start.set_key('Ctrl+Enter')
        self.start.setShortcut(QtGui.QKeySequence('Ctrl+Return'))
        self.start.setAccessibleDescription('工作台快捷键 Ctrl+Enter')
        self.start.setToolTip('启动日志监听与实时配音（Ctrl+Enter）')
        self.start.setObjectName('primary')
        glow = W.QGraphicsDropShadowEffect(self.start)
        glow.setOffset(0, 1)
        glow.setBlurRadius(18)
        glow.setColor(QtGui.QColor(216, 180, 103, 110))
        self.start.setGraphicsEffect(glow)
        self.start.clicked.connect(self.start_live)
        self.stop = AccentButton('停止', 'stop')
        self.stop.setObjectName('danger')
        self.stop.setToolTip('停止日志监听和配音播放')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_live)
        for button in (self.start, self.stop):
            button.setMinimumHeight(36)
            heading.addWidget(button)
        control_layout.addLayout(heading)
        layout.addWidget(control_card)

        columns = W.QHBoxLayout()
        columns.setSpacing(14)
        dialogue_card = self.stage = DialogueStage(self.root)
        self.voice_popup = None
        dialogue = W.QVBoxLayout(dialogue_card)
        dialogue.setContentsMargins(24, 17, 24, 16)
        dialogue.setSpacing(7)
        dialogue_head = W.QHBoxLayout()
        accent = W.QFrame()
        accent.setObjectName('liveAccent')
        accent.setFixedSize(3, 15)
        dialogue_head.addWidget(accent)
        dialogue_head.addWidget(label('当前对白', 'stageCaption'))
        dialogue_head.addWidget(caption('CURRENT DIALOGUE'))
        dialogue_head.addStretch()
        self.hints = statistic('')
        dialogue_head.addWidget(self.hints)
        self.demo_button = AccentButton('预览效果', 'play')
        self.demo_button.setObjectName('stagePreview')
        self.demo_button.setAutoDefault(False)
        self.demo_button.setToolTip(DEMO_TIP)
        self.demo_button.clicked.connect(self.toggle_demo)
        dialogue_head.addWidget(self.demo_button)
        self.background_button = W.QToolButton()
        self.background_button.setObjectName('backgroundSettings')
        self.background_button.setAccessibleName('背景设置')
        self.background_button.setToolTip('设置主舞台背景、遮罩与虚化')
        self.background_button.setFixedSize(25,25)
        bind_button_icon(self.background_button, 'landscape')
        self.background_button.clicked.connect(self.open_background)
        dialogue_head.addWidget(self.background_button)
        dialogue.addLayout(dialogue_head)
        dialogue.addStretch(2)
        self.preview = SpeakerBadge()
        self.preview.clicked.connect(self.open_voice_menu)
        self.preview.setFont(studio_font(HOME_SANS, 17, QtGui.QFont.Weight.DemiBold, tracking=0.8))
        dialogue.addWidget(self.preview, 0, QtCore.Qt.AlignLeft)
        self.subtitle = DialogueBody()
        self.subtitle.setObjectName('liveSubtitle')
        self.subtitle.setFont(studio_font(HOME_SANS, 25))
        self.subtitle.setMaximumWidth(564)
        self.subtitle.setAccessibleName('当前对白正文')
        self.subtitle.setPlaceholderText('进入游戏对白后，这里会显示角色和台词。')
        dialogue.addWidget(self.subtitle, 3)
        self.wave = ResonanceWaveWidget()
        self.wave.clock.timeout.connect(self.link_light.update)
        self.wave.setMaximumHeight(44)
        dialogue.addWidget(self.wave)
        self.progress = W.QProgressBar()
        self.progress.setObjectName('liveProgress')
        self.progress.setAccessibleName('配音播放进度')
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        dialogue.addWidget(self.progress)
        dialogue.addSpacing(3)
        self.pause = TransportButton('暂停配音', 'pause')
        self.pause.setCheckable(True)
        self.pause.toggled.connect(self.pause_audio)
        self.replay = TransportButton('重播本句', 'replay')
        self.replay.clicked.connect(self.replay_audio)
        self.skip = TransportButton('跳过本句', 'skip')
        self.skip.clicked.connect(self.skip_audio)
        self.display_hotkeys(config)
        transport = W.QHBoxLayout()
        self.transport_row = transport
        controls = W.QHBoxLayout()
        levels = W.QHBoxLayout()
        transport.addLayout(controls)
        transport.addLayout(levels)
        for button in (self.pause, self.replay, self.skip):
            button.setEnabled(False)
            controls.addWidget(button)
        controls.addStretch()
        levels.addStretch()
        self.counter = statistic('已播放 0 句')
        levels.addWidget(self.counter)
        levels.addSpacing(4)
        levels.addWidget(label('音量', 'liveHint'))
        self.volume = W.QSlider(QtCore.Qt.Horizontal)
        self.volume.setObjectName('liveVolume')
        self.volume.setAccessibleName('配音音量')
        self.volume.setRange(0, 150)
        self.volume.setValue(100)
        self.volume.setFixedWidth(67)
        self.volume.setEnabled(False)
        self.volume.valueChanged.connect(self.change_volume)
        levels.addWidget(self.volume)
        dialogue.addLayout(transport)
        columns.addWidget(dialogue_card, 7)

        side = W.QVBoxLayout()
        side.setSpacing(12)
        task_card, task_layout = card('sideCard')
        self.task_caption = label('当前任务', 'liveCaption')
        task_head = W.QHBoxLayout()
        task_head.addWidget(self.task_caption)
        task_head.addStretch()
        task_layout.addLayout(task_head)
        self.task = label('尚未识别任务', 'liveTask')
        self.task.setFont(studio_font(HOME_SANS, 23, QtGui.QFont.Weight.DemiBold, tracking=.8))
        task_layout.addWidget(self.task)
        self.task_audio = label('等待游戏日志中的任务信息', 'liveHint')
        task_layout.addWidget(self.task_audio)
        self.open_task = AccentButton('查看识别到的任务', 'quest_wave')
        self.open_task.setEnabled(False)
        self.open_task.clicked.connect(lambda: self.task_selected.emit(self.task_id))
        task_layout.addSpacing(2)
        task_layout.addWidget(self.open_task, 0, QtCore.Qt.AlignLeft)
        side.addWidget(task_card)
        history_card, history = card('sideCard')
        history_head = W.QHBoxLayout()
        history_head.addWidget(label('最近对白', 'liveCaption'))
        self.history_count = label('0', 'countBadge')
        self.history_count.setAlignment(QtCore.Qt.AlignCenter)
        self.history_count.setWordWrap(False)
        history_head.addWidget(self.history_count)
        history_head.addStretch()
        history.addLayout(history_head)
        self.history_pages = W.QStackedWidget()
        self.history_empty = W.QWidget()
        empty = W.QVBoxLayout(self.history_empty)
        empty.setContentsMargins(0, 0, 0, 0)
        empty.setSpacing(9)
        empty.addStretch()
        empty_icon = W.QLabel()
        empty_icon.setPixmap(get_pixmap('tacet_voice', '#77949B', 28, device_pixel_ratio=self.devicePixelRatioF()))
        empty.addWidget(empty_icon, 0, QtCore.Qt.AlignHCenter)
        prompt = label('进入游戏对话后自动记录', 'historyEmptyText')
        prompt.setAlignment(QtCore.Qt.AlignCenter)
        empty.addWidget(prompt)
        empty.addStretch()
        self.history_pages.addWidget(self.history_empty)
        self.history = W.QListWidget()
        self.history.setObjectName('liveHistory')
        self.history.setAccessibleName('最近对白，点击可重听已有录音')
        self.history.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.history.setVerticalScrollMode(W.QAbstractItemView.ScrollPerPixel)
        self.history.setTextElideMode(QtCore.Qt.ElideRight)
        self.history.setMinimumHeight(44)
        self.history.setSizePolicy(W.QSizePolicy.Ignored, W.QSizePolicy.Ignored)
        model = self.history.model()
        for change in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            change.connect(self.update_history_count)
        self.history.itemClicked.connect(self.listen_history)
        self.history_pages.addWidget(self.history)
        history.addWidget(self.history_pages, 1)
        side.addWidget(history_card, 2)
        columns.addLayout(side, 3)
        layout.addLayout(columns, 1)

        self.footer = W.QFrame()
        self.footer.setObjectName('homeFooter')
        footer = W.QHBoxLayout(self.footer)
        footer.setContentsMargins(12, 4, 6, 4)
        self.library = LibraryLink('已有配音尚未读取')
        self.library.setObjectName('libraryLink')
        self.library.setAutoDefault(False)
        self.library.setFont(studio_font(('Consolas', *HOME_SANS), 12, tracking=.45))
        self.library.setCursor(QtCore.Qt.PointingHandCursor)
        self.library.setAccessibleDescription('打开任务库')
        self.library.setToolTip('打开任务库')
        footer.addWidget(self.library, 1, QtCore.Qt.AlignLeft)
        self.details = W.QToolButton()
        self.details.setText('运行记录')
        self.details.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        bind_button_icon(self.details, 'terminal')
        self.details.setCheckable(True)
        footer.addWidget(self.details)
        layout.addWidget(self.footer)
        diagnostic, info = card()
        facts = W.QGridLayout()
        facts.setColumnStretch(1, 1)
        facts.setColumnStretch(3, 1)
        self.capture_status = label('尚未连接游戏', 'liveValue')
        self.audio_status = label('等待连接', 'liveValue')
        self.ocr_status = label('等待开始', 'liveValue')
        self.calibration_status = label('按剧情和字幕编号匹配', 'liveValue')
        self.task_basis = label('等待剧情开始事件', 'liveValue')
        self.preview_note = label('尚无生成消息', 'liveValue')
        for index, (key, value) in enumerate((('字幕识别', self.ocr_status), ('识别方式', self.calibration_status),
                                              ('任务依据', self.task_basis), ('生成状态', self.preview_note),
                                              ('游戏日志', self.capture_status), ('声音播放', self.audio_status))):
            row, column = divmod(index, 2)
            facts.addWidget(label(key, 'liveHint'), row, column * 2, QtCore.Qt.AlignTop)
            facts.addWidget(value, row, column * 2 + 1)
        info.addLayout(facts)
        self.log = W.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setAccessibleName('运行记录')
        self.log.setPlaceholderText('日志监听和播放事件会记录在这里。')
        self.log.setFixedHeight(86)
        info.addWidget(self.log)
        self.diagnostic = diagnostic
        diagnostic.setVisible(False)
        self.details.toggled.connect(diagnostic.setVisible)
        layout.addWidget(diagnostic)
        self.probe = GameProcessProbe(self.root)
        self.probe.sampled.connect(self.process_sampled)
        self.probe_pending = False
        self.next_probe = 0.
        self.process_badge.display('off', '正在检测鸣潮进程')
        self.refresh_telemetry()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh_capture)
        self.timer.start(200)
        if auto_start is None:
            auto_start = self.root.resolve() == WUWA_ROOT.resolve() and self.auto.isChecked()
        if auto_start:
            QtCore.QTimer.singleShot(500, lambda:self.start_live() if self.isVisible() or (self.hud and self.hud.isVisible()) else None)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'transport_row'):
            direction = W.QBoxLayout.TopToBottom if event.size().width() < 870 else W.QBoxLayout.LeftToRight
            self.transport_row.setDirection(direction)
            below = event.size().width() < 1040 or self.start.window() is self
            if below != self.telemetry_below:
                self.telemetry_below = below
                if below:
                    self.control_layout.insertWidget(1, self.telemetry, 0, QtCore.Qt.AlignLeft)
                else:
                    self.state_header.insertWidget(3, self.telemetry)

    def process_sampled(self, state, detail):
        self.probe_pending = False
        self.process_badge.display(state, detail)
        self.game_detected = state == 'ready'
        self.update_launch_button()

    def update_launch_button(self):
        active = self.game_detected or self.link_active
        self.launch_button.setText('鸣潮已启动' if active else '启动鸣潮')
        self.launch_button.setEnabled(self.game_detected is False and not active and not self.launch_busy)
        self.launch_button.setToolTip('已连接鸣潮日志' if self.link_active else
                                     '已检测到鸣潮游戏进程' if active else
                                     '通过官方启动器打开鸣潮，并开始监听对白')

    def refresh_telemetry(self):
        now = time.monotonic()
        if not self.probe_pending and now >= self.next_probe:
            self.next_probe = now + 2
            self.probe_pending = True
            QtCore.QThreadPool.globalInstance().start(self.probe.sample)
        if self.running() and self.worker.log_readable:
            self.log_badge.display('ready', '正在读取本次游戏的 Client.log')
        else:
            self.log_badge.display('off', '等待读取游戏日志' if self.running() else '日志监听未运行')
        if self.jobs.stopping.is_set() or not self.jobs.thread.is_alive():
            self.tts_badge.display('off', '合成服务已停止')
        else:
            self.tts_badge.display(*self.jobs.activity)


    def save_auto(self, enabled):
        path = self.root/'config.json'
        config = json.loads(path.read_text(encoding='utf-8'))
        config['live_auto_start'] = bool(enabled)
        atomic_text(path,json.dumps(config,ensure_ascii=False,indent=2))

    def running(self):
        return bool(self.worker and self.worker.isRunning())

    def display_hotkeys(self, config):
        keys = config.get('hotkeys', {})
        for control, action, default in ((self.pause, 'pause', 'F8'), (self.replay, 'replay', 'F9'),
                                         (self.skip, 'skip', 'F10')):
            control.set_key(keys.get(action, default).upper())

    def toggle_demo(self):
        if self.demo_snapshot is not None:
            self.stop_demo()
            return
        if self.link_active or self.recognized or self.progress_start is not None:
            return
        from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
        if self.demo_player is None:
            self.demo_player = QMediaPlayer(self)
            self.demo_output = QAudioOutput(self)
            self.demo_player.setAudioOutput(self.demo_output)
            self.demo_player.mediaStatusChanged.connect(self.demo_media_status)
            self.demo_player.errorOccurred.connect(self.demo_audio_error)
        self.demo_snapshot = (self.preview.text(), bool(self.preview.property('named')),
                              self.subtitle.toPlainText(), self.progress.value())
        self.demo_button.setText('结束预览')
        self.demo_button.set_glyph('stop')
        self.demo_button.setToolTip('结束本次效果预览')
        self.subtitle.setPlainText('看，它们在围着未琢玉跳舞呢，\n真是群有趣的小家伙。')
        self.set_speaker('鲙娘', recognised=True)
        self.wave.set_playing(True)
        self.progress.setValue(0)
        self.demo_clock.start()
        self.demo_silent = False
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull() or not DEMO_CLIP.is_file():
            self.demo_audio_error()
            return
        self.demo_output.setDevice(device)
        self.demo_output.setVolume(min(self.volume.value() / 100, 1.0))
        self.demo_player.setSource(QtCore.QUrl.fromLocalFile(str(DEMO_CLIP)))
        self.demo_player.setPosition(0)
        self.demo_player.play()

    def demo_media_status(self, status):
        if self.demo_snapshot is not None and status == self.demo_player.MediaStatus.EndOfMedia:
            self.stop_demo()

    def demo_audio_error(self):
        if self.demo_snapshot is not None:
            self.demo_silent = True
            self.demo_player.stop()
            self.demo_button.setText('结束静音预览')
            self.demo_button.setToolTip('音频输出不可用，点击结束静音预览')

    def update_demo(self):
        elapsed = self.demo_clock.elapsed()
        if self.demo_silent:
            if elapsed >= DEMO_DURATION_MS:
                self.stop_demo()
            else:
                self.progress.setValue(1000 * elapsed // DEMO_DURATION_MS)
        elif elapsed > DEMO_DURATION_MS + 2000:
            self.stop_demo()
        else:
            duration = self.demo_player.duration() or DEMO_DURATION_MS
            self.progress.setValue(min(1000, 1000 * self.demo_player.position() // duration))

    def stop_demo(self):
        if self.demo_snapshot is None:
            return
        speaker, named, body, progress = self.demo_snapshot
        self.demo_snapshot = None
        self.demo_player.stop()
        self.wave.set_playing(False)
        self.subtitle.setPlainText(body)
        self.set_speaker(speaker, recognised=named)
        self.progress.setValue(progress)
        self.demo_button.setText('预览效果')
        self.demo_button.set_glyph('play')
        self.demo_button.setToolTip(DEMO_TIP)

    def hideEvent(self, event):
        if self.voice_popup:
            self.voice_popup.close()
        self.stop_demo()
        super().hideEvent(event)

    def start_live(self):
        if self.running():
            return
        self.stop_demo()
        self.played = self.last_sequence = 0
        self.last_frame_at = 0.
        self.counter.setText('已播放 0 句')
        self.reset_line('等待对白')
        self.clear_history()
        self.task_id = ''
        self.open_task.setEnabled(False)
        self.task.setText('正在识别当前任务…')
        self.task_audio.setText('等待游戏日志中的任务信息')
        self.task_basis.setText('等待剧情开始事件')
        self.capture_status.setText('正在连接')
        self.ocr_status.setText('等待字幕编号')
        self.audio_status.setText('等待读取本地音频')
        self.start.setEnabled(False)
        self.start.graphicsEffect().setEnabled(False)
        self.stop.setEnabled(True)
        self.state.setText('正在一键配置…')
        self.worker = LogWorker(self.root,self,jobs=self.jobs)
        self.active_run = self.worker.run_id
        self.worker.message.connect(self.receive)
        self.worker.finished.connect(self.finished)
        if self.root.resolve()==WUWA_ROOT.resolve():
            from wuwa_hotkeys import PlaybackHotkeys
            config=json.loads((self.root/'config.json').read_text(encoding='utf-8'))
            self.display_hotkeys(config)
            owner=self.window()
            if hasattr(owner,'finished') and hasattr(owner.finished,'connect'):
                self.hotkeys=PlaybackHotkeys(owner,config.get('hotkeys',{}),
                    {'pause':self.pause.click,'replay':self.replay.click,'skip':self.skip.click},
                    game_pid=lambda:self.worker.game_pid if self.worker else None,
                    on_replay=lambda source:self.replay_audio(source=source))
                self.worker.record(state='hotkeys_ready',replay_key=config.get('hotkeys',{}).get('replay','F9'),
                    native_registered=0x4101 in self.hotkeys.registered,game_key_detection=self.hotkeys.timer is not None)
                if self.hotkeys.errors:
                    self.note('快捷键占用：'+'、'.join(self.hotkeys.errors)+'；仍可使用页面按钮')
        self.worker.start()

    def stop_live(self):
        if self.worker:
            self.worker.cancel()
            self.state.setText('正在停止监听与播放…')
        self.stop.setEnabled(False)

    def pause_audio(self, paused):
        if self.worker and self.worker.controller:
            self.worker.controller.pause(paused)
            self.audio_status.setText('配音已暂停' if paused else '已恢复配音')
        self.pause.set_glyph('play' if paused else 'pause')
        self.pause.setText('恢复配音' if paused else '暂停配音')
        if paused:
            self.reset_progress()
        if self.hud:
            self.hud.sync_controls()

    def replay_audio(self, *, source='button'):
        controller = self.worker.controller if self.worker else None
        if controller:
            accepted = controller.replay()
            self.worker.record(state='manual_replay',source=source,accepted=accepted)
            if not accepted:
                self.audio_status.setText('配音已暂停，请先恢复' if self.pause.isChecked() else '当前没有可重播的台词')

    def skip_audio(self):
        if self.worker and self.worker.controller:
            self.worker.controller.skip()
            self.audio_status.setText('本句已跳过')
            self.reset_progress()

    def finished(self):
        if self.hotkeys:
            self.hotkeys.close()
            self.hotkeys=None
        self.start.setEnabled(True)
        self.start.graphicsEffect().setEnabled(True)
        self.start.setText('重新连接 / 刷新音频')
        for control in (self.stop,self.pause,self.replay,self.skip):
            control.setEnabled(False)
        self.pause.blockSignals(True)
        self.pause.setChecked(False)
        self.pause.setText('暂停配音')
        self.pause.set_glyph('pause')
        self.pause.blockSignals(False)
        self.volume.setEnabled(False)
        if self.hud:
            self.hud.sync_controls()
        self.capture_status.setText('已停止')
        self.set_link_active(False)
        if self.task_id:
            self.task.setText('上次识别：'+self.task.text())
            self.task_id=''
            self.open_task.setEnabled(False)
            self.task_basis.setText('监听已停止，保留上次识别结果')
        self.reset_line('监听已停止')
        if self.state.text() == '正在停止监听与播放…':
            self.state.setText('监听已停止')
        self.audio_status.setText('已停止')
        self.ocr_status.setText('已停止')
        self.active_run = None
        self.publish_status(force=True)
        self.stopped.emit()

    def reset_line(self, placeholder):
        if self.voice_popup:
            self.voice_popup.close()
        self.subtitle.clear()
        self.set_speaker(placeholder, recognised=False)
        self.reset_progress()
        self.hints.clear()
        self.recognized = {}
        self.last_playback = {}

    def set_link_active(self, connected):
        if self.link_active != connected:
            self.demo_button.setEnabled(not connected)
            self.demo_button.setToolTip('实时监听已连接游戏，断开后可预览' if connected else DEMO_TIP)
            self.link_active = connected
            self.update_launch_button()
            self.state_capsule.setProperty('connected', connected)
            for widget in (self.state_capsule, self.state):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()
            self.link_light.connected = connected
            self.link_light.update()

    def open_background(self):
        show_at(BackgroundMenu(self.stage),self.background_button)

    def open_voice_menu(self):
        from wuwa_catalog import Catalog
        from ui.stage_voices import VoiceMenu, presets
        current = self.recognized
        try:
            with closing(Catalog(self.root)) as catalog:
                row = catalog.db.execute('SELECT id,speaker_id,speaker FROM lines WHERE id=?',
                                         (current.get('catalog_id'),)).fetchone()
                if row is None:
                    self.preview_note.setText('此句仅有配音包录音，请在任务库更新台词索引后换声')
                    W.QToolTip.showText(self.preview.mapToGlobal(self.preview.rect().bottomLeft()),self.preview_note.text(),self.preview)
                    return
                binding = catalog.binding(row['speaker_id'])
                config = json.loads((self.root/'config.json').read_text(encoding='utf-8'))
                entries = list(presets(catalog,config,row['speaker_id']))
                menu = VoiceMenu(self, entries, binding['voice'] if binding else '')
        except (OSError, ValueError, sqlite3.Error) as exc:
            W.QToolTip.showText(self.preview.mapToGlobal(self.preview.rect().bottomLeft()),
                                '音色库未能打开：' + str(exc),self.preview)
            return
        self.voice_popup = menu
        menu.selected.connect(lambda voice: self.apply_stage_voice(menu,current,dict(row),voice))
        menu.destroyed.connect(lambda: self.forget_voice_menu(menu))
        show_at(menu,self.preview)
        menu.search.setFocus()

    def forget_voice_menu(self, menu):
        if self.voice_popup is menu:
            self.voice_popup = None

    def apply_stage_voice(self, menu, observation, row, voice):
        from wuwa_catalog import Catalog
        from ui.stage_voices import bind_current
        if self.recognized is not observation or not self.running():
            menu.close()
            return
        try:
            with closing(Catalog(self.root)) as catalog:
                binding = catalog.binding(row['speaker_id'])
                same = binding and binding['voice'] == voice
                if not same:
                    bind_current(catalog,row,voice)
        except (OSError,ValueError,sqlite3.Error) as exc:
            menu.note.setText(str(exc))
            return
        menu.close()
        expected = row['id'], voice
        if same and expected in ((self.worker.current_row, self.worker.current_voice), self.worker.packed_binding):
            self.replay_audio()
        else:
            self.note('已切换' + row['speaker'] + '的音色，正在核对当前句')
            self.worker.request_voice_refresh(row['id'],voice)

    def set_speaker(self, speaker, *, recognised):
        switchable = bool(recognised and self.demo_snapshot is None and self.recognized.get('catalog_id'))
        self.preview.setEnabled(switchable)
        self.preview.setCursor(QtCore.Qt.PointingHandCursor if switchable else QtCore.Qt.ArrowCursor)
        self.preview.setToolTip('切换角色音色并重播当前句' if switchable else
                                '固定示例录音，进入游戏对白后可切换角色音色' if self.demo_snapshot else '')
        neutral = ('旁白', '角色#', '角色待', '未知', '？？', '??')
        named = recognised and bool(speaker.strip(' ?？')) and not speaker.startswith(neutral)
        self.preview.setText(speaker)
        self.preview.setProperty('named', named)
        self.preview.style().unpolish(self.preview)
        self.preview.style().polish(self.preview)
        self.refresh_hud_dialogue()

    def refresh_hud_dialogue(self):
        if self.hud:
            if self.hud_history_line:
                self.hud.set_dialogue(*self.hud_history_line)
            else:
                self.hud.set_dialogue(self.preview.text(), self.subtitle.toPlainText(),
                                      bool(self.preview.property('named')))

    def show_hud(self):
        home = self.window()
        if self.hud is None:
            self.hud = LiveHud(self)
            self.hud.restore_requested.connect(self.restore_home)
            area = home.screen().availableGeometry()
            self.hud.move(area.right() - self.hud.width() - 15, area.top() + 16)
        self.refresh_hud_dialogue()
        self.hud.sync_controls()
        self.hud.set_listening(self.link_active, self.running())
        self.hud.set_playing(self.progress_start is not None)
        self.hud.show()
        home.hide()

    def restore_home(self):
        home = self.window()
        home.show()
        home.raise_()
        home.activateWindow()
        self.hud.hide()

    def change_volume(self, percent):
        self.volume.setToolTip(f'音量 {percent}% · 从下次播放生效')
        controller = self.worker.controller if self.worker else None
        if controller:
            controller.player.set_volume(percent / 100)

    def update_history_count(self):
        count = self.history.count()
        self.history_count.setText(str(count))
        self.history_pages.setCurrentWidget(self.history if count else self.history_empty)

    def clear_history(self):
        self.history.clear()

    def remember_line(self, observation):
        body = observation.get('body') or ''
        if not body.strip():
            return
        speaker = observation.get('speaker') or '角色待确认'
        key = (observation.get('catalog_id'), speaker, body)
        first = self.history.item(0)
        if first and first.data(QtCore.Qt.UserRole) == key:
            return
        text = ' '.join(body.split())
        item = W.QListWidgetItem(f'[{speaker}] {text}')
        item.setData(QtCore.Qt.UserRole, key)
        item.setToolTip('暂无可重听的录音')
        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
        self.history.insertItem(0, item)
        if self.history.count() > 60:
            self.history.takeItem(60)
        self.history.scrollToTop()

    def enable_history_clip(self, clip):
        key = (clip.catalog_id, clip.speaker or '角色待确认', clip.text)
        for index in range(self.history.count()):
            item = self.history.item(index)
            if item.data(QtCore.Qt.UserRole) == key:
                item.setData(QtCore.Qt.UserRole + 1, clip)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsEnabled)
                item.setToolTip('点击重听 · ' + clip.text)

    def listen_history(self, item):
        clip = item.data(QtCore.Qt.UserRole + 1)
        controller = self.worker.controller if self.worker else None
        if clip and controller and controller.audition(clip):
            return
        self.audio_status.setText('配音已暂停，请先恢复' if self.pause.isChecked() else '暂无可重听的录音或监听已停止')

    def reset_progress(self):
        self.hud_history_line = None
        if self.hud:
            self.refresh_hud_dialogue()
            self.hud.set_playing(False)
        self.wave.set_playing(False)
        self.progress_start = None
        self.progress.setValue(0)

    def update_progress(self):
        if self.progress_start is not None and self.progress_duration > 0:
            elapsed = time.monotonic() - self.progress_start
            self.progress.setValue(min(999, max(0, int(1000 * elapsed / self.progress_duration))))

    def publish_status(self,force=False):
        now=time.monotonic()
        if not force and now-self.last_publish<1:
            return
        self.last_publish=now
        payload=dict(version=13,recognition_source="game_log",pid=os.getpid(),updated_at=time.time(),running=self.running(),
            status=self.state.text(),capture=self.capture_status.text(),ocr=self.ocr_status.text(),
            calibration=self.calibration_status.text(),audio=self.audio_status.text(),
            task=self.task.text(),task_id=self.task_id,task_basis=self.task_basis.text(),
            task_audio=self.task_audio.text(),library=self.library.text(),foreground=self.preview_note.text(),
            frame_sequence=self.last_sequence,frame_age_seconds=now-self.last_frame_at if self.last_frame_at else None,
            played=self.played,auto_start=self.auto.isChecked(),
            recognized_text=self.recognized.get('body',''),recognized_speaker=self.recognized.get('speaker',''),
            subtitle_id=self.recognized.get('subtitle_id'),flow=self.recognized.get('flow'),
            body_cache_reused=self.recognized.get('body_pixels_unchanged',False),
            playback_text=self.last_playback.get('text',''),playback_row_id=self.last_playback.get('row_id',''),
            playback_state=self.last_playback.get('state',''))
        try:
            atomic_text(self.root/'live-status.json',json.dumps(payload,ensure_ascii=False,indent=2))
        except OSError as exc:
            self.note('工作状态记录未保存：'+str(exc))

    def note(self, message):
        if message == self._last_log:
            return
        self._last_log = message
        self.events.append(time.strftime('%H:%M:%S')+'  '+message)
        self.log.setPlainText('\n'.join(self.events))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def generation_status(self, message):
        self.preview_note.setText(message)
        self.note(message)

    def receive(self, event):
        if event.get('run_id') != self.active_run:
            return
        if self.demo_snapshot is not None and (
            event.get('game_attached') or event.get('clear_line') or 'observation' in event
            or 'playback' in event or event.get('log_connection') == '已连接鸣潮日志'
        ):
            self.stop_demo()
        if 'status' in event:
            self.state.setText(event['status'])
            self.note(event['status'])
        if 'error' in event:
            self.note(event['error'])
        if 'status_text' in event:
            self.ocr_status.setText(event['status_text'])
        if event.get('game_attached'):
            self.clear_history()
        if 'log_connection' in event:
            self.capture_status.setText(event['log_connection'])
            self.set_link_active(event['log_connection'] == '已连接鸣潮日志')
        if event.get('clear_line'):
            self.reset_line('等待对白')
            self.task_id = ''
            self.task.setText('等待剧情')
            self.task_basis.clear()
            self.open_task.setEnabled(False)
            self.ocr_status.setText('等待字幕编号')
            self.audio_status.setText('等待当前台词')
            self.task_audio.clear()
        if 'audio_message' in event:
            self.audio_status.setText(event['audio_message'])
        if 'generation_message' in event:
            self.preview_note.setText(event['generation_message'])
            self.note(event['generation_message'])
        if 'task' in event:
            value = event['task']
            self.task_caption.setText('当前选中的任务' if value.get('context')=='selected' else '当前任务')
            prefix = {'candidate':'待确认：','last_seen':'上次识别：'}.get(value['state'],'')
            self.task.setText(prefix+value['name'])
            self.task_basis.setText(value['basis'])
            self.task_id = value.get('id','') if value['state']=='confirmed' else ''
            self.open_task.setEnabled(bool(self.task_id))
            count=self.task_counts.get(self.task_id,0)
            self.task_audio.setText(f'此任务已有 {count} 句配音' if count else
                ('此任务暂无已生成配音，可点击“查看识别到的任务”准备。' if self.task_id else
                 ('本地台词库暂无此任务，可在任务库更新索引。' if value.get('unindexed') else '确认任务后显示可用配音')))
        if 'log_line' in event:
            self.hints.setText('字幕编号 '+str(event['log_line']))
        if 'observation' in event:
            value = event['observation']
            if self.voice_popup and self.recognized != value:
                self.voice_popup.close()
            self.recognized=value
            self.remember_line(value)
            self.subtitle.setPlainText(value.get('body') or '')
            self.ocr_status.setText('已匹配日志字幕')
            self.set_speaker(value.get('speaker') or '角色待确认', recognised=True)
        if 'library' in event:
            value = event['library']
            self.task_counts=value.get('task_counts',{})
            pending=value.get('review_count',0)
            self.library.setText(f"已有配音：{value['lines']} 句 · {len(value['projects'])} 个项目"+
                                 (f' · {pending} 句读音待核对' if pending else ''))
            self.library.setToolTip('\n'.join(['打开任务库', *(x['reason'] for x in value['held'])]))
            self.audio_status.setText('等待匹配对白' if value['lines'] else '当前没有已生成的有效音频')
        if 'history_clip' in event:
            self.enable_history_clip(event['history_clip'])
        if 'player_volume' in event:
            percent = round(event['player_volume'] * 100)
            with QtCore.QSignalBlocker(self.volume):
                self.volume.setValue(percent)
            self.volume.setToolTip(f'音量 {percent}% · 从下次播放生效')
            self.volume.setEnabled(True)
        if 'audio_device' in event:
            self.audio_status.setToolTip('输出设备：'+event['audio_device'])
        if 'audio_error' in event:
            self.audio_status.setText(event['audio_error'])
            self.note(event['audio_error'])
        if 'playback' in event:
            value = event['playback']
            # Repeated lines can have identical text but different subtitle records.
            if value.get('catalog_id'):
                current_line = value['catalog_id'] == self.recognized.get('catalog_id')
            else:
                current_line = not value.get('text') or value['text'] == self.recognized.get('body')
            state = value['state']
            history = value.get('history', False)
            if state == 'playing' and (current_line or history):
                self.hud_history_line = None
                if history:
                    speaker = '角色待确认'
                    for index in range(self.history.count()):
                        identity, name, body = self.history.item(index).data(QtCore.Qt.UserRole)
                        if identity == value.get('catalog_id') and body == value.get('text'):
                            speaker = name
                            break
                    self.hud_history_line = (speaker, value.get('text', ''), speaker not in ('角色待确认', '旁白'))
                self.refresh_hud_dialogue()
                self.wave.set_playing(True)
                if self.hud:
                    self.hud.set_playing(True)
                self.progress_start = value.get('started_at')
                self.progress_duration = value.get('duration', 0)
                self.progress.setValue(0)
            elif self.progress_start is not None and value.get('started_at') == self.progress_start:
                self.hud_history_line = None
                self.refresh_hud_dialogue()
                if self.hud:
                    self.hud.set_playing(False)
                self.wave.set_playing(False)
                self.progress_start = None
                self.progress.setValue(1000 if state == 'completed' else 0)
            if history:
                if state in ('playing', 'error'):
                    self.audio_status.setText(value['message'])
                elif state == 'completed':
                    self.audio_status.setText('历史重听结束')
                return
            if value['state']=='playing':
                self.played += 1
                self.counter.setText(f'已播放 {self.played} 句')
            if value['state'] in {'playing','error','unmatched'}:
                self.note(value['message'])
            if not current_line:
                return
            self.last_playback = value
            if value['state'] != 'interrupted' or not self.pause.isChecked() and self.audio_status.text() != '本句已跳过':
                self.audio_status.setText(value['message'])

    def refresh_capture(self):
        self.refresh_telemetry()
        if self.hud and self.hud.isVisible():
            self.hud.set_listening(self.link_active, self.running())
        if self.demo_snapshot is None:
            self.update_progress()
        else:
            self.update_demo()
        self.publish_status()
        if not self.running():
            return
        controller = self.worker.controller
        for control in (self.pause,self.replay,self.skip):
            control.setEnabled(controller is not None)
        if self.hud and self.hud.isVisible():
            self.hud.sync_controls()

    def closeEvent(self,event):
        if self.running():
            self.stop_live()
            event.ignore()
            return
        if self.hud:
            self.hud.hide()
        self.timer.stop()
        self.jobs.close()
        event.accept()
