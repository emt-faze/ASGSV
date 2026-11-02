from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wuwa-dubber/src"))
from audio_edges import _decode_pcm, trim_wav_edges
from wuwa_dubber.speech_cache import SpeechCancelled


class AudioEdgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.rate = 16000

    def tearDown(self):
        self.directory.cleanup()

    def sound(self, duration, parts):
        samples = np.zeros(round(duration*self.rate))
        for start, end, amplitude in parts:
            a, b = round(start*self.rate), round(end*self.rate)
            samples[a:b] = amplitude*np.sin(2*np.pi*230*np.arange(b-a)/self.rate)
        return samples

    def write(self, samples, name="original.wav"):
        samples = np.asarray(samples)
        path = self.root/name
        with wave.open(str(path), "wb") as out:
            out.setparams((1 if samples.ndim == 1 else samples.shape[1], 2, self.rate, 0, "NONE", "not compressed"))
            out.writeframes((samples*32767).astype('<i2').tobytes())
        return path

    def process(self, samples, windows):
        source = self.write(samples)
        target = self.root/"processed.wav"
        with patch("audio_edges._speech_windows", return_value=windows):
            result = trim_wav_edges(source, target)
        with wave.open(str(source), "rb") as original, wave.open(str(target), "rb") as output:
            original.setpos(result["start_frame"])
            self.assertEqual(original.readframes(result["end_frame"]-result["start_frame"]),
                             output.readframes(output.getnframes()))
            self.assertEqual(original.getframerate(), output.getframerate())
            self.assertEqual(original.getnchannels(), output.getnchannels())
        return result

    def test_outer_padding_removed_but_long_internal_pause_and_all_samples_kept(self):
        result = self.process(self.sound(4, [(1, 1.4, .2), (2.3, 2.7, .2)]), [(1, 1.4), (2.3, 2.7)])
        self.assertGreater(result['removed_head_seconds'], .5)
        self.assertGreater(result['removed_tail_seconds'], .5)
        self.assertLessEqual(result['start_frame'], self.rate)
        self.assertGreaterEqual(result['end_frame'], 2.7*self.rate)

    def test_low_onset_and_release_are_preserved_beside_detected_speech(self):
        samples = self.sound(2, [(.5, .64, .003), (.64, 1, .2), (1, 1.18, .002)])
        result = self.process(samples, [(.64, 1.1)])
        self.assertLess(result['start_frame'], .5*self.rate)
        self.assertGreater(result['end_frame'], 1.18*self.rate)

    def test_sustained_humming_outside_vad_still_protects_beginning(self):
        result = self.process(self.sound(2, [(.2, .45, .05), (.9, 1.2, .2)]), [(.9, 1.2)])
        self.assertLess(result['start_frame'], .2*self.rate)

    def test_brief_isolated_click_does_not_anchor_long_silence(self):
        result = self.process(self.sound(2, [(.01, .03, .2), (.9, 1.3, .2)]), [(.9, 1.3)])
        self.assertGreater(result['removed_head_seconds'], .5)
        self.assertLess(result['start_frame'], .9*self.rate)

    def test_entirely_quiet_whisper_is_not_aggressively_cut(self):
        result = self.process(self.sound(2, [(.5, 1.4, .004)]), [])
        self.assertEqual((result['start_frame'], result['end_frame']), (0, 32000))

    def test_silence_and_ambiguous_constant_audio_are_kept(self):
        for samples in (np.zeros(32000), np.full(32000, .01)):
            with self.subTest(level=float(samples.max())):
                result = self.process(samples, [])
                self.assertEqual((result['start_frame'], result['end_frame']), (0, 32000))

    def test_very_short_clip_is_never_trimmed(self):
        result = self.process(self.sound(.3, [(.1, .2, .2)]), [(.1, .2)])
        self.assertEqual((result['start_frame'], result['end_frame']), (0, 4800))

    def test_two_character_vocalization_keeps_whole_onset_even_when_vad_misses_it(self):
        source = self.write(self.sound(2, [(.02, .25, .002), (.7, 1.2, .2)]))
        with patch('audio_edges._speech_windows', return_value=[(.7, 1.2)]):
            result = trim_wav_edges(source, self.root/'processed.wav', text='嗯哼？')
        self.assertEqual(result['start_frame'], 0)
        self.assertTrue(result['onset_protected'])
        self.assertGreater(result['removed_tail_seconds'], 0)

    def test_already_compact_clip_is_kept(self):
        result = self.process(self.sound(1, [(.02, .96, .2)]), [(.1, .9)])
        self.assertEqual((result['start_frame'], result['end_frame']), (0, 16000))

    def test_stereo_opposite_phase_and_silent_channel_keep_audio(self):
        mono = self.sound(2, [(.6, 1.3, .2)])
        for other in (-mono, np.zeros(len(mono))):
            result = self.process(np.column_stack((mono, other)), [(.6, 1.3)])
            self.assertLessEqual(result['start_frame'], .6*self.rate)
            self.assertGreaterEqual(result['end_frame'], 1.3*self.rate)

    def test_missing_detector_falls_back_to_conservative_silence_removal(self):
        source = self.write(self.sound(2, [(.7, 1.3, .03)]))
        target = self.root/'processed.wav'
        with patch('audio_edges._speech_windows', side_effect=RuntimeError('missing model')):
            result = trim_wav_edges(source, target)
        self.assertEqual(result['method'], 'near_silence_only')
        self.assertLessEqual(result['start_frame'], .7*self.rate)
        self.assertGreaterEqual(result['end_frame'], 1.3*self.rate)

    def test_in_place_processing_is_atomic(self):
        source = self.write(self.sound(2, [(.7, 1.3, .2)]))
        original = source.read_bytes()
        with patch('audio_edges._speech_windows', return_value=[(.7, 1.3)]):
            result = trim_wav_edges(source, source)
        self.assertLess(len(source.read_bytes()), len(original))
        self.assertGreater(result['removed_head_seconds'], 0)
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_cancel_after_detection_preserves_both_source_and_existing_destination(self):
        source = self.write(self.sound(2, [(.7, 1.3, .2)]))
        target = self.root/'existing.wav'
        target.write_bytes(b'previous playable output')
        original = source.read_bytes()
        stop = threading.Event()
        def detect(*args):
            stop.set()
            return [(.7, 1.3)]
        with patch('audio_edges._speech_windows', side_effect=detect), self.assertRaises(SpeechCancelled):
            trim_wav_edges(source, target, stop)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(target.read_bytes(), b'previous playable output')
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_failed_replace_preserves_output_and_cleans_staging(self):
        source = self.write(self.sound(2, [(.7, 1.3, .2)]))
        target = self.root/'existing.wav'
        target.write_bytes(b'previous')
        with patch('audio_edges._speech_windows', return_value=[]), \
             patch.object(Path, 'replace', side_effect=PermissionError), self.assertRaises(PermissionError):
            trim_wav_edges(source, target)
        self.assertEqual(target.read_bytes(), b'previous')
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_pcm_decoding_handles_unsigned_8_and_signed_16_24_32_bit(self):
        for width in (1, 2, 3, 4):
            limit = 2**(8*width-1)
            values = (-limit, 0, limit-1)
            raw = bytes((0, 128, 255)) if width == 1 else b''.join(v.to_bytes(width, 'little', signed=True) for v in values)
            decoded = _decode_pcm(raw, width, 1)[:, 0]
            np.testing.assert_allclose(decoded, np.array(values)/limit, atol=1e-7)


if __name__ == '__main__':
    unittest.main()
