"""One output owner. A new subtitle cancels pending and active audio immediately."""
from concurrent.futures import ThreadPoolExecutor
import threading
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

    def _emit(self, state, message, line):
        self.callback(dict(state=state, message=message, text=line.text, row_id=line.row_id, voice=line.voice))

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
            self.token.set()
            self.token = threading.Event()
            self.thread.submit(self._play, self.line, self.token)
            return True

    def _play(self, line, token):
        did_start = False
        def started():
            nonlocal did_start
            if not token.is_set():
                did_start = True
                self._emit('playing', '正在播放 · ' + line.speaker, line)
        try:
            if token.is_set():
                return
            if file_digest(line.path) != line.sha256:
                raise ValueError('音频校验失败')
            self.player.play(line.path, cancel_event=token, on_started=started)
            if did_start:
                self._emit('interrupted' if token.is_set() else 'completed',
                           '已停止上一句' if token.is_set() else '本句播放完成', line)
        except Exception as exc:
            if not token.is_set():
                self._emit('error', '播放失败 · ' + str(exc), line)

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

