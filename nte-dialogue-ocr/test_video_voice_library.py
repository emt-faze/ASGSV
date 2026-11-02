import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import test_voice_library as fixtures
from test_voice_library import wav
from generator_voices import VoiceImportTask, ImportCancelled, load_profiles
from voice_library import VoiceLibraryDraft, scan_character_audio_groups, sha256


class VideoLibraryTests(unittest.TestCase):
    setUp = fixtures.VoiceLibraryTests.setUp
    tearDown = fixtures.VoiceLibraryTests.tearDown
    source = fixtures.VoiceLibraryTests.source
    draft = fixtures.VoiceLibraryTests.draft
    builder = fixtures.VoiceLibraryTests.builder
    confirm = fixtures.VoiceLibraryTests.confirm

    def video(self, name='新角色', filename='录屏.MP4'):
        path = self.root/'source'/name/filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'video fixture, do not modify')
        return path

    def prepare_video(self, draft, total=120, **kwargs):
        with patch('voice_library.probe_audio_duration', return_value=total), \
             patch.object(VoiceImportTask, 'convert', side_effect=lambda source, target, start, duration: wav(target, duration)) as convert:
            self.builder(draft, **kwargs).prepare()
        return convert

    def test_scanner_groups_audio_and_common_video_formats(self):
        for suffix in ('.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v', '.ts'):
            self.video('角色甲', 'recording'+suffix)
        self.source('角色甲')
        self.video('角色乙')
        groups = scan_character_audio_groups(self.root/'source')
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(next(g for g in groups if g.speaker=='角色甲').files), 8)

    def test_short_video_prepares_without_manual_range(self):
        video = self.video()
        draft = self.draft()
        convert = self.prepare_video(draft, total=8)
        self.assertEqual(convert.call_args.args[0], video)
        self.assertEqual(convert.call_args.args[2:], (0, 8))
        self.assertEqual(draft.rows[0]['state'], 'ready')
        self.assertFalse(load_profiles(self.data))

    def test_long_video_waits_for_explicit_clip(self):
        self.video()
        draft = self.draft()
        convert = self.prepare_video(draft)
        convert.assert_not_called()
        self.assertEqual(draft.rows[0]['state'], 'failed')
        self.assertIn('选取视频片段', draft.rows[0]['error'])
        self.assertFalse(load_profiles(self.data))

    def test_long_video_exact_range_round_trip_and_publish_provenance(self):
        video = self.video()
        checksum = sha256(video)
        draft = self.draft()
        row = draft.set_video_clip('新角色', ['小名'], dict(source=str(video), start=42.5, duration=8), row=draft.rows[0])
        convert = self.prepare_video(draft)
        self.assertEqual(convert.call_args.args[2:], (42.5, 8))
        self.assertEqual(draft.chosen(row)['start'], 42.5)
        self.confirm(draft)
        draft.save()
        loaded = VoiceLibraryDraft.load(draft.directory)
        self.assertEqual(self.builder(loaded).publish()['imported'], 1)
        profile = load_profiles(self.data)[0]
        reference = self.data/'profiles'/profile.reference_audio
        info = json.loads((reference.parent/'import.json').read_text(encoding='utf-8'))
        self.assertEqual(info['start_seconds'], 42.5)
        self.assertEqual(sha256(reference), loaded.chosen(loaded.rows[0])['sha256'])
        self.assertEqual(checksum, sha256(video))

    def test_invalid_or_out_of_bounds_range_never_registers_voice(self):
        video = self.video()
        draft = self.draft()
        for start, duration in ((-1, 8), (0, 31), (float('nan'), 8)):
            with self.subTest(start=start, duration=duration), self.assertRaises(ValueError):
                draft.set_video_clip('新角色', [], dict(source=str(video), start=start, duration=duration))
        row = draft.set_video_clip('新角色', [], dict(source=str(video), start=115, duration=10), row=draft.rows[0])
        convert = self.prepare_video(draft)
        convert.assert_not_called()
        self.assertIn('超出视频末尾', row['error'])
        self.assertFalse(load_profiles(self.data))

    def test_changing_range_clears_previous_reference_and_review(self):
        video = self.video()
        draft = self.draft()
        self.prepare_video(draft, total=8)
        self.confirm(draft)
        row = draft.rows[0]
        old = draft.reference(draft.chosen(row))
        draft.set_video_clip('新角色', [], dict(source=str(video), start=40, duration=8), row=row)
        self.assertEqual(row['state'], 'pending')
        self.assertEqual(row['candidates'], [])
        self.assertEqual(row['reviewed'], '')
        self.assertTrue(old.is_file())
        self.assertEqual(self.builder(draft).publish()['held'], 1)

    def test_prepare_one_video_does_not_process_other_rows(self):
        first = self.video('角色甲')
        self.video('角色乙')
        draft = self.draft()
        row = next(r for r in draft.rows if r['speaker']=='角色甲')
        draft.set_video_clip('角色甲', [], dict(source=str(first), start=10, duration=8), row=row)
        row['include'] = False  # Preparing the selection must not toggle the publication checkbox.
        with patch('voice_library.probe_audio_duration', return_value=120), \
             patch.object(VoiceImportTask, 'convert', side_effect=lambda source, target, start, duration: wav(target, duration)) as convert:
            self.builder(draft).prepare(row_id=row['id'])
        self.assertEqual(convert.call_count, 1)
        self.assertEqual(row['state'], 'ready')
        self.assertFalse(row['include'])
        self.assertEqual(next(r for r in draft.rows if r is not row)['state'], 'pending')

    def test_cancelled_video_extraction_leaves_no_publishable_draft(self):
        video = self.video()
        draft = self.draft()
        draft.set_video_clip('新角色', [], dict(source=str(video), start=10, duration=8), row=draft.rows[0])
        stop = threading.Event()
        def cancel(source, target, *_):
            wav(target, 1)
            stop.set()
            raise ImportCancelled('stopped')
        with patch('voice_library.probe_audio_duration', return_value=120), \
             patch.object(VoiceImportTask, 'convert', side_effect=cancel), self.assertRaises(ImportCancelled):
            self.builder(draft, stop=stop).prepare()
        self.assertFalse(list(draft.directory.glob('*.wav')))
        self.assertFalse(load_profiles(self.data))
        loaded = VoiceLibraryDraft.load(draft.directory)
        self.prepare_video(loaded)
        self.assertTrue(loaded.chosen(loaded.rows[0]))


