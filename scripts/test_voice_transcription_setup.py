import json
from pathlib import Path
import sys
import tempfile
import unittest

import install_voice_transcription as installer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'nte-dialogue-ocr'))
from voice_library_asr import load_config


class VoiceTranscriptionSetupTests(unittest.TestCase):
    def test_relative_configuration_survives_moving_the_package(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base/'original'
            (root/'nte-dialogue-ocr').mkdir(parents=True)
            modules = root/'local-voice-model/asr-runtime'
            model = root/'local-voice-model/models/faster-whisper-small'
            model.mkdir(parents=True)
            for name in installer.MODEL_FILES:
                (model/name).write_text('fixture')
            self.assertTrue(installer.configure(root,modules,model))
            moved = base/'moved package'
            root.rename(moved)
            config = load_config(moved/'nte-dialogue-ocr/voice-library-runtime.json')
            self.assertEqual(Path(config['model_path']),moved/'local-voice-model/models/faster-whisper-small')
            self.assertEqual(Path(config['module_path']),moved/'local-voice-model/asr-runtime')

    def test_existing_custom_configuration_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'nte-dialogue-ocr').mkdir()
            path = root/'nte-dialogue-ocr/voice-library-runtime.json'
            path.write_bytes(b'existing user settings')
            self.assertFalse(installer.configure(root,root/'modules',root/'model'))
            self.assertEqual(path.read_bytes(),b'existing user settings')

    def test_incomplete_model_does_not_publish_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'nte-dialogue-ocr').mkdir()
            with self.assertRaises(ValueError):
                installer.configure(root,root/'modules',root/'model')
            self.assertFalse((root/'nte-dialogue-ocr/voice-library-runtime.json').exists())


if __name__=='__main__':
    unittest.main()
