"""Initialize portable generator paths without changing existing user settings."""
import argparse
from pathlib import Path
import json
from datetime import datetime, timezone
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def configure(root=ROOT):
    path = root / 'nte-dialogue-ocr/config.json'
    example = root / 'nte-dialogue-ocr/config.example.json'
    value = json.loads((path if path.exists() else example).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict) or not isinstance(value.get('dubbing', {}), dict):
        raise ValueError('Existing generator settings must be a JSON object')
    before = json.dumps(value, sort_keys=True)
    options = value.setdefault('dubbing', {})
    for key, setting in dict(code_root='../wuwa-dubber', data_root='../wuwa-dubber/data',
                             mode='system', enabled=False).items():
        options.setdefault(key, setting)
    changed = not path.exists() or before != json.dumps(value, sort_keys=True)
    if changed:
        if path.exists():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
            backup = path.parent / 'backups' / ('before-generator-install-' + stamp) / path.name
            backup.parent.mkdir(parents=True)
            shutil.copy2(path, backup)
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        temporary.replace(path)
    return dict(settings_updated=changed)


def configure_portable(root=ROOT):
    sys.path.insert(0, str(root / 'wuwa-dubber/src'))
    from wuwa_dubber.config import AppPaths, load_config

    paths = AppPaths(root / 'wuwa-dubber/data')
    if not paths.config.is_file():
        load_config(paths)
    return configure(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--portable', action='store_true')
    args = parser.parse_args()
    print(json.dumps(configure_portable() if args.portable else configure()))
