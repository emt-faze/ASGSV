import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'wuwa-dubber/src'))
from generator_voices import VoiceImportTask, ImportCancelled, load_profiles
from voice_library import (scan_character_audio_groups, rank_audio_candidates, probe_audio_duration,
    _read_flac_duration, CharacterAudioGroup, VoiceLibraryDraft, BatchVoiceProfileBuilder,
    normalize_transcript, sha256, inspect_reference, FasterWhisperTranscriber, check_cancel)


def wav(path, seconds=8, sample=2000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, 32000, 0, 'NONE', 'not compressed'))
        audio.writeframes(int(sample).to_bytes(2, 'little', signed=True)*int(seconds*32000))


class Transcriber:
    def __init__(self, *_):
        self.calls = []
        self.closed = False

    def transcribe(self, path, *, cancel_event=None):
        self.calls.append((path, sha256(path)))
        return '你好，准备好了吗？'

    def close(self):
        self.closed = True


class VoiceLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root/'data'
        self.data.mkdir()
        (self.data/'config.json').write_text('{}', encoding='utf-8')
        self.paths = patch('generator_voices.voice_paths', return_value=(self.root, self.data))
        self.paths.start()
        self.transcriber = Transcriber()

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def source(self, name='新角色', filename='a.wav', seconds=8):
        path = self.root/'source'/name/'角色包_人声'/filename
        wav(path, seconds)
        return path

    def draft(self):
        return VoiceLibraryDraft.create(self.root/'drafts', self.root/'source', scan_character_audio_groups(self.root/'source'))

    def builder(self, draft, **kwargs):
        return BatchVoiceProfileBuilder({}, draft, transcriber_factory=lambda _: self.transcriber,
            quality_reader=lambda *_: dict(speech_ratio=.8, quiet_fraction=.2), **kwargs)

    def prepare(self, draft):
        with patch.object(VoiceImportTask, 'convert', side_effect=lambda source, target, *_: shutil.copyfile(source, target)):
            self.builder(draft).prepare()

    def confirm(self, draft, row=None):
        row = row or draft.rows[0]
        row['reviewed'] = draft.review_key(row)

    def test_scan_groups_characters_and_ignores_project_directories(self):
        self.source('今汐')
        self.source('安可')
        wav(self.root/'source/data/ignored.wav')
        wav(self.root/'source/.venv/ignored.wav')
        groups = scan_character_audio_groups(self.root/'source')
        self.assertEqual([g.speaker for g in groups], ['今汐', '安可'])
        single = scan_character_audio_groups(self.root/'source/今汐')
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0].speaker, '今汐')

    def test_parent_loose_audio_does_not_merge_child_characters(self):
        self.source('角色甲')
        self.source('角色乙')
        wav(self.root/'source/loose.wav')
        groups = scan_character_audio_groups(self.root/'source')
        self.assertEqual([g.speaker for g in groups], ['source', '角色乙', '角色甲'])
        self.assertEqual(len(groups[0].files), 1)

    def test_rank_uses_duration_and_source_and_ignores_invalid_values(self):
        paths = [Path('角色/剧情/story.flac'), Path('角色/角色包_人声/short.flac'),
                 Path('角色/角色包_人声/ideal.flac'), Path('bad.flac')]
        durations = dict(story=9.1, short=4., ideal=8.5, bad=float('nan'))
        ranked = rank_audio_candidates(CharacterAudioGroup('角色', self.root, tuple(paths)),
            duration_reader=lambda p, _: durations[p.stem], max_candidates=2)
        self.assertEqual([c.path.stem for c in ranked], ['ideal', 'story'])

    def test_flac_header_and_truncated_metadata(self):
        path = self.root/'clip.flac'
        packed = (32000 << 44) | (1 << 41) | (15 << 36) | (9*32000)
        body = b'\0'*10 + packed.to_bytes(8, 'big') + b'\0'*16
        path.write_bytes(b'fLaC\x80\x00\x00\x22'+body)
        self.assertEqual(probe_audio_duration(path, 'missing-ffprobe'), 9)
        for content in (b'fLaC', b'fLaC\x80\x00\x00\x22'+body[:15], b'fLaC\x81\xff\xff\xff'):
            path.write_bytes(content)
            self.assertIsNone(_read_flac_duration(path))

    def test_wav_probe_needs_no_external_program_and_timeout_is_nonfatal(self):
        source = self.source(seconds=6)
        self.assertEqual(probe_audio_duration(source, 'missing-ffprobe'), 6)
        with patch('voice_library.run_process', side_effect=TimeoutError), patch('voice_library.shutil.which', return_value=None):
            self.assertIsNone(probe_audio_duration(self.root/'invalid.mp3', 'missing-ffprobe'))

    def test_cancel_during_scan_and_candidate_ranking(self):
        self.source()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(ImportCancelled):
            scan_character_audio_groups(self.root/'source', cancel_event=stop)
        stop.clear()
        def reader(*_):
            stop.set()
            return 9
        group = CharacterAudioGroup('角色', self.root, (Path('a'), Path('b')))
        with self.assertRaises(ImportCancelled):
            rank_audio_candidates(group, duration_reader=reader, cancel_event=stop)

    def test_transcript_preserves_sentence_endings_and_english_words(self):
        self.assertEqual(normalize_transcript(' 你好，  世 界。 '), '你好， 世界。')
        self.assertEqual(normalize_transcript(' hello  world! '), 'hello world!')

    def test_preparation_never_registers_voice_and_transcribes_staged_wav(self):
        source = self.source()
        draft = self.draft()
        self.prepare(draft)
        self.assertEqual(load_profiles(self.data), [])
        candidate = draft.chosen(draft.rows[0])
        self.assertEqual(self.transcriber.calls[0][1], candidate['sha256'])
        self.assertNotEqual(self.transcriber.calls[0][0], source)
        self.assertEqual(candidate['text'], '你好，准备好了吗？')
        self.assertEqual(draft.rows[0]['reviewed'], '')
        self.assertTrue(self.transcriber.closed)

    def test_publish_requires_review_and_copies_exact_reviewed_samples(self):
        self.source()
        draft = self.draft()
        self.prepare(draft)
        self.assertEqual(self.builder(draft).publish()['held'], 1)
        self.confirm(draft)
        with patch.object(VoiceImportTask, 'convert', side_effect=AssertionError('Must not reconvert reviewed audio')):
            result = self.builder(draft).publish()
        self.assertEqual(result['imported'], 1)
        saved = load_profiles(self.data)[0]
        self.assertEqual(sha256(self.data/'profiles'/saved.reference_audio), draft.chosen(draft.rows[0])['sha256'])
        self.assertEqual(self.builder(draft).publish()['imported'], 0)

    def test_changed_transcript_invalidates_prior_review(self):
        self.source()
        draft = self.draft()
        self.prepare(draft)
        self.confirm(draft)
        draft.chosen(draft.rows[0])['text'] += '修改'
        self.assertEqual(self.builder(draft).publish()['held'], 1)
        self.assertFalse(load_profiles(self.data))

    def test_changed_wav_rejected_without_partial_profile(self):
        self.source()
        draft = self.draft()
        self.prepare(draft)
        self.confirm(draft)
        wav(draft.reference(draft.chosen(draft.rows[0])), sample=3000)
        self.assertEqual(self.builder(draft).publish()['failed'], 1)
        self.assertFalse(load_profiles(self.data))
        self.assertFalse(list((self.data/'.voice-imports').iterdir()))

    def test_existing_role_and_alias_are_skipped_and_never_overwritten(self):
        self.source()
        first = self.draft()
        self.prepare(first)
        first.rows[0]['aliases'] = ['旧别名']
        self.confirm(first)
        self.builder(first).publish()
        original = load_profiles(self.data)[0]
        reference = self.data/'profiles'/original.reference_audio
        checksum = sha256(reference)
        self.source('旧别名')
        second = self.draft()
        self.prepare(second)
        self.assertTrue(all(r['state'] == 'skipped' for r in second.rows))
        self.assertEqual(sha256(reference), checksum)
        self.assertEqual(len(load_profiles(self.data)), 1)

    def test_draft_round_trip_and_source_moved_after_preparation(self):
        source = self.source()
        draft = self.draft()
        self.prepare(draft)
        self.confirm(draft)
        draft.save()
        source.unlink()
        loaded = VoiceLibraryDraft.load(draft.directory)
        self.assertEqual(self.builder(loaded).publish()['imported'], 1)

    def test_draft_reference_cannot_escape_its_directory(self):
        self.source()
        draft = self.draft()
        self.prepare(draft)
        draft.chosen(draft.rows[0])['reference'] = '../../outside.wav'
        draft.save()
        with self.assertRaisesRegex(ValueError, '路径无效'):
            VoiceLibraryDraft.load(draft.directory)

    def test_malformed_draft_rejected_before_rendering(self):
        self.source()
        draft = self.draft()
        self.prepare(draft)
        original = json.loads((draft.directory/'draft.json').read_text(encoding='utf-8'))
        mutations = [lambda data: data.update(rows=[None]),
                     lambda data: data['rows'][0].update(aliases='alias'),
                     lambda data: data['rows'][0].update(selected=99),
                     lambda data: data['rows'][0]['candidates'][0].update(seconds='8 seconds'),
                     lambda data: data['rows'][0]['candidates'][0].update(quality={'speech_ratio': 'high'}),
                     lambda data: data['rows'][0]['candidates'][0].update(sha256='broken')]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                data = json.loads(json.dumps(original))
                mutate(data)
                (draft.directory/'draft.json').write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, '草稿'):
                    VoiceLibraryDraft.load(draft.directory)

    def test_transcription_timeout_stops_actual_worker(self):
        config = self.root/'runtime.json'
        config.write_text('{}', encoding='utf-8')
        transcriber = FasterWhisperTranscriber(self.root/'asr', config_path=config)
        real_popen = subprocess.Popen
        processes = []
        def stalled_worker(*args, **kwargs):
            process = real_popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            processes.append(process)
            return process
        try:
            with patch('voice_library.subprocess.Popen', side_effect=stalled_worker), \
                 patch('voice_library.time.monotonic', side_effect=[0, 181]):
                with self.assertRaises(TimeoutError):
                    transcriber.transcribe(self.source())
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll())
            self.assertIsNone(transcriber.process)
        finally:
            transcriber.close()
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(3)

    def test_partial_cancel_keeps_ready_reference_and_continue_retries_transcription(self):
        self.source()
        draft = self.draft()
        stop = threading.Event()
        def cancelled(*args, **kwargs):
            stop.set()
            return '尚未确认'
        with patch.object(self.transcriber, 'transcribe', side_effect=cancelled), \
             patch.object(VoiceImportTask, 'convert', side_effect=lambda source, target, *_: shutil.copyfile(source, target)):
            with self.assertRaises(ImportCancelled):
                self.builder(draft, stop=stop).prepare()
        loaded = VoiceLibraryDraft.load(draft.directory)
        self.assertEqual(loaded.rows[0]['state'], 'ready')
        self.assertEqual(loaded.chosen(loaded.rows[0])['text'], '')
        with patch.object(VoiceImportTask, 'convert', side_effect=AssertionError('Reference should be reused')):
            self.builder(loaded).prepare()
        self.assertTrue(loaded.chosen(loaded.rows[0])['text'])

    def test_asr_error_leaves_editable_draft_for_manual_text(self):
        self.source()
        draft = self.draft()
        with patch.object(self.transcriber, 'transcribe', side_effect=RuntimeError('转写组件不可用')):
            self.prepare(draft)
        row = draft.rows[0]
        candidate = draft.chosen(row)
        self.assertEqual(row['state'], 'ready')
        self.assertIn('不可用', candidate['asr_error'])
        candidate['text'] = '我手动核对的台词。'
        self.confirm(draft)
        self.assertEqual(self.builder(draft).publish()['imported'], 1)

    def test_silent_reference_rejected(self):
        path = self.source()
        wav(path, sample=0)
        with self.assertRaisesRegex(ValueError, '没有声音'):
            inspect_reference(path)


