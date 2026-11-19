"""Build reproducible generator archives from public source and pinned OCR models."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
import build_release

ROOT = Path(__file__).resolve().parents[1]
DATE = (2026, 11, 15, 0, 0, 0)


def source_files(root=ROOT):
    root = root.resolve()
    selected = set(build_release.source_files(root))
    selected.update(root/name for name in ('安装台词生成工具.bat', '打开台词生成工具.bat', '设置默认角色声线.bat', '安装声线自动转写.bat'))
    ocr = root/'nte-dialogue-ocr'
    selected.add(ocr/'assets/daily-quest-fingerprints.json.gz')
    top = {'.gitignore', '.gitattributes', 'VERSION', 'GENERATOR_VERSION', 'README.md',
           'THIRD_PARTY_NOTICES.md', 'requirements.txt', 'config.example.json', '台词生成工具使用说明.txt',
           'voice-library-runtime.example.json'}
    selected.update(p for p in ocr.iterdir() if p.is_file() and (p.name in top or p.suffix in {'.py','.ps1','.cmd'}))
    for directory, suffixes in {'scripts':{'.py'}, 'docs':{'.md'}}.items():
        selected.update(p for p in (ocr/directory).rglob('*') if p.is_file() and p.suffix in suffixes)
    for directory in ('assets/game-entry', 'assets/ocr-v6'):
        selected.update(p for p in (ocr/directory).iterdir() if p.is_file() and p.suffix != '.onnx')
    selected.update(ocr/'assets/audio-edges'/name for name in
                    ('silero_vad_v6.onnx','LICENSE-silero-vad.txt','LICENSE-faster-whisper.txt','README.txt'))
    selected.update(p for p in (root/'tools/voice-library').rglob('*')
                    if p.is_file() and p.suffix in {'.py','.md'} and '__pycache__' not in p.parts)
    selected.add(ocr/'validation/README.md')
    selected.update(ocr/'ui'/name for name in ('icons.py', 'workbench.py', 'stage_background.py', 'stage_voices.py'))
    selected.update(ocr/'assets'/name for name in ('dialogue-coast.jpg', 'dialogue-preview.flac'))
    selected.update(ocr/'ww_log_decoder'/name for name in ('decoder.py','LICENSE','source.json'))
    selected.update((ocr/'nte_organizer').glob('*.py'))
    selected.update(ocr/'assets/nte-resource-reader'/name for name in
                    ('LICENSE.CUE4Parse.Apache-2.0','LICENSE.UEExtractor',
                     'reader.runtimeconfig.json','source.json','bundle.json'))
    selected.update(root/name for name in ('安装异环配音.bat','打开异环配音.bat'))
    selected.update(root/name for name in ('launcher-source/Launcher.cs','launcher-source/build.ps1',
                                          'tools/launcher_button.ps1','安装鸣潮日志配音.bat',
                                          'tools/speech-cleanup/studio_clean.py',
                                          'tools/speech-cleanup/vendor/nara_wpe-0.0.11-py3-none-any.whl',
                                          'tools/speech-cleanup/vendor/nara_wpe-0.0.11-py3-none-any.provenance.json'))
    forbidden = {'.git','.venv','runtime','data','records','generation-projects','runtime-logs','__pycache__','reference','backups',
                 'voice-library-drafts','asr-runtime'}
    for p in selected:
        relative = p.relative_to(root)
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(root) or forbidden.intersection(relative.parts):
            raise ValueError('Unsafe package input: '+str(relative))
        if p.name in {'config.json','runtime.json','generator-state.json','project.json',
                      'voice-library-runtime.json','voice-library-source.json'}:
            raise ValueError('Local state is not a release input')
    return sorted(selected, key=lambda p:p.relative_to(root).as_posix())


def verified_reader(root, directory):
    manifest = json.loads((root/'nte-dialogue-ocr/assets/nte-resource-reader/bundle.json').read_text('utf-8'))
    result = []
    for item in manifest['files']:
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts or ':' in item['path'] or '\\' in item['path']:
            raise ValueError('Invalid resource-reader path')
        path = directory/relative
        if not path.is_file() or path.is_symlink() or path.stat().st_size != item['bytes']:
            raise ValueError('Missing resource-reader component: '+item['path'])
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest() != item['sha256']:
                raise ValueError('Resource-reader component checksum differs: '+item['path'])
        result.append(('nte-dialogue-ocr/assets/nte-resource-reader/'+item['path'],path))
    return result


def verified_models(root, models_dir):
    manifest = json.loads((root/'nte-dialogue-ocr/assets/ocr-v6/models.json').read_text(encoding='utf-8'))
    result = []
    for name, spec in manifest.items():
        if Path(name).name != name or not name.endswith('.onnx'):
            raise ValueError('Invalid model manifest entry')
        path = models_dir/name
        if not path.is_file() or path.is_symlink() or path.stat().st_size != spec['bytes']:
            raise ValueError('Missing or incorrect OCR model: '+name)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest() != spec['sha256']:
                raise ValueError('OCR model checksum differs: '+name)
        result.append((f'nte-dialogue-ocr/assets/ocr-v6/{name}', path))
    return result


def build(root=ROOT, *, output, models_dir=None, reader_dir=None, launcher=None):
    version = (root/'nte-dialogue-ocr/GENERATOR_VERSION').read_text(encoding='ascii').strip()
    prefix = 'dialogue-generator-v'+version
    inputs = [(p.relative_to(root).as_posix(),p) for p in source_files(root)]
    variants = [('source',inputs)]
    if models_dir is not None:
        complete = inputs + verified_models(root, models_dir)
        if reader_dir is not None:
            existing = {name for name,path in complete}
            complete += [(name,path) for name,path in verified_reader(root,reader_dir) if name not in existing]
        if launcher is not None:
            complete.append(('ASGSV.exe',launcher))
        variants.append(('windows-with-ocr-models',complete))
    output.mkdir(parents=True,exist_ok=True)
    results = []
    for variant, files in variants:
        records = []
        archive = output/f'{prefix}-{variant}.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as bundle:
            for relative, path in sorted(files):
                data = path.read_bytes()
                records.append(dict(path=relative,size=len(data),sha256=hashlib.sha256(data).hexdigest()))
                entry = zipfile.ZipInfo(prefix+'/'+relative,date_time=DATE)
                entry.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(entry,data)
            metadata = dict(generator_version=version,variant=variant,files=records,
                            includes_ocr_models=models_dir is not None and variant!='source',
                            includes_nte_resource_reader=reader_dir is not None and variant!='source',
                            includes_tts_weights=False,includes_python_runtime=False,includes_personal_data=False)
            entry = zipfile.ZipInfo(prefix+'/FILE_MANIFEST.json',date_time=DATE)
            entry.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(entry,json.dumps(metadata,ensure_ascii=False,indent=2).encode('utf-8'))
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip():
                raise ValueError('Invalid ZIP')
            for entry in records:
                if hashlib.sha256(bundle.read(prefix+'/'+entry['path'])).hexdigest() != entry['sha256']:
                    raise ValueError('Archive entry differs: '+entry['path'])
        with archive.open('rb') as stream:
            digest = hashlib.file_digest(stream,'sha256').hexdigest()
        results.append(dict(file=archive.name,bytes=archive.stat().st_size,sha256=digest,files=len(records)))
    (output/'SHA256SUMS.txt').write_text(''.join(f"{r['sha256']}  {r['file']}\n" for r in results),encoding='ascii')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist')
    parser.add_argument('--models-dir', type=Path)
    parser.add_argument('--reader-dir', type=Path)
    parser.add_argument('--launcher', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(output=args.output,models_dir=args.models_dir,
        reader_dir=args.reader_dir,launcher=args.launcher),ensure_ascii=False,indent=2))
