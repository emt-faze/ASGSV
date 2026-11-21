"""Find a playable WuWa installation and validate the active mount targets."""
import ctypes
import json
import os
from pathlib import Path
from game_profiles import wuwa_client
import re
from wuwa_resources import mount_entries, overlay_order

GAME = 'Wuthering Waves Game'
EXE = Path('Client/Binaries/Win64/Client-Win64-Shipping.exe')
COMMON = ('Wuthering Waves','Wuthering Waves Game','鸣潮','Kuro Games','KuroGame',
          'Program Files','Program Files (x86)','Games','Game','WeGameApps','SteamLibrary/steamapps/common')
MATCH = re.compile(r'wuthering|鸣潮|kuro|库洛',re.I)


def drive_roots():
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [Path(chr(65+i)+':/') for i in range(26) if mask & (1<<i)
            and ctypes.windll.kernel32.GetDriveTypeW(chr(65+i)+':\\') in (2,3,4,6)]


def registry_paths():
    import winreg as reg
    for hive in (reg.HKEY_CURRENT_USER,reg.HKEY_LOCAL_MACHINE):
        for view in (reg.KEY_WOW64_32KEY,reg.KEY_WOW64_64KEY):
            try:
                parent=reg.OpenKey(hive,r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',0,reg.KEY_READ|view)
            except FileNotFoundError:
                continue
            with parent:
                for i in range(reg.QueryInfoKey(parent)[0]):
                    try:
                        with reg.OpenKey(parent,reg.EnumKey(parent,i)) as key:
                            values={k:v for k,v,_ in (reg.EnumValue(key,j) for j in range(reg.QueryInfoKey(key)[1]))}
                        if MATCH.search(str(values.get('DisplayName',''))):
                            for field in ('InstallLocation','DisplayIcon','UninstallString'):
                                value=values.get(field)
                                if isinstance(value,str) and value:
                                    m=re.match(r'(?:"([^"]+)"|(.+?\.exe)(?:,|\s|$))',value,re.I)
                                    path=Path(os.path.expandvars((m[1] or m[2]) if m else value))
                                    yield path.parent if path.suffix.lower()=='.exe' else path
                    except (FileNotFoundError,PermissionError):
                        continue


def config_paths(folder):
    files=[folder/'launcherDownloadConfig.json',folder/'launcherDownload/launcherDownloadConfig.json',
           folder/'kr_game_cache/KRDownloadRecordCache.json',folder/'config.ini',
           folder/'kr_game_cache/Extend_command_cache.json',*folder.glob('kr_game_cache/*/KRDownloadRecordCache.json')]
    def values(obj):
        if isinstance(obj,dict):
            for value in obj.values():
                yield from values(value)
        elif isinstance(obj,list):
            for value in obj:
                yield from values(value)
        elif isinstance(obj,str) and re.match(r'^[A-Za-z]:[\\/]',obj):
            yield Path(obj)
    for file in files:
        if not file.is_file() or file.stat().st_size>2_000_000:
            continue
        raw=file.read_text(encoding='utf-8-sig',errors='replace')
        if file.suffix=='.ini':
            for line in raw.splitlines():
                yield from values(line.partition('=')[2].strip().strip('"'))
        else:
            try:
                yield from values(json.loads(raw))
            except json.JSONDecodeError:
                continue  # Other installation sources are still checked.


def validate_install(path):
    root=Path(path)
    if root.name.casefold()==GAME.casefold():
        root=root.parent
    game=wuwa_client(root).parent
    if not (game/EXE).is_file() or not (game/EXE).stat().st_size:
        raise ValueError('缺少鸣潮游戏程序')
    paks=[p for p in (game/'Client/Content/Paks').glob('*.pak') if p.stat().st_size]
    if not paks:
        raise ValueError('缺少有效基础资源包')
    mounts=list((game/'Client/Saved/Resources').glob('*/Mount/MountResource.txt'))
    if not mounts:
        return root.resolve(), f'{len(paks)} 个基础包，当前没有热更新清单'
    checked=0
    for m in mounts:
        entries=overlay_order(mount_entries(m.read_text(encoding='utf-8-sig')))
        if not entries:
            raise ValueError('资源挂载清单为空')
        for e in entries:
            p=m.parent.parent/e['path']
            if not p.suffix:
                p=p.with_suffix('.pak')
            if not p.is_file() or not p.stat().st_size:
                raise ValueError('挂载资源缺失 · '+p.name)
            checked+=1
    return root.resolve(),f'{len(paks)} 个基础包、{checked} 个挂载文件'


def detect_install(preferred,stop,progress):
    import psutil
    drives=drive_roots()
    progress('扫描盘符 '+'、'.join(d.drive for d in drives)+'，检查注册表、游戏进程和启动器配置')
    candidates=[]
    for proc in psutil.process_iter(['name','exe'],ad_value=None):
        if proc.info['name']==EXE.name and proc.info['exe']:
            candidates.append((Path(proc.info['exe']).parents[3],'运行中的游戏'))
    if preferred:
        candidates.append((Path(preferred),'已配置目录'))
    candidates.extend((p,'注册表') for p in registry_paths())
    for d in drives:
        for name in COMMON:
            p=d/name
            if p.is_dir():
                candidates.append((p,'常见安装位置'))
                candidates.extend((q,'常见安装位置') for q in p.iterdir() if MATCH.search(q.name) and q.is_dir())
    for path,_ in candidates.copy():
        candidates.extend((p,'启动器配置') for p in config_paths(path))
    seen=set()
    invalid=[]
    def check(p,source):
        if stop.is_set():
            raise InterruptedError('已取消游戏目录探测')
        root=p.parent if p.name.casefold()==GAME.casefold() else p
        if root in seen or not (wuwa_client(root).parent/EXE).is_file():
            return None
        seen.add(root)
        try:
            root,details=validate_install(root)
        except ValueError as exc:
            invalid.append(f'{root} · {exc}')
            return None
        return str(root),source,details
    for path,source in candidates:
        result=check(path,source)
        if result:
            return result
    # A custom installation may sit at any depth; skip Windows internals and junctions.
    denied=[]
    for drive in drives:
        progress('正在搜索自定义安装位置 · '+drive.drive)
        for folder,dirs,_ in os.walk(drive,onerror=lambda exc:denied.append(str(exc.filename)) if len(denied)<5 else None):
            if stop.is_set():
                raise InterruptedError('已取消游戏目录探测')
            path=Path(folder)
            if GAME in dirs or ('Client' in dirs and (path/EXE).is_file()):
                result=check(path,'盘符扫描')
                if result:
                    return result
            dirs[:]=[n for n in dirs if n.casefold() not in {'windows','$recycle.bin','system volume information','.git','winsxs'}
                     and not (path/n).is_junction()]
    detail='；'.join(invalid[:3]) or ('部分目录无读取权限' if denied else '没有找到游戏程序')
    raise ValueError(detail+'。可点击「手动选择」指定启动器根目录。')

