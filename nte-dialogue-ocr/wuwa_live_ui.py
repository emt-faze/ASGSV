"""Visible live dashboard; no capture, OCR or synthesis runs on the GUI thread."""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import time

from PySide6 import QtCore, QtGui, QtWidgets as W

from dialogue_generation import atomic_text
from game_profiles import WUWA_ROOT
from wuwa_log_live import LogWorker


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
        self.rois = None
        self.scene = None
        self.events = deque(maxlen=80)
        self._last_log = ''
        self._last_health = ''
        self._last_health_at = 0.
        self.last_publish = 0.
        from workbench_layout import build_live_dashboard
        build_live_dashboard(self)
        config = json.loads((self.root/'config.json').read_text(encoding='utf-8'))
        self.auto.setChecked(config.get('live_auto_start', True))
        self.auto.toggled.connect(self.save_auto)
        self.start.clicked.connect(self.start_live)
        self.stop.clicked.connect(self.stop_live)
        self.pause.toggled.connect(self.pause_audio)
        self.replay.clicked.connect(self.replay_audio)
        self.skip.clicked.connect(self.skip_audio)
        self.open_task.clicked.connect(lambda: self.task_selected.emit(self.task_id))
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh_capture)
        self.timer.start(200)
        if auto_start is None:
            auto_start = self.root.resolve() == WUWA_ROOT.resolve() and self.auto.isChecked()
        if auto_start:
            QtCore.QTimer.singleShot(500, lambda:self.start_live() if self.isVisible() else None)

    def save_auto(self, enabled):
        path = self.root/'config.json'
        config = json.loads(path.read_text(encoding='utf-8'))
        config['live_auto_start'] = bool(enabled)
        atomic_text(path,json.dumps(config,ensure_ascii=False,indent=2))

    def running(self):
        return bool(self.worker and self.worker.isRunning())

    def start_live(self):
        if self.running():
            return
        self.played = self.last_sequence = 0
        self.last_frame_at = 0.
        self.counter.setText('已播放 0 句')
        self.subtitle.clear()
        self.task_id = ''
        self.open_task.setEnabled(False)
        self.task.setText('正在识别当前任务…')
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.state.setText('正在一键配置…')
        self.worker = LogWorker(self.root,self,jobs=self.jobs)
        self.active_run = self.worker.run_id
        self.worker.message.connect(self.receive)
        self.worker.finished.connect(self.finished)
        if self.root.resolve()==WUWA_ROOT.resolve():
            from wuwa_hotkeys import PlaybackHotkeys
            config=json.loads((self.root/'config.json').read_text(encoding='utf-8'))
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
        self.pause.setText('恢复配音' if paused else '暂停配音')

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

    def finished(self):
        if self.hotkeys:
            self.hotkeys.close()
            self.hotkeys=None
        self.start.setEnabled(True)
        self.start.setText('重新连接 / 刷新音频')
        for control in (self.stop,self.pause,self.replay,self.skip):
            control.setEnabled(False)
        self.pause.blockSignals(True)
        self.pause.setChecked(False)
        self.pause.setText('暂停配音')
        self.pause.blockSignals(False)
        self.capture_status.setText('已停止')
        if self.task_id:
            self.task.setText('上次识别：'+self.task.text())
            self.task_id=''
            self.open_task.setEnabled(False)
            self.task_basis.setText('监听已停止，保留上次识别结果')
        self.preview.clear()
        self.preview.setText('监听已停止')
        self.publish_status(force=True)
        self.stopped.emit()

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
        if 'status' in event:
            self.state.setText(event['status'])
            self.note(event['status'])
        if 'error' in event:
            self.note(event['error'])
        if 'status_text' in event:
            self.ocr_status.setText(event['status_text'])
        if 'log_connection' in event:
            self.capture_status.setText(event['log_connection'])
        if event.get('clear_line'):
            self.subtitle.clear()
            self.recognized = {}
            self.task_id = ''
            self.task.setText('等待剧情')
            self.task_basis.clear()
            self.open_task.setEnabled(False)
            self.preview.setText('等待当前台词')
            self.ocr_status.setText('等待字幕编号')
            self.hints.clear()
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
            self.recognized=value
            self.subtitle.setPlainText((value.get('speaker') or '角色待确认')+'\n'+(value.get('body') or ''))
            self.ocr_status.setText('已匹配日志字幕')
            self.preview.setText(value.get('speaker') or '当前对白')
        if 'library' in event:
            value = event['library']
            self.task_counts=value.get('task_counts',{})
            pending=value.get('review_count',0)
            self.library.setText(f"已有配音：{value['lines']} 句 · {len(value['projects'])} 个项目"+
                                 (f' · {pending} 句读音待核对' if pending else ''))
            self.library.setToolTip('\n'.join(x['reason'] for x in value['held']))
            self.audio_status.setText('等待匹配对白' if value['lines'] else '当前没有已生成的有效音频')
        if 'audio_device' in event:
            self.audio_status.setToolTip('输出设备：'+event['audio_device'])
        if 'audio_error' in event:
            self.audio_status.setText(event['audio_error'])
            self.note(event['audio_error'])
        if 'playback' in event:
            value = event['playback']
            self.last_playback=value
            self.audio_status.setText(value['message'])
            if value['state']=='playing':
                self.played += 1
                self.counter.setText(f'已播放 {self.played} 句')
            if value['state'] in {'playing','error','unmatched'}:
                self.note(value['message'])

    def refresh_capture(self):
        self.publish_status()
        if not self.running():
            return
        controller = self.worker.controller
        for control in (self.pause,self.replay,self.skip):
            control.setEnabled(controller is not None)

    def closeEvent(self,event):
        if self.running():
            self.stop_live()
            event.ignore()
            return
        self.timer.stop()
        self.jobs.close()
        event.accept()
