"""Local NTE localization lookup with unambiguous, stable sentence identities."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from prepared_playback import text_key


@dataclass(frozen=True)
class NteLine:
    namespace: str
    text_key: str
    text: str
    speaker_key: str
    speaker: str
    fingerprint: str

    @property
    def identity(self):
        return self.namespace + '/' + self.text_key


def catalog_path(root, config=None):
    root = Path(root).resolve()
    if config is None:
        config = json.loads((root / 'config.json').read_text('utf-8-sig'))
    path = Path(config.get('nte_hybrid', {}).get('catalog_path') or 'nte-data/catalog.sqlite')
    return (root / path).resolve()


def resource_stamp(game_root):
    root = Path(game_root) / 'Client/WindowsNoEditor/HT/Content'
    files = sorted(path for folder in ('Paks', 'PatchPaks', 'TagPatchPaks')
                   for path in (root / folder).rglob('*')
                   if path.suffix.lower() in {'.pak', '.utoc', '.ucas'} and path.is_file())
    if not files:
        raise ValueError('未找到异环资源文件，请检查游戏安装目录')
    return hashlib.sha256(json.dumps([(str(p.relative_to(root)), p.stat().st_size,
                                      p.stat().st_mtime_ns) for p in files]).encode()).hexdigest()


def actor_key(value):
    if re.search(r'(?:^|_)Player(?:_|$)', value, re.I):
        return 'PLAYER'
    found = re.search(r'(?:^|_)(C?NPC)_?(\d+)(?:_|$)', value, re.I)
    return (found[1] + found[2]).upper() if found else ''


def rendered_text(body, player_name='', gender=None):
    body = re.sub(r'</?[A-Za-z][^>]*>|</>', '', body)
    if player_name:
        body = re.sub(r'\{playername\}', lambda _: player_name, body, flags=re.I)
    if gender is not None:
        body = re.sub(r'\{Gender\}\|gender\(([^,]+),([^)]+)\)', lambda m: m[gender + 1], body)
    return body


def build_catalog(entries, destination, metadata):
    """Publish a completely built database in one replace, preserving the old one on failure."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.' + uuid4().hex + '.sqlite')
    names = {actor_key(item['key']): item['text'] for item in entries
             if item['namespace'] == 'ST_ActorName' and item['key'].endswith('_Name')
             and actor_key(item['key'])}
    db = sqlite3.connect(temporary)
    try:
        db.executescript('''
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE lines(namespace TEXT NOT NULL, text_key TEXT NOT NULL,
                body TEXT NOT NULL, speaker_key TEXT NOT NULL, speaker TEXT NOT NULL,
                fingerprint TEXT NOT NULL, normalized TEXT NOT NULL, dialogue INTEGER NOT NULL,
                PRIMARY KEY(namespace, text_key));
            CREATE INDEX lookup_key ON lines(text_key);
            CREATE INDEX lookup_text ON lines(normalized);
        ''')
        rows = []
        for item in entries:
            namespace, key, body = item['namespace'], item['key'], item['text']
            speaker_key = actor_key(key)
            speaker = '鉴定师' if speaker_key == 'PLAYER' else names.get(speaker_key, '')
            eligible = bool(re.match(r'^ST_(?:.*dialog|main|sidequest|legend|voice|bubble|likeability|liking|chat|story|news|ambiance)',
                                     namespace, re.I)) and 'ui' not in namespace.lower() and not body.startswith('<!>')
            fingerprint = hashlib.sha256(body.encode('utf-8')).hexdigest()
            rows.append((namespace, key, body, speaker_key, speaker, fingerprint,
                         text_key(rendered_text(body)), int(eligible)))
        if not rows:
            raise ValueError('游戏原文为空，未替换已有台词库')
        db.executemany('INSERT INTO lines VALUES(?,?,?,?,?,?,?,?)', rows)
        values = dict(metadata, game='nte', language='zh-CN', schema='1', entry_count=str(len(rows)))
        db.executemany('INSERT INTO metadata VALUES(?,?)', [(k, str(v)) for k, v in values.items()])
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('台词库校验失败')
        db.close()
        temporary.replace(destination)
    finally:
        db.close()
        temporary.unlink(missing_ok=True)


class NteCatalog:
    def __init__(self, path, *, player_name='鉴定师', aliases=None):
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True,
                                  check_same_thread=False)
        self.metadata = dict(self.db.execute('SELECT key,value FROM metadata'))
        if self.metadata.get('game') != 'nte' or self.metadata.get('schema') != '1':
            self.db.close()
            raise ValueError('请更新异环原文库')
        self.player_name = player_name.strip() or '鉴定师'
        self.aliases = dict(aliases or {})
        self.aliases[text_key('鉴定师')] = self.role(self.player_name)
        self.dynamic = []
        for row in self.db.execute("SELECT * FROM lines WHERE dialogue=1 AND body LIKE '%{%' ").fetchall():
            for gender in (0, 1):
                line = self._line(row, gender=gender)
                if line and line not in self.dynamic:
                    self.dynamic.append(line)

    def close(self):
        self.db.close()

    def role(self, name):
        key = text_key(name)
        return self.aliases.get(key, key)

    def _line(self, row, *, gender=None):
        line = NteLine(*row[:6])
        text = rendered_text(line.text, self.player_name, gender)
        if re.search(r'\{[^}]+\}', text) or text.startswith('<!>'):
            return None
        return replace(line, text=text, fingerprint=hashlib.sha256(text.encode('utf-8')).hexdigest(),
                       speaker=self.player_name if line.speaker_key == 'PLAYER' else line.speaker)

    def by_key(self, key):
        key = key.removeprefix('Play_Vo_')
        rows = self.db.execute('SELECT * FROM lines WHERE text_key=?', (key,)).fetchall()
        return self._line(rows[0]) if len(rows) == 1 else None

    def match(self, text, speaker='', *, prefix=False):
        normalized = text_key(text)
        if not normalized:
            return None, '等待字幕'
        # Include longer candidates even for exact text, because a typewriter can
        # briefly display a shorter, otherwise valid sentence.
        rows = self.db.execute('SELECT * FROM lines WHERE dialogue=1 AND normalized>=? AND normalized<? LIMIT 201',
                               (normalized, normalized + '\U0010ffff')).fetchall()
        candidates = [line for row in rows if row[6].startswith(normalized) if (line := self._line(row))]
        candidates += [line for line in self.dynamic if text_key(line.text).startswith(normalized) and line not in candidates]
        role = self.role(speaker)
        if role:
            candidates = [line for line in candidates if not line.speaker or self.role(line.speaker) == role]
        if len(candidates) != 1 or len(rows) > 200:
            return None, '等待更多字幕或角色名' if candidates else '原文库中尚未定位这句'
        line = candidates[0]
        exact = text_key(line.text) == normalized
        if not exact and (not prefix or len(normalized) < 8):
            return None, '等待完整字幕'
        if not line.speaker and speaker:
            line = replace(line, speaker=speaker)
        return line, '字幕定位' if exact else '字幕前缀定位'
