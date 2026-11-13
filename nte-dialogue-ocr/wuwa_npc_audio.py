"""Read-only NPC resource diagnostics; never promotes public references to local proof."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from game_profiles import DEFAULT_INSTALL, WUWA_ROOT, safe_output
from wuwa_resources import COMMIT, mount_entries, overlay_order

ROSTER = {
    '100038': '\u8fbe\u5a1c', '651': '\u8d1d\u8bd7', '100047': '\u4f0a\u4ec0\u9ea6\u5c14',
    '300108': '\u51ef\u5c14\u6885\u5c14', '100041': '\u6258\u5217\u6258', '100040': '\u4e9a\u54c8',
}
MAX_AUDIO_BYTES = 64 * 1024 * 1024
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def readonly_db(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def allowed_scopes(review, states):
    if review.get('game_id') != 'wuwa':
        raise ValueError('Cross-game task report rejected')
    result = []
    for row in review['tasks']:
        candidates = row.get('candidates', [])
        if (row.get('category') not in ('side', 'activity') or not row.get('progress_saved')
                or len(candidates) != 1):
            continue
        qid = str(candidates[0]['quest_id'])
        if candidates[0].get('progress') == 'completed' or states.get(qid) != 'pending':
            continue
        result.append('quest:' + qid)
    return sorted(set(result))


def load_references(root, review_path):
    review = json.loads(Path(review_path).read_text(encoding='utf-8'))
    folder = root / 'sources' / COMMIT
    source = json.loads((folder / 'source.json').read_text(encoding='utf-8'))
    if source.get('commit') != COMMIT:
        raise ValueError('Source commit mismatch')
    audio_path = folder / 'audio.json'
    if sha256(audio_path) != source['files']['audio']['sha256']:
        raise ValueError('Audio reference table integrity check failed')
    audio = {r['Id']: r for r in json.loads(audio_path.read_text(encoding='utf-8'))}
    with closing(readonly_db(root / 'dialogue.sqlite')) as db:
        account_row = db.execute("SELECT value FROM meta WHERE key='progress_account'").fetchone()
        if not account_row:
            raise ValueError('No bound progress account')
        states = dict(db.execute('SELECT quest_id,status FROM quest_progress WHERE account=?', (account_row[0],)))
        scopes = allowed_scopes(review, states)
        if not scopes:
            raise ValueError('No verified pending non-companion task scopes')
        pictures = {}
        for task in review['tasks']:
            if len(task.get('candidates', [])) != 1 or 'quest:' + str(task['candidates'][0]['quest_id']) not in scopes:
                continue
            evidence = task['evidence']
            picture = (root / evidence['path']).resolve()
            if not picture.is_relative_to((root / 'reports/quest-panel').resolve()):
                raise ValueError('Task screenshot outside the evidence directory')
            if picture not in pictures:
                pictures[picture] = sha256(picture)
            if pictures[picture] != evidence['sha256']:
                raise ValueError('Task screenshot integrity check failed')
        marks, roles = ','.join('?' for _ in scopes), ','.join('?' for _ in ROSTER)
        records = db.execute(f'''SELECT DISTINCT l.* FROM lines l JOIN scope_lines s ON s.line_id=l.id
            WHERE s.scope_id IN ({marks}) AND l.speaker_id IN ({roles})
              AND l.game_id='wuwa' AND l.voice_status='voiced'
            ORDER BY l.speaker_id,l.id''', (*scopes, *ROSTER)).fetchall()
        references, transcripts = [], {}
        for row in records:
            if row['source_id'] != COMMIT or row['speaker'] != ROSTER[row['speaker_id']]:
                raise ValueError('NPC identity or source conflict; no name-based fallback')
            ref = audio.get(row['plot_key'])
            if not ref or not ref.get('FileName'):
                raise ValueError('Catalog reference is absent from the pinned audio table')
            filename = ref['FileName']
            if any(c in filename for c in '/\\\x00') or filename in ('.', '..'):
                raise ValueError('Unsafe audio reference name')
            scopes_for_line = sorted(r[0] for r in db.execute(
                f'SELECT scope_id FROM scope_lines WHERE line_id=? AND scope_id IN ({marks})', (row['id'], *scopes)))
            references.append(dict(id=row['id'], speaker_id=row['speaker_id'], speaker=row['speaker'],
                flow_id=row['flow_id'], state_id=row['state_id'], branch=row['branch'],
                dialogue_id=row['line_id'], text_id=row['text_id'], plot_key=row['plot_key'],
                filename=filename, quest_scopes=scopes_for_line, source_commit=COMMIT,
                public_version=source['resource_version'], public_region=source['region'],
                raw_text_sha256=digest(row['raw_text']), language_parameters={k: ref.get(k) for k in
                    ('GlobalLanguage', 'CheckGenderZh', 'VarParams')}, status='reference_only',
                local_identity_verified=False, resources=[], raw_sha256=None, wav_sha256=None))
            transcripts[row['id']] = row['raw_text']
    return references, transcripts, scopes, source


def inventory_archives(install_root):
    install = Path(install_root).resolve()
    client = install / 'Wuthering Waves Game/Client'
    entries, manifests = {}, []
    for path in sorted((client / 'Content/Paks').glob('*.pak')):
        entries[str(path.resolve())] = dict(path=str(path.resolve()), priority=0, order=0,
            language='unknown', origin='base_directory', overlay_verified=False)
    roots = sorted((client / 'Saved/Resources').glob('*/Mount'))
    if len(roots) != 1:
        raise ValueError('Cannot establish a single active resource manifest root')
    for name, language in (('MountResource.txt', 'unknown'), ('MountLang_zh.txt', 'zh')):
        manifest = roots[0] / name
        text = manifest.read_text(encoding='utf-8-sig')
        parsed = mount_entries(text)
        manifests.append(dict(path=str(manifest), sha256=sha256(manifest), entries=parsed))
        for entry in parsed:
            path = (manifest.parent.parent / (entry['path'] + '.pak')).resolve()
            if not path.is_relative_to(install):
                raise ValueError('Mounted resource escapes game directory')
            if entry['action'] == 'del':
                entries.pop(str(path), None)
        for entry in overlay_order(parsed):
            path = (manifest.parent.parent / (entry['path'] + '.pak')).resolve()
            entries[str(path)] = dict(path=str(path), priority=entry['priority'], order=entry['order'],
                language=language, origin=str(manifest), overlay_verified=True,
                manifest_entry=entry['path'])
    return list(entries.values()), manifests


def archive_stats(archives):
    result = []
    for archive in archives:
        path = Path(archive['path'])
        if path.exists():
            stat = path.stat()
            result.append(dict(path=str(path), bytes=stat.st_size, mtime_ns=stat.st_mtime_ns))
        else:
            result.append(dict(path=str(path), missing=True))
    return result


def protected_snapshot(root):
    """Record content hashes without persisting account IDs or database contents."""
    result = {}
    with closing(readonly_db(root / 'dialogue.sqlite')) as db:
        for table in ('meta', 'lines', 'scopes', 'scope_lines', 'voice_bindings', 'reviews',
                      'quest_progress', 'quest_progress_audit'):
            h, count = hashlib.sha256(), 0
            for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid'):
                h.update(json.dumps(list(row), ensure_ascii=True, separators=(',', ':')).encode())
                h.update(b'\n')
                count += 1
            result['table:' + table] = dict(rows=count, sha256=h.hexdigest())
    dg = root.parents[1]
    files = [root / 'config.json', dg / 'nte-dialogue-ocr/config.json']
    for folder in (dg / 'wuwa-dubber/data/profiles', root / 'npc-voices', dg / 'game-data/nte'):
        if folder.is_dir():
            files.extend(p for p in folder.rglob('*') if p.is_file())
    for path in sorted(set(files)):
        if path.is_file():
            result['file:' + str(path)] = sha256(path)
    return result


def choose_resource(reference, archives, diagnostics):
    """Only a complete Chinese overlay can identify a unique local resource."""
    by_path = {a['path']: a for a in archives}
    matches, blockers = [], []
    for diagnostic in diagnostics:
        archive = by_path[diagnostic['path']]
        if archive['language'] == 'zh' and diagnostic['status'] != 'index_readable':
            blockers.append(dict(path=archive['path'], reason=diagnostic['status']))
        for match in diagnostic.get('matches', []):
            if match['filename'].casefold() != reference['filename'].casefold():
                continue
            matches.append(dict(match, archive=archive['path'], language=archive['language'],
                priority=archive['priority'], order=archive['order'],
                overlay_verified=archive['overlay_verified'],
                stored_index_sha256=diagnostic.get('stored_index_sha256')))
    if blockers:
        return dict(status='blocked', reason='chinese_overlay_unreadable', blockers=blockers, resources=matches)
    if not any(a['language'] == 'zh' for a in archives):
        return dict(status='blocked', reason='chinese_overlay_missing', resources=matches)
    if reference.get('language_parameters', {}).get('CheckGenderZh') or reference.get('language_parameters', {}).get('VarParams'):
        return dict(status='blocked', reason='unresolved_audio_parameters', resources=matches)
    chinese = [m for m in matches if m['language'] == 'zh' and m['overlay_verified']]
    if not chinese:
        return dict(status='reference_only', reason='no_exact_chinese_resource_match', resources=matches)
    # Different virtual paths are not override relationships even if basenames agree.
    paths = {m['virtual_path'].casefold() for m in chinese}
    if len(paths) != 1:
        return dict(status='blocked', reason='ambiguous_resource_paths', resources=chinese)
    latest = max((m['priority'], m['order']) for m in chinese)
    winners = [m for m in chinese if (m['priority'], m['order']) == latest]
    if len(winners) != 1:
        return dict(status='blocked', reason='ambiguous_overlay_priority', resources=chinese)
    match = winners[0]
    if match['encrypted'] and not match.get('key_matched'):
        return dict(status='blocked', reason='encrypted_audio_missing_parameters', resources=[match])
    return dict(status='local_resource_found', reason='local_speaker_and_text_verification_required', resources=[match])


def audition_rank(duration):
    if 5 <= duration <= 15:
        return (0, abs(duration - 9))
    if 3 <= duration <= 30:
        return (1, abs(duration - 9))
    return None


def select_auditions(candidates):
    unique = {}
    for candidate in candidates:
        rank = audition_rank(candidate['seconds'])
        if not candidate.get('local_identity_verified') or rank is None:
            continue
        key = candidate['raw_sha256']
        unique.setdefault(key, (rank, candidate))
    return [row for _, row in sorted(unique.values(), key=lambda v: (v[0], v[1]['raw_sha256']))[:3]]


def decode_audio(raw, cache, ffmpeg, ffprobe):
    """Lossless PCM audition; content-addressed cache is verified on every reuse."""
    raw = Path(raw)
    if not raw.is_file() or raw.stat().st_size > MAX_AUDIO_BYTES:
        raise ValueError('Audio file missing or exceeds extraction limit')
    raw_hash = sha256(raw)
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    recipe = dict(raw_sha256=raw_hash, ffmpeg_sha256=sha256(ffmpeg), format='pcm_s16le', schema=1)
    key = digest(recipe)
    wav, meta = cache / (key + '.wav'), cache / (key + '.json')
    if meta.exists() and wav.exists():
        saved = json.loads(meta.read_text(encoding='utf-8'))
        if saved.get('recipe') == recipe and saved.get('wav_sha256') == sha256(wav):
            return dict(saved, path=str(wav), reused=True)
        raise ValueError('Corrupt audio cache; original files retained')
    if wav.exists():
        raise ValueError('Uncommitted audio exists; original file retained for recovery')
    fd, temporary = tempfile.mkstemp(suffix='.wav', dir=cache)
    os.close(fd)
    try:
        subprocess.run([str(ffmpeg), '-nostdin', '-v', 'error', '-y', '-i', str(raw),
            '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', temporary], check=True,
            capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
        probe = subprocess.run([str(ffprobe), '-v', 'error', '-show_format', '-show_streams',
            '-of', 'json', temporary], check=True, capture_output=True, text=True,
            encoding='utf-8', timeout=30, creationflags=CREATE_NO_WINDOW)
        details = json.loads(probe.stdout)
        if not any(s.get('codec_type') == 'audio' for s in details['streams']):
            raise ValueError('Decoded file has no audio stream')
        seconds = float(details['format']['duration'])
        if seconds <= 0:
            raise ValueError('Decoded file has no duration')
        result = dict(recipe=recipe, raw_sha256=raw_hash, wav_sha256=sha256(temporary), seconds=seconds)
        os.replace(temporary, wav)
        atomic_json(meta, result)
        return dict(result, path=str(wav), reused=False)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run_probe(archives, references, run_dir, tools, key_file=None):
    request = dict(game_id='wuwa', archives=[a['path'] for a in archives],
                   filenames=sorted({r['filename'] for r in references}))
    if key_file:
        request['key_file'] = str(Path(key_file).resolve())
    request_path = run_dir / 'probe-request.json'
    atomic_json(request_path, request)
    command = [str(tools / 'dotnet/dotnet.exe'),
               str(tools / 'cue4parse-probe/bin/Release/net8.0/Probe.dll'), '--npc-diagnose', str(request_path)]
    if key_file:
        command[:2] = [str(tools / 'dotnet10/dotnet.exe'), str(tools / 'npc-audio-probe/bin/Release/net10.0/NpcProbe.dll')]
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',
               DOTNET_CLI_HOME=str(tools / 'dotnet-home'))
    checkpoints = run_dir / 'archive-checkpoints'
    checkpoints.mkdir(exist_ok=True)
    diagnostics = []
    for number, archive in enumerate(archives):
        path = checkpoints / (digest(archive['path']) + '.json')
        signature = digest(dict(archive=archive_stats([archive]), filenames=request['filenames'],
                                probe=sha256(command[1]), key_source=sha256(key_file) if key_file else None))
        saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if saved and saved.get('signature') == signature and saved.get('payload_hash') == digest(saved['result']):
            diagnostics.append(saved['result'])
            continue
        single_request = run_dir / 'probe-current.json'
        atomic_json(single_request, dict(request, archives=[archive['path']]))
        try:
            result = subprocess.run([*command[:-1], str(single_request)], capture_output=True, text=True,
                encoding='utf-8', timeout=90, creationflags=CREATE_NO_WINDOW, env=env)
            with (run_dir / 'probe-stderr.log').open('a', encoding='utf-8') as stream:
                stream.write(result.stderr)
            parsed = json.loads(result.stdout.strip().splitlines()[-1])
            if result.returncode or parsed.get('game_id') != 'wuwa' or len(parsed.get('archives', [])) != 1:
                raise ValueError('Invalid archive diagnostics: ' + result.stdout[-1000:])
            diagnostic = parsed['archives'][0]
            if diagnostic['path'] != archive['path']:
                raise ValueError('Archive diagnostic path mismatch')
        except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
            diagnostic = dict(path=archive['path'], status='probe_failed', error=str(exc), matches=[])
        diagnostics.append(diagnostic)
        if diagnostic['status'] != 'probe_failed':
            atomic_json(path, dict(signature=signature, result=diagnostic, payload_hash=digest(diagnostic)))
        print('Archive', number + 1, '/', len(archives), diagnostic['status'], flush=True)
    atomic_json(run_dir / 'archive-diagnostics.json', dict(schema=1, game_id='wuwa', archives=diagnostics))
    return diagnostics


def write_report(run_dir, report, transcripts):
    summary = []
    for sid, name in ROSTER.items():
        refs = [r for r in report['references'] if r['speaker_id'] == sid]
        summary.append(dict(speaker_id=sid, speaker=name, references=len(refs),
            unique_audio_references=len({r['filename'] for r in refs}),
            states=dict(Counter(r['status'] for r in refs)), auditions=0))
    report['summary'] = summary
    atomic_json(run_dir / 'manifest.json', report)
    atomic_json(run_dir / 'transcripts.json', transcripts)
    labels = {'blocked': '\u89e3\u6790\u53d7\u963b', 'reference_only': '\u4ec5\u6709\u5f15\u7528',
        'local_resource_found': '\u627e\u5230\u8d44\u6e90\uff0c\u8eab\u4efd\u5f85\u6838\u5bf9'}
    rows = ''.join('<tr><td>' + html.escape(r['speaker']) + '</td><td>' + str(r['references']) +
        '</td><td>' + html.escape(', '.join(labels.get(k, k) + ': ' + str(v) for k, v in r['states'].items())) +
        '</td><td>0</td></tr>' for r in summary)
    details = ''.join('<details><summary>' + html.escape(ROSTER[sid]) + '</summary>' + ''.join(
        '<details><summary>' + html.escape(r['plot_key']) + '</summary><p>' + html.escape(transcripts[r['id']]) +
        '</p></details>' for r in report['references'] if r['speaker_id'] == sid) + '</details>' for sid in ROSTER)
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += '<title>NPC audio review</title><style>body{font:16px system-ui;margin:24px auto;padding:0 16px;max-width:980px;color:#202124;background:#fff}table{width:100%;border-collapse:collapse}th,td{padding:10px;text-align:left;border-bottom:1px solid #ddd;overflow-wrap:anywhere}details{margin:12px 0}summary{cursor:pointer}p{overflow-wrap:anywhere}h1{font-size:24px}</style>'
    page += '<h1>NPC \u539f\u58f0\u89e3\u6790</h1><p>\u672c\u6b21\u65e0\u5df2\u9a8c\u8bc1\u53ef\u8bd5\u542c\u97f3\u9891\u3002\u672a\u8fdb\u884c\u914d\u97f3\u751f\u6210\u3002</p>'
    page += '<table><thead><tr><th>NPC</th><th>\u5f15\u7528\u6570</th><th>\u89e3\u6790\u72b6\u6001</th><th>\u8bd5\u542c\u6570</th></tr></thead><tbody>' + rows + '</tbody></table>'
    reasons = sorted({r.get('reason', '') for r in report['references']} - {''})
    page += '<h2>\u963b\u585e\u539f\u56e0</h2><p>' + html.escape(', '.join(reasons)) + '</p>'
    if 'chinese_overlay_unreadable' in reasons:
        page += '<p>\u4e2d\u6587\u8bed\u97f3\u5305\u7d22\u5f15\u672a\u8bfb\u901a\uff0c\u8be6\u60c5\u89c1 <a href="archive-diagnostics.json">\u9010\u5305\u8bca\u65ad</a>\u3002</p>'
    page += '<h2>\u53f0\u8bcd\uff08\u9ed8\u8ba4\u6536\u8d77\uff09</h2>' + details + '</html>'
    (run_dir / 'review.html').write_text(page, encoding='utf-8')


def run(root, install, review_path):
    root, install = Path(root).resolve(), Path(install).resolve()
    output = safe_output(root / 'npc-audio', install)
    output.mkdir(parents=True, exist_ok=True)
    before = protected_snapshot(root)
    references, transcripts, scopes, source = load_references(root, review_path)
    archives, manifests = inventory_archives(install)
    versions = sorted({m.group(1) for manifest in manifests for entry in manifest['entries']
                       if (m := re.match(r'Resource/(\d+\.\d+\.\d+)/', entry['path']))})
    before_stats = archive_stats(archives)
    tools = root.parents[1] / 'tools'
    identity = dict(game_id='wuwa', roster=ROSTER, review_sha256=sha256(review_path),
        sources=source, manifests=manifests, archives=before_stats,
        references=[(r['id'], r['filename']) for r in references],
        probe_sha256=sha256(tools / 'cue4parse-probe/bin/Release/net8.0/Probe.dll'),
        pipeline_sha256=sha256(Path(__file__)))
    run_id = digest(identity)
    run_dir = output / 'runs' / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    report = dict(schema=1, game_id='wuwa', run_id=run_id, started_at=datetime.now(timezone.utc).isoformat(),
        status='running', read_only_game=True, source_versions_equivalent=False,
        public_commit=COMMIT, public_version=source['resource_version'], selected_scopes=scopes,
        local_resource_versions=versions, identity=identity, references=references, generation_performed=False,
        verification_limitation='Public speaker/text mappings are not automatically accepted as current local proof.')
    atomic_json(run_dir / 'checkpoint.json', report)
    try:
        print('Diagnosing', len(archives), 'archives for', len(references), 'NPC references', flush=True)
        diagnostics = run_probe(archives, references, run_dir, tools)
        for row in references:
            row.update(choose_resource(row, archives, diagnostics))
        report['archive_summary'] = dict(Counter(d['status'] for d in diagnostics))
        report['status'] = 'blocked' if any(r['status'] == 'blocked' for r in references) else 'awaiting_local_identity_verification'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report['status'] = 'failed'
        report['error'] = str(exc)
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
    finally:
        after = protected_snapshot(root)
        after_stats = archive_stats(archives)
        unchanged = before == after and before_stats == after_stats
        report['verification'] = dict(protected_content_unchanged=before == after,
            archive_metadata_unchanged=before_stats == after_stats, snapshots='preservation.json')
        atomic_json(run_dir / 'preservation.json', dict(before=before, after=after,
            archives_before=before_stats, archives_after=after_stats))
        if not unchanged:
            report['status'] = 'preservation_conflict'
        write_report(run_dir, report, transcripts)
        atomic_json(run_dir / 'checkpoint.json', dict(run_id=run_id, status=report['status']))
        atomic_json(output / 'latest.json', dict(run_id=run_id, manifest=str(run_dir / 'manifest.json'),
            review=str(run_dir / 'review.html'), status=report['status']))
    print(json.dumps(dict(status=report['status'], summary=report['summary'],
        verification=report['verification'], report=str(run_dir / 'review.html')), ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', type=Path, default=WUWA_ROOT / 'reports/task-review-results-20261104.json')
    args = parser.parse_args()
    run(WUWA_ROOT, DEFAULT_INSTALL, args.review)


if __name__ == '__main__':
    main()
