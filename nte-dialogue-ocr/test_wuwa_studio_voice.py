import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from wuwa_studio_voice import create_studio_voice, StudioClient, configure_client, process_export, studio_recipe
from dialogue_generation import digest
from wuwa_dubber.tts.client import Qwen3TtsClient


class StudioVoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        reference=self.root/'ref.wav'
        with wave.open(str(reference),'wb') as wav:
            wav.setparams((1,2,24000,0,'NONE','not compressed'))
            wav.writeframes(b'\x00\x10'*96000)
        self.voice=create_studio_voice(self.root,'1','NPC',reference,'Verified reference',
            source={'test':True},selection='B')
        self.row=SimpleNamespace(voice=self.voice,evidence={'speaker_id':'1'},speaker='NPC')
        self.base=Qwen3TtsClient('http://127.0.0.1:9882','test')

    def test_seed_is_sent_and_part_of_cache_identity_without_changing_normal_client(self):
        client=StudioClient(self.base,dict(seed=43,strength=.55,soft=True,processor_sha256='hash'))
        with patch.object(Qwen3TtsClient,'_request',return_value=(b'RIFF','audio/wav')) as send:
            client._request('/v1/synthesize',payload={'text':'test'})
            self.assertEqual(send.call_args.kwargs['payload']['variation_seed'],43)
            client._request('/v1/cancel',payload={'request_id':'x'})
            self.assertNotIn('variation_seed',send.call_args.kwargs['payload'])
        with patch.object(Qwen3TtsClient,'health',return_value={'model_revision':'r','supports_variation_seed':True}):
            normal=self.base.cache_identity()
            studio=client.cache_identity()
            self.assertNotEqual(normal,studio)
            self.assertNotIn('studio_recipe',normal)
            client.studio_options['seed']=47
            self.assertNotEqual(studio,client.cache_identity())

    def test_old_server_cannot_silently_ignore_saved_seed(self):
        client=StudioClient(self.base,dict(seed=43,strength=.55,soft=True,processor_sha256='hash'))
        with patch.object(Qwen3TtsClient,'health',return_value={'model_revision':'old'}):
            with self.assertRaises(ValueError):
                client.cache_identity()

    def test_retake_seed_is_bound_to_exact_voice_and_text(self):
        self.row.text='Test line'
        self.row.evidence['studio_take']=dict(voice=self.voice,text_sha256=digest(self.row.text),seed=47)
        self.assertEqual(studio_recipe(self.root,self.row)['seed'],47)
        self.row.text='Changed line'
        self.assertEqual(studio_recipe(self.root,self.row)['seed'],43)
        self.row.text='Test line'
        self.row.evidence['studio_take']['seed']=-1
        with self.assertRaises(ValueError):
            studio_recipe(self.root,self.row)

    def test_normal_row_restores_original_client_and_cancellation_owner(self):
        runtime=SimpleNamespace(client=self.base)
        configure_client(runtime,self.root,self.row)
        self.assertIsInstance(runtime.client,StudioClient)
        normal=SimpleNamespace(voice='character:Other')
        configure_client(runtime,self.root,normal)
        self.assertIs(runtime.client,self.base)

    def test_cancel_and_changed_processor_preserve_staging_and_do_not_spawn(self):
        stage=self.root/'staging.wav'
        stage.write_bytes(b'original')
        cancelled=threading.Event()
        cancelled.set()
        with patch('wuwa_studio_voice.subprocess.Popen') as spawn:
            with self.assertRaises(InterruptedError):
                process_export(None,self.row,stage,cancelled,self.root)
            with patch('wuwa_studio_voice.file_digest',return_value='changed'):
                with self.assertRaises(ValueError):
                    process_export(None,self.row,stage,threading.Event(),self.root)
            spawn.assert_not_called()
        self.assertEqual(stage.read_bytes(),b'original')


if __name__=='__main__':
    unittest.main()
