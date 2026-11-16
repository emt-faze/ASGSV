"""Install the pinned NTE resource reader used by the public Windows package."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import urllib.request
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def valid(directory, manifest):
    for item in manifest['files']:
        path = directory / item['path']
        if not path.is_file() or path.stat().st_size != item['bytes']:
            return False
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                return False
    return True


def prepare(manifest, destination, *, archive=None, opener=urllib.request.urlopen):
    destination = Path(destination).resolve()
    for item in manifest['files']:
        path = PurePosixPath(item['path'])
        if path.is_absolute() or '..' in path.parts or '\\' in item['path'] or ':' in item['path']:
            raise ValueError('Invalid resource-reader manifest path')
    if valid(destination, manifest):
        print('Resource reader verified.', flush=True)
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.nte-reader-', dir=destination.parent) as temporary:
        temporary = Path(temporary)
        bundle = Path(archive) if archive else temporary/'reader.zip'
        if archive is None:
            print('Downloading the NTE resource reader...', flush=True)
            request = urllib.request.Request(manifest['url'], headers={'User-Agent':'ASGSV-NTE-installer'})
            with opener(request, timeout=60) as response, bundle.open('wb') as output:
                shutil.copyfileobj(response, output, 1024*1024)
        with bundle.open('rb') as stream:
            digest = hashlib.file_digest(stream,'sha256').hexdigest()
        if bundle.stat().st_size != manifest['bytes'] or digest != manifest['sha256']:
            raise ValueError('Resource-reader download checksum mismatch')
        staged = temporary/'reader'
        staged.mkdir()
        expected = {item['path'] for item in manifest['files']}
        with zipfile.ZipFile(bundle) as source:
            if len(source.namelist()) != len(expected) or set(source.namelist()) != expected:
                raise ValueError('Resource-reader archive contents differ from the manifest')
            source.extractall(staged)
        if not valid(staged,manifest):
            raise ValueError('Resource-reader file verification failed')
        (staged/'bundle.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n','utf-8')
        backup = destination.with_name(destination.name+'.backup-'+uuid4().hex)
        had_previous = destination.exists()
        if had_previous:
            destination.replace(backup)
        try:
            staged.replace(destination)
        except Exception:
            if had_previous:
                backup.replace(destination)
            raise
        if had_previous:
            shutil.rmtree(backup)
    print('Resource reader installed and verified.',flush=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--verify-only',action='store_true')
    args = parser.parse_args()
    directory = ROOT/'assets/nte-resource-reader'
    manifest = json.loads((directory/'bundle.json').read_text('utf-8'))
    if args.verify_only:
        if not valid(directory,manifest):
            raise ValueError('Install the NTE resource reader first')
        print('Resource reader verified.')
    else:
        prepare(manifest,directory,archive=args.archive)


if __name__ == '__main__':
    main()
