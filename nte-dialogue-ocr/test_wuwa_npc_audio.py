import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

import wuwa_npc_audio as npc
from wuwa_resources import overlay_order, mount_entries


class NpcAudioTests(unittest.TestCase):
    def reference(self):
        return dict(filename='vo_example', language_parameters={}, local_identity_verified=False)

    def archive(self, path='base.pak', priority=4, order=0, language='zh'):
        return dict(path=path, priority=priority, order=order, language=language, overlay_verified=True)

    def diagnostic(self, path='base.pak', status='index_readable', virtual='Voice/vo_example.wem'):
        return dict(path=path, status=status, stored_index_sha256='abc', matches=[dict(
            filename='vo_example', virtual_path=virtual, encrypted=False, bytes=100)])

    def test_hotfix_wins_using_manifest_priority_not_name(self):
        archives = [self.archive(), self.archive('older_named_patch.pak', 10)]
        result = npc.choose_resource(self.reference(), archives,
            [self.diagnostic(), self.diagnostic('older_named_patch.pak')])
        self.assertEqual(result['resources'][0]['archive'], 'older_named_patch.pak')
        self.assertEqual(result['status'], 'local_resource_found')
        self.assertIn('verification_required', result['reason'])

    def test_unreadable_hotfix_blocks_even_readable_base(self):
        archives = [self.archive(), self.archive('patch.pak', 10)]
        result = npc.choose_resource(self.reference(), archives,
            [self.diagnostic(), self.diagnostic('patch.pak', 'encrypted_index_missing_key')])
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['blockers'][0]['reason'], 'encrypted_index_missing_key')

    def test_same_basename_different_virtual_paths_not_merged(self):
        archives = [self.archive(), self.archive('patch.pak', 10)]
        result = npc.choose_resource(self.reference(), archives,
            [self.diagnostic(), self.diagnostic('patch.pak', virtual='Other/vo_example.wem')])
        self.assertEqual(result['reason'], 'ambiguous_resource_paths')

    def test_missing_file_is_not_unvoiced(self):
        diagnostic = self.diagnostic()
        diagnostic['matches'] = []
        result = npc.choose_resource(self.reference(), [self.archive()], [diagnostic])
        self.assertEqual(result['status'], 'reference_only')
        self.assertNotIn('unvoiced', str(result))

    def test_equal_overlay_priority_is_ambiguous(self):
        result = npc.choose_resource(self.reference(), [self.archive(), self.archive('other.pak')],
            [self.diagnostic(), self.diagnostic('other.pak')])
        self.assertEqual(result['reason'], 'ambiguous_overlay_priority')

    def test_gender_variant_requires_evidence(self):
        ref = self.reference()
        ref['language_parameters']['CheckGenderZh'] = True
        result = npc.choose_resource(ref, [self.archive()], [self.diagnostic()])
        self.assertEqual(result['reason'], 'unresolved_audio_parameters')

    def test_different_language_not_accepted(self):
        result = npc.choose_resource(self.reference(), [self.archive(language='unknown')], [self.diagnostic()])
        self.assertEqual(result['reason'], 'chinese_overlay_missing')

    def test_encrypted_file_not_extracted(self):
        diagnostic = self.diagnostic()
        diagnostic['matches'][0]['encrypted'] = True
        result = npc.choose_resource(self.reference(), [self.archive()], [diagnostic])
        self.assertEqual(result['reason'], 'encrypted_audio_missing_parameters')

    def test_manifest_delete_and_order(self):
        entries = mount_entries('::mount\nLang_zh/Base/a,4\nLang_zh/Patch/b,10\n::del\nLang_zh/Base/a\n')
        self.assertEqual([r['path'] for r in overlay_order(entries)], ['Lang_zh/Patch/b'])

    def test_unsafe_manifest_rejected(self):
        with self.assertRaises(ValueError):
            mount_entries('::mount\n../game,10\n')

    def test_only_uniquely_reviewed_pending_non_companion_tasks(self):
        def task(qid, category='side', saved=True):
            return dict(category=category, progress_saved=saved, candidates=[dict(quest_id=qid)])
        rows = [task('1'), task('2', 'companion'), task('3'), task('4', saved=False), task('5')]
        rows[-1]['candidates'].append(dict(quest_id='6'))
        result = npc.allowed_scopes(dict(game_id='wuwa', tasks=rows), {'1':'pending','2':'pending','3':'completed','4':'pending','5':'pending'})
        self.assertEqual(result, ['quest:1'])

    def test_cross_game_report_rejected(self):
        with self.assertRaises(ValueError):
            npc.allowed_scopes(dict(game_id='nte', tasks=[]), {})

    def test_auditions_require_local_proof_and_deduplicate(self):
        candidates = [dict(seconds=t, raw_sha256=str(t), local_identity_verified=True) for t in (2, 4, 6, 9, 12, 16, 31)]
        candidates += [dict(seconds=8, raw_sha256='unknown', local_identity_verified=False), candidates[3]]
        self.assertEqual([r['seconds'] for r in npc.select_auditions(candidates)], [9, 12, 6])

    def test_fallback_duration_does_not_splice(self):
        candidates = [dict(seconds=t, raw_sha256=str(t), local_identity_verified=True) for t in (3, 30)]
        self.assertEqual(len(npc.select_auditions(candidates)), 2)

    def test_atomic_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'test.json'
            npc.atomic_json(path, {'status': 'blocked'})
            self.assertEqual(json.loads(path.read_text()), {'status': 'blocked'})
            self.assertEqual([p.name for p in Path(temp).iterdir()], ['test.json'])

    def test_archive_checkpoint_resumes_without_reprobing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / 'run'
            run.mkdir()
            tools = root / 'tools'
            dll = tools / 'cue4parse-probe/bin/Release/net8.0/Probe.dll'
            dll.parent.mkdir(parents=True)
            dll.write_bytes(b'test probe')
            pak = root / 'base.pak'
            pak.write_bytes(b'fixture')
            archive = self.archive(str(pak))
            diagnostic = self.diagnostic(str(pak), 'unsupported_or_corrupt_header')
            stdout = json.dumps(dict(game_id='wuwa', archives=[diagnostic]))
            result = subprocess.CompletedProcess([], 0, stdout, '')
            with patch.object(npc.subprocess, 'run', return_value=result) as mock:
                npc.run_probe([archive], [self.reference()], run, tools)
                npc.run_probe([archive], [self.reference()], run, tools)
                self.assertEqual(mock.call_count, 1)
                pak.write_bytes(b'updated fixture')
                npc.run_probe([archive], [self.reference()], run, tools)
                self.assertEqual(mock.call_count, 2)

    def test_catalog_ids_scope_and_public_identity_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_dir = root / 'sources' / npc.COMMIT
            source_dir.mkdir(parents=True)
            audio_path = source_dir / 'audio.json'
            npc.atomic_json(audio_path, [dict(Id='test_line', FileName='vo_test_line')])
            npc.atomic_json(source_dir / 'source.json', dict(commit=npc.COMMIT, resource_version='3.7.8',
                region='Global', files={'audio': {'sha256': npc.sha256(audio_path)}}))
            picture = root / 'reports/quest-panel/fixture/001.png'
            picture.parent.mkdir(parents=True)
            picture.write_bytes(b'test evidence')
            db = sqlite3.connect(root / 'dialogue.sqlite')
            try:
                db.executescript('''CREATE TABLE meta(key TEXT,value TEXT);
                    CREATE TABLE quest_progress(account TEXT,quest_id TEXT,status TEXT);
                    CREATE TABLE scope_lines(scope_id TEXT,line_id TEXT);
                    CREATE TABLE lines(id TEXT,speaker_id TEXT,speaker TEXT,source_id TEXT,plot_key TEXT,
                    game_id TEXT,voice_status TEXT,raw_text TEXT,flow_id TEXT,state_id TEXT,branch TEXT,
                    line_id TEXT,text_id TEXT);''')
                db.execute('INSERT INTO meta VALUES(?,?)', ('progress_account', 'fixture'))
                db.executemany('INSERT INTO quest_progress VALUES(?,?,?)',
                    [('fixture','1','pending'), ('fixture','2','pending'), ('fixture','3','completed')])
                for line, sid, scope in [('a', '100038', '1'), ('b', 'other', '1'),
                                         ('c', '100038', '2'), ('d', '100038', '3')]:
                    db.execute('INSERT INTO lines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (line,sid,npc.ROSTER['100038'],npc.COMMIT,'test_line','wuwa','voiced',
                         'private transcript','flow','state','branch',line,'text'))
                    db.execute('INSERT INTO scope_lines VALUES(?,?)', ('quest:'+scope,line))
                db.execute('INSERT INTO scope_lines VALUES(?,?)', ('quest:1','a'))
                db.commit()
                review = root / 'review.json'
                evidence = dict(path=str(picture.relative_to(root)), sha256=npc.sha256(picture))
                npc.atomic_json(review, dict(game_id='wuwa', tasks=[dict(category=category,
                    progress_saved=True, candidates=[dict(quest_id=qid)], evidence=evidence)
                    for qid, category in [('1','side'),('2','companion'),('3','side')]]))
                refs, transcripts, scopes, _ = npc.load_references(root, review)
                self.assertEqual([r['id'] for r in refs], ['a'])
                self.assertEqual(scopes, ['quest:1'])
                self.assertFalse(refs[0]['local_identity_verified'])
                self.assertNotIn('raw_text', refs[0])
                self.assertEqual(transcripts['a'], 'private transcript')
                db.execute('UPDATE lines SET source_id=? WHERE id=?', ('wrong-version', 'a'))
                db.commit()
                with self.assertRaisesRegex(ValueError, 'source conflict'):
                    npc.load_references(root, review)
            finally:
                db.close()

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg unavailable')
    def test_decode_cache_reuse_and_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / 'reference.wav'
            with wave.open(str(raw), 'wb') as wav:
                wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\x00\x00' * 16000)
            args = (raw, root / 'cache', shutil.which('ffmpeg'), shutil.which('ffprobe'))
            first = npc.decode_audio(*args)
            second = npc.decode_audio(*args)
            self.assertFalse(first['reused'])
            self.assertTrue(second['reused'])
            self.assertEqual(first['wav_sha256'], second['wav_sha256'])
            Path(first['path']).write_bytes(b'bad')
            with self.assertRaises(ValueError):
                npc.decode_audio(*args)


if __name__ == '__main__':
    unittest.main()
