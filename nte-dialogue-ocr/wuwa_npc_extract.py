"""Extract bounded original NPC audio candidates using locally supplied public keys."""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave

import wuwa_npc_audio as npc
from game_profiles import WUWA_ROOT, DEFAULT_INSTALL

KEY_FILE = WUWA_ROOT / 'npc-audio/key-sources/2ef2b5679cede4666e832b63e121d36bec38a6a0.json'
KEY_HASH = 'e98860de6eadad8f9c6e239561ff24d6cc3fb7478ed9c928a57e2a789f03221d'


def decode_candidate(raw, cache, tools, ffmpeg, ffprobe):
    try:
        return npc.decode_audio(raw, cache, ffmpeg, ffprobe)
    except subprocess.CalledProcessError:
        pass
    decoder = tools / 'vgmstream-r2168/vgmstream-cli.exe'
    dependencies = sorted(decoder.parent.glob('*.dll')) + [decoder]
    recipe = dict(raw_sha256=npc.sha256(raw), decoder='vgmstream-r2168',
                  binaries={p.name: npc.sha256(p) for p in dependencies}, loop=False)
    key = npc.digest(recipe)
    cache.mkdir(parents=True, exist_ok=True)
    wav, metadata = cache / (key + '.wav'), cache / (key + '.json')
    if wav.exists() and metadata.exists():
        saved = json.loads(metadata.read_text(encoding='utf-8'))
        if saved.get('recipe') != recipe or saved.get('wav_sha256') != npc.sha256(wav):
            raise ValueError('Decoded cache integrity mismatch; retained')
        return dict(saved, path=str(wav), reused=True)
    fd, temporary = tempfile.mkstemp(suffix='.wav', dir=cache)
    os.close(fd)
    try:
        result = subprocess.run([str(decoder), '-i', '-o', temporary, str(raw)], check=True,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
            creationflags=npc.CREATE_NO_WINDOW)
        with wave.open(temporary, 'rb') as stream:
            if stream.getsampwidth() != 2 or not stream.getnframes():
                raise ValueError('Decoded WAV is not nonempty PCM16')
            seconds = stream.getnframes() / stream.getframerate()
            frames, rate, channels = stream.getnframes(), stream.getframerate(), stream.getnchannels()
        subprocess.run([str(ffmpeg), '-nostdin', '-v', 'error', '-i', temporary, '-f', 'null', '-'],
            check=True, capture_output=True, timeout=90, creationflags=npc.CREATE_NO_WINDOW)
        wav_hash = npc.sha256(temporary)
        if wav.exists() and npc.sha256(wav) != wav_hash:
            raise ValueError('Uncommitted WAV differs; retained for review')
        if not wav.exists():
            os.replace(temporary, wav)
        saved = dict(recipe=recipe, raw_sha256=recipe['raw_sha256'], wav_sha256=wav_hash,
            seconds=seconds, samples=frames, sample_rate=rate, channels=channels,
            decoder_report=result.stdout)
        npc.atomic_json(metadata, saved)
        return dict(saved, path=str(wav), reused=False)
    finally:
        Path(temporary).unlink(missing_ok=True)


def choose_candidates(references, transcripts):
    candidates = []
    for sid in npc.ROSTER:
        rows = [r for r in references if r['speaker_id'] == sid and r['status'] == 'local_resource_found']
        # This estimates which complete utterances to inspect, never their actual duration.
        rows.sort(key=lambda r: (abs(len(re.sub(r'\s|<[^>]*>', '', transcripts[r['id']])) - 40), r['id']))
        seen = set()
        for row in rows:
            resource = row['resources'][0]
            identity = (resource['archive'], resource['virtual_path'])
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(row)
            if len(seen) == 3:
                break
    return candidates