class VideoLibraryUiTests(unittest.TestCase):
    setUp = fixtures.VoiceLibraryTests.setUp
    tearDown = fixtures.VoiceLibraryTests.tearDown
    video = VideoLibraryTests.video

    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_video_picker_validates_name_and_file_and_returns_range(self):
        from voice_library_ui import VideoClipDialog
        from PySide6 import QtWidgets
        dialog = VideoClipDialog()
        try:
            dialog.accept()
            self.assertIsNone(dialog.result_values)
            video = self.video()
            dialog.source.setCurrentText(str(video))
            dialog.accept()
            self.assertIn('角色名', dialog.status.text())
            dialog.speaker.setText('视频角色')
            dialog.start.setValue(80)
            dialog.duration.setValue(8)
            dialog.accept()
            self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
            self.assertEqual(dialog.result_values['clip']['start'], 80)
            self.assertIn('00:01:20.00', dialog.range_hint.text())
        finally:
            dialog.close()

    def test_video_button_creates_reviewable_draft_without_adding_profile(self):
        from voice_library_ui import VoiceLibraryDialog, VideoClipDialog
        from PySide6 import QtCore
        video = self.video()
        dialog = VoiceLibraryDialog({}, self.root)
        dialog.show()
        def choose():
            picker = dialog.clip_dialog
            self.assertIsInstance(picker, VideoClipDialog)
            picker.source.setCurrentText(str(video))
            picker.speaker.setText('视频角色')
            picker.start.setValue(42.5)
            picker.duration.setValue(8)
            picker.accept()
        try:
            with patch('voice_library.probe_audio_duration', return_value=120), \
                 patch.object(VoiceImportTask, 'convert', side_effect=lambda source, target, start, duration: wav(target, duration)), \
                 patch('voice_library.FasterWhisperTranscriber.transcribe', return_value='视频中的台词。'):
                QtCore.QTimer.singleShot(20, choose)
                dialog.add_video()
                deadline = time.monotonic()+6
                while dialog.busy() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.app.processEvents()
                self.assertFalse(dialog.busy())
                self.assertEqual(dialog.editor_row['state'], 'ready')
                self.assertFalse(dialog.confirmed.isChecked())
                self.assertEqual(dialog.text.toPlainText(), '视频中的台词。')
                self.assertIn('00:00:42.50', dialog.clip_label.text())
                self.assertFalse(load_profiles(self.data))
        finally:
            dialog.cancel()
            if dialog.worker:
                dialog.worker.wait(5000)
            self.app.processEvents()
            dialog.close()


if __name__ == '__main__':
    unittest.main()
