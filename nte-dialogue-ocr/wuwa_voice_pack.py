"""Read portable finished quest recordings for the matching resource build."""
import json
from pathlib import Path
import re

from prepared_playback import PreparedLine


def mount_signature(inventory):
    base = Path(inventory['install_root']) / 'Wuthering Waves Game/Client/Saved/Resources'
    return sorted([Path(m['path']).relative_to(base).as_posix(), m['sha256']] for m in inventory['mounts'])


def load_pack(root, *, allow_missing_audio=False):
    folder = root / 'voice-pack'
    manifest = folder / 'pack.json'
    if not manifest.is_file():
        return {}
    data = json.loads(manifest.read_text(encoding='utf8'))
    inventory = json.loads((root/'sources/local-inventory.json').read_text(encoding='utf8'))
    signature = mount_signature(inventory)
    if (data['format'] != 1 or not signature
            or signature not in [data['mounts'], *data.get('additional_mounts', [])]):
        return {}
    result = {}
    for row in data['lines']:
        key = row['state'], row['number']
        sha = row['sha256']
        if key in result or not re.fullmatch('[0-9a-f]{64}', sha):
            raise ValueError('任务配音包的台词编号或文件校验值无效')
        path = folder / 'audio' / (sha + '.wav')
        if not allow_missing_audio and not path.is_file():
            raise ValueError('任务配音包缺少音频文件，请重新解压')
        clip = PreparedLine(row['id'], row['text'], row['speaker'], row['label'], path, sha,
                            True, row['id'], row['voice'])
        result[key] = (row, clip)
    return result


def pack_matches(catalog, rows, entry, clip):
    source = rows[0] if len(rows)==1 else None
    matches = not rows or (source is not None and source['id']==clip.catalog_id
                           and source['fingerprint']==entry['fingerprint']
                           and source['voice_status']!='voiced'
                           and not (source['voice_status']=='unknown' and source['reviewed_at']))
    binding = catalog.binding(entry['speaker_id'])
    explicit = catalog.db.execute('SELECT 1 FROM binding_origins WHERE speaker_id=?',
                                  (entry['speaker_id'],)).fetchone()
    if binding and (explicit or catalog.cloud_only() or binding['voice']!='system:default'):
        matches = matches and binding['voice']==clip.binding_voice
    from wuwa_cast_review import reviewed_roles, review_problem
    roles = reviewed_roles(catalog,entry['quest'])
    if roles is not None and entry['speaker_id'] in roles:
        voice = clip.binding_voice.removeprefix('breeze:')
        matches = matches and not review_problem(catalog,entry['quest'],entry['speaker_id'],voice)
    return matches
