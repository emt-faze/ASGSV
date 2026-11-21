"""Read-only local inventory and integrity-checked, pinned public table cache."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from http.client import HTTPException
from pathlib import Path, PurePosixPath
import re
import threading
import subprocess
from time import monotonic
from urllib.error import HTTPError
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

# Size, upstream Git blob and SHA-256 of the exact bytes at COMMIT.
# Keeping these with the code lets a complete cache work without GitHub.
PINNED_FILES = {
    'flow': (1233731, '6fd46dc5e4703f4db05f649aaafd63c004f926cd',
        '88830564a2168e0282003da0a1263ee3dcb94e61c75674c151bfef656899ec44'),
    'states': (54736883, '4f9d7a3b838e8bcd50ca799cd7b3a287cb673d3c',
        '41fa2d80b7791b56c62a02b8d47f90f7e9ce196a5929b86030d7c277cacd099e'),
    'speakers': (2777398, '524b5c3eb36a1e1d923c262f75079c8c7acfe858',
        '425ef12e4971cefc5b377eabee05d0d9deae59252dfbee018c9f73cba5db8d44'),
    'speaker_text': (461506, '48d217203084d9fe60150264e982070b93a591d8',
        '790306a2dfc8ef7057a20d08bdf09772f6145d04d859d551d3425b5629e05e83'),
    'text': (49257310, 'cce000dd84e30a595eddc077942ba9fd377362e2',
        '99f8cfb17ebcde84ffbd7ffa8b0a567f2fe49f74d06cfe863d7e3b352331a95f'),
    'audio': (14542985, 'd6b247a66eb1fbbfbd1f75711afee217443756b7',
        '61c2d03c4074366b0854b5a16d8062acf44e17fe803751f17910d527f3110afc'),
    'quests': (3028028, '7941f2c4cb33b759ee60453efbe3eb0ba82c2f51',
        'a9ba95fbdf3a5262cebcd95f64b5300e2bcab927b6ad8fe288ccce1090da26da'),
    'quest_nodes': (28006599, 'bdc75d10a569e135042b84d600d75711905852f3',
        'ee402fca8f7103c213fbf74b6a02a7f7012eed607c4aa6d1a481bc228d513142'),
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


class _TransferError(Exception):
    """A failed source can be retried without discarding verified files."""


def _check_stop(stop):
    if stop.is_set():
        raise InterruptedError('已停止下载；再次更新会从已保存的位置继续')


def _sources(remote, size):
    yield 'GitHub Raw', f'https://raw.githubusercontent.com/{REPOSITORY}/{COMMIT}/{remote}'
    if size <= 20 * 1024 * 1024:
        yield 'jsDelivr', f'https://cdn.jsdelivr.net/gh/{REPOSITORY}@{COMMIT}/{remote}'
    yield 'GitHub API', f'https://api.github.com/repos/{REPOSITORY}/contents/{remote}?ref={COMMIT}'


def _transfer(url, partial, size, stop, report_progress):
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {'User-Agent': 'ASGSV-WuWa-local', 'Accept-Encoding': 'identity',
               'Accept': 'application/vnd.github.raw+json' if url.startswith('https://api.github.com/')
               else 'application/octet-stream'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20)
    except HTTPError as exc:
        exc.close()
        raise _TransferError(f'HTTP {exc.code}') from exc
    except (OSError, HTTPException) as exc:
        raise _TransferError(f'连接失败或超时（{exc}）') from exc

    with response:
        if response.status == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if (not match or int(match[1]) != offset or int(match[3]) != size
                    or not offset <= int(match[2]) < size):
                raise _TransferError('服务器返回了错误的续传位置')
            position = offset
            end = int(match[2]) + 1
        elif response.status == 200:
            # Some CDNs ignore Range. Consume the prefix, retaining the on-disk
            # progress even if the new connection dies before it catches up.
            position, end = 0, size
        else:
            raise _TransferError(f'意外的 HTTP {response.status}')
        if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
            raise _TransferError('服务器返回了不支持续传的压缩数据')
        with partial.open('ab') as output:
            while True:
                _check_stop(stop)
                try:
                    chunk = response.read1(256 * 1024)
                except (OSError, HTTPException) as exc:
                    raise _TransferError(f'读取中断或超时（{exc}）') from exc
                if not chunk:
                    break
                after = position + len(chunk)
                if after > end:
                    raise _TransferError('返回内容超过锁定版本的文件长度')
                if after > offset:
                    output.write(chunk[max(0, offset - position):])
                    report_progress(after)
                position = after
        if position != size or position != end:
            raise _TransferError('连接提前结束')


def _download_table(key, remote, path, stop, progress):
    size, _, sha256 = PINNED_FILES[key]
    if path.is_file() and path.stat().st_size == size and file_digest(path) == sha256:
        progress(f'复用已校验的 {key}')
        return
    part = path.with_suffix('.part')
    sources = list(_sources(remote, size))
    last_error = ''
    for attempt, (label, url) in enumerate(sources * 2, 1):
        _check_stop(stop)
        if part.exists() and part.stat().st_size >= size:
            if part.stat().st_size == size and file_digest(part) == sha256:
                part.replace(path)
                return
            part.unlink()
        offset = part.stat().st_size if part.exists() else 0
        progress(f'下载 {key} · {label} · {offset / size:.0%}（第 {attempt}/{2 * len(sources)} 次）')
        last_update = monotonic()

        def report_progress(count):
            nonlocal last_update
            now = monotonic()
            if now - last_update >= 0.8 or count == size:
                progress(f'下载 {key} · {label} · {count / size:.0%}（{count / 1048576:.1f}/{size / 1048576:.1f} MiB）')
                last_update = now

        try:
            _transfer(url, part, size, stop, report_progress)
            _check_stop(stop)
            if file_digest(part) != sha256:
                part.unlink()
                raise _TransferError('SHA-256 校验不匹配')
            part.replace(path)
            return
        except _TransferError as exc:
            last_error = f'{label}：{exc}'
            progress(f'{key} · {last_error}')
            if attempt == len(sources) and stop.wait(1):
                _check_stop(stop)
    raise ValueError(f'{key} 下载未完成（{last_error}）。检查网络后再次点击「更新台词索引」可续传。')


def download_tables(root=WUWA_ROOT, stop=None, progress=print):
    from PySide6.QtCore import QLockFile
    stop = stop or threading.Event()
    folder = safe_output(Path(root) / 'sources' / COMMIT)
    folder.mkdir(parents=True, exist_ok=True)
    guard = QLockFile(str(folder / 'download.lock'))
    guard.setStaleLockTime(0)
    if not guard.tryLock(0):
        if guard.error() == QLockFile.LockError.LockFailedError:
            raise ValueError('另一个窗口正在更新台词索引，请稍后再试')
        raise OSError(f'无法锁定台词缓存目录，请检查写入权限：{folder}')
    try:
        report = dict(SOURCE, files={})
        for key, remote in TABLES.items():
            _check_stop(stop)
            _download_table(key, remote, folder / (key + '.json'), stop, progress)
            size, blob, sha256 = PINNED_FILES[key]
            report['files'][key] = dict(path=remote, sha256=sha256, git_blob=blob, bytes=size)
        _check_stop(stop)
        atomic_text(folder / 'source.json', json.dumps(report, ensure_ascii=False, indent=2))
        return folder
    finally:
        guard.unlock()


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
