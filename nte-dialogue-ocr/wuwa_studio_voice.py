"""Persistent, immutable studio voice recipes selected by the user."""
import json
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4
import wave

from dialogue_generation import atomic_text, digest, file_digest
from wuwa_npc_voices import load_variant, variant_path


def create_studio_voice(root, speaker_id, speaker, reference, transcript, *, source, selection):
    reference=Path(reference).resolve()
    processor=Path(__file__).resolve().parent.parent/'tools/speech-cleanup/studio_clean.py'
    with wave.open(str(reference),'rb') as wav:
        seconds=wav.getnframes()/wav.getframerate()
        if wav.getnchannels()!=1 or not 3 <= seconds <= 30:
            raise ValueError('Studio reference must be 3–30 seconds of mono speech')
    recipe=dict(version=1,game_id='wuwa',speaker_id=str(speaker_id),speaker=speaker,
        source_speaker='贝诗 · B方案',source_reference_sha256=file_digest(reference),
        prompt_text=transcript,reference_language='zh',pitch_semitones=0,speed=1.0,
        algorithm='studio-preprocessed-reference-copy-v1',source_provenance=source,
        studio=dict(seed=43,strength=.55,soft=True,processor_sha256=file_digest(processor)),
        user_selection=selection)
    voice='npc:'+digest(recipe)
    target=variant_path(root,voice)
    if target.exists():
        load_variant(root,voice,speaker_id=speaker_id,speaker=speaker)
        return voice
    stage=target.parent/('.pending-'+uuid4().hex)
    stage.mkdir(parents=True)
    try:
        shutil.copyfile(reference,stage/'reference.wav')
        saved=dict(recipe=recipe,reference_sha256=file_digest(stage/'reference.wav'),
            duration_seconds=seconds,label='盈叶 · B方案（AI 改编）',transcript_reviewed=False,
            user_style_approved=True,approval_scope='audition style only, not all task performances')
        atomic_text(stage/'variant.json',json.dumps(saved,ensure_ascii=False,indent=2))
        stage.rename(target)
    finally:
        if stage.exists():
            for name in ('reference.wav','variant.json'):
                (stage/name).unlink(missing_ok=True)
            stage.rmdir()
    return voice


def studio_recipe(root, row):
    if not row.voice.startswith('npc:'):
        return None
    _,saved=load_variant(root,row.voice,speaker_id=row.evidence.get('speaker_id'),speaker=row.speaker)
    options=saved['recipe'].get('studio')
    if options is not None:
        options=dict(options)
        take=row.evidence.get('studio_take',{})
        if take.get('voice')==row.voice and take.get('text_sha256')==digest(row.text):
            options['seed']=take['seed']
        if (type(options.get('seed')) is not int or not 0<=options['seed']<=2147483647
                or not 0<=options.get('strength',-1)<=1 or type(options.get('soft')) is not bool):
            raise ValueError('Invalid studio recipe')
    return options


def configure_client(runtime, root, row):
    """Keep normal clients/cache identities unchanged; cancellation owns this client."""
    from wuwa_dubber.tts.client import Qwen3TtsClient
    options=studio_recipe(root,row)
    base=getattr(runtime.client,'studio_base_client',runtime.client)
    if options is None:
        runtime.client=base
        return
    if not isinstance(base,Qwen3TtsClient):
        raise ValueError('This studio voice requires the local Qwen3 service')
    runtime.client=StudioClient(base,options)


# Loaded after voice_paths has made the existing shared package available.
from dialogue_dubbing import voice_paths
voice_paths({})
from wuwa_dubber.tts.client import Qwen3TtsClient


class StudioClient(Qwen3TtsClient):
    def __init__(self,base,options):
        super().__init__(base.endpoint,base.token,timeout=base.timeout)
        self.studio_base_client=base
        self.studio_options=dict(options)

    def health(self):
        result=super().health()
        if not result.get('supports_variation_seed'):
            raise ValueError('Local server must support the saved studio variation seed')
        return result

    def _request(self,path,*,payload=None,method='POST',timeout=None):
        if path=='/v1/synthesize':
            payload=dict(payload,variation_seed=self.studio_options['seed'])
        return super()._request(path,payload=payload,method=method,timeout=timeout)

    def cache_identity(self):
        result=super().cache_identity()
        result['studio_recipe']=dict(self.studio_options)
        return result


def process_export(project,row,staging,stop,root):
    options=studio_recipe(root,row)
    if options is None:
        return None
    dg=Path(__file__).resolve().parent.parent
    script=dg/'tools/speech-cleanup/studio_clean.py'
    if file_digest(script)!=options['processor_sha256']:
        raise ValueError('Studio processor changed; keep existing audio and review the recipe')
    if stop.is_set():
        raise InterruptedError('Studio processing cancelled')
    output=Path(staging).with_name('.studio-'+uuid4().hex+'.wav')
    metadata=output.with_suffix('.processing.json')
    command=[str(dg/'local-voice-model/.venv/Scripts/python.exe'),str(script),str(staging),str(output),
             '--strength',str(options['strength'])]
    if options['soft']:
        command.append('--soft')
    process=None
    try:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+45
        while True:
            if stop.is_set() or time.monotonic()>deadline:
                raise InterruptedError('Studio processing cancelled or timed out')
            try:
                _,error=process.communicate(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                pass
        if process.returncode:
            raise ValueError('Studio processing failed: '+error.decode('utf-8',errors='replace')[-800:])
        record=json.loads(metadata.read_text(encoding='utf-8'))
        if record['sha256']!=file_digest(output) or record['source_sha256']!=file_digest(staging):
            raise ValueError('Studio export hash mismatch')
        if stop.is_set():
            raise InterruptedError('Studio processing cancelled')
        output.replace(staging)
        return dict(options=options,report=record)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        output.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)
