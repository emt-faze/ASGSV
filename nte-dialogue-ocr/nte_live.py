"""NTE log-first session with resident OCR and an independent visual cancellation watch."""
from __future__ import annotations

import copy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import threading
import time

from dialogue_dubbing import VoiceRuntime
from generator_ocr import PlaybackFrameWatch
from generator_playback import playback_settings
from nte_audio import NteAudio, HybridPlayback
from nte_catalog import NteCatalog, catalog_path, resource_stamp
from nte_log_stream import NteLogTail
from prepared_playback import text_key


class OcrSchedule:
    def __init__(self, *, idle_interval=1.0):
        self.active_until = 0.0
        self.last_probe = -float('inf')
        self.idle_interval = idle_interval

    def wake(self, now, duration=4.0):
        self.active_until = max(self.active_until, now + duration)

    def ready(self, captured_at):
        if captured_at < self.active_until or captured_at - self.last_probe >= self.idle_interval:
            self.last_probe = captured_at
            return True
        return False


class NteLiveSession:
    def __init__(self, root, config, callback, *, preferred_project=None):
        self.root, self.config, self.callback = Path(root), copy.deepcopy(config), callback
        self.preferred_project = preferred_project
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.controller = self.capture = self.watch = self.catalog = None
        self.schedule = OcrSchedule()
        self.visual_id = None
        self.native_voice = None
        self.last_blank = False
        self.log_status = ''
        self.metrics_lock = threading.Lock()
        self.directory = self.root / 'nte-data/sessions' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')

    def emit(self, event):
        self.callback(event)
        if 'state' in event:
            with self.metrics_lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                with (self.directory / 'events.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(dict(event, monotonic=time.monotonic()), ensure_ascii=False) + '\n')

    def cancel(self):
        self.stop_event.set()
        if self.controller:
            self.controller.stop()

    def _watch_event(self, event):
        with self.lock:
            if 'frame_changed' in event:
                at = event['frame_changed']
                if self.native_voice and self.native_voice['reference_at'] is not None and at > self.native_voice['reference_at']:
                    self.native_voice = None
                self.controller.invalidate(at, expected_id=self.visual_id)
                self.schedule.wake(at)
                self.visual_id = None
            elif event.get('capture_state') == 'unavailable':
                self._state('unavailable')
            elif 'error' in event:
                self.controller.invalidate(time.monotonic())
                self.emit(event)
                self.stop_event.set()

    def _state(self, state):
        with self.lock:
            if state in ('blank', 'unavailable'):
                if not self.last_blank:
                    self.controller.invalidate(time.monotonic())
                    self.visual_id = None
                self.last_blank = True
                if state == 'blank':
                    self.native_voice = None

    def _visual(self, image, observation, offset):
        self.watch.arm(image, observation, offset)
        # A cached WAV can start before the watch's next 50 ms tick. Check the
        # newest pixels synchronously before admitting an older OCR result.
        packet = self.capture.peek()
        if packet is None or packet.captured_at <= observation.get('captured_monotonic', 0):
            return
        from ocr_core import roi_to_pixels
        width, height = packet.client_size
        left, top, right, bottom = roi_to_pixels((height, width), self.config['body_roi'])
        x, y = packet.origin
        body = packet.image[max(0, top-y):bottom-y, max(0, left-x):right-x]
        with self.watch.lock:
            changed = self.watch.guard.changed(body, packet.captured_at) if body.size else True
        if changed:
            self._watch_event({'frame_changed': packet.captured_at})

    def observe(self, observation):
        with self.lock:
            captured = observation.get('captured_monotonic', 0)
            if (not isinstance(captured, (int, float)) or not math.isfinite(captured) or
                    self.stop_event.is_set() or captured < self.controller.minimum_capture):
                return
            self.last_blank = False
            confidence = observation.get('body_confidence', 0)
            if (not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or confidence < .9 or
                    any(observation.get(key) for key in ('source_clipped', 'coverage_warning', 'review_coverage_warning'))):
                return
            early = observation.get('prefix_probe') is True
            if early and not observation.get('prefix_stable'):
                return
            if not early and not observation.get('render_stable'):
                return
            speaker_score = observation.get('speaker_confidence') or 0
            speaker = observation.get('speaker', '') if isinstance(speaker_score, (int, float)) and math.isfinite(speaker_score) and speaker_score >= .9 else ''
            if self.native_voice and captured >= self.native_voice['at']:
                self.native_voice['reference_at'] = captured
            line, reason = self.catalog.match(observation.get('body', ''), speaker, prefix=early)
            if line:
                self.schedule.wake(time.monotonic(), 2)
                accepted = self.controller.select(line, 'subtitle', captured, native=bool(self.native_voice))
                if accepted:
                    self.visual_id = line.identity
            else:
                current = self.controller.current
                if current and current['source'] == 'subtitle':
                    actual = text_key(observation.get('body', ''))
                    expected = text_key(current['line'].text)
                    if actual and not expected.startswith(actual):
                        self.controller.invalidate(captured)
                self.callback(dict(match_status=reason))

    def log_event(self, event, at):
        with self.lock:
            if self.stop_event.is_set():
                return
            self.schedule.wake(at)
            if event.kind == 'page':
                self.native_voice = None
                self.controller.invalidate(at)
                self.visual_id = None
                self.last_blank = False
            elif event.kind == 'line':
                if event.native_voice:
                    if not self.native_voice or self.native_voice['key'] != event.key:
                        self.native_voice = dict(key=event.key, at=at, reference_at=None)
                else:
                    self.native_voice = None
                self.last_blank = False
                if self.capture is None or not self.capture.ready_for_ocr():
                    return
                line = self.catalog.by_key(event.key)
                if line:
                    self.controller.select(line, 'log', at, native=event.native_voice)
                else:
                    self.controller.invalidate(at)
                    self.callback(dict(match_status='日志编号尚未匹配，正在核对字幕'))
            elif event.kind == 'hide':
                if self.native_voice and (not event.key or self.native_voice['key'] == event.key):
                    self.native_voice = None
                current = self.controller.current
                if current and (event.key == current['line'].text_key or
                                not event.key and current['source'] == 'log'):
                    self.controller.invalidate(at)
                    self.visual_id = None

    def _logs(self):
        tail = self.tail
        while not self.stop_event.wait(.05):
            try:
                before = tail.resets
                events = tail.poll()
                if tail.resets != before:
                    self.controller.invalidate(time.monotonic())
                for event in events:
                    self.log_event(event, time.monotonic())
                if self.log_status != tail.state:
                    self.log_status = tail.state
                    self.callback(dict(log_status=tail.state))
            except OSError as exc:
                message = '日志暂不可读，字幕定位继续运行 · ' + str(exc)
                if self.log_status != message:
                    self.log_status = message
                    self.callback(dict(log_status=message))
            except Exception as exc:
                self.emit(dict(error='日志处理失败 · ' + str(exc)))
                self.cancel()

    def run(self, window, *, seconds=None):
        from app import run_loop
        from PySide6.QtCore import QLockFile
        options = self.config.get('dubbing', {})
        settings = self.config.get('nte_hybrid', {})
        self.directory.mkdir(parents=True, exist_ok=True)
        database = catalog_path(self.root, self.config)
        database.parent.mkdir(parents=True, exist_ok=True)
        lease = QLockFile(str(database.parent / 'live.lock'))
        lease.setStaleLockTime(0)
        if not lease.tryLock(0):
            raise ValueError('已有异环实时配音正在运行，请切换到已打开的窗口')
        logs = None
        try:
            self.callback(dict(status_text='正在检查原文库与声音设置…'))
            _, aliases = playback_settings(options)
            self.catalog = NteCatalog(database,
                                      player_name=settings.get('player_name', '鉴定师'), aliases=aliases)
            current_stamp = resource_stamp(settings.get('game_root', ''))
            if current_stamp != self.catalog.metadata.get('resource_stamp'):
                raise ValueError('异环资源已更新，请在任务库与配音准备页点击“自动识别并提取”后开始')
            runtime = VoiceRuntime(options)
            runtime.prepare(self.stop_event, lambda text: self.callback(dict(status_text=text)))
            if self.stop_event.is_set():
                return
            audio = NteAudio(self.root, runtime, aliases, self.preferred_project)
            self.controller = HybridPlayback(audio, self.emit)
            log_path = settings.get('log_path') or str(Path(os.environ['LOCALAPPDATA']) / 'HT/Saved/Logs/HT.log')
            self.tail = NteLogTail(log_path)
            logs = threading.Thread(target=self._logs, name='nte-log-reader', daemon=True)
            config = copy.deepcopy(self.config)
            config.update(capture_fps=10, render_stable_seconds=.2, ocr_warmup=True)
            config['dubbing']['enabled'] = False

            def captured(capture):
                self.capture = capture
                self.watch = PlaybackFrameWatch(capture, config, self._watch_event)
                self.watch.start()
                logs.start()
                self.callback(dict(status_text='已连接异环，等待当前台词', ready=True))

            def status(event):
                if 'status_text' in event:
                    self.callback(dict(status_text=event['status_text']))

            run_loop(window, config, self.stop_event, status, seconds=seconds, on_capture=captured,
                     records_root=self.root, on_state=self._state, on_observation=self.observe,
                     on_prefix_observation=self.observe, on_visual_observation=self._visual,
                     frame_ready=lambda packet: self.schedule.ready(packet.captured_at))
        finally:
            self.stop_event.set()
            if self.watch:
                self.watch.close()
            if logs and logs.ident is not None:
                logs.join()
            if self.controller:
                self.controller.close()
            if self.catalog:
                self.catalog.close()
            lease.unlock()
