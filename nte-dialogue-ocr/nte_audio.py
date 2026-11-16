"""Reuse reviewed project WAVs and the shared, voice-aware speech cache."""
from dataclasses import replace
import json
from pathlib import Path
import threading
import time

from dialogue_generation import GenerationProject, atomic_text, digest, file_digest
from prepared_playback import PreparedLibrary, PreparedLine, prefix_text, text_key


class NteAudio:
    def __init__(self, root, runtime, aliases, preferred_project=None):
        self.root, self.runtime, self.aliases = Path(root), runtime, aliases
        self.preferred = Path(preferred_project).resolve() if preferred_project else None
        self.libraries = {}
        from wuwa_dubber.speech_cache import SpeechCache
        self.cache = SpeechCache(runtime.config, runtime.cache_dir, runtime.client)

    def existing(self, line):
        paths = sorted((self.root / 'generation-projects').glob('*/project.json'))
        if self.preferred:
            preferred = self.preferred / 'project.json' if self.preferred.is_dir() else self.preferred
            paths = [preferred] + [p for p in paths if p.resolve() != preferred]
        for path in paths:
            try:
                stamp = path.stat().st_mtime_ns
                if path not in self.libraries or self.libraries[path][0] != stamp:
                    project = GenerationProject.load(path)
                    if project.game_id != 'nte':
                        continue
                    self.libraries[path] = (stamp, PreparedLibrary(project, self.aliases))
                library = self.libraries[path][1]
                prepared, _ = library.match(line.text, line.speaker)
                if prepared and prefix_text(prepared.text) == prefix_text(line.text) and file_digest(prepared.path) == prepared.sha256:
                    return prepared
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def resolve(self, line, token):
        from wuwa_dubber.models import Utterance
        from wuwa_dubber.speech_cache import SpeechCancelled
        started = time.monotonic()
        record = dict(speaker=line.speaker, speaker_status='verified', body=line.text)
        profile, _ = self.runtime.select_profile(record)
        # Explicit per-character settings take precedence over a previous voice.
        role = lambda name: self.aliases.get(text_key(name), text_key(name))
        explicit = any(role(name) == role(line.speaker)
                       for name in self.runtime.options.get('system_character_voices', {}))
        prepared = self.existing(line)
        if prepared and explicit and prepared.voice != profile.speaker:
            prepared = None
        if token.is_set():
            raise SpeechCancelled()
        if prepared:
            result = replace(prepared, text=line.text, speaker=line.speaker, catalog_id=line.identity)
            origin = 'project'
        else:
            utterance = Utterance(line.text, speaker=profile.speaker, source='nte_hybrid')
            cached = self.cache.get_or_create(utterance, profile, cancel_event=token)
            result = PreparedLine(line.identity, line.text, line.speaker, profile.speaker,
                                  cached.path, file_digest(cached.path), not self.runtime.system_mode,
                                  catalog_id=line.identity)
            origin = 'cache' if cached.cache_hit else 'generated'
        if token.is_set():
            raise SpeechCancelled()
        link = dict(catalog_id=line.identity, text_fingerprint=line.fingerprint, speaker_key=line.speaker_key,
                    speaker=line.speaker, voice=result.voice, path=str(result.path), sha256=result.sha256,
                    origin=origin)
        atomic_text(self.root / 'nte-data/audio-links' / (digest(line.identity) + '.json'),
                    json.dumps(link, ensure_ascii=False, indent=2))
        return result, origin, time.monotonic() - started


