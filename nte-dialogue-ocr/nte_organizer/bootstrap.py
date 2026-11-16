"""Locate this installation's shared NTE data and reusable ASGSV modules."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
DEFAULT_ASGSV = ROOT.parent
VERSION = '1.0.0'


def shared_root():
    settings = ROOT / 'settings.json'
    options = json.loads(settings.read_text('utf-8-sig')) if settings.is_file() else {}
    path = Path(options.get('asgsv_root', DEFAULT_ASGSV)).expanduser()
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    required = ('nte_catalog.py', 'nte_log_stream.py', 'dialogue_generation.py')
    if not all((path / name).is_file() for name in required):
        raise ValueError('找不到已安装的异环配音组件，请检查 settings.json 中的 asgsv_root')
    return path


ASGSV = shared_root()
sys.path.insert(0, str(ASGSV))


def load_settings():
    from nte_catalog import catalog_path
    config = json.loads((ASGSV / 'config.json').read_text('utf-8-sig'))
    settings = ROOT / 'settings.json'
    options = json.loads(settings.read_text('utf-8-sig')) if settings.is_file() else {}
    previous = Path(options.get('catalog_path') or ROOT / 'data/catalog.sqlite').resolve()
    legacy = {ROOT / 'data/catalog.sqlite', ASGSV.parents[1] / '异环台词整理/data/catalog.sqlite'}
    if 'catalog_path' not in config.get('nte_hybrid', {}) and previous not in legacy:
        config.setdefault('nte_hybrid', {})['catalog_path'] = str(previous)
    return dict(asgsv_root=str(ASGSV),
                catalog_path=str(catalog_path(ASGSV, config)),
                player_name=options.get('player_name', config.get('nte_hybrid', {}).get('player_name', '鉴定师')),
                gender=options.get('gender'),
                log_directory=options.get('log_directory', str(Path.home() / 'AppData/Local/HT/Saved/Logs')),
                last_project=options.get('last_project', ''))


def save_settings(options):
    from dialogue_generation import atomic_text
    config_path = ASGSV / 'config.json'
    config = json.loads(config_path.read_text('utf-8-sig'))
    config.setdefault('nte_hybrid', {})['catalog_path'] = options['catalog_path']
    atomic_text(config_path, json.dumps(config, ensure_ascii=False, indent=2))
    atomic_text(ROOT / 'settings.json', json.dumps(options, ensure_ascii=False, indent=2))
