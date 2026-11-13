import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

import wuwa_npc_audio as audio
import wuwa_npc_extract as extraction


class NpcExtractionTests(unittest.TestCase):
    def test_candidate_selection_is_bounded_deduplicated_and_explicit(self):
        rows, texts = [], {}
        for sid in [*audio.ROSTER, 'unapproved-character']:
            for n in range(6):
                key = sid + '-' + str(n)
                row = dict(id=key, speaker_id=sid, status='local_resource_found',
                           resources=[dict(archive='base', virtual_path=str(n // 2))])
                rows.append(row)
                texts[key] = 'x' * 40
        chosen = extraction.choose_candidates(rows, texts)
        self.assertEqual(len(chosen), 18)
        self.assertEqual({r['speaker_id'] for r in chosen}, set(audio.ROSTER))

    def test_blocked_and_missing_candidates_not_exported(self):
        rows = [dict(id='x', speaker_id='651', status='blocked')]
        self.assertEqual(extraction.choose_candidates(rows, {'x': 'text'}), [])

    def test_key_matched_encrypted_audio_can_be_inspected_not_auto_approved(self):
        archive = dict(path='base', language='zh', priority=4, order=0, overlay_verified=True)
        diagnostic = dict(path='base', status='index_readable', matches=[dict(
            filename='vo_x', virtual_path='zh_vo_x.wem', encrypted=True, key_matched=True)])
        result = audio.choose_resource(dict(filename='vo_x', language_parameters={}), [archive], [diagnostic])
        self.assertEqual(result['status'], 'local_resource_found')
        self.assertIn('verification_required', result['reason'])

    def test_html_escapes_text_and_keeps_transcripts_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = dict(auditions=[dict(id='x', speaker_id='651', status='decoded_candidate',
                wav_path=str(root / 'x.wav'), seconds=8)])
            extraction.review_page(root, report, {'x': '<script>spoiler</script>'})
            page = (root / 'review.html').read_text(encoding='utf-8')
            self.assertIn('&lt;script&gt;', page)
            self.assertNotIn('<script>', page)
            self.assertNotIn('<details open', page)
            self.assertIn('preload="none"', page)
            self.assertNotIn('autoplay', page)

    def test_fallback_decode_cache_and_no_loops(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            decoder = root / 'tools/vgmstream-r2168/vgmstream-cli.exe'
            decoder.parent.mkdir(parents=True)
            decoder.write_bytes(b'fixture decoder')
            raw = root / 'source.wem'
            raw.write_bytes(b'fixture original')
            calls = []
            def invoke(command, **kwargs):
                calls.append(command)
                if command[0] == str(decoder):
                    self.assertIn('-i', command)
                    self.assertNotIn('-l', command)
                    with wave.open(command[command.index('-o') + 1], 'wb') as w:
                        w.setparams((1,2,16000,0,'NONE','not compressed'))
                        w.writeframes(b'\x00\x01' * 16000)
                return subprocess.CompletedProcess(command, 0, 'decoded fixture', '')
            with patch.object(audio, 'decode_audio', side_effect=subprocess.CalledProcessError(1, ['ffmpeg'])), \
                    patch.object(extraction.subprocess, 'run', side_effect=invoke):
                args = (raw, root / 'cache', root / 'tools', 'ffmpeg', 'ffprobe')
                first = extraction.decode_candidate(*args)
                second = extraction.decode_candidate(*args)
                self.assertEqual(first['seconds'], 1)
                self.assertFalse(first['reused'])
                self.assertTrue(second['reused'])
                self.assertEqual(len(calls), 2)
                Path(first['path']).write_bytes(b'corrupted')
                with self.assertRaises(ValueError):
                    extraction.decode_candidate(*args)

    @unittest.skipUnless(Path(r'F:\Dubbing\DG\tools\npc-audio-probe\bin\Release\net10.0\NpcProbe.dll').exists(), 'Optional .NET probe unavailable')
    def test_exporter_rejects_cross_game_request(self):
        with tempfile.TemporaryDirectory() as temp:
            request = Path(temp) / 'request.json'
            request.write_text(json.dumps(dict(game_id='nte')), encoding='utf-8')
            p = subprocess.run([r'F:\Dubbing\DG\tools\dotnet10\dotnet.exe',
                r'F:\Dubbing\DG\tools\npc-audio-probe\bin\Release\net10.0\NpcProbe.dll',
                '--npc-export', str(request)], capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(p.returncode, 2)
            self.assertIn('Only WuWa', json.loads(p.stdout)['error'])

    @unittest.skipUnless(extraction.KEY_FILE.exists(), 'Pinned public key file unavailable')
    def test_exporter_rejects_archive_outside_game_root(self):
        with tempfile.TemporaryDirectory() as temp:
            request = Path(temp) / 'request.json'
            request.write_text(json.dumps(dict(game_id='wuwa', key_file=str(extraction.KEY_FILE),
                entries=[dict(archive=str(Path(temp)/'outside.pak'), virtual_path='unused', filename='unused')])), encoding='utf-8')
            p = subprocess.run([r'F:\Dubbing\DG\tools\dotnet10\dotnet.exe',
                r'F:\Dubbing\DG\tools\npc-audio-probe\bin\Release\net10.0\NpcProbe.dll',
                '--npc-export', str(request)], capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(p.returncode, 2)
            self.assertIn('permitted root', json.loads(p.stdout)['error'])


if __name__ == '__main__':
    unittest.main()
