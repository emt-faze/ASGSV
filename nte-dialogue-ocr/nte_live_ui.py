"""User-facing NTE hybrid playback, reusing game capture and calibration controls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from PySide6 import QtCore, QtWidgets as W
from dialogue_generation import atomic_text
from generator_ocr import OcrCollectionDialog
from nte_live import NteLiveSession
from nte_catalog import catalog_path
from generator_ui_support import configure_dialog, fit_window_to_screen, set_help

ROOT = Path(__file__).resolve().parent


class LiveWorker(QtCore.QThread):
    message = QtCore.Signal(object)

    def __init__(self, window, root, config, project, parent=None):
        super().__init__(parent)
        self.window = window
        self.session = NteLiveSession(root, config, self.message.emit, preferred_project=project)

    @property
    def capture(self):
        return self.session.capture

    def cancel(self):
        self.session.cancel()

    def run(self):
        try:
            if self.window is None:
                from game_launch import ensure_game
                self.window = ensure_game(self.session.config, stop=self.session.stop_event,
                    progress=lambda text: self.message.emit(dict(status_text=text)))['window']
            if not self.session.stop_event.is_set():
                self.session.run(self.window)
        except Exception as exc:
            self.message.emit(dict(error=str(exc)))


class NteLiveDialog(OcrCollectionDialog):
    def __init__(self, root=ROOT, project=None, parent=None, *, autoconnect=False):
        self.project, self.autoconnect = project, autoconnect
        self.updater = None
        self.last_serial = -1
        self.timings = {'project': [], 'cache': [], 'generated': []}
        super().__init__(root, parent)
        self.setWindowTitle('异环实时配音 · 日志与字幕')
        self.resize(940, 780)
        self.note.setText('日志定位台词编号，字幕补充定位游戏原文。已有配音优先使用，缺少时按声音设置生成。\n'
                          '对话中持续识别，平时降低识别频率。翻页会停止上一句。')
        self.start.setText('开始实时配音')
        self.stop.setText('停止实时配音')
        self.return_button.setText('关闭')
        self.count.setText('原文库尚未读取')
        self.preview.setMinimumHeight(120)
        self.current.setMaximumHeight(160)
        self.current.setPlaceholderText('当前角色、游戏原文和匹配来源')
        self.source = W.QLabel('等待连接游戏')
        self.source.setWordWrap(True)
        self.layout().insertWidget(2, self.source)
        self.playback = W.QLabel('配音尚未开始')
        self.playback.setWordWrap(True)
        self.layout().insertWidget(self.layout().count()-1, self.playback)
        self.latency = W.QLabel('画面采集或日志到达 → 开始播放，等待采样')
        self.latency.setWordWrap(True)
        self.layout().insertWidget(self.layout().count()-1, self.latency)
        controls = W.QHBoxLayout()
        self.pause_button = W.QPushButton('暂停 F8')
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self.pause_playback)
        self.replay_button = W.QPushButton('重播 F9')
        self.replay_button.clicked.connect(lambda: self.controller_action('replay'))
        self.skip_button = W.QPushButton('跳过 F10')
        self.skip_button.clicked.connect(lambda: self.controller_action('skip'))
        for button in (self.pause_button, self.replay_button, self.skip_button):
            button.setEnabled(False)
            controls.addWidget(button)
        self.voice_button = W.QPushButton('系统中文声设置')
        self.voice_button.clicked.connect(self.voice_settings)
        self.update_button = W.QPushButton('更新游戏原文库')
        self.update_button.clicked.connect(self.update_catalog)
        controls.addWidget(self.voice_button)
        controls.addWidget(self.update_button)
        self.layout().insertLayout(self.layout().count()-1, controls)
        self.start.setObjectName('primary')
        configure_dialog(self, 'nte_live')
        self.load_catalog_status()
        from wuwa_hotkeys import PlaybackHotkeys
        self.hotkeys = PlaybackHotkeys(self, {}, dict(pause=self.pause_button.click,
            replay=self.replay_button.click, skip=self.skip_button.click))
        if self.hotkeys.errors:
            self.status.setText('部分快捷键被占用，可使用窗口按钮操作 · ' + '、'.join(self.hotkeys.errors))
        if autoconnect:
            QtCore.QTimer.singleShot(100, self.start_collection)
        fit_window_to_screen(self, 940, 780)

    def target(self):
        return self.windows.currentData() if self.autoconnect else super().target()

    def busy(self):
        return super().busy() or bool(self.updater and self.updater.state() != QtCore.QProcess.NotRunning)

    def set_busy(self, value):
        super().set_busy(value)
        if hasattr(self, 'voice_button'):
            self.voice_button.setEnabled(not value)
            self.update_button.setEnabled(not value)

    def load_catalog_status(self):
        from nte_catalog import NteCatalog
        try:
            catalog = NteCatalog(catalog_path(self.root))
            self.count.setText(f"中文资源文本 {int(catalog.metadata['entry_count']):,} 条  ·  已建立编号与原文索引")
            catalog.close()
        except Exception as exc:
            self.count.setText('请更新游戏原文库 · ' + str(exc))

    def make_worker(self, window):
        self.last_serial = -1
        self.timings = {'project': [], 'cache': [], 'generated': []}
        self.pause_button.blockSignals(True)
        self.pause_button.setChecked(False)
        self.pause_button.setText('暂停 F8')
        set_help(self.pause_button, '暂停实时配音播放，可按 F8 或点击同一按钮继续。')
        self.pause_button.blockSignals(False)
        return LiveWorker(window, self.root, self.config, self.project, self)

    def receive(self, event):
        if 'status_text' in event:
            self.status.setText(event['status_text'])
        if 'error' in event:
            self.status.setText('连接已停止 · ' + event['error'])
        if 'log_status' in event:
            self.source.setText(event['log_status'])
        if event.get('ready'):
            for button in (self.pause_button, self.replay_button, self.skip_button):
                button.setEnabled(True)
        if 'match_status' in event:
            self.source.setText(event['match_status'])
        if 'state' not in event or event.get('serial', 0) < self.last_serial:
            return
        self.last_serial = event['serial']
        self.playback.setText(event['message'])
        if event.get('text'):
            source = '日志编号定位' if event.get('source') == 'log' else '字幕定位原文'
            self.current.setPlainText(f"{event.get('speaker') or '未指定角色'} · {source}\n{event['text']}\n{event['catalog_id']}")
        elif event['state'] == 'waiting':
            self.current.clear()
        if 'latency_ms' in event:
            self.timings[event['audio_origin']].append(event['latency_ms'])
            cached = self.timings['project'] + self.timings['cache']
            generated = self.timings['generated']
            labels = []
            for name, samples in (('已有音频', cached), ('首次合成', generated)):
                labels.append(f'{name}平均 {sum(samples)/len(samples):.0f} 毫秒（{len(samples)}次）' if samples else name + '等待采样')
            self.latency.setText('采集画面或日志到达 → 播放  ·  ' + '  ·  '.join(labels))

    def controller_action(self, method, *args):
        if self.worker and self.worker.session.controller:
            getattr(self.worker.session.controller, method)(*args)

    def pause_playback(self, paused):
        self.pause_button.setText('继续 F8' if paused else '暂停 F8')
        set_help(self.pause_button, '继续实时配音播放，也可按 F8。' if paused else
                 '暂停实时配音播放，可按 F8 或点击同一按钮继续。')
        self.controller_action('pause', paused)

    def stop_work(self):
        super().stop_work()
        for button in (self.pause_button, self.replay_button, self.skip_button):
            button.setEnabled(False)
        if self.updater and self.updater.state() != QtCore.QProcess.NotRunning:
            self.updater.kill()

    def finished_work(self):
        super().finished_work()
        if not self.busy():
            for button in (self.pause_button, self.replay_button, self.skip_button):
                button.setEnabled(False)

    def voice_settings(self):
        from system_voice_dialog import SystemVoiceDialog
        self.config = json.loads((self.root / 'config.json').read_text(encoding='utf-8-sig'))
        dialog = SystemVoiceDialog(self.config.get('dubbing', {}), self)
        if dialog.exec() == W.QDialog.Accepted:
            current = json.loads((self.root / 'config.json').read_text(encoding='utf-8-sig'))
            current['dubbing'] = dialog.result_options
            atomic_text(self.root / 'config.json', json.dumps(current, ensure_ascii=False, indent=2))
            self.config = current

    def update_catalog(self):
        if self.busy():
            return
        self.config = json.loads((self.root / 'config.json').read_text(encoding='utf-8-sig'))
        log = self.root / 'nte-data/catalog-update.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        self.updater = QtCore.QProcess(self)
        self.updater.setWorkingDirectory(str(self.root))
        self.updater.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.updater.setStandardOutputFile(str(log))
        self.updater.finished.connect(self.catalog_updated)
        self.updater.errorOccurred.connect(self.catalog_update_error)
        self.updater.start(sys.executable, ['-s', '-X', 'utf8', str(self.root / 'nte_resources.py'),
            '--auto', '--config', str(self.root / 'config.json')])
        self.set_busy(True)
        self.status.setText('正在读取已安装的异环资源并更新原文库…')

    def catalog_update_error(self, error):
        self.status.setText('原文库更新进程未完成 · ' + self.updater.errorString())
        if error == QtCore.QProcess.FailedToStart:
            self.set_busy(False)
            if self.closing:
                self.close()

    def catalog_updated(self, code, status):
        self.set_busy(False)
        self.load_catalog_status()
        self.status.setText('游戏原文库已更新' if code == 0 else '原文库更新未完成，请查看 nte-data/catalog-update.log')
        if self.closing:
            self.close()

    def closeEvent(self, event):
        super().closeEvent(event)
        if event.isAccepted():
            self.hotkeys.close()


def show_live(root=ROOT, project=None, *, autoconnect=False):
    from capture_windows import enable_dpi_awareness
    enable_dpi_awareness()
    application = W.QApplication.instance() or W.QApplication(sys.argv[:1])
    dialog = NteLiveDialog(root, project, autoconnect=autoconnect)
    dialog.show()
    return application.exec()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project')
    parser.add_argument('--autoconnect', action='store_true')
    args = parser.parse_args()
    raise SystemExit(show_live(project=args.project, autoconnect=args.autoconnect))
