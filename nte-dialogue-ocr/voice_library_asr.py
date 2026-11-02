"""Isolated offline CPU transcription; input lines are private parent requests."""
import json
import os
from pathlib import Path
import re
import sys


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    for key in ('module_path', 'model_path'):
        if config.get(key):
            config[key] = str((path.parent / config[key]).resolve())
    return config


def main():
    config = load_config(sys.argv[1])
    work = Path(sys.argv[2]).resolve()
    if config.get('module_path'):
        sys.path.insert(0, str(Path(config['module_path']).resolve()))
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    model = None
    for line in sys.stdin:
        request = json.loads(line)
        identity = request['id']
        if not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise ValueError('Invalid request identity')
        try:
            path = Path(request['audio']).resolve()
            if not path.is_file():
                raise ValueError('参考音频不存在')
            if model is None:
                from faster_whisper import WhisperModel
                model_path = Path(config['model_path']).resolve()
                if not (model_path/'model.bin').is_file():
                    raise ValueError('本地转写模型缺失；可以先手动填写台词')
                model = WhisperModel(str(model_path), device='cpu', compute_type='int8', cpu_threads=4,
                                     num_workers=1, local_files_only=True)
            parts, _ = model.transcribe(str(path), language='zh', beam_size=5, temperature=0,
                condition_on_previous_text=False, vad_filter=True,
                vad_parameters={'min_silence_duration_ms': 300}, initial_prompt='以下是简体中文剧情对白。')
            segments = [dict(text=p.text, start=p.start, end=p.end) for p in parts]
            result = dict(text=''.join(p['text'] for p in segments).strip(), segments=segments)
        except Exception as exc:
            result = dict(error=str(exc))
        target = work/(identity+'.json')
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        temp.replace(target)


if __name__ == '__main__':
    main()
