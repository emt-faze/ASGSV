"""Read SDK login and in-game role evidence without confusing the two identifiers."""
from functools import lru_cache
import hashlib
from pathlib import Path
import re

from game_profiles import wuwa_client
from wuwa_log_stream import TABLE, format_of

SDK = re.compile(r'\[uId:\s*([0-9]+)\]')
ROLE = re.compile(r'设置当前UID\s*\[UID:\s*([1-9][0-9]{5,19})\]')
LOGIN = 'LoginProcedure-LoginRequest-登录成功'
ROLE_MARKER = '设置当前UID'.encode()


def account_key(identity):
    return hashlib.sha256(('wuwa-local-account:' + identity).encode()).hexdigest()


def log_files(install):
    folder = wuwa_client(install) / 'Saved/Logs'
    backups = sorted(folder.glob('Client-backup-*.log'), key=lambda p: p.name)
    current = folder / 'Client.log'
    return (backups + ([current] if current.is_file() else []))[-12:]


def log_events(path, *, quests=True):
    with path.open('rb') as stream:
        kind = format_of(stream.read(512))
        if kind is None:
            return
        encoded, offset = kind
        stream.seek(offset)
        tail = b''
        left = max(0, path.stat().st_size - offset)
        while left:
            chunk = stream.read(min(1 << 20, left))
            if not chunk:
                break
            left -= len(chunk)
            lines = (tail + (chunk.translate(TABLE) if encoded else chunk)).split(b'\n')
            tail = lines.pop()
            for line in lines:
                if ((quests and b'[Quest]' in line) or ROLE_MARKER in line
                        or b'[Login]' in line and (b'LoginProcedure-LoginRequest-' in line or b'[uId:' in line)):
                    yield line.decode('utf8', 'replace')


class LoginSession:
    def __init__(self):
        self.pending = self.sdk = self.role = None

    def feed(self, line):
        if '[Login]' in line:
            sdk = SDK.search(line)
            if sdk:
                self.pending = account_key(sdk[1])
                self.sdk = self.role = None
                return 'sdk'
            if LOGIN in line:
                self.sdk, self.pending, self.role = self.pending, None, None
                return 'login'
        if '[Log]' in line and (role := ROLE.search(line)):
            self.role = account_key('role:' + role[1])
            return role[1][-4:]
        return None


@lru_cache(maxsize=32)
def _roles(path, mtime_ns, size):
    session = LoginSession()
    result = set()
    for line in log_events(path, quests=False):
        event = session.feed(line)
        if event and session.role:
            result.add((session.sdk or '', session.role, event))
    return tuple(result)


def observed_roles(install):
    roles = set()
    for path in log_files(install):
        try:
            stat = path.stat()
            roles.update(_roles(path, stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            continue  # Concurrent log rotation.
    return roles

