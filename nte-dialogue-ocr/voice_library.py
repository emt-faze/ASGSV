"""Reviewed voice-library drafts, adapted from emt-faze/ASGSV's voice-library tool.

Upstream: tools/voice-library/wuwa_dubber/voice_library.py at
2f51dc9a9decadfc2be68f5c7bf78dc5b74cd811. See voice-library-source.json.
Preparing or transcribing a draft never registers a voice.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from uuid import uuid4
import wave

from dialogue_generation import atomic_text
from generator_voices import VoiceImportTask, ImportCancelled, check_names, load_profiles

AUDIO_EXTENSIONS = frozenset({'.flac', '.wav', '.mp3', '.m4a', '.aac', '.ogg', '.opus'})
VIDEO_EXTENSIONS = frozenset({'.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v', '.ts'})
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
EXCLUDED_DIRECTORIES = frozenset({'.git', '.venv', '__pycache__', 'artifacts', 'assets', 'data',
                                  'logs', 'scripts', 'src', 'tests', 'node_modules', 'backups'})
PREFERRED_DIRECTORIES = ('角色包_人声', '资料', '剧情')


def check_cancel(stop):
    if stop is not None and stop.is_set():
        raise ImportCancelled('已停止，已完成的草稿和声线保留')


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@dataclass(frozen=True)
class CharacterAudioGroup:
    speaker: str
    directory: Path
    files: tuple[Path, ...]


@dataclass(frozen=True)
class AudioCandidate:
    path: Path
    duration_seconds: float
    source_priority: int
    start_seconds: float = 0


def validate_video_clip(clip):
    if (not isinstance(clip, dict) or not isinstance(clip.get('source'), str)
        or Path(clip['source']).suffix.lower() not in VIDEO_EXTENSIONS
        or any(type(clip.get(key)) not in (int, float) or not math.isfinite(clip[key])
               for key in ('start', 'duration'))
        or clip['start'] < 0 or not 3 <= clip['duration'] <= 30):
        raise ValueError('视频截取范围无效：开始时间需大于等于 0，片段时长需为 3–30 秒')


def _source_priority(path):
    parts = {part.casefold() for part in path.parts}
    return next((i for i, name in enumerate(PREFERRED_DIRECTORIES) if name.casefold() in parts), 3)


def _duration_score(duration):
    return 0 if 6 <= duration <= 12 else 1 if 5 <= duration <= 15 else 2


def scan_character_audio_groups(root, *, cancel_event=None, max_files=10000):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('请选择包含角色音频或视频的文件夹')
    count = 0
    def collect(directory):
        nonlocal count
        files = []
        for current, directories, names in os.walk(directory, followlinks=False):
            check_cancel(cancel_event)
            directories[:] = sorted(name for name in directories if not name.startswith('.')
                and name.casefold() not in EXCLUDED_DIRECTORIES
                and not (Path(current)/name).is_symlink()
                and not getattr(Path(current)/name, 'is_junction', lambda: False)())
            for name in sorted(names):
                check_cancel(cancel_event)
                path = Path(current)/name
                if path.suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                try:
                    if not path.resolve().is_relative_to(root) or path.stat().st_size == 0:
                        continue
                except OSError:
                    continue
                count += 1
                if count > max_files:
                    raise ValueError('素材数量超过一万，请选择更具体的素材文件夹')
                files.append(path)
        return tuple(sorted(files, key=lambda p: (_source_priority(p), str(p).casefold())))
    direct = any(p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS for p in root.iterdir())
    children = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')
                      and p.name.casefold() not in EXCLUDED_DIRECTORIES and not p.is_symlink()
                      and not getattr(p, 'is_junction', lambda: False)())
    # Recognized material subfolders describe one character; other subfolders
    # describe independent characters, even if the parent also has loose files.
    groups = []
    if children and all(p.name in PREFERRED_DIRECTORIES for p in children):
        return [CharacterAudioGroup(root.name, root, collect(root))]
    for child in children:
        files = collect(child)
        if files:
            groups.append(CharacterAudioGroup(child.name, child, files))
    if direct:
        files = tuple(sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS
                             and p.resolve().is_relative_to(root) and p.stat().st_size > 0))
        if count + len(files) > max_files:
            raise ValueError('素材数量超过限制，请选择更具体的素材文件夹')
        groups.insert(0, CharacterAudioGroup(root.name, root, files))
    return groups


def _read_flac_duration(path):
    try:
        with Path(path).open('rb') as stream:
            if stream.read(4) != b'fLaC':
                return None
            for _ in range(128):
                header = stream.read(4)
                if len(header) != 4:
                    return None
                length = int.from_bytes(header[1:], 'big')
                if header[0] & 127 == 0:
                    if length != 34:
                        return None
                    info = stream.read(34)
                    if len(info) != 34:
                        return None
                    packed = int.from_bytes(info[10:18], 'big')
                    rate, samples = packed >> 44, packed & ((1 << 36)-1)
                    return samples/rate if rate and samples else None
                if header[0] & 128:
                    return None
                stream.seek(length, os.SEEK_CUR)
    except OSError:
        return None


def run_process(command, *, stop=None, timeout=15):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    began = time.monotonic()
    try:
        while True:
            check_cancel(stop)
            if time.monotonic()-began > timeout:
                raise TimeoutError('音频处理超时')
            try:
                output, error = process.communicate(timeout=.1)
                return process.returncode, output, error
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)


def probe_audio_duration(path, ffprobe_path='ffprobe', *, ffmpeg_path='ffmpeg', cancel_event=None):
    path = Path(path)
    check_cancel(cancel_event)
    if path.suffix.lower() == '.flac':
        duration = _read_flac_duration(path)
        if duration is not None:
            return duration
    if path.suffix.lower() == '.wav':
        try:
            with wave.open(str(path), 'rb') as audio:
                return audio.getnframes()/audio.getframerate()
        except (OSError, wave.Error, EOFError):
            pass
    try:
        if Path(ffprobe_path).is_file() or shutil.which(ffprobe_path):
            code, output, _ = run_process([ffprobe_path, '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', str(path)], stop=cancel_event)
            duration = float(output.strip()) if code == 0 else None
        else:
            _, _, error = run_process([ffmpeg_path, '-hide_banner', '-nostdin', '-i', str(path),
                                       '-t', '0', '-f', 'null', '-'], stop=cancel_event)
            match = re.search(rb'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', error)
            duration = sum(float(value)*factor for value, factor in zip(match.groups(), (3600, 60, 1))) if match else None
        return duration if duration is not None and math.isfinite(duration) and duration > 0 else None
    except (OSError, TimeoutError, ValueError):
        return None


def rank_audio_candidates(group, *, ffprobe_path='ffprobe', max_candidates=4, duration_reader=None, cancel_event=None):
    reader = duration_reader or probe_audio_duration
    ranked = []
    for path in group.files:
        check_cancel(cancel_event)
        duration = reader(path, ffprobe_path)
        if duration is not None and math.isfinite(duration) and 3 <= duration <= 30:
            ranked.append(AudioCandidate(path, duration, _source_priority(path)))
    ranked.sort(key=lambda c: (_duration_score(c.duration_seconds), c.source_priority,
                              abs(c.duration_seconds-9), str(c.path).casefold()))
    return ranked[:max(1, max_candidates)]


def normalize_transcript(text):
    # Keep sentence endings and English word boundaries for reference alignment.
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', text)


def inspect_reference(path, stop=None):
    import numpy as np
    from audio_edges import _speech_windows
    with wave.open(str(path), 'rb') as audio:
        rate = audio.getframerate()
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32)/32768
    check_cancel(stop)
    if not len(samples) or np.max(np.abs(samples)) < .002:
        raise ValueError('片段几乎没有声音')
    ratio = None
    try:
        windows = _speech_windows(samples[:, None], rate, stop)
        ratio = min(1., sum(end-start for start, end in windows)/(len(samples)/rate))
    except Exception:
        check_cancel(stop)
    return dict(speech_ratio=round(ratio, 3) if ratio is not None else None,
                quiet_fraction=round(float(np.mean(np.abs(samples) < .003)), 3))


class VoiceLibraryDraft:
    def __init__(self, directory, root, rows):
        self.directory, self.root, self.rows = Path(directory), str(root), rows

    @classmethod
    def create(cls, parent, root, groups):
        directory = Path(parent)/('batch-'+uuid4().hex[:12])
        directory.mkdir(parents=True)
        rows = [dict(id=uuid4().hex[:12], speaker=g.speaker, aliases=[], files=[str(p) for p in g.files],
                     candidates=[], selected=0, include=True, reviewed='', state='pending', error='') for g in groups]
        draft = cls(directory, root, rows)
        draft.save()
        return draft

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.is_dir():
            path = path/'draft.json'
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('rows'), list):
            raise ValueError('无法识别这个声线草稿')
        if not isinstance(data.get('root'), str):
            raise ValueError('草稿素材目录无效')
        draft = cls(path.parent, data['root'], data['rows'])
        ids = set()
        for row in draft.rows:
            if (not isinstance(row, dict)
                or any(not isinstance(row.get(key), str) for key in ('id', 'speaker', 'reviewed', 'state', 'error'))
                or not re.fullmatch(r'[0-9a-f]{12}', row['id'])
                or row['state'] not in {'pending', 'ready', 'imported', 'skipped', 'failed'}
                or type(row.get('include')) is not bool
                or type(row.get('selected')) is not int
                or any(not isinstance(row.get(key), list) or any(not isinstance(v, str) for v in row[key])
                       for key in ('aliases', 'files'))
                or not isinstance(row.get('candidates'), list)):
                raise ValueError('草稿角色条目不完整，请重新扫描素材')
            if row['id'] in ids:
                raise ValueError('草稿条目重复')
            ids.add(row['id'])
            if not 0 <= row['selected'] < max(1, len(row['candidates'])):
                raise ValueError('草稿所选片段无效')
            if 'clip' in row:
                validate_video_clip(row['clip'])
                if row['clip']['source'] not in row['files']:
                    raise ValueError('草稿视频不在当前角色的素材中')
            for candidate in row['candidates']:
                if (not isinstance(candidate, dict)
                    or any(not isinstance(candidate.get(key), str)
                           for key in ('source', 'reference', 'sha256', 'text', 'asr_error'))
                    or not re.fullmatch(r'[0-9a-f]{64}', candidate['sha256'])
                    or type(candidate.get('seconds')) not in (int, float)
                    or not math.isfinite(candidate['seconds']) or not 2.99 <= candidate['seconds'] <= 30.01
                    or not isinstance(candidate.get('quality'), dict)):
                    raise ValueError('草稿参考片段资料不完整，请重新准备')
                for key in ('speech_ratio', 'quiet_fraction'):
                    value = candidate['quality'].get(key)
                    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1):
                        raise ValueError('草稿参考片段分析结果无效')
                start = candidate.get('start', 0)
                if type(start) not in (int, float) or not math.isfinite(start) or start < 0:
                    raise ValueError('草稿参考片段开始时间无效')
                draft.reference(candidate)
        return draft

    def set_video_clip(self, speaker, aliases, clip, *, row=None):
        validate_video_clip(clip)
        if not speaker.strip():
            raise ValueError('请填写这段视频中说话的角色名')
        if row is not None and (row not in self.rows or row['state'] in ('imported', 'skipped')):
            raise ValueError('已保存或已跳过的声线不能通过草稿替换')
        if row is None:
            row = dict(id=uuid4().hex[:12], files=[], include=True)
            self.rows.append(row)
        if clip['source'] not in row['files']:
            row['files'].append(clip['source'])
        row.update(speaker=speaker.strip(), aliases=list(aliases), clip=dict(clip), candidates=[],
                   selected=0, reviewed='', state='pending', error='')
        self.save()
        return row

    def save(self):
        atomic_text(self.directory/'draft.json', json.dumps(dict(version=1, root=self.root, rows=self.rows),
                                                          ensure_ascii=False, indent=2, allow_nan=False))

    def reference(self, candidate):
        path = (self.directory/candidate['reference']).resolve()
        if not path.is_relative_to(self.directory.resolve()) or path.suffix != '.wav':
            raise ValueError('草稿参考音频路径无效')
        return path

    @staticmethod
    def chosen(row):
        index = row.get('selected', 0)
        return row['candidates'][index] if isinstance(index, int) and 0 <= index < len(row['candidates']) else None

    def review_key(self, row):
        candidate = self.chosen(row)
        if candidate is None or not candidate.get('text', '').strip():
            return ''
        payload = [row['speaker'], row['aliases'], candidate['sha256'], candidate['text']]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


class FasterWhisperTranscriber:
    """One cancellable, isolated CPU worker per preparation/transcription task."""
    def __init__(self, work_directory, *, config_path=None):
        self.directory = Path(work_directory)/('asr-'+uuid4().hex[:8])
        self.directory.mkdir(parents=True)
        self.config_path = Path(config_path or Path(__file__).with_name('voice-library-runtime.json'))
        self.process = self.log = None

    def transcribe(self, audio_path, *, cancel_event=None):
        try:
            return self._transcribe(audio_path, cancel_event=cancel_event)
        except Exception:
            # A failed/stalled request must not keep consuming CPU or queue later
            # roles behind it. The next request can start a fresh worker.
            self.close()
            raise

    def _transcribe(self, audio_path, *, cancel_event=None):
        check_cancel(cancel_event)
        if self.process is None:
            if not self.config_path.is_file():
                raise ValueError('本地转写组件尚未配置；可以手动填写参考台词')
            self.log = (self.directory/'worker.log').open('wb')
            self.process = subprocess.Popen([sys.executable, '-X', 'utf8', '-u',
                str(Path(__file__).with_name('voice_library_asr.py')), str(self.config_path), str(self.directory)],
                stdin=subprocess.PIPE, stdout=self.log, stderr=self.log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0))
        identity = uuid4().hex
        result = self.directory/(identity+'.json')
        self.process.stdin.write((json.dumps(dict(id=identity, audio=str(audio_path)))+'\n').encode('utf-8'))
        self.process.stdin.flush()
        began = time.monotonic()
        while not result.is_file():
            check_cancel(cancel_event)
            if self.process.poll() is not None:
                raise ValueError('本地转写进程未能完成；可以手动填写或重试')
            if time.monotonic()-began > 180:
                raise TimeoutError('本段转写超过三分钟，已停止；可以手动填写或重试')
            if cancel_event is not None:
                cancel_event.wait(.1)
            else:
                time.sleep(.1)
        data = json.loads(result.read_text(encoding='utf-8'))
        if data.get('error'):
            raise ValueError(data['error'])
        return normalize_transcript(data.get('text', ''))

    def close(self):
        process, log = self.process, self.log
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(3)
        finally:
            if process is not None and process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if log:
                log.close()
            self.process = self.log = None


class BatchVoiceProfileBuilder:
    def __init__(self, options, draft, *, stop=None, progress=lambda message: None,
                 transcriber_factory=FasterWhisperTranscriber, quality_reader=inspect_reference):
        self.options, self.draft = options, draft
        self.stop, self.progress = stop or threading.Event(), progress
        self.transcriber_factory, self.quality_reader = transcriber_factory, quality_reader

    def prepare(self, *, row_id=None):
        transcriber = self.transcriber_factory(self.draft.directory)
        try:
            for number, row in enumerate(self.draft.rows, 1):
                check_cancel(self.stop)
                if row_id is not None and row['id'] != row_id:
                    continue
                if (not row['include'] and row_id is None) or row['state'] in ('imported', 'skipped'):
                    continue
                if row['state'] == 'ready':
                    chosen = self.draft.chosen(row)
                    if chosen and not chosen.get('text', '').strip():
                        self.transcribe_row(row, transcriber=transcriber)
                    continue
                self.progress(f"正在准备 {number}/{len(self.draft.rows)}：{row['speaker']}")
                try:
                    self._prepare_row(row, transcriber)
                except ImportCancelled:
                    raise
                except Exception as exc:
                    row['state'], row['error'] = 'failed', str(exc)
                self.draft.save()
        finally:
            transcriber.close()
            self.draft.save()

    def _prepare_row(self, row, transcriber):
        importer = VoiceImportTask(self.options, {})
        importer.stop_event = self.stop
        from wuwa_dubber.text_processing import normalize_speaker
        requested = {normalize_speaker(name).casefold() for name in [row['speaker'], *row['aliases']]}
        owners = [p.speaker for p in load_profiles(importer.data_root) if requested.intersection(
            normalize_speaker(name).casefold() for name in [p.speaker, *p.aliases])]
        if owners:
            row['state'], row['error'] = 'skipped', '名称或别名已属于声线：'+'、'.join(owners)
            return
        check_names(importer.data_root, row['speaker'], row['aliases'])
        from wuwa_dubber.tool_discovery import discover_ffmpeg
        executable = importer.ffmpeg
        if not Path(executable).is_file() and not shutil.which(executable):
            executable, _ = discover_ffmpeg()
        group = CharacterAudioGroup(row['speaker'], Path(self.draft.root), tuple(Path(p) for p in row['files']))
        reader = lambda path, probe: probe_audio_duration(path, probe, ffmpeg_path=executable, cancel_event=self.stop)
        if 'clip' in row:
            clip = row['clip']
            validate_video_clip(clip)
            total = reader(Path(clip['source']), 'ffprobe')
            if total is None:
                raise ValueError('无法读取视频时长，请检查视频是否完整')
            if clip['start'] + clip['duration'] > total + .05:
                raise ValueError(f'截取范围超出视频末尾（总长 {total:.2f} 秒），请点击“选取视频片段…”调整')
            candidates = [AudioCandidate(Path(clip['source']), clip['duration'], 0, clip['start'])]
        else:
            candidates = rank_audio_candidates(group, max_candidates=4, duration_reader=reader, cancel_event=self.stop)
        prepared, errors = [], []
        for candidate in candidates:
            check_cancel(self.stop)
            target = self.draft.directory/(uuid4().hex[:16]+'.wav')
            try:
                importer.convert(candidate.path, target, candidate.start_seconds, candidate.duration_seconds)
                duration = importer.check_audio(target)
                quality = self.quality_reader(target, self.stop)
                prepared.append(dict(source=str(candidate.path), start=candidate.start_seconds, reference=target.name, seconds=duration,
                                     sha256=sha256(target), quality=quality, text='', asr_error=''))
            except ImportCancelled:
                raise
            except Exception as exc:
                errors.append(f'{candidate.path.name}：{exc}')
            finally:
                if target.is_file() and not any(c['reference'] == target.name for c in prepared):
                    target.unlink()
        if not prepared:
            if not errors and any(p.suffix.lower() in VIDEO_EXTENSIONS for p in group.files):
                raise ValueError('没有 3–30 秒的可用短片；请点击下方“选取视频片段…”指定角色说话的范围')
            raise ValueError(errors[-1] if errors else '没有 3–30 秒的可用片段；长录音可通过“添加角色声线”手动截取')
        prepared.sort(key=lambda c: ((c['quality'].get('speech_ratio') is not None and c['quality']['speech_ratio'] < .35),
                                    c['quality'].get('quiet_fraction', 0) > .7))
        row.update(candidates=prepared[:3], selected=0, reviewed='', state='ready', error='')
        self.draft.save()
        self.transcribe_row(row, transcriber=transcriber)

    def transcribe_row(self, row, *, transcriber=None):
        candidate = self.draft.chosen(row)
        if candidate is None:
            return
        own = transcriber is None
        transcriber = transcriber or self.transcriber_factory(self.draft.directory)
        try:
            reference = self.draft.reference(candidate)
            if sha256(reference) != candidate['sha256']:
                raise ValueError('参考片段已变化，请重新准备草稿')
            self.progress('正在本地转写：'+row['speaker'])
            text = transcriber.transcribe(reference, cancel_event=self.stop)
            check_cancel(self.stop)
            candidate['text'] = text
            candidate['asr_error'] = '' if text else '没有识别到文字，请试听后手动填写'
            row['reviewed'] = ''
        except ImportCancelled:
            raise
        except Exception as exc:
            candidate['asr_error'] = str(exc)
        finally:
            if own:
                transcriber.close()
            self.draft.save()

    def publish(self):
        summary = dict(imported=0, held=0, failed=0)
        for row in self.draft.rows:
            check_cancel(self.stop)
            if not row['include'] or row['state'] in ('imported', 'skipped'):
                continue
            key = self.draft.review_key(row)
            if not key or row['reviewed'] != key:
                summary['held'] += 1
                continue
            candidate = self.draft.chosen(row)
            try:
                self.progress('正在保存已核对声线：'+row['speaker'])
                values = dict(speaker=row['speaker'], aliases=row['aliases'], source=candidate['source'],
                              prompt=candidate['text'], start=candidate.get('start', 0), duration=candidate['seconds'], speed=1)
                task = VoiceImportTask(self.options, values)
                task.stop_event = self.stop
                profile = task.run(prepared_reference=self.draft.reference(candidate), expected_sha256=candidate['sha256'])
                row.update(state='imported', profile=profile, error='')
                summary['imported'] += 1
            except ImportCancelled:
                raise
            except Exception as exc:
                row['error'] = str(exc)
                summary['failed'] += 1
            self.draft.save()
        return summary
