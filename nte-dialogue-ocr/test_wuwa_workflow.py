import tempfile
import unittest
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

from wuwa_catalog import Catalog
from wuwa_task_evidence import audio_decision, fb_string, state_references
from wuwa_workflow import scope_voice_label, voice_label, project_quality, quality_label


class QualityTests(unittest.TestCase):
    def test_voice_hash_is_not_a_display_name(self):
        label = voice_label(dict(voice='npc:' + 'a' * 64, transcript_reviewed=True))
        self.assertNotIn('aaaa', label)
        self.assertEqual(voice_label(dict(voice='character:Name', transcript_reviewed=True)), 'Name')

    def test_report_requires_current_audio_text_game_and_unique_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wav = root / 'audio.wav'
            wav.write_bytes(b'test audio')
            row = SimpleNamespace(id='one', text='original')
            project = SimpleNamespace(directory=root, game_id='wuwa', rows=[row], audio_path=lambda _: wav)
            path = root / 'quality-report.json'
            item = dict(id='one', expected='original', sha256=hashlib.sha256(wav.read_bytes()).hexdigest(), content_check='pass')
            report = dict(game_id='wuwa', items=[item])
            self.assertEqual(project_quality(project), {'one': 'unchecked'})
            path.write_text(json.dumps(report), encoding='utf-8')
            self.assertEqual(project_quality(project), {'one': 'pass'})
            wav.write_bytes(b'changed')
            self.assertEqual(project_quality(project), {'one': 'unchecked'})
            wav.write_bytes(b'test audio')
            row.text = 'changed'
            self.assertEqual(project_quality(project), {'one': 'unchecked'})
            row.text = 'original'
            report['game_id'] = 'nte'
            path.write_text(json.dumps(report), encoding='utf-8')
            self.assertEqual(project_quality(project), {'one': 'unchecked'})
            report.update(game_id='wuwa', items=[item, item])
            path.write_text(json.dumps(report), encoding='utf-8')
            self.assertEqual(project_quality(project), {'one': 'unchecked'})
            path.write_text('invalid', encoding='utf-8')
            self.assertEqual(project_quality(project), {'one': 'unchecked'})

    def test_machine_pass_never_claims_naturalness_accepted(self):
        self.assertIn('\u5f85\u8bd5\u542c', quality_label({'one': 'pass'}))


class EvidenceTests(unittest.TestCase):
    def test_absence_without_runtime_proof_is_unknown(self):
        self.assertEqual(audio_decision({'Type': 'Talk'}, [], False), 'unknown')

    def test_runtime_gate_is_not_file_absence_heuristic(self):
        self.assertEqual(audio_decision({'Type': 'Talk'}, [], True), 'unvoiced')
        self.assertEqual(audio_decision({'PlayVoice': True}, [], True), 'voiced')
        self.assertEqual(audio_decision({'PlayVoice': 'false'}, [], True), 'unknown')

    def test_alternate_audio_paths_remain_held(self):
        for key in ('TalkAkEvent', 'TalkEndAkEvent', 'UniversalTone'):
            self.assertEqual(audio_decision({key: {'Id': 1}}, [], True), 'unknown')
        self.assertEqual(audio_decision({}, [{'Name': 'PostAkEvent', 'Params': {}}], True), 'unknown')

    def test_options_are_not_proven_dialogue(self):
        self.assertEqual(audio_decision({'Type': 'Option'}, [], True), 'unknown')

    def test_explicit_flow_refs_only(self):
        self.assertEqual(state_references([{'FlowListName': 'a', 'FlowId': 2, 'StateId': 3},
                                           {'FlowListName': 'a', 'FlowId': 2}]), {'a_2_3'})

    def test_corrupt_buffer_rejected(self):
        with self.assertRaises(ValueError):
            fb_string(b'abc', 16)

    def test_voice_badges_do_not_claim_unknown_is_unvoiced(self):
        row = dict(total=2, ready=1, unknown=1, voiced=0)
        self.assertEqual(scope_voice_label(row), '\u5f85\u6838\u5b9e')
        self.assertEqual(scope_voice_label(dict(total=2, ready=0, unknown=0, voiced=2)), '\u5df2\u6709\u539f\u58f0')

    def test_progress_filter_and_pagination_count_agree(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog = Catalog(Path(temp))
            try:
                for i, status in enumerate(('pending', 'completed', 'unknown')):
                    identity = str(i)
                    catalog.db.execute('INSERT INTO scopes VALUES(?,?,?,?)', ('quest:'+identity, identity, '', 'quest'))
                    catalog.db.execute('INSERT INTO lines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (identity,'wuwa','s','f','state','b','line','text','speaker','name','body','',
                         'unknown','basis','fp','',''))
                    catalog.db.execute('INSERT INTO scope_lines VALUES(?,?)', ('quest:'+identity, identity))
                    catalog.db.execute('INSERT INTO quest_progress VALUES(?,?,?,?,?,?)',
                        (catalog.progress.account,identity,status,'test','{}','2026-11-04'))
                catalog.db.commit()
                for status in ('pending', 'completed', 'unknown'):
                    rows = catalog.scopes(progress_status=status)
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(catalog.scope_count(progress_status=status), 1)
                    self.assertEqual(rows[0]['progress_status'], status)
                self.assertEqual(catalog.scope_count(hide_completed=True, progress_status='completed'), 0)
            finally:
                catalog.close()


if __name__ == '__main__':
    unittest.main()
