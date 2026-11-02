import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtWidgets
from dialogue_generation import GenerationProject, BatchGenerator
from dialogue_dubbing import VoiceRuntime
from generator_voice_review import describe_voice, uncertain_rows, apply_voice_choices, review_dialog
from test_dialogue_generation import Runtime, Client
from wuwa_dubber.models import VoiceProfile
from wuwa_dubber.speaker_matcher import SpeakerMatcher
from system_voices import SystemVoiceRouter


class VoiceReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = GenerationProject.create(self.root / 'projects', mode='character')
        self.client = Client()
        self.runtime = Runtime(self.root, self.client)
        self.runtime.system_mode = False
        self.runtime.config.narrator_profile = '通用旁白'
        self.runtime.profiles = {p.speaker: p for p in [VoiceProfile('瓦亚波洛尼亚'),
            VoiceProfile('明音凛', aliases=['明音']), VoiceProfile('通用旁白', aliases=['旁白'])]}
        self.refresh()
        self.runtime.select_profile = VoiceRuntime.select_profile.__get__(self.runtime)

    def refresh(self):
        self.runtime.matcher = SpeakerMatcher(list(self.runtime.profiles.values()))

    def tearDown(self):
        self.temp.cleanup()

    def row(self, speaker, **kwargs):
        return self.project.add_dialogue('测试台词。', speaker, **kwargs)

    def test_exact_and_registered_alias_are_distinct_and_automatic(self):
        for name, label in [('明音凛', '角色名命中'), ('明音', '别名命中')]:
            decision = describe_voice(self.runtime, self.row(name))
            self.assertEqual(decision.label, label)
            self.assertEqual(decision.profile.speaker, '明音凛')
            self.assertFalse(decision.needs_review)

    def test_fuzzy_name_and_tied_candidates_require_explicit_choice(self):
        row = self.row('瓦亚波洛尼雅')
        self.assertTrue(describe_voice(self.runtime, row).needs_review)
        self.runtime.profiles['瓦亚波洛尼娅'] = VoiceProfile('瓦亚波洛尼娅')
        self.refresh()
        decision = describe_voice(self.runtime, row)
        self.assertEqual(decision.label, '模糊候选')
        self.assertEqual(len(decision.candidates), 2)
        row.edit(voice='character:瓦亚波洛尼亚')
        self.assertFalse(describe_voice(self.runtime, row).needs_review)

    def test_conflicting_exact_aliases_do_not_silently_choose_first(self):
        self.runtime.profiles['另一个角色'] = VoiceProfile('另一个角色', aliases=['明音'])
        self.refresh()
        decision = describe_voice(self.runtime, self.row('明音'))
        self.assertEqual(decision.label, '别名冲突')
        self.assertTrue(decision.needs_review)

    def test_named_fallback_requires_review_but_narration_does_not(self):
        self.assertTrue(describe_voice(self.runtime, self.row('陌生人')).needs_review)
        for name in ('', '旁白', '通用旁白'):
            self.assertFalse(describe_voice(self.runtime, self.row(name)).needs_review)

    def test_uncertain_ocr_identity_requires_review_even_if_text_passed(self):
        row = self.row('明音')
        row.confirmed = False
        row.edited = False
        row.evidence = dict(status='verified', speaker_status='candidate', body_confidence=.99)
        self.assertFalse(row.review_reason())
        self.assertEqual(describe_voice(self.runtime, row).label, '角色待核对')
        row.edit(voice='character:明音凛')
        self.assertFalse(describe_voice(self.runtime, row).needs_review)

    def test_batch_holds_fuzzy_voice_without_loading_model_but_generates_exact(self):
        fuzzy = self.row('瓦亚波洛尼雅')
        runner = BatchGenerator(self.project, {}, runtime_factory=lambda _: self.runtime)
        result = runner.run()
        self.assertEqual(result['voices_held'], 1)
        self.assertFalse(self.runtime.prepared)
        self.assertFalse(self.client.calls)
        exact = self.row('明音')
        result = BatchGenerator(self.project, {}, runtime_factory=lambda _: self.runtime).run()
        self.assertEqual(result['completed'], 1)
        self.assertEqual(result['held'], 1)
        self.assertEqual(exact.audio['voice'], '明音凛')
        self.assertEqual(fuzzy.status, 'pending')

    def test_explicit_choice_persists_without_confirming_text_or_deleting_wav(self):
        row = self.row('瓦亚波洛尼雅')
        row.confirmed = False
        old = self.project.directory / 'original.wav'
        old.write_bytes(b'original audio')
        row.audio = {'file': old.name}
        changed = apply_voice_choices(self.project, {row.id: 'character:瓦亚波洛尼亚'})
        self.assertEqual(changed, 1)
        loaded = GenerationProject.load(self.project.directory).rows[0]
        self.assertEqual(loaded.voice, 'character:瓦亚波洛尼亚')
        self.assertFalse(loaded.confirmed)
        self.assertEqual(old.read_bytes(), b'original audio')
        self.assertEqual(loaded.audio, {})

    def test_system_binding_and_alias_use_same_review_rules(self):
        self.runtime.system_mode = True
        self.runtime.system_router = SystemVoiceRouter({'system_character_voices': {'明音凛': 'young_female'}},
                                                       self.runtime.profiles.values())
        self.assertEqual(describe_voice(self.runtime, self.row('明音')).label, '别名命中')
        self.assertTrue(describe_voice(self.runtime, self.row('陌生人')).needs_review)
        self.assertFalse(describe_voice(self.runtime, self.row('')).needs_review)
        self.assertFalse(describe_voice(self.runtime, self.row('陌生人', voice='system:default')).needs_review)

    def test_confirming_same_voice_reuses_cache_and_keeps_previous_export(self):
        row = self.row('明音')
        BatchGenerator(self.project, {}, runtime_factory=lambda _: self.runtime).run()
        original = self.project.audio_path(row)
        self.assertIsNotNone(original)
        apply_voice_choices(self.project, {row.id: 'character:明音凛'})
        result = BatchGenerator(self.project, {}, runtime_factory=lambda _: self.runtime).run()
        self.assertEqual(result['cached'], 1)
        self.assertEqual(len(self.client.calls), 1)
        self.assertTrue(original.is_file())

    def test_dialog_groups_same_problem_and_leaves_unselected_rows_alone(self):
        rows = [self.row('瓦亚波洛尼雅'), self.row('瓦亚波洛尼雅'), self.row('陌生人')]
        before = (self.project.directory / 'project.json').read_bytes()
        dialog = review_dialog(None, uncertain_rows(self.runtime, rows), [('明音凛', 'character:明音凛')])
        self.assertEqual(dialog.table.rowCount(), 2)
        combo = dialog.table.cellWidget(0, 3)
        self.assertEqual(combo.currentData(), '')
        combo.setCurrentIndex(1)
        dialog.accept()
        self.assertEqual(set(dialog.choices), {rows[0].id, rows[1].id})
        self.assertEqual((self.project.directory / 'project.json').read_bytes(), before)
        apply_voice_choices(self.project, dialog.choices)
        self.assertEqual(rows[2].voice, 'auto')
        dialog.close()

    def test_generator_shows_source_and_cancel_review_prevents_generation(self):
        from dialogue_generator_ui import GeneratorWindow
        self.row('瓦亚波洛尼雅')
        self.project.save()
        with patch('dialogue_generator_ui.VoiceRuntime', return_value=self.runtime):
            window = GeneratorWindow({'mode': 'character'}, root=self.root, project_path=self.project.directory)
            try:
                self.assertEqual(window.table.item(0, 7).text(), '模糊候选')
                def reject_review():
                    self.assertIsNotNone(window.review_dialog)
                    window.review_dialog.reject()
                QtCore.QTimer.singleShot(0, reject_review)
                window.start_generation()
                self.assertIsNone(window.worker)
                self.assertFalse(self.client.calls)
                self.assertEqual(window.project.rows[0].voice, 'auto')
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
