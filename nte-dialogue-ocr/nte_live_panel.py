"""NTE session controller in the shared live-workbench presentation."""
from collections import deque
import json
from pathlib import Path

from PySide6 import QtCore, QtWidgets as W

from dialogue_generation import atomic_text
from nte_live_ui import LiveWorker
from workbench_layout import build_live_dashboard


class NteLivePanel(W.QWidget):
    project_selected = QtCore.Signal(object)
    stopped = QtCore.Signal()
    running_changed = QtCore.Signal(bool)

    def __init__(self, root, parent=None, *, auto_start=None):
        super().__init__(parent)
        self.root = Path(root)
        self.worker = self.hotkeys = None
        self.project_path = self.active_project = None
        self.project_summary = ''
        self.played = self.last_sequence = 0
        self.last_serial = -1
        self.last_error = ''
        self.events = deque(maxlen=80)
        build_live_dashboard(self)
        self.description.setText('打开异环，一键连接，通过日志和字幕定位游戏原文。')
        self.calibration_status.setText('日志编号优先，字幕补充定位')
        self.task_caption.setText('当前配音项目')
        self.task.setText('尚未选择台词项目')
        self.task_basis.setText('在任务库与配音准备页选择项目')
        self.task_audio.setText('已有音频优先，缺少时按当前声音设置生成')
        self.open_task.setText('查看当前项目')
        self.preview.setText('等待游戏画面')
        self.preview.setMinimumSize(240, 150)
        self.preview.setSizePolicy(W.QSizePolicy.Ignored, W.QSizePolicy.Expanding)
        self.preview_note.setText('日志与字幕定位')
        config = self.config()
        self.auto.setChecked(config.get('nte_hybrid', {}).get('auto_start', False))
        self.auto.toggled.connect(self.save_auto)
        self.start.clicked.connect(self.start_live)
        self.stop.clicked.connect(self.stop_live)
        self.pause.toggled.connect(self.pause_audio)
        self.replay.clicked.connect(self.replay_audio)
        self.skip.clicked.connect(self.skip_audio)
        self.open_task.clicked.connect(self.show_project)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh_capture)
        self.timer.start(200)
        if auto_start is None:
            auto_start = self.auto.isChecked()
        if auto_start:
            QtCore.QTimer.singleShot(500, lambda: self.start_live() if self.isVisible() else None)

    def config(self):
        return json.loads((self.root / 'config.json').read_text(encoding='utf-8-sig'))

    def save_auto(self, enabled):
        try:
            config = self.config()
            config.setdefault('nte_hybrid', {})['auto_start'] = bool(enabled)
            atomic_text(self.root / 'config.json', json.dumps(config, ensure_ascii=False, indent=2))
        except Exception as exc:
            self.note('自动连接设置未保存 · ' + str(exc))

    def set_project(self, path, summary=''):
        self.project_path = Path(path) if path else None
        self.project_summary = summary
        if not self.running():
            self.task.setText(self.project_path.name if self.project_path else '尚未选择台词项目')
            self.task_basis.setText('当前选择的本地台词项目')
            self.task_audio.setText(summary or '已有音频优先，缺少时按当前声音设置生成')
            self.library.setText('等待当前台词的音频来源')
            self.open_task.setEnabled(self.project_path is not None)

    def show_project(self):
        path = self.active_project if self.running() else self.project_path
        if path:
            self.project_selected.emit(path)

    def running(self):
        return bool(self.worker and self.worker.isRunning())

    def note(self, text):
        if text and (not self.events or self.events[-1] != text):
            self.events.append(text)
            self.log.setPlainText('\n'.join(self.events))
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def start_live(self):
        if self.running() or not self.start.isEnabled():
            return
        try:
            from capture_windows import list_game_windows
            config = self.config()
            if not config.get('calibrated') or not config.get('body_roi'):
                raise ValueError('请先在任务库与配音准备页完成字幕校准')
            windows = [window for window in list_game_windows() if not window.is_launcher]
            self.active_project = self.project_path
            self.last_serial = -1
            self.last_error = ''
            self.last_sequence = self.played = 0
            self.counter.setText('已播放 0 句')
            self.subtitle.clear()
            self.pause.blockSignals(True)
            self.pause.setChecked(False)
            self.pause.setText('暂停配音')
            self.pause.blockSignals(False)
            self.worker = LiveWorker(windows[0] if windows else None, self.root, config, self.active_project, self)
            worker = self.worker
            self.worker.message.connect(lambda event: self.receive(event) if self.worker is worker else None)
            self.worker.finished.connect(self.finished)
            self.start.setEnabled(False)
            self.stop.setEnabled(True)
            self.state.setText('正在连接异环并准备配音…')
            self.task.setText(self.active_project.name if self.active_project else '按游戏原文实时生成')
            self.open_task.setEnabled(self.active_project is not None)
            self.worker.start()
            self.running_changed.emit(True)
        except Exception as exc:
            self.state.setText('连接未完成 · ' + str(exc))
            self.note(str(exc))
            self.start.setEnabled(True)
            self.stop.setEnabled(False)

    def stop_live(self):
        if self.worker and self.running():
            self.worker.cancel()
            self.state.setText('正在停止识别与播放…')
            self.stop.setEnabled(False)

    def controller_action(self, method, *args):
        controller = self.worker.session.controller if self.worker else None
        if controller:
            return getattr(controller, method)(*args)
        return False

    def pause_audio(self, paused):
        self.controller_action('pause', paused)
        self.pause.setText('恢复配音' if paused else '暂停配音')

    def replay_audio(self):
        if not self.controller_action('replay'):
            self.audio_status.setText('配音已暂停，请先恢复' if self.pause.isChecked() else '当前没有可重播的台词')

    def skip_audio(self):
        self.controller_action('skip')

    def receive(self, event):
        if 'status_text' in event:
            self.state.setText(event['status_text'])
        if 'log_status' in event:
            self.capture_status.setText(event['log_status'])
        if 'match_status' in event:
            self.ocr_status.setText(event['match_status'])
        if 'error' in event:
            self.last_error = event['error']
            self.state.setText('连接已停止 · ' + event['error'])
            self.note(event['error'])
        if event.get('ready'):
            for control in (self.pause, self.replay, self.skip):
                control.setEnabled(True)
            owner = self.window()
            if self.hotkeys is None and hasattr(owner, 'finished') and hasattr(owner.finished, 'connect'):
                from wuwa_hotkeys import PlaybackHotkeys
                self.hotkeys = PlaybackHotkeys(owner, self.config().get('hotkeys', {}),
                    {'pause': self.pause.click, 'replay': self.replay.click, 'skip': self.skip.click})
                if self.hotkeys.errors:
                    self.note('快捷键占用，可使用页面按钮 · ' + '、'.join(self.hotkeys.errors))
        if 'state' not in event or event.get('serial', 0) < self.last_serial:
            return
        self.last_serial = event['serial']
        self.audio_status.setText(event['message'])
        if event.get('text'):
            self.subtitle.setPlainText((event.get('speaker') or '角色待确认') + '\n' + event['text'])
            self.task_basis.setText('日志编号定位' if event.get('source') == 'log' else '字幕定位游戏原文')
            self.hints.setText(event.get('catalog_id', ''))
            self.ocr_status.setText('已定位当前台词')
        elif event['state'] == 'waiting':
            self.subtitle.clear()
            self.hints.setText('等待当前台词编号')
        if event['state'] == 'playing':
            self.played += 1
            self.counter.setText(f'已播放 {self.played} 句')
            origin = {'project': '项目已有配音', 'cache': '已有音频缓存', 'generated': '本次生成配音'}.get(event.get('audio_origin'), '')
            timing = f" · 采集到输出 {event['latency_ms']:.0f} 毫秒" if 'latency_ms' in event else ''
            self.library.setText(origin + timing)
        if event['state'] in ('playing', 'error', 'native', 'skipped', 'paused'):
            self.note(event['message'])

    def refresh_capture(self):
        if not self.running() or self.worker.capture is None:
            return
        packet = self.worker.capture.peek()
        if packet is not None and packet.sequence > self.last_sequence:
            from generator_ocr import pixmap
            self.last_sequence = packet.sequence
            self.preview.setPixmap(pixmap(packet.image).scaled(max(100, self.preview.width()-24),
                max(60, self.preview.height()-24), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            self.preview_note.setText('当前异环字幕区域')

    def finished(self):
        completed,self.worker = self.worker,None
        if self.hotkeys:
            self.hotkeys.close()
            self.hotkeys = None
        self.start.setEnabled(True)
        self.start.setText('重新连接 / 刷新音频')
        for control in (self.stop, self.pause, self.replay, self.skip):
            control.setEnabled(False)
        self.pause.blockSignals(True)
        self.pause.setChecked(False)
        self.pause.setText('暂停配音')
        self.pause.blockSignals(False)
        self.capture_status.setText('已停止')
        self.state.setText('连接已停止 · '+self.last_error if self.last_error else '已停止 · 可重新连接')
        self.preview.clear()
        self.preview.setText('监听已停止')
        self.preview_note.setText('日志与字幕定位')
        self.set_project(self.project_path, self.project_summary)
        self.running_changed.emit(False)
        self.stopped.emit()
        if completed:
            completed.deleteLater()

    def shutdown(self):
        self.timer.stop()
        if self.hotkeys:
            self.hotkeys.close()
            self.hotkeys = None
