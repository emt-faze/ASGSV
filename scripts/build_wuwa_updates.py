"""Build signed WuWa offline/online packages from a verified published portable ZIP."""
import argparse
import base64
from copy import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'nte-dialogue-ocr'))


def key_file():
    return Path(os.environ['LOCALAPPDATA']) / 'ASGSV-Release' / 'signing-key.dpapi'


def key(create=False):
    import win32crypt
    path = key_file()
    if create and not path.exists():
        path.parent.mkdir(exist_ok=True, parents=True)
        secret = Ed25519PrivateKey.generate().private_bytes_raw()
        with path.open('xb') as stream:
            stream.write(win32crypt.CryptProtectData(secret, 'ASGSV release signing', None, None, None, 1))
    return Ed25519PrivateKey.from_private_bytes(win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 1)[1])


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def signed(body):
    import wuwa_update as update
    signer = key()
    if signer.public_key().public_bytes_raw().hex() != update.PUBLIC_KEY:
        raise ValueError('Publisher key does not match the embedded trust key')
    return json.dumps({'body': body, 'signature': base64.b64encode(signer.sign(update.canonical(body))).decode()},
                      ensure_ascii=False).encode()


def package(out, name, props, payload):
    entries = {s: [len(b), hashlib.sha256(b).hexdigest()] for s, b in payload.items()}
    path = out / name
    with ZipFile(path, 'w', ZIP_DEFLATED, compresslevel=5) as z:
        z.writestr('update.json', signed(dict(schema=1, **props, files=entries)))
        for s, b in payload.items():
            z.writestr('payload/' + s, b)
    return dict(**props, asset=name, size=path.stat().st_size, sha256=checksum(path))


def build(args):
    from build_generator_release import source_files
    import wuwa_update as updater
    # Bind the public destination in both clients, including the first-upgrade assistant.
    repository = args.cnb_repository or updater.CNB_REPOSITORY
    updater.cnb_releases(repository)
    update_module = (ROOT / 'nte-dialogue-ocr/wuwa_update.py').read_text(encoding='utf-8')
    binding = [line for line in update_module.splitlines() if line.startswith('CNB_REPOSITORY = ')]
    if len(binding) != 1:
        raise ValueError('Cannot bind the public CNB repository to the release')
    update_module = update_module.replace(binding[0], 'CNB_REPOSITORY = ' + repr(repository)).encode('utf-8')
    version = (ROOT/'nte-dialogue-ocr/GENERATOR_VERSION').read_text().strip()
    from wuwa_claim_components import DIRECTORY, verified_wheels
    wheels = verified_wheels(args.claim_wheels)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    if not args.base_sha or checksum(args.base) != args.base_sha:
        raise ValueError('Published base ZIP SHA-256 mismatch')
    with ZipFile(args.base) as old:
        manifest = json.loads(old.read('ASGSV/FILE_MANIFEST.json'))
        base = manifest['version']
        if updater.semver(version) <= updater.semver(base):
            raise ValueError('Target version must increase')
        changes = {}
        for path in source_files(ROOT):
            name = path.relative_to(ROOT).as_posix()
            if not updater.program_name(name) or path.name == 'auto_record_demos.py':
                continue
            data = update_module if name == 'nte-dialogue-ocr/wuwa_update.py' else path.read_bytes()
            prior = manifest['files'].get(name)
            if prior == hashlib.sha256(data).hexdigest():
                continue
            # A checkout may use CRLF where the portable baseline used LF.
            if prior and path.suffix.lower() in {'.py','.md','.ps1','.cs','.txt','.bat'}:
                original = old.read('ASGSV/' + name)
                if original.replace(b'\r\n',b'\n') == data.replace(b'\r\n',b'\n'):
                    continue
            changes[name] = data
        if 'nte-dialogue-ocr/GENERATOR_VERSION' not in changes:
            raise ValueError('No program version change')
        program = package(out, f'ASGSV-v{version}-Update.zip',
                          dict(kind='app', name='工作台程序', version=version, base=base), changes)
        manifest['version'] = version
        manifest['files'].update({n:hashlib.sha256(b).hexdigest() for n,b in changes.items()})
        portable = out/f'ASGSV-v{version}-Windows-x64-Portable.zip'
        with ZipFile(portable.with_suffix('.partial'), 'w', ZIP_DEFLATED, compresslevel=4) as z:
            for item in old.infolist():
                name = item.filename.removeprefix('ASGSV/')
                if name.startswith('tools/breezeblue-wheels/'):
                    manifest['files'].pop(name, None)
                    continue
                if name in changes or name == 'FILE_MANIFEST.json':
                    continue
                z.writestr(copy(item), old.read(item))
            for wheel in wheels:
                relative = DIRECTORY + '/' + wheel.name
                z.write(wheel, 'ASGSV/' + relative)
                manifest['files'][relative] = checksum(wheel)
            for n,b in changes.items():
                z.writestr('ASGSV/'+n,b)
            z.writestr('ASGSV/FILE_MANIFEST.json', json.dumps(manifest,ensure_ascii=False,indent=2).encode())
        portable.with_suffix('.partial').replace(portable)
        listing = [program]
        if not args.previous_release:
            add_resources(out, listing, old, args, version)
    if args.previous_release:
        previous = updater.signed_body((args.previous_release / 'wuwa-updates.json').read_bytes())
        if previous['tag'] != 'wuwa-v' + base:
            raise ValueError('Published feed does not match the portable baseline')
        for item in previous['packages']:
            path = args.previous_release / item['asset']
            if path.name != item['asset'] or path.stat().st_size != item['size'] or checksum(path) != item['sha256']:
                raise ValueError('Previous release asset differs from its signed feed')
            shutil.copyfile(path, out / path.name)
            listing.append(item)
    finish_release(out, listing, portable, version, update_module)
    print(f'{len(changes)} changed program files; {len(listing)} signed packages')


