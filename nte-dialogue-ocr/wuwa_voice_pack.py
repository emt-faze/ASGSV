"""Read portable finished quest recordings for the matching resource build."""
import json
from pathlib import Path
import re

from prepared_playback import PreparedLine


def mount_signature(inventory):
    base = Path(inventory['install_root']) / 'Wuthering Waves Game/Client/Saved/Resources'
    return sorted([Path(m['path']).relative_to(base).as_posix(), m['sha256']] for m in inventory['mounts'])


def load_pack(root):
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
        if not path.is_file():
            raise ValueError('任务配音包缺少音频文件，请重新解压')
        clip = PreparedLine(row['id'], row['text'], row['speaker'], row['label'], path, sha,
                            True, row['id'], row['voice'])
        result[key] = (row, clip)
    return result