class VoiceLibraryUiTests(unittest.TestCase):
    setUp = VoiceLibraryTests.setUp
    tearDown = VoiceLibraryTests.tearDown
    source = VoiceLibraryTests.source
    draft = VoiceLibraryTests.draft
    builder = VoiceLibraryTests.builder
    prepare = VoiceLibraryTests.prepare
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def make_dialog(self):
        from voice_library_ui import VoiceLibraryDialog
        self.source(filename='a.wav')
        self.source(filename='b.wav', seconds=10)
        draft = self.draft()
        self.prepare(draft)
        dialog = VoiceLibraryDialog({}, self.root)
        dialog.draft = draft
        dialog.render()
        dialog.table.setCurrentCell(0, 1)
        dialog.load_editor(0)
        return dialog

    def test_ui_edit_and_candidate_change_require_fresh_confirmation(self):
        dialog = self.make_dialog()
        try:
            dialog.confirmed.setChecked(True)
            self.assertTrue(dialog.editor_row['reviewed'])
            dialog.text.setPlainText('修改后的文字。')
            dialog.save_editor()
            self.assertFalse(dialog.editor_row['reviewed'])
            dialog.confirmed.setChecked(True)
            dialog.candidates.setCurrentIndex(1)
            self.assertFalse(dialog.confirmed.isChecked())
            self.assertEqual(dialog.editor_row['selected'], 1)
            self.assertEqual(dialog.text.toPlainText(), '')
        finally:
            dialog.close()

    def test_ui_empty_source_does_not_scan_current_directory(self):
        from voice_library_ui import VoiceLibraryDialog
        dialog = VoiceLibraryDialog({}, self.root)
        try:
            dialog.start_task('scan')
            self.assertIsNone(dialog.worker)
            self.assertIn('选择', dialog.status.text())
        finally:
            dialog.close()

    def test_ui_close_cancels_work_and_preserves_draft(self):
        dialog = self.make_dialog()
        entered = threading.Event()
        def preparing(builder):
            entered.set()
            builder.stop.wait(3)
            check_cancel(builder.stop)
        dialog.show()
        try:
            with patch.object(BatchVoiceProfileBuilder, 'prepare', preparing):
                dialog.start_task('prepare')
                self.assertTrue(entered.wait(2))
                self.assertTrue(dialog.busy())
                dialog.close()
                deadline = time.monotonic()+4
                while (dialog.busy() or dialog.isVisible()) and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.app.processEvents()
                self.assertFalse(dialog.busy())
                self.assertFalse(dialog.isVisible())
                self.assertTrue(dialog.worker.stop.is_set())
                loaded = VoiceLibraryDraft.load(dialog.draft.directory)
                self.assertEqual(loaded.rows[0]['state'], 'ready')
                self.assertFalse(load_profiles(self.data))
        finally:
            dialog.cancel()
            if dialog.worker:
                dialog.worker.wait(5000)
            self.app.processEvents()
            dialog.close()

    def test_ui_publishes_only_after_checkbox_and_reopens_draft(self):
        from voice_library_ui import VoiceLibraryDialog
        dialog = self.make_dialog()
        try:
            dialog.confirmed.setChecked(True)
            dialog.start_task('publish')
            deadline = time.monotonic()+4
            while dialog.busy() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.app.processEvents()
            self.assertFalse(dialog.busy())
            self.assertEqual(dialog.imported_count, 1)
            self.assertEqual(len(load_profiles(self.data)), 1)
        finally:
            dialog.close()
        reopened = VoiceLibraryDialog({}, self.root)
        try:
            self.assertEqual(reopened.draft.rows[0]['state'], 'imported')
            self.assertFalse(reopened.editor.isEnabled())
        finally:
            reopened.close()


if __name__ == '__main__':
    unittest.main()
