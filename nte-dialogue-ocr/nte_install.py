"""Discover an installed NTE client from local Windows installation records."""
from __future__ import annotations

import configparser
import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import re

GAME_DIRECTORY = Path('Client/WindowsNoEditor/HT')
LAUNCHER_NAMES = ('NTEGame.exe', 'NTEGlobalGame.exe', 'NTETWGame.exe')
GAME_NAMES = ('Neverness To Everness', 'Neverness to Everness', 'NevernessToEverness', 'NTE', '异环', '異環')
GAME_TOKENS = ('neverness', 'ntegame', 'nteglobalgame', 'ntetwgame', 'ntelauncher', '异环', '異環')


@dataclass(frozen=True)
class Installation:
    root: Path
    source: str
    launcher: str = ''


def normalize_game_root(value):
    """Accept the install folder, a client subfolder, or an executable path."""
    value = os.path.expandvars(str(value or '').strip().strip('"'))
    if not value:
        return None
    match = re.match(r'^(.*?\.exe)(?:".*|,\s*-?\d+|\s+[-/].*)?$', value, re.I)
    path = Path(match[1] if match else value).expanduser()
    try:
        if not path.is_absolute():
            return None
        for candidate in (path, *path.parents):
            client = candidate / GAME_DIRECTORY
            if not (client / 'Binaries/Win64/HTGame.exe').is_file():
                continue
            if any((client / 'Content/Paks').rglob('*.pak')):
                return candidate.resolve()
    except (OSError, ValueError):
        pass
    return None


def launcher_hints(value):
    """Read only installation-path fields from launcher settings."""
    path = Path(str(value).strip('"'))
    base = path.parent if path.suffix.lower() == '.exe' else path
    for folder in (base, base / 'NTELauncher'):
        config = folder / 'UserData/Config/Config.ini'
        try:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read(config, encoding='utf-8-sig')
            for section in parser.sections():
                for key in ('gamepath', 'installpath', 'installlocation', 'clientpath', 'gameroot'):
                    value = parser.get(section, key, fallback='').strip()
                    if value:
                        hint = Path(os.path.expandvars(value.strip('"')))
                        yield hint if hint.is_absolute() else folder / hint
        except (OSError, UnicodeError, configparser.Error):
            continue


def registry_hints():
    import winreg
    key_path = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, index)) as item:
                                values = {}
                                for field in ('DisplayName', 'InstallLocation', 'DisplayIcon', 'UninstallString'):
                                    try:
                                        values[field] = str(winreg.QueryValueEx(item, field)[0])
                                    except OSError:
                                        pass
                                if any(token in ' '.join(values.values()).lower() for token in GAME_TOKENS):
                                    for field in ('InstallLocation', 'DisplayIcon', 'UninstallString'):
                                        if values.get(field):
                                            yield values[field], 'Windows 安装记录'
                        except OSError:
                            continue
            except OSError:
                continue


def shortcut_hints():
    import pythoncom
    from win32com.client import Dispatch
    pythoncom.CoInitialize()
    try:
        shell = Dispatch('WScript.Shell')
        for name in ('Desktop', 'AllUsersDesktop', 'StartMenu', 'AllUsersStartMenu'):
            folder = Path(shell.SpecialFolders(name))
            try:
                for shortcut in folder.rglob('*.lnk'):
                    label = shortcut.stem.lower()
                    if any(token in label for token in (*GAME_TOKENS, 'nte')):
                        try:
                            yield shell.CreateShortcut(str(shortcut)).TargetPath, '游戏快捷方式'
                        except Exception:
                            continue
            except OSError:
                continue
    finally:
        shell = None
        pythoncom.CoUninitialize()


def common_hints():
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for index in range(26):
        if not mask & (1 << index):
            continue
        drive = Path(chr(65 + index) + ':\\')
        if ctypes.windll.kernel32.GetDriveTypeW(str(drive)) != 3:
            continue
        for folder in ('', 'Games', 'Game', '游戏', 'Program Files', 'Program Files (x86)',
                       'WeGameApps', 'SteamLibrary/steamapps/common', 'Steam/steamapps/common'):
            for name in GAME_NAMES:
                yield drive / folder / name, '常用游戏目录'


def installation_hints(config, progress):
    from capture_windows import list_game_windows
    progress('正在检查游戏窗口与安装记录…')
    try:
        for window in list_game_windows():
            if getattr(window, 'process_path', ''):
                yield window.process_path, '正在运行的游戏或启动器'
    except (OSError, RuntimeError):
        pass
    if config.get('launcher_path'):
        yield config['launcher_path'], '已保存的启动器'
    yield from registry_hints()
    progress('正在检查游戏快捷方式与常用安装目录…')
    try:
        yield from shortcut_hints()
    except Exception:
        # COM may be unavailable in a restricted Windows session.
        pass
    yield from common_hints()


def discover_installation(config, *, game_root=None, progress=lambda message: None):
    if game_root is not None:
        root = normalize_game_root(game_root)
        if root is None:
            raise ValueError('所选目录中未找到完整的异环客户端，请选择包含 Client 的游戏安装目录')
        return installation(root, '手动选择')
    root = normalize_game_root(config.get('nte_hybrid', {}).get('game_root'))
    if root:
        return installation(root, '已保存的游戏目录')
    found = {}
    for hint, source in installation_hints(config, progress):
        for value in (hint, *launcher_hints(hint)):
            root = normalize_game_root(value)
            if root:
                found.setdefault(root, installation(root, source))
    if len(found) == 1:
        return next(iter(found.values()))
    if len(found) > 1:
        paths = '、'.join(str(path) for path in found)
        raise ValueError('找到多个异环安装，请点击“选择目录”指定使用哪一份。已找到 ' + paths)
    raise ValueError('尚未找到完整的异环客户端，请安装游戏后重试，或点击“选择目录”指定安装位置')


def installation(root, source):
    launcher = next((str(path) for folder in (root / 'NTELauncher', root)
                     for name in LAUNCHER_NAMES if (path := folder / name).is_file()), '')
    return Installation(root, source, launcher)