def export_raw(candidates, key_file, run_dir, tools):
    entries = [dict(archive=r['resources'][0]['archive'], virtual_path=r['resources'][0]['virtual_path'],
                    filename=r['filename']) for r in candidates]
    request = run_dir / 'export-request.json'
    npc.atomic_json(request, dict(game_id='wuwa', key_file=str(key_file), entries=entries))
    result = subprocess.run([str(tools / 'dotnet10/dotnet.exe'),
        str(tools / 'npc-audio-probe/bin/Release/net10.0/NpcProbe.dll'), '--npc-export', str(request)],
        capture_output=True, text=True, encoding='utf-8', timeout=180, creationflags=npc.CREATE_NO_WINDOW,
        env=dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_CLI_HOME=str(tools / 'dotnet-home')))
    (run_dir / 'export-stderr.log').write_text(result.stderr, encoding='utf-8')
    data = json.loads(result.stdout.strip().splitlines()[-1])
    if result.returncode or data.get('game_id') != 'wuwa':
        raise RuntimeError('Raw extraction failed: ' + str(data))
    npc.atomic_json(run_dir / 'raw-export.json', data)
    return {(r['archive'], r['virtual_path']): r for r in data['results']}


def review_page(run_dir, report, transcripts):
    labels = {'decoded_candidate': '\u5df2\u89e3\u7801\uff0c\u8eab\u4efd\u4e0e\u53f0\u8bcd\u5f85\u8bd5\u542c\u786e\u8ba4',
        'decode_failed': '\u89e3\u7801\u53d7\u963b', 'extraction_failed': '\u63d0\u53d6\u5931\u8d25',
        'duration_out_of_range': '\u5b8c\u6574\u53e5\u65f6\u957f\u4e0d\u57283\u81f330\u79d2\u5185'}
    body = '<h1>NPC \u539f\u58f0\u8bd5\u542c</h1><p>\u539f\u59cb\u6e38\u620f\u97f3\u9891\uff0c\u975eAI\u751f\u6210\u3002\u89d2\u8272\u5bf9\u5e94\u4e0e\u53f0\u8bcd\u4ecd\u5f85\u4f60\u786e\u8ba4\uff0c\u5c1a\u672a\u5efa\u7acb\u58f0\u7ebf\u3002</p>'
    for sid, name in npc.ROSTER.items():
        body += '<section><h2>' + html.escape(name) + '</h2>'
        rows = [r for r in report['auditions'] if r['speaker_id'] == sid]
        for index, row in enumerate(rows, 1):
            body += '<article><h3>' + str(index) + ' · ' + html.escape(labels.get(row['status'], row['status'])) + '</h3>'
            if row['status'] == 'decoded_candidate':
                relative = os.path.relpath(row['wav_path'], run_dir).replace('\\', '/')
                body += '<audio controls preload="none" src="' + html.escape(relative, quote=True) + '"></audio>'
                body += '<p>' + str(round(row['seconds'], 2)) + ' s</p>'
            body += '<details><summary>\u53f0\u8bcd</summary><p>' + html.escape(transcripts[row['id']]) + '</p></details>'
            body += '</article>'
        body += '</section>'
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NPC audio auditions</title>'
    page += '<style>body{font:16px system-ui;max-width:960px;margin:24px auto;padding:0 16px;color:#202124;background:white}h1{font-size:24px}h2{font-size:20px}h3{font-size:16px}section{border-top:1px solid #ddd;padding:16px 0}article{margin-bottom:20px}audio{width:100%;max-width:500px}p{overflow-wrap:anywhere}summary{cursor:pointer}</style>'
    page += body + '</html>'
    (run_dir / 'review.html').write_text(page, encoding='utf-8')


