"""Immutable, game-local NPC voice variants derived from user reference audio."""
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from uuid import uuid4
import wave

from dialogue_generation import atomic_text, digest, file_digest
from game_profiles import safe_output


def parameters(pitch, speed):
    pitch, speed = float(pitch), float(speed)
    if not math.isfinite(pitch) or not -2 <= pitch <= 2:
        raise ValueError('NPC 音高须在 -2 到 +2 半音之间')
    if not math.isfinite(speed) or not .9 <= speed <= 1.1:
        raise ValueError('NPC 语速须在 0.90 到 1.10 之间')
    return round(pitch, 2), round(speed, 2)


def variant_path(root, voice):
    if not re.fullmatch(r'npc:[a-f0-9]{64}', voice):
        raise ValueError('无效的 NPC 声线标识')
    base = safe_output(Path(root) / 'npc-voices').resolve()
    path = (base / voice[4:]).resolve()
    if path.parent != base:
        raise ValueError('NPC 声线路径越界')
    return path


def load_variant(root, voice, *, speaker_id=None, speaker=None):
    from wuwa_dubber.models import VoiceProfile
    path = variant_path(root, voice)
    saved = json.loads((path / 'variant.json').read_text(encoding='utf-8'))
    recipe = saved['recipe']
    if recipe.get('game_id') != 'wuwa' or recipe.get('version') != 1 or digest(recipe) != voice[4:]:
        raise ValueError('NPC 声线来源或参数校验失败')
    if speaker_id is not None and str(speaker_id) != recipe['speaker_id']:
        raise ValueError('不能将另一个 NPC 的固定声线用于当前角色')
    if speaker is not None and speaker != recipe['speaker']:
        raise ValueError('NPC 角色名称不一致')
    parameters(recipe['pitch_semitones'], recipe['speed'])
    reference = path / 'reference.wav'
    if reference.resolve().parent != path or file_digest(reference) != saved['reference_sha256']:
        raise ValueError('NPC 参考音频已变化，请重新建立声线')
    profile = VoiceProfile(recipe['speaker'] + '（AI 改编）', reference_audio=str(reference),
                           prompt_text=recipe['prompt_text'], reference_language=recipe['reference_language'],
                           speed=recipe['speed'])
    return profile, saved


def create_variant(root, speaker_id, speaker, base, *, pitch=-.75, speed=1.03,
                   ffmpeg=None, stop=None):
    from wuwa_dubber.tool_discovery import discover_ffmpeg
    from wuwa_dubber.speech_cache import valid_wav
    stop = stop or threading.Event()
    pitch, speed = parameters(pitch, speed)
    speaker_id, speaker = str(speaker_id).strip(), str(speaker).strip()
    if not speaker_id or not speaker:
        raise ValueError('NPC 标识与名称不能为空')
    reference = Path(base.reference_audio)
    if not reference.is_absolute() or not reference.is_file() or not base.prompt_text.strip():
        raise ValueError('基础声线须有本地参考音频和参考台词')
    recipe = dict(version=1, game_id='wuwa', speaker_id=speaker_id, speaker=speaker,
                  source_speaker=base.speaker, source_reference_sha256=file_digest(reference),
                  prompt_text=base.prompt_text, reference_language=base.reference_language,
                  pitch_semitones=pitch, speed=speed, algorithm='asetrate-atempo-32k-v1')
    voice = 'npc:' + digest(recipe)
    target = variant_path(root, voice)
    if target.exists():
        load_variant(root, voice, speaker_id=speaker_id, speaker=speaker)
        return voice
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / ('.pending-' + uuid4().hex)
    stage.mkdir()
    process = None
    try:
        factor = 2 ** (pitch / 12)
        audio = stage / 'reference.wav'
        # Restore reference duration after the small pitch/timbre shift.
        filters = f'aresample=32000,asetrate={32000*factor:.8f},aresample=32000,atempo={1/factor:.10f}'
        command = [ffmpeg or discover_ffmpeg()[0], '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-i', str(reference), '-map', '0:a:0', '-vn', '-ac', '1', '-af', filters,
                   '-ar', '32000', '-c:a', 'pcm_s16le', str(audio)]
        if stop.is_set():
            raise InterruptedError('已取消建立 NPC 声线')
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 30
        while True:
            if stop.is_set() or time.monotonic() > deadline:
                raise InterruptedError('建立 NPC 声线已取消或超时')
            try:
                _, error = process.communicate(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise ValueError('参考声线处理失败：' + error.decode('utf-8', errors='replace')[-600:])
        if not valid_wav(audio):
            raise ValueError('改编参考音频无效')
        with wave.open(str(audio), 'rb') as stream:
            duration = stream.getnframes() / stream.getframerate()
            if not 3 <= duration <= 30:
                raise ValueError('改编参考音频须为 3 到 30 秒')
        if stop.is_set():
            raise InterruptedError('已取消建立 NPC 声线')
        saved = dict(recipe=recipe, reference_sha256=file_digest(audio), duration_seconds=duration,
                     label='AI 改编声线，非该 NPC 原声', transcript_reviewed=False)
        atomic_text(stage / 'variant.json', json.dumps(saved, ensure_ascii=False, indent=2))
        try:
            stage.rename(target)
        except OSError:
            if not target.exists():
                raise
            load_variant(root, voice, speaker_id=speaker_id, speaker=speaker)
        return voice
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        if stage.exists() and stage.resolve().parent == target.parent.resolve():
            shutil.rmtree(stage)


def choices(root, *, speaker_id=None):
    result = []
    for path in sorted((Path(root) / 'npc-voices').glob('*/variant.json')):
        if not re.fullmatch('[a-f0-9]{64}', path.parent.name):
            continue
        voice = 'npc:' + path.parent.name
        profile, saved = load_variant(root, voice)
        recipe = saved['recipe']
        if speaker_id is not None and str(speaker_id) != recipe['speaker_id']:
            continue
        label = (f"{recipe['speaker']}（AI 改编 · {recipe['source_speaker']} · "
                 f"{recipe['pitch_semitones']:+.2f} 半音 · {recipe['speed']:.2f}x）")
        result.append((label, voice, profile))
    return result