def add_resources(out, listing, old, args, version):
    meta = json.loads(old.read('ASGSV/game-data/wuwa/voice-pack/pack.json'))
    for quest in sorted({r['quest'] for r in meta['lines']}):
        rows = [r for r in meta['lines'] if r['quest']==quest]
        payload = {'pack.json': json.dumps(dict(meta, lines=rows),ensure_ascii=False).encode()}
        for r in rows:
            n = 'audio/'+r['sha256']+'.wav'
            if n not in payload:
                payload[n] = old.read('ASGSV/game-data/wuwa/voice-pack/'+n)
        listing.append(package(out, quest.replace(':','-')+'-r'+str(args.revision)+'.zip',
                               dict(kind='quest',name=rows[0]['quest_name'],scope=quest,
                                    revision=args.revision, minimum=version),payload))
    from wuwa_resources import TABLES
    index = {s+'.json':(args.index/(s+'.json')).read_bytes() for s in [*TABLES,'source']}
    listing.append(package(out,'wuwa-index-r'+str(args.revision)+'.zip',
                           dict(kind='index',name='中文台词索引',revision=args.revision,minimum=version),index))


def finish_release(out, listing, portable, version, update_module):
    # The .17 bootstrap runs in a separate folder and does not overwrite program files.
    bootstrap = out/'ASGSV-Update-Assistant.zip'
    with ZipFile(bootstrap,'w',ZIP_DEFLATED) as z:
        z.writestr('ASGSV-Update/wuwa_update.py', update_module)
        z.write(ROOT/'nte-dialogue-ocr/wuwa_update_ui.py','ASGSV-Update/wuwa_update_ui.py')
        z.write(ROOT/'nte-dialogue-ocr/combo_popup.py','ASGSV-Update/combo_popup.py')
        z.writestr('ASGSV-Update/Start-Update.bat',
                   b'@echo off\r\ncd /d "%~dp0"\r\nif not exist "..\\runtime\\python\\python.exe" (echo Place ASGSV-Update next to ASGSV.exe & pause & exit /b 1)\r\n"..\\runtime\\python\\python.exe" -I -X utf8 "%~dp0wuwa_update_ui.py"\r\nif errorlevel 1 pause\r\n')
    tag = 'wuwa-v'+version
    (out/'wuwa-updates.json').write_bytes(signed(dict(schema=1,tag=tag,packages=listing)))
    paths = [portable, bootstrap, out/'wuwa-updates.json', *(out/e['asset'] for e in listing)]
    (out/'SHA256SUMS.txt').write_text(''.join(f'{checksum(p)}  {p.name}\n' for p in paths), encoding='ascii')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init-key',action='store_true')
    parser.add_argument('--base',type=Path)
    parser.add_argument('--base-sha')
    parser.add_argument('--index',type=Path)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--claim-wheels',type=Path)
    parser.add_argument('--previous-release',type=Path, help='Reuse signed resources and preserve earlier upgrade paths')
    parser.add_argument('--cnb-repository', help='Public CNB repository path (group/repository)')
    parser.add_argument('--revision',type=int,default=1)
    args = parser.parse_args()
    if args.init_key:
        print(key(create=True).public_key().public_bytes_raw().hex())
    else:
        build(args)
