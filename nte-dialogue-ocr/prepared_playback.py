"""Match fresh subtitles to a frozen project index and play existing WAVs only."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from pathlib import Path
import threading
import time
import unicodedata
from playback_metrics import timing_sample


def text_key(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', str(text or '')).casefold() if c.isalnum())


def prefix_text(text):
    """Count punctuation as characters; normalize typography, never guess words."""
    value = ''.join(unicodedata.normalize('NFC', str(text or '')).split())
    # NFKC on the whole string would expand each ellipsis into three characters.
    value = ''.join(unicodedata.normalize('NFKC', c) if '\uff00' <= c <= '\uffef' else c for c in value)
    value = value.translate(str.maketrans({'。':'.', '「':'"', '」':'"', '『':'"', '』':'"',
                                         '“':'"', '”':'"', '‘':"'", '’':"'", '⋯':'…'}))
    return re.sub(r'(?<!\.)\.{3}(?:\.{3})?(?!\.)', lambda m:'…'*(len(m[0])//3), value)


@dataclass(frozen=True)
class PreparedLine:
    row_id: str
    text: str
    speaker: str
    voice: str
    path: Path
    sha256: str
    character_voice: bool

    @property
    def identity(self):
        return text_key(self.text), text_key(self.voice), self.sha256


class PreparedLibrary:
    def __init__(self, project, aliases=None):
        self.aliases = dict(aliases or {})
        self.lines, self.by_text = [], {}
        self.by_prefix, self.known_prefixes = {}, {}
        self.known_texts = set()
        self.excluded = 0
        for row in project.rows:
            canonical = prefix_text(row.text)
            self.known_texts.add(canonical)
            if len(canonical) >= 5:
                known = (canonical, self.role_key(row.speaker), row.voice)
                self.known_prefixes.setdefault(canonical[:5], set()).add(known)
            path = project.audio_path(row) if row.status == 'done' and not row.review_reason() else None
            if path is None:
                self.excluded += 1
                continue
            line = PreparedLine(row.id, row.text, row.speaker, row.audio.get('voice', ''), path,
                                row.audio['sha256'], project.mode == 'character')
            self.lines.append(line)
            self.by_text.setdefault(text_key(row.text), []).append(line)
            if len(canonical) >= 5:
                self.by_prefix.setdefault(canonical[:5], []).append(line)

    def role_key(self, name):
        key = text_key(name)
        return self.aliases.get(key, key)

    def unmatched_keys(self, records):
        """Count current complete record revisions, never transient OCR events."""
        missing = set()
        for record in records:
            text = record.get('body', '')
            key, canonical = text_key(text), prefix_text(text)
            if record.get('status') != 'verified' or not key:
                continue
            # Audio exists for this text: an uncertain role/version is a
            # pending selection, not a sentence with no matching audio.
            if key in self.by_text:
                continue
            if any(known != canonical and known.startswith(canonical) for known in self.known_texts):
                continue  # A typed beginning is not a new complete sentence.
            missing.add((self.role_key(record.get('speaker')), key))
        return missing

    def match(self, text, speaker=''):
        choices = self.by_text.get(text_key(text), [])
        if not choices:
            return None, '当前项目没有这句的完整音频'
        role = self.role_key(speaker)
        if role:
            # Explicit character voice assignments can correct an OCR role name.
            # They also distinguish duplicate text spoken by different characters.
            voices = [line for line in choices if line.character_voice and self.role_key(line.voice) == role]
            choices = voices or [line for line in choices if self.role_key(line.speaker) == role]
            if not choices:
                return None, '台词相同，但角色不对应，等待角色名识别稳定'
        distinct = {line.identity: line for line in choices}
        if len(distinct) != 1:
            return None, '这句有多个音频版本，请识别角色名或在项目中明确声线'
        return next(iter(distinct.values())), ''

    def resolve(self, text, speaker='', *, early=False):
        """Unique prefix is global, before role filtering; collisions need full text."""
        canonical = prefix_text(text)
        if not early:
            exact, reason = self.match(text, speaker)
            if exact:
                return exact, '', 'full'
        if len(canonical) < 5 or not text_key(canonical):
            return None, '不足五个字符，等待完整短句', ''
        prefix = canonical[:5]
        choices = {line.identity:line for line in self.by_prefix.get(prefix, [])}
        if len(choices) != 1 or len(self.known_prefixes.get(prefix, ())) != 1:
            return None, ('前五字符有多个候选，等待整句匹配' if choices else '当前项目没有对应的前五字符'), ''
        line = next(iter(choices.values()))
        if not prefix_text(line.text).startswith(canonical):
            return None, '已识别后文与候选台词不符，已停止提前播放', 'conflict'
        role = self.role_key(speaker)
        expected_role = line.voice if line.character_voice else line.speaker
        if role and role != self.role_key(expected_role):
            return None, '前五字符匹配，但角色不对应，等待角色名稳定', 'conflict'
        return line, '', 'prefix'


class PreparedPlayback:
    """One audio owner, one replaceable pending line, no synthesis dependency."""
    def __init__(self, library, player, callback=lambda event: None, *, clock=time.monotonic,
                 max_age=2.5, confirmations=2):
        self.library, self.player, self.callback = library, player, callback
        self.clock, self.max_age, self.confirmations = clock, max_age, confirmations
        self._condition = threading.Condition(threading.RLock())
        self._closed = self._paused = False
        self._pending = self._active = None
        self._active_line = None
        self._serial = 0
        self._body = ''
        self._match_kind = ''
        self._prefix_since = None
        self._timing_seed = None
        self._blocked_prefixes = set()
        self._current = self._candidate = self._consumed = None
        self._count = 0
        self._last_capture = self._minimum_capture = -math.inf
        self._thread = threading.Thread(target=self._consume, name='prepared-dialogue-playback', daemon=True)
        self._thread.start()

    @property
    def alive(self):
        return self._thread.is_alive()

    @property
    def generation(self):
        with self._condition:
            return self._serial

    def _emit(self, state, message, line=None, **details):
        self.callback(dict(state=state, message=message, row_id=line.row_id if line else None,
                           text=line.text if line else '', voice=line.voice if line else '',
                           generation=details.pop('generation', self._serial), **details))

    def _cancel_locked(self):
        self._serial += 1
        self._pending = None
        if self._active:
            self._active.set()

    def _reset_match_locked(self):
        self._cancel_locked()
        self._current = self._candidate = None
        self._count = 0
        self._timing_seed = None

    def invalidate(self, *, captured_at=None, unavailable=False):
        with self._condition:
            if self._closed:
                return
            if captured_at is not None and captured_at <= self._minimum_capture:
                return
            was_current = bool(self._current or self._candidate or self._pending or self._active)
            if unavailable and (self._active or self._pending):
                self._consumed = None  # Resume only after fresh, confirmed subtitles return.
            self._minimum_capture = max(self._minimum_capture,
                                        self.clock() if captured_at is None else captured_at)
            self._reset_match_locked()
            self._consumed = None
            self._blocked_prefixes.clear()
            self._match_kind = ''
            if was_current:
                self._emit('waiting', '画面暂不可用，已停止播放' if unavailable else '字幕画面已变化，等待新台词')

    def observe(self, observation):
        with self._condition:
            if self._closed:
                return
            received = self.clock()
            try:
                captured = float(observation.get('captured_monotonic', observation.get('monotonic_seconds')))
                age = self.clock()-captured
                score = float(observation.get('body_confidence', 0))
            except (ValueError, TypeError):
                self.invalidate()
                return
            if not math.isfinite(captured) or captured < self._minimum_capture or captured <= self._last_capture:
                return
            self._last_capture = captured
            if not -1 <= age <= self.max_age:
                self.invalidate()
                self._emit('stale', '识别结果已过期，等待最新字幕')
                return
            body = text_key(observation.get('body'))
            early = observation.get('prefix_probe') is True
            previous_body, self._body = self._body, body
            if not body:
                if early and self._current:
                    return  # Independent photographed-glyph monitoring owns blank/page interruption.
                self._reset_match_locked()
                self._consumed = None
                self._blocked_prefixes.clear()
                self._emit('blank', '当前没有需要播放的字幕')
                return
            if (not math.isfinite(score) or score < .9 or any(observation.get(key) for key in
                    ('source_clipped', 'coverage_warning', 'review_coverage_warning'))):
                if (self._current and (early
                        or prefix_text(observation.get('body'))[:5] == prefix_text(self._current.text)[:5])):
                    self._emit('checking', '后文暂未读清，保持本句并继续核对', self._current)
                    return
                self._reset_match_locked()
                self._emit('held', '字幕可能缺字或识别不稳定，等待下一次识别')
                return
            try:
                speaker_score = float(observation.get('speaker_confidence') or 0)
            except (ValueError, TypeError):
                speaker_score = 0
            speaker = observation.get('speaker', '') if math.isfinite(speaker_score) and speaker_score >= .9 else ''
            line, reason, kind = self.library.resolve(observation.get('body'), speaker, early=early)
            prefix = prefix_text(observation.get('body'))[:5]
            if kind == 'conflict':
                self._blocked_prefixes.add(prefix)
            if kind == 'prefix' and prefix in self._blocked_prefixes:
                line, reason = None, '本页前缀曾与后文冲突，等待整句确认'
            if line is None:
                if early and self._current and kind != 'conflict':
                    self._emit('checking', '前缀暂未确认，继续核对当前字幕', self._current)
                    return
                self._reset_match_locked()
                if body != previous_body:
                    self._consumed = None
                self._emit('unmatched', reason, recognized=observation.get('body', ''))
                return
            if self._current is None or self._current.identity != line.identity:
                self._cancel_locked()
                if body != previous_body:
                    self._consumed = None
            elif kind == 'full' and self._match_kind == 'prefix':
                self._emit('verified', '后文已核对，本句继续播放', line)
            self._current = line
            self._match_kind = kind
            if kind == 'full':
                self._blocked_prefixes.discard(prefix)
            if self._candidate != line.identity:
                self._candidate, self._count = line.identity, 0
                self._prefix_since = captured
                # A visual recheck may carry OCR from an older captured frame.
                try:
                    origin = float(observation.get('recognized_capture') or captured)
                    if not math.isfinite(origin) or origin > captured:
                        origin = captured
                except (TypeError, ValueError):
                    origin = captured
                self._timing_seed = dict(capture=origin, received=received)
            # A render-stable observation already represents several distinct
            # captured frames. Do not wait for another expensive OCR/review pass.
            visually_stable = observation.get('render_stable') is True or observation.get('prefix_stable') is True
            self._count += self.confirmations if visually_stable else 1
            if self._paused or line.identity == self._consumed:
                return
            if ((self._pending and self._pending[0].identity==line.identity)
                    or (self._active and not self._active.is_set() and self._active_line==line.identity)):
                return
            if (self._count < self.confirmations or (kind == 'prefix' and not visually_stable
                    and captured-(captured if self._prefix_since is None else self._prefix_since) < .15)):
                self._emit('matching', '前五字符已匹配，等待稳定确认' if kind == 'prefix' else '已找到音频，等待字幕稳定', line)
                return
            self._queue_locked(line)

    def _queue_locked(self, line, *, trigger='automatic'):
        self._cancel_locked()
        timing = dict(self._timing_seed or {}, queued=self.clock(), trigger=trigger, match_kind=self._match_kind)
        self._pending = (line, self._serial, timing)
        self._emit('queued', '前五字符唯一匹配，准备播放并核对后文' if self._match_kind == 'prefix' else '已匹配，准备播放本地音频', line,
                   match_kind=self._match_kind)
        self._condition.notify_all()

    def pause(self, value=True):
        with self._condition:
            self._paused = bool(value)
            if value:
                self._cancel_locked()
            elif (self._current and self._count >= self.confirmations
                  and self.clock()-self._last_capture <= self.max_age):
                self._queue_locked(self._current, trigger='resume')
            self._emit('paused' if value else 'ready', '已暂停自动播放，识别仍继续' if value else '已继续自动播放')

    def skip(self):
        with self._condition:
            self._cancel_locked()
            if self._current:
                self._consumed = self._current.identity
            self._emit('skipped', '已跳过当前句，等待下一句')

    def replay(self):
        with self._condition:
            if (not self._closed and not self._paused and self._current
                    and self._count >= self.confirmations and self.clock()-self._last_capture <= self.max_age):
                self._queue_locked(self._current, trigger='replay')
            else:
                self._emit('held', '当前没有已确认且可播放的字幕')

    def stop(self):
        with self._condition:
            self._closed = True
            self._cancel_locked()
            self._condition.notify_all()

    def close(self, timeout=None):
        self.stop()
        self._thread.join(timeout)
        return not self.alive

    def _consume(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                (line, generation, timing), self._pending = self._pending, None
                dequeued = self.clock()
                cancel = threading.Event()
                self._active = cancel
                self._active_line = line.identity
            started = False
            def on_started():
                nonlocal started
                with self._condition:
                    if not started and not cancel.is_set() and generation==self._serial:
                        started=True
                        self._consumed=line.identity
                        latency = timing_sample(timing, dequeued, self.clock())
                        self._emit('playing','正在播放：'+(line.speaker or line.voice),line,generation=generation,
                                   latency=latency)
            try:
                from wuwa_dubber.speech_cache import valid_wav
                if not valid_wav(line.path) or hashlib.sha256(line.path.read_bytes()).hexdigest() != line.sha256:
                    raise ValueError('音频已更改或损坏，请停止自动播放并重新生成这句')
                with self._condition:
                    if self.clock()-self._last_capture > self.max_age and not cancel.is_set():
                        self._consumed = self._current = self._candidate = None
                        self._count = 0
                        self._emit('stale', '等待最新字幕，已取消过期播放', line, generation=generation)
                        continue
                if not cancel.is_set():
                    self.player.play(line.path, cancel_event=cancel, on_started=on_started)
                if started and not cancel.is_set():
                    self._emit('completed', '本句播放完成，等待下一句', line, generation=generation)
            except Exception as exc:
                if not cancel.is_set():
                    with self._condition:
                        self._consumed=line.identity  # Do not loop on a device/file error; explicit replay can retry.
                    self._emit('error', '播放失败：'+str(exc), line, generation=generation)
            finally:
                with self._condition:
                    if self._active is cancel:
                        self._active = None
                        self._active_line = None
                    self._condition.notify_all()
