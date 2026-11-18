"""Build a relocatable Windows x64 package using an embeddable Python ZIP and installed wheels."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from build_generator_release import ROOT, source_files, verified_models

QT_PLUGINS = {'platforms', 'imageformats', 'iconengines', 'styles', 'multimedia',
              'networkinformation', 'tls'}
QT_ADDONS = {'Qt6Multimedia.dll', 'QtMultimedia.pyd'}
EXCLUDED_WHEELS = {'pip', 'opencv_python_headless'}


def wheel_files(site):
    for record in sorted(site.glob('*.dist-info/RECORD')):
        wheel = record.parent.name.removesuffix('.dist-info').rsplit('-', 1)[0]
        if wheel in EXCLUDED_WHEELS:
            continue
        for name, *_ in csv.reader(io.StringIO(record.read_text(encoding='utf-8'))):
            path = site / name
            if not path.is_file() or not path.resolve().is_relative_to(site) or path.is_symlink():
                continue
            relative = path.relative_to(site)
            if '__pycache__' in relative.parts or path.suffix in {'.pyc', '.pyi', '.pdb', '.chm'}:
                continue
            if relative.parts[0] == 'PySide6':
                if relative.parts[1] == 'plugins' and relative.parts[2] not in QT_PLUGINS:
                    continue
                if relative.parts[1] in {'qml', 'include', 'metatypes', 'typesystems', 'translations',
                                          'glue', 'scripts', 'doc', 'examples'} or path.suffix == '.exe':
                    continue
                if (wheel == 'pyside6_addons' and relative.parts[1] != 'plugins'
                        and path.name not in QT_ADDONS and not path.name.startswith(('av', 'sw'))):
                    continue
            yield path, relative


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
        runtime = root / 'runtime'
        with ZipFile(args.python_zip) as bundle:
            bundle.extractall(runtime)
        (runtime / 'python312._pth').write_text(
            'python312.zip\n.\nLib/site-packages\n../nte-dialogue-ocr\n../wuwa-dubber/src\nimport site\n',
            encoding='ascii')
        site = args.site_packages.resolve()
        for source, relative in wheel_files(site):
            copy(source, runtime / 'Lib/site-packages' / relative)
        for name in ('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll'):
            copy(site / 'PySide6' / name, runtime / name)
        count = add_voice_pack(args.voice_pack, root)
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                        str(root / 'launcher-source/build.ps1')], check=True)
        files = sorted(p for p in root.rglob('*') if p.is_file())
        files_manifest = {p.relative_to(root).as_posix(): checksum(p) for p in files}
        manifest = root / 'FILE_MANIFEST.json'
        manifest.write_text(json.dumps({'version': version, 'quest_lines': count, 'files': files_manifest},
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
    for name in ('output', 'python-zip', 'site-packages', 'models-dir', 'voice-pack'):
        parser.add_argument('--' + name, required=True, type=Path)
    print(build(parser.parse_args()))
