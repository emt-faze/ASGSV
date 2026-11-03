"""Regressions for dark OS palettes and contextual help across real dialogs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6 import QtCore, QtGui, QtWidgets
from dialogue_generator_ui import GeneratorWindow, ManualDialogueDialog
from dialogue_export_ui import DialogueExportDialog
from generator_ocr import OcrCollectionDialog
from generator_playback import PreparedPlaybackDialog
from generator_voices import AddVoiceDialog
from voice_library_ui import VoiceLibraryDialog, VideoClipDialog
from generator_voice_review import review_dialog
from system_voice_dialog import SystemVoiceDialog
from playback_metrics import PlaybackMetrics, show_metrics
import test_dialogue_generation as fixtures


def dark_palette():
    palette = QtGui.QPalette()
    for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive, QtGui.QPalette.Disabled):
        for role in (QtGui.QPalette.Window, QtGui.QPalette.Base, QtGui.QPalette.Button,
                     QtGui.QPalette.AlternateBase, QtGui.QPalette.ToolTipBase):
            palette.setColor(group, role, QtGui.QColor('#242424'))
        for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText,
                     QtGui.QPalette.ToolTipText, QtGui.QPalette.HighlightedText):
            palette.setColor(group, role, QtGui.QColor('#ffffff'))
    return palette


def contrast(first, second):
    def luminance(color):
        parts = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                 for value in (color.redF(), color.greenF(), color.blueF())]
        return sum(value * weight for value, weight in zip(parts, (.2126, .7152, .0722)))
    first, second = sorted((luminance(first), luminance(second)))
    return (second + .05) / (first + .05)


class GeneratorAppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle('Fusion')

    def setUp(self):
        self.original_palette = self.app.palette()
        self.app.setPalette(dark_palette())
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'config.json').write_text(json.dumps({'capture_backend': 'wgc'}), encoding='utf-8')
        self.runtime = fixtures.Runtime(self.root, fixtures.Client())
        self.patches = [patch('dialogue_generator_ui.VoiceRuntime', return_value=self.runtime),
                        patch('system_voice_dialog.VoiceRuntime', return_value=self.runtime),
                        patch('system_voice_dialog.ROOT', self.root),
                        patch('dialogue_generator_ui.voice_paths', return_value=(self.root, self.root)),
                        patch('generator_voices.voice_paths', return_value=(self.root, self.root)),
                        patch('capture_windows.list_game_windows', return_value=[])]
        for item in self.patches:
            item.start()
        self.dialogs = []
        self.window = self.keep(GeneratorWindow({'mode': 'system'}, root=self.root))

    def keep(self, dialog):
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def tearDown(self):
        QtWidgets.QToolTip.hideText()
        for dialog in reversed(self.dialogs):
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        for item in reversed(self.patches):
            item.stop()
        self.app.setPalette(self.original_palette)
        self.temp.cleanup()

    def assert_readable(self, widget, foreground, background):
        for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive, QtGui.QPalette.Disabled):
            palette = widget.palette()
            self.assertGreaterEqual(contrast(palette.color(group, foreground), palette.color(group, background)),
                                    4.5, (type(widget).__name__, group, foreground, background))

    def test_dark_palette_cannot_mix_with_light_controls_even_after_system_change(self):
        self.window.project.add_text('伊洛伊：界面文字应清晰可见。\n娜娜莉：第二句。')
        self.window.render()
        self.window.table.setCurrentCell(0, 2)
        # Emulate switching OS theme while the generator is already open.
        self.app.setPalette(dark_palette())
        self.app.processEvents()
        for widget in (self.window, self.window.count_label, self.window.status, self.window.editor):
            self.assert_readable(widget, QtGui.QPalette.WindowText, QtGui.QPalette.Window)
        for widget in (self.window.text, self.window.voice, self.window.speaker, self.window.table):
            self.assert_readable(widget, QtGui.QPalette.Text, QtGui.QPalette.Base)
        self.assert_readable(self.window.table, QtGui.QPalette.Text, QtGui.QPalette.AlternateBase)
        self.assert_readable(self.window.table, QtGui.QPalette.HighlightedText, QtGui.QPalette.Highlight)
        self.assert_readable(self.window.table.horizontalHeader(), QtGui.QPalette.ButtonText, QtGui.QPalette.Button)
        for button in self.window.findChildren(QtWidgets.QPushButton):
            if button.objectName() != 'primary':
                self.assert_readable(button, QtGui.QPalette.ButtonText, QtGui.QPalette.Button)

    def test_help_is_available_on_disabled_buttons_without_invoking_action(self):
        button = self.window.stop_generation
        self.assertFalse(button.isEnabled())
        button.setFocus()
        position = button.rect().center()
        event = QtGui.QHelpEvent(QtCore.QEvent.ToolTip, position, button.mapToGlobal(position))
        QtWidgets.QApplication.sendEvent(button, event)
        self.app.processEvents()
        self.assertTrue(QtWidgets.QToolTip.isVisible())
        self.assertIn('已经完成', QtWidgets.QToolTip.text())
        self.assertIsNone(self.window.worker)
        self.assertEqual(self.window.project.rows, [])
        self.assert_readable(button, QtGui.QPalette.ToolTipText, QtGui.QPalette.ToolTipBase)

    def test_each_real_dialog_exposes_button_help_and_readable_text(self):
        project = self.window.project
        self.keep(ManualDialogueDialog([('自动', 'auto')]))
        self.keep(DialogueExportDialog(project))
        self.keep(OcrCollectionDialog(self.root))
        self.keep(PreparedPlaybackDialog(self.root, project, {}))
        self.keep(AddVoiceDialog({}))
        self.keep(VoiceLibraryDialog({}, self.root, self.window))
        self.keep(VideoClipDialog(parent=self.window))
        self.keep(review_dialog(None, [], [('自动', 'auto')]))
        self.keep(SystemVoiceDialog({}))
        with patch.object(QtWidgets.QDialog, 'exec', lambda dialog: self.keep(dialog)):
            show_metrics(None, PlaybackMetrics(project.directory))
        for dialog in self.dialogs:
            with self.subTest(dialog=dialog.windowTitle()):
                self.assert_readable(dialog, QtGui.QPalette.WindowText, QtGui.QPalette.Window)
                for button in dialog.findChildren(QtWidgets.QPushButton):
                    if button.text():
                        self.assertTrue(button.toolTip(), (dialog.windowTitle(), button.text()))
                        self.assertTrue(button.accessibleDescription())
                        if button.objectName() != 'primary':
                            self.assert_readable(button, QtGui.QPalette.ButtonText, QtGui.QPalette.Button)

    def test_playback_help_changes_with_pause_resume_and_session_reset(self):
        dialog = self.keep(PreparedPlaybackDialog(self.root, self.window.project, {}))
        self.assertNotIn('采集到当前项目', dialog.start.toolTip())
        self.assertIn('配音', dialog.start.toolTip())
        dialog.pause_button.setChecked(True)
        self.assertIn('<b>继续自动播放</b>', dialog.pause_button.toolTip())
        self.assertIn('从当前字幕', dialog.pause_button.toolTip())
        dialog.finished_work()
        self.assertFalse(dialog.pause_button.isChecked())
        self.assertIn('<b>暂停自动播放</b>', dialog.pause_button.toolTip())

    def test_system_preview_help_tracks_start_and_finish(self):
        dialog = self.keep(SystemVoiceDialog({}))
        worker = MagicMock()
        worker.isRunning.return_value = False
        with patch('system_voice_dialog.PreviewWorker', return_value=worker):
            dialog.start_preview()
        self.assertIn('<b>停止试听</b>', dialog.preview.toolTip())
        dialog.preview_finished()
        self.assertIn('<b>试听所选声音</b>', dialog.preview.toolTip())


if __name__ == '__main__':
    unittest.main()
