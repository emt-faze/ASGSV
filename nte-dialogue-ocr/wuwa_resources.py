"""Read-only local inventory and integrity-checked, pinned public table cache."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import threading
import subprocess
import urllib.request

from dialogue_generation import atomic_text, file_digest
from game_profiles import DEFAULT_INSTALL, WUWA_ROOT, safe_output

COMMIT = '9218d612ad815e398e064e577e42aaf878899968'
REPOSITORY = 'Arikatsu/WutheringWaves_Data'
SOURCE = dict(repository=REPOSITORY, commit=COMMIT, region='Global', game_version='3.7.0',
              resource_version='3.7.8', language='zh-Hans', verified_local=False)
TABLES = {
    'flow': 'BinData/flow/flow.json',
    'states': 'BinData/flowState/flowstate.json',
    'speakers': 'BinData/speaker/speaker.json',
    'speaker_text': 'Textmaps/zh-Hans/speaker/Speaker.json',
    'text': 'Textmaps/zh-Hans/multi_text/MultiText.json',
    'audio': 'BinData/plot_audio/plotaudio.json',
    'quests': 'BinData/QuestData/questdata.json',
    'quest_nodes': 'BinData/QuestNodeData/questnodedata.json',
}


def mount_entries(text):
    section = ''
    entries = []
    for line in text.splitlines():
        if line.startswith('::'):
            section = line.strip(':').lower()
        elif line.strip() and section in {'mount', 'del'}:
            fields = next(csv.reader([line]))
            path = PurePosixPath(fields[0])
            if path.is_absolute() or '..' in path.parts or '\\' in str(path):
                raise ValueError('Unsafe mount entry')
            entries.append(dict(path=str(path), priority=int(fields[1]) if len(fields) > 1 and fields[1] else 0,
                                order=len(entries), action=section, hashes=fields[2:4]))
    return entries


def overlay_order(entries):
    # Manifest priority wins; sequence is the tie breaker, never version strings.
    active = {}
    for entry in entries:
        if entry['action'] == 'del':
            active.pop(entry['path'], None)
        else:
            active[entry['path']] = entry
    return sorted(active.values(), key=lambda e: (e['priority'], e['order']))


def inventory(install_root=DEFAULT_INSTALL, output_root=WUWA_ROOT):
    install = Path(install_root).resolve()
    output = safe_output(output_root, install)
    client = install / 'Wuthering Waves Game' / 'Client'
    if not (client / 'Binaries/Win64/Client-Win64-Shipping.exe').is_file():
        raise ValueError('未找到鸣潮实际游戏程序')
    mounts = []
    versions = set()
    for path in sorted((client / 'Saved/Resources').glob('*/Mount/Mount*.txt')):
        if path.name not in {'MountResource.txt', 'MountLang_zh.txt'}:
            continue
        entries = mount_entries(path.read_text(encoding='utf-8-sig'))
        for entry in overlay_order(entries):
            match = re.search(r'^Resource/(\d+\.\d+\.\d+)/', entry['path'])
            if match:
                versions.add(match[1])
        mounts.append(dict(path=str(path), sha256=file_digest(path), entries=entries))
    result = dict(game_id='wuwa', install_root=str(install), resource_versions=sorted(versions),
                  mounts=mounts, base_paks=[dict(path=str(p), bytes=p.stat().st_size)
                    for p in sorted((client / 'Content/Paks').glob('*.pak'))],
                  parser='CUE4Parse 1.2.2', parser_status='not_probed', coverage_verified=False)
    atomic_text(output / 'sources/local-inventory.json', json.dumps(result, ensure_ascii=False, indent=2))
    return result


def fetch_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'ASGSV-WuWa-local'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def git_blob_hash(path):
    h = hashlib.sha1(f'blob {path.stat().st_size}\0'.encode('ascii'))
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def download_tables(root=WUWA_ROOT, stop=None, progress=print):
    stop = stop or threading.Event()
    folder = safe_output(Path(root) / 'sources' / COMMIT)
    folder.mkdir(parents=True, exist_ok=True)
    tree = fetch_json(f'https://api.github.com/repos/{REPOSITORY}/git/trees/{COMMIT}?recursive=1')
    entries = {e['path']: e for e in tree['tree']}
    report = dict(SOURCE, files={})
    for key, remote in TABLES.items():
        if stop.is_set():
            raise InterruptedError('下载已停止；完成的文件将在续跑时复用')
        expected = entries[remote]
        path = folder / (key + '.json')
        if path.exists() and git_blob_hash(path) == expected['sha']:
            progress('复用 ' + key)
        else:
            progress('下载 ' + key)
            temp = path.with_suffix('.part')
            try:
                request = urllib.request.Request(f'https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{remote}',
                                                 headers={'User-Agent': 'ASGSV-WuWa-local'})
                with urllib.request.urlopen(request, timeout=60) as response, temp.open('wb') as output:
                    while chunk := response.read(1024 * 1024):
                        if stop.is_set():
                            raise InterruptedError('下载已停止')
                        output.write(chunk)
                if temp.stat().st_size != expected['size'] or git_blob_hash(temp) != expected['sha']:
                    raise ValueError('固定提交校验不匹配：' + key)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        report['files'][key] = dict(path=remote, sha256=file_digest(path), git_blob=expected['sha'], bytes=path.stat().st_size)
        atomic_text(folder / 'source.json', json.dumps(report, ensure_ascii=False, indent=2))
    return folder


def probe_local(root=WUWA_ROOT, install_root=DEFAULT_INSTALL, progress=print):
    info = inventory(install_root, root)
    client = Path(install_root) / 'Wuthering Waves Game/Client'
    directories = {client / 'Content/Paks'}
    for manifest in info['mounts']:
        base = Path(manifest['path']).parent.parent
        for entry in overlay_order(manifest['entries']):
            directories.add((base / entry['path']).parent)
    tools = Path(__file__).resolve().parent.parent / 'tools'
    command = [str(tools / 'dotnet/dotnet.exe'), str(tools / 'cue4parse-probe/bin/Release/net8.0/Probe.dll')]
    reports = []
    for directory in sorted(directories):
        progress('只读探测 ' + str(directory))
        try:
            result = subprocess.run([*command, str(directory)], capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            data = json.loads(result.stdout.strip().splitlines()[-1])
            reports.append(data)
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
            reports.append(dict(directory=str(directory), status='probe_failed', error=str(exc)))
    info.update(parser_status='partial_indexes_only', coverage_verified=False, probes=reports,
                limitation='索引可读不代表数据关联已验证；未加载档案/自定义数据格式未解析。不能凭缺失文件确认无配音。')
    atomic_text(Path(root) / 'sources/local-probe.json', json.dumps(info, ensure_ascii=False, indent=2))
    return info


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    info = inventory()
    print(json.dumps(dict(resource_versions=info['resource_versions'], base_paks=len(info['base_paks']))))
    if args.download:
        print(download_tables())
    if args.probe:
        result = probe_local()
        print(json.dumps([dict(directory=p['directory'], files=p.get('files'), mounted=p.get('mounted_archives'),
                               unloaded=p.get('unloaded_archives'), status=p['status']) for p in result['probes']]))
