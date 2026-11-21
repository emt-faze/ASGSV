"""Sign the optional quest directory after publishing or revising a complete quest pack."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_wuwa_updates import ROOT, checksum, signed
import wuwa_update as update
from wuwa_quest_download import CATALOG, parse_catalog


def build(folder, playtests=None):
    # A playtest attests a specific archive digest and local mount signature after gameplay.
    # Without an explicit matching record the UI reports completion only.
    path = folder / CATALOG
    prior = parse_catalog(path.read_bytes()) if path.exists() else {}
    evidence = {e['sha256']:e['tested_builds'] for e in prior.values()} if playtests is None else playtests
    entries = []
    for line in (folder / 'SHA256SUMS.txt').read_text('ascii').splitlines():
        sha, filename = line.split('  ', 1)
        if not re.fullmatch(r'quest-[0-9]+-r[1-9][0-9]*\.zip', filename):
            raise ValueError('Unexpected file in quest directory')
        archive = folder / filename
        if checksum(archive) != sha:
            raise ValueError('Unexpected checksum: ' + filename)
        with ZipFile(archive) as z:
            m = update.inspect_zip(z)
            raw = z.read('payload/pack.json')
            if hashlib.sha256(raw).hexdigest() != m['files']['pack.json'][1]:
                raise ValueError('Invalid pack metadata: ' + filename)
            lines = json.loads(raw)['lines']
        entries.append({**{k:m[k] for k in ('scope','kind','name','minimum','revision')},
                        'asset':filename,'size':archive.stat().st_size,'sha256':sha,
                        'lines':len(lines),'tested_builds':evidence.get(sha,[])})
    data = signed({'schema':1,'kind':'quest-catalog','quests':entries})
    parse_catalog(data)
    update.atomic(path, data)
    print(f'Signed {len(entries)} complete quest packs')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', nargs='?', type=Path, default=ROOT / 'quest-packs')
    parser.add_argument('--playtests', type=Path, help='JSON mapping archive SHA-256 to confirmed game mount hashes')
    args = parser.parse_args()
    build(args.folder, json.loads(args.playtests.read_bytes()) if args.playtests else None)
