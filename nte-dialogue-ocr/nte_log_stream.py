"""Incremental read-only decoding of NTE logs, including partial writes and rotation."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

KEYS = (b'1zh6IOlIohrR88UNPjiLisrkWACUQYuz', b'UVbP6pjjw5KZhvddie3tfhg1pVkkveY8')
STAMP = re.compile(r'^\[(\d{4}\.\d\d\.\d\d-\d\d\.\d\d\.\d\d\.\d+)\]\[([+-]\d{4})\]')


def decode_line(raw):
    raw = raw.removeprefix(b'\xef\xbb\xbf').strip()
    encrypted = base64.b64decode(raw, validate=True)
    if not encrypted or len(encrypted) % 16:
        raise ValueError('异环日志密文长度无效')
    for key in KEYS:
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        plain = decryptor.update(encrypted) + decryptor.finalize()
        # The game's line writer also uses literal tab padding after |SPLIT|.
        padding = plain[-1]
        if 1 <= padding <= 16 and plain[-padding:] == bytes([padding]) * padding:
            plain = plain[:-padding]
        try:
            text = plain.decode('utf-8').rstrip('\x00\t\r\n')
            if '|SPLIT|' in text or STAMP.match(text):
                return [part for part in text.split('|SPLIT|') if part.strip()]
        except UnicodeDecodeError:
            continue
    raise ValueError('当前异环日志格式无法解码')


@dataclass(frozen=True)
class LogEvent:
    kind: str
    key: str = ''
    native_voice: bool = False
    timestamp: float | None = None


def parse_event(text):
    stamp = STAMP.match(text)
    timestamp = datetime.strptime(stamp[1] + stamp[2], '%Y.%m.%d-%H.%M.%S.%f%z').timestamp() if stamp else None
    if '[DlgVoice]' in text and 'InnerPlayVoice:' in text:
        found = re.search(r'event=(Play_Vo_[A-Za-z0-9_]+)', text)
        if found:
            return LogEvent('line', found[1].removeprefix('Play_Vo_'), True, timestamp)
    if '[VoiceMsg]' in text:
        found = re.search(r'MsgID=([A-Za-z0-9_]+)', text)
        key = found[1] if found else ''
        if 'ShowCurMsg:' in text and key:
            voice = re.search(r'hasVoice=(\d+)', text)
            return LogEvent('line', key, bool(voice and int(voice[1])), timestamp)
        if 'InnerPlayDialogueVoice:' in text and key:
            return LogEvent('line', key, True, timestamp)
        if 'HideMsg:' in text:
            return LogEvent('hide', key, timestamp=timestamp)
    if 'UUI_Dialogue::GetDlgSeqObj' in text:
        return LogEvent('page', timestamp=timestamp)
    if 'WillInteract' in text:
        return LogEvent('wake', timestamp=timestamp)
    return None


class NteLogTail:
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.started = clock()
        self.identity = None
        self.offset = 0
        self.partial = b''
        self.prefix = b''
        self.errors = 0
        self.decoded = 0
        self.resets = 0
        self.state = '等待游戏日志'
        self._open_at_end()

    def _open_at_end(self):
        try:
            with self.path.open('rb') as stream:
                stat = self.path.stat()
                self.identity = (stat.st_dev, stat.st_ino, getattr(stat, 'st_birthtime_ns', 0))
                self.prefix = stream.read(128)
                stream.seek(0, 2)
                self.offset = stream.tell()
                # Discard the remainder of an incomplete historical physical line.
                if self.offset:
                    stream.seek(-1, 2)
                    self.discard_partial = stream.read(1) != b'\n'
                else:
                    self.discard_partial = False
                self.state = '日志已连接'
        except FileNotFoundError:
            self.discard_partial = False

    def poll(self):
        try:
            with self.path.open('rb') as stream:
                stat = self.path.stat()
                identity = (stat.st_dev, stat.st_ino, getattr(stat, 'st_birthtime_ns', 0))
                prefix = stream.read(len(self.prefix))
                if identity != self.identity or stat.st_size < self.offset or prefix != self.prefix:
                    self.identity, self.offset, self.partial = identity, 0, b''
                    self.discard_partial = False
                    self.resets += 1
                    stream.seek(0)
                    self.prefix = stream.read(128)
                stream.seek(self.offset)
                block = stream.read(1024 * 1024)
                self.offset += len(block)
        except FileNotFoundError:
            self.state = '等待游戏日志'
            return []
        data = self.partial + block
        lines = data.split(b'\n')
        self.partial = lines.pop()
        if len(self.partial) > 4 * 1024 * 1024:
            self.partial, self.discard_partial = b'', True
            self.errors += 1
        events = []
        for raw in lines:
            if self.discard_partial:
                self.discard_partial = False
                continue
            if not raw.strip():
                continue
            try:
                decoded = decode_line(raw)
                self.decoded += 1
                for text in decoded:
                    event = parse_event(text)
                    if event and event.timestamp is not None and self.started <= event.timestamp <= self.clock() + 2:
                        if self.clock() - event.timestamp <= 5:
                            events.append(event)
            except (ValueError, UnicodeError):
                self.errors += 1
        self.state = '日志格式需要检查，字幕定位继续运行' if self.errors else '日志已连接'
        return events
