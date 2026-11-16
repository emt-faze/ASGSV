"""Rebuild the NTE Chinese catalog from this machine's installed game resources."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from nte_catalog import build_catalog, catalog_path, resource_stamp
from nte_install import discover_installation

ROOT = Path(__file__).resolve().parent


def update_catalog(game_root, output, progress=lambda message: None):
    from PySide6.QtCore import QLockFile
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    lease = QLockFile(str(Path(output).parent / 'live.lock'))
    lease.setStaleLockTime(0)
    if not lease.tryLock(0):
        raise ValueError('请先停止异环实时配音，再更新原文库')
    try:
        stamp = resource_stamp(game_root)
        progress('正在加载游戏资源读取组件…')
        runtime = ROOT / 'assets/nte-resource-reader'
        sys.path.insert(0, str(runtime / 'pythonlibs'))
        sys.path.insert(0, str(runtime / 'assemblies'))
        from pythonnet import load
        load('coreclr', runtime_config=str(runtime / 'reader.runtimeconfig.json'),
             dotnet_root=str(runtime / 'dotnet'))
        import clr
        clr.AddReference(str(runtime / 'assemblies/cue4parse.dll'))
        from System import StringComparer
        from System.IO import SearchOption
        from CUE4Parse.FileProvider import DefaultFileProvider
        from CUE4Parse.UE4.Versions import EGame, VersionContainer
        from CUE4Parse.Encryption.Aes import FAesKey
        from CUE4Parse.UE4.Objects.Core.Misc import FGuid
        from CUE4Parse.UE4.Readers import FByteArchive
        from CUE4Parse.UE4.Localization import FTextLocalizationResource
        versions = VersionContainer(EGame.GAME_NevernessToEverness)
        provider = DefaultFileProvider(str(Path(game_root) / 'Client/WindowsNoEditor/HT'),
                                       SearchOption.AllDirectories, versions, StringComparer.OrdinalIgnoreCase)
        try:
            progress('正在读取异环资源包…')
            provider.Initialize()
            provider.SubmitKey(FGuid(0, 0, 0, 0), FAesKey('0x370E80AA7DCA90A938A4D85013C5A16129AB3079902532B3325CA5B72B740FC7'))
            provider.Mount()
            path = 'HT/Content/Localization/Game/zh-CN/game.locres'
            raw = provider.SaveAsset(path)
            resource = FTextLocalizationResource(FByteArchive(path, raw, versions))
            entries = [dict(namespace=str(namespace.Key.Str), key=str(item.Key.Str),
                            text=str(item.Value.LocalizedString))
                       for namespace in resource.Entries for item in namespace.Value]
            progress(f'正在整理 {len(entries):,} 条中文资源并建立台词索引…')
            if resource_stamp(game_root) != stamp:
                raise ValueError('游戏资源正在更新，请更新完成后重试')
            build_catalog(entries, output, dict(resource_stamp=stamp,
                game_root=str(Path(game_root).resolve()),
                localization_sha256=hashlib.sha256(bytes(raw)).hexdigest()))
            return len(entries)
        finally:
            provider.Dispose()
    finally:
        lease.unlock()


def catalog_status(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        metadata = dict(db.execute('SELECT key,value FROM metadata'))
        if metadata.get('game') != 'nte' or metadata.get('schema') != '1':
            raise ValueError('原文库需要重新提取')
        entries, dialogue = db.execute('SELECT count(*),coalesce(sum(dialogue),0) FROM lines').fetchone()
    finally:
        db.close()
    return dict(metadata=metadata, entries=entries, dialogue=dialogue)


def prepare_catalog(config_path, output=None, *, game_root=None, force=False, progress=lambda message: None):
    from dialogue_generation import atomic_text
    config_path = Path(config_path)
    config = json.loads(config_path.read_text('utf-8-sig'))
    found = discover_installation(config, game_root=game_root, progress=progress)
    progress('已找到异环 · ' + str(found.root))
    output = Path(output).resolve() if output else catalog_path(config_path.parent, config)
    stamp = resource_stamp(found.root)
    try:
        status = catalog_status(output)
    except (OSError, ValueError, sqlite3.Error):
        status = dict(metadata={})
    updated = force or status['metadata'].get('resource_stamp') != stamp
    if updated:
        update_catalog(found.root, output, progress)
        status = catalog_status(output)
    # Re-read settings so UI changes made during extraction are retained.
    config = json.loads(config_path.read_text('utf-8-sig'))
    config.setdefault('nte_hybrid', {}).update(game_root=str(found.root), catalog_path=str(output))
    if found.launcher:
        config['launcher_path'] = found.launcher
    atomic_text(config_path, json.dumps(config, ensure_ascii=False, indent=2))
    return dict(game_root=str(found.root), source=found.source, catalog_path=str(output),
                entries=status['entries'], dialogue=status['dialogue'], updated=updated)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--game-root')
    parser.add_argument('--output')
    parser.add_argument('--config', default=str(ROOT / 'config.json'))
    parser.add_argument('--auto', action='store_true')
    parser.add_argument('--ensure', action='store_true')
    args = parser.parse_args()
    def report(**event):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    try:
        if args.auto:
            result = prepare_catalog(args.config, args.output, game_root=args.game_root,
                force=not args.ensure, progress=lambda message: report(progress=message))
        else:
            if not args.game_root:
                raise ValueError('请使用 --auto 自动识别游戏，或提供 --game-root 安装目录')
            result = dict(entries=update_catalog(args.game_root, args.output or ROOT / 'nte-data/catalog.sqlite'))
            print(json.dumps(result), flush=True)
            return 0
        report(result=result)
        return 0
    except Exception as exc:
        report(error=str(exc))
        return 1


if __name__ == '__main__':
    sys.exit(main())
