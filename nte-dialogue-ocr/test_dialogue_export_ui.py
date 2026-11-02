import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtWidgets
from dialogue_export_ui import DialogueExportDialog
from dialogue_generator_ui import GeneratorWindow
from dialogue_generation import GenerationProject
import test_dialogue_generation as fixtures


class DialogueExportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = GenerationProject.create(self.root/'projects')
        self.project.add_text('黑羽：第一句\n伊洛伊：第二句')
        self.dialog = DialogueExportDialog(self.project)
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        self.dialog.close()
        self.wait(lambda: not self.dialog.busy())
        self.dialog.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self, condition, seconds=5):
        deadline = time.monotonic()+seconds
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.app.processEvents()
        self.assertTrue(condition(), 'Export UI timed out')

    def test_empty_checked_scope_disables_export(self):
        for row in self.project.rows:
            row.selected = False
        self.dialog.scope.setCurrentIndex(1)
        self.assertFalse(self.dialog.save_button.isEnabled())
        self.assertIn('将导出 0 条', self.dialog.summary.text())
        self.dialog.scope.setCurrentIndex(0)
        self.assertTrue(self.dialog.save_button.isEnabled())

    def test_save_dialog_cancellation_creates_no_file(self):
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=('', '')):
            self.dialog.save_button.click()
        self.assertIsNone(self.dialog.worker)
        self.assertEqual(list((self.project.directory/'exports').iterdir()), [])

    def test_save_dialog_adds_selected_extension_and_reports_completion(self):
        path = self.root/'用户选择的台词'
        self.dialog.format.setCurrentIndex(1)
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(path), '')):
            self.dialog.save_button.click()
        self.wait(lambda: self.dialog.result_report is not None)
        self.assertTrue(path.with_suffix('.csv').is_file())
        self.assertEqual(self.dialog.result_report['rows'], 2)
        self.assertTrue(self.dialog.open_button.isEnabled())
        self.assertFalse(self.dialog.stop_button.isEnabled())
        self.assertTrue(self.dialog.save_button.isEnabled())

    def test_failure_can_be_retried_without_losing_original_file(self):
        path = self.root/'existing.txt'
        path.write_bytes(b'keep original')
        with patch('dialogue_export.os.replace', side_effect=PermissionError('文件正被占用')):
            self.dialog.start_export(path)
            self.wait(lambda: self.dialog.worker.error != '')
        self.assertIn('文件正被占用', self.dialog.status.text())
        self.assertEqual(path.read_bytes(), b'keep original')
        self.assertTrue(self.dialog.save_button.isEnabled())
        self.dialog.start_export(path)
        self.wait(lambda: self.dialog.result_report is not None)
        self.assertIn('第一句', path.read_bytes().decode('utf-8-sig'))

    def test_close_cancels_active_export_before_hiding(self):
        entered, release = threading.Event(), threading.Event()
        def slow_export(task):
            entered.set()
            release.wait(3)
            task.check_cancel()
        with patch('dialogue_export.DialogueExportTask.run', slow_export):
            self.dialog.start_export(self.root/'cancel.txt')
            self.wait(entered.is_set)
            self.dialog.close()
            self.assertTrue(self.dialog.closing)
            self.assertTrue(self.dialog.isVisible())
            self.assertFalse(self.dialog.save_button.isEnabled())
            release.set()
            self.wait(lambda: not self.dialog.isVisible())
        self.assertIsNone(self.dialog.result_report)
        self.assertFalse((self.root/'cancel.txt').exists())

    def test_main_button_exports_current_unsaved_editor_text(self):
        client = fixtures.Client()
        runtime = fixtures.Runtime(self.root, client)
        with patch('dialogue_generator_ui.VoiceRuntime', return_value=runtime):
            window = GeneratorWindow({'mode': 'system'}, root=self.root)
        try:
            window.project.add_text('黑羽：原文')
            window.render()
            window.table.setCurrentCell(0, 2)
            window.text.setPlainText('尚未切换行的修改\n保留换行')
            path = self.root/'editor.txt'
            def export_and_close(dialog):
                dialog.start_export(path)
                self.wait(lambda: dialog.result_report is not None)
                dialog.close()
            with patch.object(DialogueExportDialog, 'exec', export_and_close):
                window.export_button.click()
            self.assertIn('尚未切换行的修改\n保留换行', path.read_bytes().decode('utf-8-sig'))
            self.assertEqual(client.calls, [])
            self.assertIn('已导出 1 条台词', window.status.text())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
