import array
import json
import math
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
import wave

from dialogue_dubbing import voice_paths
from dialogue_generation import select_voice, file_digest
from wuwa_catalog import Catalog
from wuwa_npc_voices import create_variant, load_variant, parameters, choices

voice_paths({})
from wuwa_dubber.models import VoiceProfile, Utterance
from wuwa_dubber.config import AppConfig
from wuwa_dubber.speech_cache import SpeechCache


class Variants(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / 'original.wav'
        with wave.open(str(source), 'wb') as stream:
            stream.setparams((1, 2, 32000, 0, 'NONE', 'not compressed'))
            samples = array.array('h', (int(6000*math.sin(i*2*math.pi*220/32000)) for i in range(128000)))
            stream.writeframes(samples.tobytes())
        self.base = VoiceProfile('Reference', reference_audio=str(source), prompt_text='Reference transcript.')
        self.ffmpeg = shutil.which('ffmpeg')

    def tearDown(self):
        self.temp.cleanup()

    def create(self, speaker_id='100', speaker='NPC', **kwargs):
        if not self.ffmpeg:
            self.skipTest('Local FFmpeg is not installed')
        return create_variant(self.root, speaker_id, speaker, self.base, ffmpeg=self.ffmpeg, **kwargs)

    def test_reuses_identity_without_touching_original(self):
        before = file_digest(Path(self.base.reference_audio))
        voice = self.create()
        profile, meta = load_variant(self.root, voice, speaker_id='100', speaker='NPC')
        modified = Path(profile.reference_audio).stat().st_mtime_ns
        self.assertEqual(self.create(), voice)
        self.assertEqual(Path(profile.reference_audio).stat().st_mtime_ns, modified)
        self.assertEqual(file_digest(Path(self.base.reference_audio)), before)
        self.assertFalse(meta['transcript_reviewed'])
        self.assertAlmostEqual(meta['duration_seconds'], 4, delta=.1)
        self.assertEqual(profile.speed, 1.03)
        self.assertEqual(choices(self.root, speaker_id='200'), [])

    def test_parameters_and_npc_identity_change_cache_key(self):
        voices = [self.create(), self.create(speed=.97), self.create(pitch=.75), self.create('200')]
        self.assertEqual(len(set(voices)), 4)
        cache = SpeechCache(AppConfig(), self.root / 'cache', SimpleNamespace(cache_identity=lambda: {'model': 'test'}))
        paths = [cache.path_for(Utterance('Text'), load_variant(self.root, voice)[0]) for voice in voices]
        # Same-name NPCs may share identical audio if all synthesis inputs match.
        self.assertEqual(len(set(paths[:3])), 3)

    def test_invalid_parameters(self):
        for pitch, speed in [(3, 1), (-3, 1), (float('nan'), 1), (0, float('inf')), (0, .8), (0, 1.2)]:
            with self.assertRaises(ValueError):
                parameters(pitch, speed)

    def test_cancel_does_not_publish(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(InterruptedError):
            self.create(stop=stop)
        self.assertEqual(list((self.root / 'npc-voices').iterdir()), [])

    def test_tampered_reference_is_rejected(self):
        voice = self.create()
        profile, _ = load_variant(self.root, voice)
        with Path(profile.reference_audio).open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaises(ValueError):
            load_variant(self.root, voice)

    def test_identity_mismatch_and_path_traversal(self):
        voice = self.create()
        for options in (dict(speaker_id='200'), dict(speaker='Other')):
            with self.assertRaises(ValueError):
                load_variant(self.root, voice, **options)
        for value in ('npc:../other', 'character:Reference', 'npc:' + 'z'*64):
            with self.assertRaises(ValueError):
                load_variant(self.root, value)

    def test_cannot_route_cross_game_or_without_identity(self):
        voice = self.create()
        runtime = SimpleNamespace(system_mode=False, profiles={}, options=dict(game_id='wuwa', catalog_root=self.root))
        row = SimpleNamespace(voice=voice, speaker='NPC', evidence=dict(game_id='wuwa', speaker_id='100'))
        self.assertEqual(select_voice(runtime, row)[0].speed, 1.03)
        runtime.options['game_id'] = 'nte'
        with self.assertRaises(ValueError):
            select_voice(runtime, row)
        runtime.options['game_id'] = 'wuwa'
        row.evidence['game_id'] = 'nte'
        with self.assertRaises(ValueError):
            select_voice(runtime, row)
        row.evidence = dict(game_id='wuwa')
        with self.assertRaises(ValueError):
            select_voice(runtime, row)

    def test_catalog_binding_does_not_transfer_review(self):
        voice = self.create()
        catalog = Catalog(self.root)
        try:
            catalog.set_binding('100', 'NPC', voice, False)
            self.assertFalse(catalog.binding('100')['transcript_reviewed'])
            with self.assertRaises(ValueError):
                catalog.set_binding('200', 'NPC', voice, True)
            self.assertIsNone(catalog.binding('200'))
        finally:
            catalog.close()

    def test_manifest_cannot_silently_change_pitch(self):
        voice = self.create()
        path = self.root / 'npc-voices' / voice[4:] / 'variant.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['recipe']['pitch_semitones'] = 1.5
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            load_variant(self.root, voice)


if __name__ == '__main__':
    unittest.main()
