"""Game boundaries. Existing callers remain NTE unless they explicitly opt in."""
from __future__ import annotations

import json
import ntpath
import re
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent
WUWA_ROOT = CODE_ROOT.parent / 'game-data' / 'wuwa'
DEFAULT_INSTALL = r'F:\Wuthering Waves'
GAME_IDS = {'nte', 'wuwa'}


def wuwa_client(install_root):
    root = Path(install_root).resolve()
    if root.name.casefold() == 'client-win64-shipping.exe':
        root = root.parents[2]
    if root.name.casefold() == 'client' and (root/'Binaries').is_dir():
        return root
    if (root/'Client').is_dir() and not (root/'Wuthering Waves Game/Client').is_dir():
        return root/'Client'
    return root/'Wuthering Waves Game/Client'


def validate_game(game_id):
    if game_id not in GAME_IDS:
        raise ValueError('Unknown game: ' + str(game_id))
    return game_id


def wuwa_window_kind(path, window_class, install_root):
    if not install_root:
        return None
    root = ntpath.normcase(ntpath.abspath(install_root))
    actual = ntpath.normcase(ntpath.abspath(path))
    game = ntpath.normcase(str(wuwa_client(root) / 'Binaries/Win64/Client-Win64-Shipping.exe'))
    if actual == ntpath.normcase(game) and window_class == 'UnrealWindow':
        return 'game'
    if actual == ntpath.join(root, 'launcher.exe') and window_class in {'CefBrowserWindow', 'Chrome_WidgetWin_1', 'Qt5QWindowIcon', 'Qt6QWindowIcon'}:
        return 'launcher'
    if ntpath.dirname(ntpath.dirname(actual)) != root:
        return None
    relative = ntpath.relpath(actual, root)
    if (re.fullmatch(r'\d+\.\d+\.\d+\.\d+\\launcher_main\.exe', relative)
            and re.fullmatch(r'HwndWrapper\[launcher_main;;[0-9a-fA-F-]{36}\]', window_class)):
        return 'launcher'
    return None


def safe_output(path, install_root=DEFAULT_INSTALL):
    path, install = Path(path).resolve(), Path(install_root).resolve()
    if path == install or install in path.parents:
        raise ValueError('工具不可写入游戏安装目录')
    return path


def initialize_wuwa():
    from dialogue_generation import atomic_text
    root = safe_output(WUWA_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'config.json'
    if not path.exists():
        config = json.loads((CODE_ROOT / 'config.json').read_text(encoding='utf-8-sig'))
        config.update(game_id='wuwa', game_title='鸣潮', install_root=DEFAULT_INSTALL,
                      body_roi=[.18, .815, .82, .9], speaker_roi=[.38, .735, .62, .79], centered_body=True,
                      reference_size=[1920, 1080], calibrated=False, wgc_background=True, live_auto_start=True,
                      traveler_gender='female', nickname='漂泊者', narrator_voice='system:default',
                      hotkeys={'pause': 'F8', 'replay': 'F9', 'skip': 'F10'})
        config['dubbing'] = dict(config.get('dubbing', {}), game_id='wuwa',
                                 cache_root=str(root / 'cache'), mode='character')
        atomic_text(path, json.dumps(config, ensure_ascii=False, indent=2))
    return root


def wuwa_row_reason(row):
    evidence = row.evidence
    if evidence.get('game_id') != 'wuwa':
        return '不是鸣潮台词'
    if evidence.get('voice_status') != 'unvoiced' or not evidence.get('voice_basis'):
        return '未确认无原配音'
    if evidence.get('resolved_text') != row.text or evidence.get('resolved_speaker') != row.speaker:
        return '文字或角色已变化，请在台词库重新核对'
    if evidence.get('unresolved'):
        return '动态变量待核对'
    return ''