class HybridPlayback:
    """One newest synthesis job and one output owner, with cancellation per page."""
    def __init__(self, audio, callback, *, clock=time.monotonic):
        from wuwa_log_audio import LogPlayback
        self.audio, self.callback, self.clock = audio, callback, clock
        self.condition = threading.Condition(threading.RLock())
        self.synthesis_lock = threading.Lock()
        self.synthesizing = None
        self.closed = self.paused = self.skipped = False
        self.current = self.pending = None
        self.serial = 0
        self.minimum_capture = -float('inf')
        self.token = threading.Event()
        self.output = LogPlayback(audio.runtime.player, self._output_event)
        self.worker = threading.Thread(target=self._consume, name='nte-synthesis', daemon=True)
        self.relay_stop = threading.Event()
        self.relay = threading.Thread(target=self._relay, name='nte-cancel', daemon=True)
        self.worker.start()
        self.relay.start()

    def emit(self, state, message, **extra):
        current = self.current or {}
        line = current.get('line')
        self.callback(dict(state=state, message=message, serial=self.serial,
                           catalog_id=line.identity if line else '', text=line.text if line else '',
                           speaker=line.speaker if line else '', source=current.get('source', ''), **extra))

    def select(self, line, source, captured_at, *, native=False):
        with self.condition:
            if self.closed or captured_at < self.minimum_capture:
                return False
            previous = self.current
            if previous and previous['line'] == line:
                if native and not previous['native']:
                    self.token.set()
                    self.pending = None
                    self.output.clear()
                    previous['native'] = True
                    self.emit('native', '游戏正在播放原声')
                if source == 'log':
                    previous['source'] = source
                return True
            if previous and previous['source'] == 'log' and source != 'log':
                return False
            self._cancel_locked()
            self.serial += 1
            self.skipped = False
            self.current = dict(line=line, source=source, captured_at=captured_at, native=native,
                                serial=self.serial, origin='', prepared=None)
            if native:
                self.emit('native', '游戏正在播放原声')
            elif self.paused:
                self.emit('paused', '已暂停，继续时播放当前句')
            else:
                self._queue_locked()
            return True

    def _cancel_locked(self):
        self.token.set()
        self.pending = None
        self.output.clear()

    def invalidate(self, captured_at, *, expected_id=None):
        with self.condition:
            self.minimum_capture = max(self.minimum_capture, captured_at)
            if self.current and expected_id and self.current['line'].identity != expected_id:
                return
            self._cancel_locked()
            self.current = None
            self.skipped = False
            self.emit('waiting', '等待当前台词')

    def _queue_locked(self):
        self.token = threading.Event()
        self.pending = (self.current, self.token)
        self.condition.notify()
        self.emit('resolving', '正在查找已有配音，缺少时生成')

    def pause(self, value=True):
        with self.condition:
            self.paused = value
            self.output.pause(value)
            if value:
                self._cancel_locked()
                self.emit('paused', '已暂停自动播放')
            elif self.current and not self.current['native'] and not self.skipped:
                self._queue_locked()

    def skip(self):
        with self.condition:
            self.skipped = True
            self._cancel_locked()
            self.emit('skipped', '已跳过当前句')

    def replay(self):
        with self.condition:
            if not self.current or self.current['native'] or self.paused or self.closed:
                return False
            self.skipped = False
            self._cancel_locked()
            self._queue_locked()
            return True

    def _relay(self):
        cancelled = None
        while not self.relay_stop.wait(.04):
            with self.synthesis_lock:
                token = self.synthesizing
                if token is not None and token.is_set() and token is not cancelled:
                    try:
                        self.audio.runtime.client.cancel()
                    except Exception:
                        pass
                    cancelled = token

    def _consume(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                job, token = self.pending
                self.pending = None
            try:
                with self.synthesis_lock:
                    self.synthesizing = token
                prepared, origin, elapsed = self.audio.resolve(job['line'], token)
                with self.condition:
                    if token.is_set() or self.closed or self.current is not job or self.paused or self.skipped:
                        continue
                    prepared = replace(prepared, row_id=str(job['serial']) + ':' + job['line'].identity)
                    job.update(prepared=prepared, origin=origin, resolve_seconds=elapsed)
                    self.output.available(prepared)
            except Exception as exc:
                with self.condition:
                    if not token.is_set() and self.current is job:
                        self.emit('error', '配音失败，可点重播重试 · ' + str(exc))
            finally:
                with self.synthesis_lock:
                    if self.synthesizing is token:
                        self.synthesizing = None

    def _output_event(self, event):
        with self.condition:
            current = self.current
            if not current or self.closed or current.get('prepared') is None:
                return
            if event['row_id'] != current['prepared'].row_id or self.token.is_set():
                return
            details = dict(audio_origin=current['origin'], resolve_seconds=current['resolve_seconds'])
            if event['state'] == 'playing':
                details['latency_ms'] = round((self.clock() - current['captured_at']) * 1000, 1)
            self.emit(event['state'], event['message'], **details)

    def stop(self):
        with self.condition:
            self.closed = True
            self._cancel_locked()
            self.condition.notify_all()
        self.output.stop()

    def close(self):
        self.stop()
        self.worker.join()
        self.relay_stop.set()
        self.relay.join()
        self.output.close()
