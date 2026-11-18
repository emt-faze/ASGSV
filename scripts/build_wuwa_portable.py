"""Build a relocatable Windows x64 package with a clean Python 3.12.8 runtime and pinned application wheels."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from build_generator_release import ROOT, source_files, verified_models

def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def add_voice_pack(voice_pack, root):
    manifest = voice_pack / 'pack.json'
    rows = json.loads(manifest.read_text(encoding='utf-8'))['lines']
    target = root / 'game-data/wuwa/voice-pack'
    copy(manifest, target / 'pack.json')
    for sha in {row['sha256'] for row in rows}:
        if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError('Invalid quest audio checksum')
        relative = Path('audio') / (sha + '.wav')
        path = voice_pack / relative
        if checksum(path) != sha:
            raise ValueError(f'Quest audio checksum mismatch: {path.name}')
        copy(path, target / relative)
    return len(rows)


def build(args):
    version = (ROOT / 'nte-dialogue-ocr/GENERATOR_VERSION').read_text().strip()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='build-', dir=args.output) as tmp:
        root = Path(tmp) / 'ASGSV'
        for source in source_files(ROOT):
            if source.name != 'auto_record_demos.py':
                copy(source, root / source.relative_to(ROOT))
        for relative, source in verified_models(ROOT, args.models_dir):
            copy(source, root / relative)
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                        str(root / 'scripts/install_generator.ps1'), '-DependenciesOnly'], check=True)
        python = root / 'runtime/python/python.exe'
        python_version = subprocess.check_output(
            [str(python), '-I', '-c', "import platform; print(platform.python_version())"], text=True
        ).strip()
        subprocess.run([str(python), '-I', '-m', 'pip', '--version'], check=True)
        count = add_voice_pack(args.voice_pack, root)
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                        str(root / 'launcher-source/build.ps1')], check=True)
        files = sorted(p for p in root.rglob('*') if p.is_file() and p != root / 'runtime/.setup.lock')
        files_manifest = {p.relative_to(root).as_posix(): checksum(p) for p in files}
        manifest = root / 'FILE_MANIFEST.json'
        manifest.write_text(json.dumps({'version': version, 'python': python_version, 'quest_lines': count, 'files': files_manifest},
                                       ensure_ascii=False, indent=2), encoding='utf-8')
        archive = args.output / f'ASGSV-v{version}-Windows-x64-Portable.zip'
        temporary = archive.with_suffix('.partial')
        with ZipFile(temporary, 'w', ZIP_DEFLATED, compresslevel=5) as bundle:
            for path in [*files, manifest]:
                bundle.write(path, path.relative_to(root.parent).as_posix())
        with ZipFile(temporary) as bundle:
            bad_file = bundle.testzip()
            if bad_file:
                raise ValueError(f'Invalid ZIP entry: {bad_file}')
        temporary.replace(archive)
        (args.output / 'SHA256SUMS.txt').write_text(f'{checksum(archive)}  {archive.name}\n', encoding='ascii')
        return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'models-dir', 'voice-pack'):
        parser.add_argument('--' + name, required=True, type=Path)
    print(build(parser.parse_args()))
