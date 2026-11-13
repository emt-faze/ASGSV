"""Bounded read-only local dialogue evidence, independent of catalog approval."""
import argparse
import json
from pathlib import Path
import subprocess

from dialogue_generation import atomic_text, file_digest
from game_profiles import WUWA_ROOT
from wuwa_npc_audio import inventory_archives, archive_stats


def valid_snapshot(data, request, current, mounts):
    """Legacy boolean-only reports cannot establish which archives were read."""
    wanted = set(request.get('archives', []))
    expected = [s for s in current if s['path'] in wanted]
    return bool(wanted) and len(expected) == len(wanted) and not any(s.get('missing') for s in expected) and (
        data.get('archive_metadata_unchanged') is True
        and data.get('manifests') == mounts
        and data.get('archive_stats_before') == expected
        and data.get('archive_stats_after') == expected)


def inspect(queries, exports=(), archive_paths=()):
    dg = WUWA_ROOT.parents[1]
    archives, mounts = inventory_archives(r'F:\Wuthering Waves')
    if archive_paths:
        selected = {str(Path(p).resolve()) for p in archive_paths}
        if not selected.issubset({a['path'] for a in archives}):
            raise ValueError('Archive is not in the local mounted inventory')
        archives = [a for a in archives if a['path'] in selected]
    before = archive_stats(archives)
    folder = WUWA_ROOT / 'sources/local-dialogue'
    folder.mkdir(parents=True, exist_ok=True)
    key = WUWA_ROOT / 'npc-audio/key-sources/2ef2b5679cede4666e832b63e121d36bec38a6a0.json'
    if file_digest(key) != 'e98860de6eadad8f9c6e239561ff24d6cc3fb7478ed9c928a57e2a789f03221d':
        raise ValueError('Pinned key source changed')
    request = dict(game_id='wuwa', archives=[a['path'] for a in archives],
                   queries=list(queries), exports=list(exports), key_file=str(key))
    from dialogue_generation import digest
    identity = digest(dict(snapshot_schema=2, request=request, archive_stats=before,
                           binary=file_digest(dg / 'tools/npc-audio-probe/bin/Release/net10.0/NpcProbe.dll')))
    req = folder / (identity + '.request.json')
    result_path = folder / (identity + '.result.json')
    if result_path.exists():
        cached = json.loads(result_path.read_text(encoding='utf-8'))
        if valid_snapshot(cached, request, before, mounts):
            return result_path
        raise ValueError('Cached inspection snapshot is invalid; preserved for diagnosis')
    atomic_text(req, json.dumps(request, ensure_ascii=False, indent=2))
    result = subprocess.run([str(dg / 'tools/dotnet10/dotnet.exe'),
        str(dg / 'tools/npc-audio-probe/bin/Release/net10.0/NpcProbe.dll'),
        '--dialogue-inspect', str(req)], capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=240, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(result.stdout[-1500:])
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    after = archive_stats(archives)
    _, after_mounts = inventory_archives(r'F:\Wuthering Waves')
    payload.update(overlay=archives, manifests=mounts, archive_stats_before=before,
                   archive_stats_after=after,
                   archive_metadata_unchanged=before == after and mounts == after_mounts)
    if not payload['archive_metadata_unchanged']:
        raise ValueError('Resources changed during inspection')
    atomic_text(result_path, json.dumps(payload, ensure_ascii=False, indent=2))
    return result_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--query', action='append', default=[])
    parser.add_argument('--export', action='append', default=[])
    parser.add_argument('--archive', action='append', default=[])
    args = parser.parse_args()
    print(inspect(args.query, args.export, args.archive))