def run(key_file=KEY_FILE):
    root, output = WUWA_ROOT, WUWA_ROOT / 'npc-audio'
    tools = root.parents[1] / 'tools'
    key_file = Path(key_file).resolve()
    if npc.sha256(key_file) != KEY_HASH:
        raise ValueError('Pinned public key-source integrity mismatch')
    review = root / 'reports/task-review-results-20261104.json'
    references, transcripts, scopes, source = npc.load_references(root, review)
    all_archives, manifests = npc.inventory_archives(DEFAULT_INSTALL)
    archives = [a for a in all_archives if a['language'] == 'zh']
    before = npc.protected_snapshot(root)
    before_stats = npc.archive_stats(all_archives)
    identity = dict(game_id='wuwa', references=[(r['id'], r['filename']) for r in references],
        manifest=manifests, archives=npc.archive_stats(archives), key_source_sha256=KEY_HASH,
        probe_sha256=npc.sha256(tools / 'npc-audio-probe/bin/Release/net10.0/NpcProbe.dll'),
        script_sha256=npc.sha256(Path(__file__)), pipeline_sha256=npc.sha256(Path(npc.__file__)))
    run_id = npc.digest(identity)
    run_dir = output / 'runs' / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    report = dict(game_id='wuwa', run_id=run_id, identity=identity, references=references,
        auditions=[], status='running', public_version=source['resource_version'], source_versions_equivalent=False,
        generation_performed=False, cloning_performed=False, key_source_commit='2ef2b5679cede4666e832b63e121d36bec38a6a0')
    npc.atomic_json(run_dir / 'checkpoint.json', report)
    try:
        diagnostics = npc.run_probe(archives, references, run_dir, tools, key_file)
        for row in references:
            row.update(npc.choose_resource(row, archives, diagnostics))
        candidates = choose_candidates(references, transcripts)
        report['matched_references'] = sum(r['status'] == 'local_resource_found' for r in references)
        print('Matched references:', report['matched_references'], '; selected raw candidates:', len(candidates), flush=True)
        exported = export_raw(candidates, key_file, run_dir, tools)
        ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
        if not ffmpeg or not ffprobe:
            raise ValueError('FFmpeg and FFprobe are required')
        for row in candidates:
            resource = row['resources'][0]
            raw = exported[(resource['archive'], resource['virtual_path'])]
            candidate = dict(id=row['id'], speaker_id=row['speaker_id'], speaker=row['speaker'],
                plot_key=row['plot_key'], local_identity_verified=False,
                identity_basis='Pinned public speaker mapping plus exact current Chinese resource path; listening review pending', **raw)
            if raw['status'] == 'raw_extracted':
                try:
                    decoded = decode_candidate(raw['raw_path'], output / 'wav-cache', tools, ffmpeg, ffprobe)
                    candidate.update(wav_path=decoded['path'], wav_sha256=decoded['wav_sha256'],
                        seconds=decoded['seconds'], decode_reused=decoded['reused'],
                        status='decoded_candidate' if npc.audition_rank(decoded['seconds']) is not None else 'duration_out_of_range')
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    candidate.update(status='decode_failed', error=str(exc))
            report['auditions'].append(candidate)
            npc.atomic_json(run_dir / 'checkpoint.json', report)
        report['status'] = 'auditions_ready_for_review' if any(r['status'] == 'decoded_candidate' for r in report['auditions']) else 'audio_decode_blocked'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyError) as exc:
        report.update(status='failed', error=str(exc))
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
    finally:
        if report['status'] == 'running':
            report['status'] = 'failed_before_completion'
        after, after_stats = npc.protected_snapshot(root), npc.archive_stats(all_archives)
        report['verification'] = dict(protected_content_unchanged=before == after, archive_metadata_unchanged=before_stats == after_stats)
        if before != after or before_stats != after_stats:
            report['status'] = 'preservation_conflict'
        npc.atomic_json(run_dir / 'preservation.json', dict(before=before, after=after, archives_before=before_stats, archives_after=after_stats))
        npc.atomic_json(run_dir / 'manifest.json', report)
        npc.atomic_json(run_dir / 'checkpoint.json', dict(run_id=run_id, status=report['status'],
            candidates=len(report['auditions']), decoded=sum(r['status'] == 'decoded_candidate' for r in report['auditions'])))
        npc.atomic_json(run_dir / 'transcripts.json', transcripts)
        review_page(run_dir, report, transcripts)
        review_page(output, report, transcripts)
        npc.atomic_json(output / 'latest.json', dict(run_id=run_id, manifest=str(run_dir / 'manifest.json'), review=str(run_dir / 'review.html'), status=report['status']))
    print(json.dumps(dict(status=report['status'], error=report.get('error'),
        audio_states=dict(Counter(r['status'] for r in report['auditions'])), verification=report['verification'],
        report=str(run_dir / 'review.html')), ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', type=Path, default=KEY_FILE)
    args = parser.parse_args()
    run(args.key_file)
