"""Stage preferences and binding changes that arrive while a subtitle is being applied."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtGui, QtWidgets

from ui.stage_background import DEFAULT, DialogueStage
from wuwa_log_live import LogWorker


class StageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_custom_art_restores_and_mask_reuses_blur(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'gradient.png'
            image = QtGui.QImage(360,120,QtGui.QImage.Format_RGB32)
            image.fill(QtGui.QColor('blue'))
            with QtGui.QPainter(image) as painter:
                painter.fillRect(120,0,120,120,QtGui.QColor('red'))
            self.assertTrue(image.save(str(path)))
            stage = DialogueStage(folder)
            stage.resize(120,120)
            stage.options['blur'] = 12
            stage.switch('file',str(path))
            pixels = stage.layer()
            self.assertEqual(pixels.toImage().pixelColor(0,0),QtGui.QColor('red'))
            stage.options['mask'] = .45
            stage.save()
            self.assertEqual(stage.layer().cacheKey(),pixels.cacheKey())
            self.assertEqual(DialogueStage(folder).options,stage.options)
            path.unlink()
            self.assertEqual(DialogueStage(folder).options,DEFAULT)

    def test_change_during_apply_only_refreshes_matching_subtitle_and_voice(self):
        worker = LogWorker(Path('.'))
        worker.current_row, worker.current_voice = 'line', 'old'
        stamp = [('line',None,None,None,'new')]
        worker.request_voice_refresh('another','new')
        self.assertFalse(worker.needs_voice_refresh(stamp))
        worker.request_voice_refresh('line','new')
        self.assertTrue(worker.needs_voice_refresh(stamp))
        self.assertFalse(worker.needs_voice_refresh(stamp))
        worker.current_voice = 'new'
        worker.request_voice_refresh('line','new')
        self.assertFalse(worker.needs_voice_refresh(stamp))
