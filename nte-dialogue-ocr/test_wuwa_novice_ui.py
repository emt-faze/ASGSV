import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6 import QtWidgets as W
from dialogue_generator_ui import GeneratorWindow
from test_dialogue_generation import Client, Runtime


class NoviceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = W.QApplication.instance() or W.QApplication([])

    def test_spoiler_toggle_never_changes_saved_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch('dialogue_generator_ui.VoiceRuntime', return_value=Runtime(root, Client())):
                window = GeneratorWindow({'game_id': 'wuwa', 'mode': 'system'}, root=root)
                try:
                    window.project.add_text('NPC: original dialogue')
                    window.render()
                    window.table.setCurrentCell(0, 2)
                    window.show()
                    self.app.processEvents()
                    original = window.project.rows[0].text
                    self.assertNotEqual(window.table.item(0, 2).text(), original)
                    self.assertNotIn(original, window.table.item(0, 2).toolTip())
                    self.assertFalse(window.editor.isVisible())
                    self.assertFalse(window.mode.isVisible())
                    self.assertTrue(window.simple_play.isVisible())
                    window.advanced_toggle.setChecked(True)
                    self.assertFalse(window.editor.isVisible())
                    window.show_text.setChecked(True)
                    self.assertTrue(window.editor.isVisible())
                    self.assertEqual(window.table.item(0, 2).text(), original)
                    window.show_text.setChecked(False)
                    window.advanced_toggle.setChecked(False)
                    window.save_editor()
                    self.assertEqual(window.project.rows[0].text, original)
                    with patch.object(W.QMessageBox, 'question', return_value=W.QMessageBox.No) as question:
                        window.open_playback()
                        self.assertTrue(question.called)
                        self.assertIsNone(window.playback_dialog)
                finally:
                    window.close()
                    self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
