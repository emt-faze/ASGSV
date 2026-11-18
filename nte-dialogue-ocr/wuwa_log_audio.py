"""One output owner. A new subtitle cancels pending and active audio immediately."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import wave
from dialogue_generation import file_digest


class LogPlayback:
    def __init__(self, player, callback):
        self.player = player
        self.callback = callback
        self.thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix='wuwa-output')
        self.lock = threading.RLock()
        self.token = threading.Event()
        self.line = None
        self.paused = self.skipped = self.closed = False

    def clear(self):
        with self.lock:
            self.token.set()
            self.line = None
            self.skipped = False

    def _emit(self, state, message, line, **detail):
        self.callback(dict(state=state, message=message, text=line.text, row_id=line.row_id,
                           catalog_id=line.catalog_id, voice=line.voice, **detail))

    def available(self, line):
        with self.lock:
            if self.closed or self.line == line:
                return
            self.token.set()
            self.line = line
            if not self.paused and not self.skipped:
                self.replay()

    def replay(self):
        with self.lock:
            if self.line is None or self.closed or self.paused:
                return False
            self.skipped = False
            return self._submit(self.line, history=False)

    def audition(self, line):
        with self.lock:
            if self.closed or self.paused:
                return False
            return self._submit(line, history=True)

    def _submit(self, line, *, history):
        self.token.set()
        self.token = threading.Event()
        self.thread.submit(self._play, line, self.token, history)
        return True

    def _play(self, line, token, history):
        started_at = None
        duration = 0.0

        def started():
            nonlocal started_at
            if not token.is_set():
                started_at = time.monotonic()
                message = ('重听历史 · ' if history else '正在播放 · ') + line.speaker
                self._emit('playing', message, line, history=history,
                           started_at=started_at, duration=duration)
        try:
            if token.is_set():
                return
            if file_digest(line.path) != line.sha256:
                raise ValueError('音频校验失败')
            try:
                with wave.open(str(line.path), 'rb') as wav:
                    duration = wav.getnframes() / wav.getframerate()
            except (wave.Error, EOFError):
                pass  # The output device reports any actual decoding failure.
            self.player.play(line.path, cancel_event=token, on_started=started)
            if started_at is not None:
                self._emit('interrupted' if token.is_set() else 'completed',
                           '已停止上一句' if token.is_set() else '本句播放完成', line, history=history, started_at=started_at)
        except Exception as exc:
            if not token.is_set():
                self._emit('error', '播放失败 · ' + str(exc), line, history=history, started_at=started_at)

    def skip(self):
        with self.lock:
            self.skipped = True
            self.token.set()

    def pause(self, paused):
        with self.lock:
            self.paused = paused
            if paused:
                self.token.set()
            elif not self.skipped:
                self.replay()

    def stop(self):
        with self.lock:
            self.closed = True
            self.clear()

    def close(self):
        self.stop()
        self.thread.shutdown(wait=True, cancel_futures=True)

