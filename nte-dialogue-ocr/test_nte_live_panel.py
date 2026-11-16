import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6 import QtCore,QtWidgets as W
from nte_live_panel import NteLivePanel


class FakeWorker(QtCore.QObject):
    message = QtCore.Signal(object)
    finished = QtCore.Signal()

    def __init__(self,window,root,config,project,parent=None):
        super().__init__(parent)
        self.window,self.project = window,project
        self.session = SimpleNamespace(controller=Mock())
        self.active = False
        self.capture = None
        self.cancelled = False

    def start(self):
        self.active = True

    def isRunning(self):
        return self.active

    def cancel(self):
        self.cancelled = True

    def finish(self):
        self.active = False
        self.finished.emit()


class LivePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = W.QApplication.instance() or W.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'config.json').write_text(json.dumps(dict(game_id='nte',calibrated=True,body_roi=[0,0,1,1],
            dubbing={},nte_hybrid=dict(auto_start=False))),encoding='utf8')
        self.panel = NteLivePanel(self.root)

    def tearDown(self):
        self.panel.shutdown()
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def start(self):
        window = SimpleNamespace(is_launcher=False)
        with patch('nte_live_panel.LiveWorker',FakeWorker),patch('capture_windows.list_game_windows',return_value=[window]):
            self.panel.start.click()
        return self.panel.worker

    def test_embedded_controls_start_pause_skip_replay_and_stop_same_session(self):
        self.panel.set_project(self.root/'project','1 条台词')
        worker = self.start()
        self.assertEqual(worker.project,self.root/'project')
        worker.message.emit(dict(ready=True))
        self.panel.pause.click()
        worker.session.controller.pause.assert_called_once_with(True)
        self.panel.skip.click()
        worker.session.controller.skip.assert_called_once()
        self.panel.replay.click()
        worker.session.controller.replay.assert_called_once()
        self.panel.stop.click()
        self.assertTrue(worker.cancelled)
        self.assertFalse(self.panel.start.isEnabled())
        worker.finish()
        self.assertFalse(self.panel.running())
        self.assertTrue(self.panel.start.isEnabled())
        self.assertIn('已停止',self.panel.state.text())

    def test_current_subtitle_source_and_latency_are_visible(self):
        worker = self.start()
        worker.message.emit(dict(state='playing',serial=2,message='正在播放',speaker='丽妮丝',text='很荣幸为您服务。',
            source='subtitle',catalog_id='ST_Dialogue/TEST',audio_origin='cache',latency_ms=450))
        self.assertIn('丽妮丝',self.panel.subtitle.toPlainText())
        self.assertIn('游戏原文',self.panel.task_basis.text())
        self.assertIn('450',self.panel.library.text())
        self.assertEqual(self.panel.counter.text(),'已播放 1 句')
        worker.message.emit(dict(state='waiting',serial=1,message='old'))
        self.assertIn('丽妮丝',self.panel.subtitle.toPlainText())
        worker.finish()

    def test_failed_connection_keeps_error_after_cleanup(self):
        worker = self.start()
        worker.message.emit(dict(error='没有可用的游戏窗口'))
        worker.finish()
        self.assertIn('没有可用的游戏窗口',self.panel.state.text())

    def test_auto_connection_setting_preserves_existing_config(self):
        self.panel.auto.setChecked(True)
        config = json.loads((self.root/'config.json').read_text('utf8'))
        self.assertTrue(config['nte_hybrid']['auto_start'])
        self.assertEqual(config['body_roi'],[0,0,1,1])


if __name__=='__main__':
    unittest.main()
