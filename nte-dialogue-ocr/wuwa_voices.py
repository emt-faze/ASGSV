"""Local voice invalidation without waking or contacting a synthesis service."""
from pathlib import Path
from dialogue_generation import digest, file_digest


def local_voice_signature(runtime, profile):
    settings = runtime.config.to_dict()
    settings = {k:v for k,v in settings.items() if k.startswith(('tts_', 'sapi_'))
                and k not in {'tts_endpoint','tts_token'}}
    reference = file_digest(Path(profile.reference_audio)) if profile.reference_audio else ''
    return digest(dict(settings=settings, profile=profile.to_dict(), reference_sha256=reference))


def validate_audio_voices(project, options):
    from dialogue_dubbing import VoiceRuntime
    from dialogue_generation import select_voice
    runtimes = {}
    signatures = {}
    for row in project.rows:
        if row.status != 'done' or not project.audio_path(row, verify=False):
            continue
        mode = 'system' if row.voice.startswith('system:') else 'character'
        if mode not in runtimes:
            runtimes[mode] = VoiceRuntime(dict(options, mode=mode))
        runtime = runtimes[mode]
        profile, _ = select_voice(runtime, row)
        if row.voice not in signatures:
            signatures[row.voice] = local_voice_signature(runtime, profile)
        if row.audio.get('local_voice_signature') != signatures[row.voice]:
            raise ValueError('声线或本地合成参数已变化，请先继续生成以核对缓存：' + row.speaker)
