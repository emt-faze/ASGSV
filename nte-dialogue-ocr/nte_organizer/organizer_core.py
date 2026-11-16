"""Offline catalog browsing, historical-log indexing, and persistent dialogue preparation."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from uuid import uuid4

from .bootstrap import ASGSV
from dialogue_export import _cell
from dialogue_generation import DialogueRow, GenerationProject
from nte_catalog import rendered_text
from nte_log_stream import decode_line, parse_event

CHINA = timezone(timedelta(hours=8))
VOICE_LABELS = {'voiced': '日志含原声事件', 'silent': '日志标记无原声', 'unknown': '原声状态待确认'}


class Cancelled(Exception):
    pass


def check_cancel(stop):
    if stop is not None and stop.is_set():
        raise Cancelled('操作已取消')


def local_time(value=None):
    when = datetime.now(CHINA) if value is None else datetime.fromtimestamp(value, CHINA)
    return when.isoformat(timespec='seconds')


def literal_like(value):
    return '%' + str(value).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def text_issue(text):
    if not text.strip():
        return '台词为空'
    if text.startswith('<!>'):
        return '资源占位文本'
    if re.search(r'\{[^}]+\}', text):
        return '变量待填写'
    return ''


def voice_status(row):
    if row.get('voiced', 0):
        return 'voiced'
    if row.get('silent', 0):
        return 'silent'
    return 'unknown'


def scan_log(path, stop=None, progress=lambda text: None):
    """Read a bounded snapshot without altering or following the game log."""
    path = Path(path).resolve()
    digest = hashlib.sha256()
    result = dict(path=str(path), size=0, decoded=0, invalid=0, partial=0, generic=0, events=[])
    with path.open('rb') as stream:
        remaining = os.fstat(stream.fileno()).st_size
        result['size'] = remaining
        line_number = 0
        while remaining:
            check_cancel(stop)
            raw = stream.readline(min(remaining, 8 * 1024 * 1024))
            if not raw:
                raise ValueError('读取过程中日志被截断，请重新导入 ' + path.name)
            remaining -= len(raw)
            digest.update(raw)
            line_number += 1
            if not raw.endswith(b'\n'):
                if not remaining:
                    result['partial'] += 1
                    break
                # Bound even damaged lines without keeping an arbitrarily large buffer.
                while remaining and not raw.endswith(b'\n'):
                    check_cancel(stop)
                    raw = stream.readline(min(remaining, 1024 * 1024))
                    if not raw:
                        raise ValueError('读取过程中日志被截断，请重新导入 ' + path.name)
                    remaining -= len(raw)
                    digest.update(raw)
                result['invalid'] += 1
                continue
            raw = raw.removeprefix(b'\xef\xbb\xbf').strip()
            if not raw:
                continue
            try:
                texts = [raw.decode('utf-8')] if raw.startswith((b'[', b'Log file open')) else decode_line(raw)
                result['decoded'] += 1
                for index, text in enumerate(texts):
                    event = parse_event(text)
                    if event is None:
                        continue
                    if event.kind == 'page':
                        result['generic'] += 1
                    if event.kind != 'line' or not event.key:
                        continue
                    state = 'voiced' if event.native_voice else 'unknown'
                    if re.search(r'hasVoice=0(?:\D|$)', text):
                        state = 'silent'
                    identity = hashlib.sha256(text.strip().encode('utf-8')).hexdigest()
                    result['events'].append((identity, event.key, event.timestamp, state, line_number, index))
            except (ValueError, UnicodeError):
                result['invalid'] += 1
            if line_number % 1024 == 0:
                progress(f'正在读取 {path.name} · {(result["size"]-remaining)/1024**2:.1f}/{result["size"]/1024**2:.1f} MB')
    check_cancel(stop)
    result['signature'] = digest.hexdigest()
    return result


class Organizer:
    def __init__(self, data_directory, catalog_path, *, player_name='鉴定师', gender=None):
        self.directory = Path(data_directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.catalog_path = Path(catalog_path).resolve()
        self.player_name = player_name.strip() or '鉴定师'
        if gender not in (None, 0, 1):
            raise ValueError('请选择有效的主角称谓分支')
        self.gender = gender
        self.db = sqlite3.connect((self.directory / 'organizer.sqlite').as_uri(), uri=True, timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS log_files(
                signature TEXT PRIMARY KEY, size INTEGER NOT NULL, decoded INTEGER NOT NULL,
                invalid INTEGER NOT NULL, partial INTEGER NOT NULL, generic INTEGER NOT NULL, imported_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS log_sources(
                file_id TEXT NOT NULL REFERENCES log_files(signature), path TEXT NOT NULL,
                PRIMARY KEY(file_id,path));
            CREATE TABLE IF NOT EXISTS log_events(
                event_id TEXT PRIMARY KEY, text_key TEXT NOT NULL, time REAL, voice TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS log_keys ON log_events(text_key);
            CREATE TABLE IF NOT EXISTS log_occurrences(
                file_id TEXT NOT NULL REFERENCES log_files(signature), line_number INTEGER NOT NULL,
                event_index INTEGER NOT NULL, event_id TEXT NOT NULL REFERENCES log_events(event_id),
                PRIMARY KEY(file_id,line_number,event_index));
            CREATE INDEX IF NOT EXISTS event_sources ON log_occurrences(event_id);
            CREATE TABLE IF NOT EXISTS log_summary(
                text_key TEXT PRIMARY KEY, event_count INTEGER NOT NULL, first_seen REAL, last_seen REAL,
                voiced INTEGER NOT NULL, silent INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS work_items(
                identity TEXT PRIMARY KEY, namespace TEXT NOT NULL, text_key TEXT NOT NULL,
                speaker_key TEXT NOT NULL, original_speaker TEXT NOT NULL, original_text TEXT NOT NULL,
                speaker TEXT NOT NULL, text TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                selected INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL,
                fingerprint TEXT NOT NULL, added_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS work_position ON work_items(position);
        ''')
        try:
            self.attach_catalog()
        except Exception:
            self.db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.db.close()

    def attach_catalog(self):
        if not self.catalog_path.is_file():
            self.db.execute("ATTACH DATABASE ':memory:' AS catalog")
            self.db.executescript('''
                CREATE TABLE catalog.metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE catalog.lines(namespace TEXT, text_key TEXT, body TEXT,
                    speaker_key TEXT, speaker TEXT, fingerprint TEXT, normalized TEXT, dialogue INTEGER);
            ''')
            self.metadata = dict(game='nte', schema='1', entry_count='0')
            return
        self.db.execute('ATTACH DATABASE ? AS catalog', (self.catalog_path.as_uri() + '?mode=ro',))
        self.metadata = dict(self.db.execute('SELECT key,value FROM catalog.metadata'))
        if self.metadata.get('game') != 'nte' or self.metadata.get('schema') != '1':
            raise ValueError('请选择异环适配版生成的原文库 catalog.sqlite')

    def detach_catalog(self):
        self.db.commit()
        self.db.execute('DETACH DATABASE catalog')

    def stats(self):
        return dict(resources=self.db.execute('SELECT count(*) FROM catalog.lines').fetchone()[0],
                    dialogue=self.db.execute('SELECT count(*) FROM catalog.lines WHERE dialogue=1').fetchone()[0],
                    events=self.db.execute('SELECT count(*) FROM log_events').fetchone()[0],
                    work=self.db.execute('SELECT count(*) FROM work_items').fetchone()[0],
                    selected=self.db.execute('SELECT count(*) FROM work_items WHERE selected=1').fetchone()[0])

    def groups(self, dialogue_only=True):
        where = 'WHERE dialogue=1' if dialogue_only else ''
        return [row[0] for row in self.db.execute('SELECT DISTINCT namespace FROM catalog.lines ' + where + ' ORDER BY namespace')]

    def speakers(self):
        rows = self.db.execute("SELECT DISTINCT speaker FROM catalog.lines WHERE dialogue=1 AND speaker!='' ORDER BY speaker")
        return sorted({row[0] for row in rows} | {self.player_name})

    def _where(self, filters):
        clauses, values = [], []
        if filters.get('dialogue_only', True):
            clauses.append('l.dialogue=1')
        keyword = filters.get('keyword', '').strip()
        if keyword:
            clauses.append("(l.body LIKE ? ESCAPE '\\' OR l.text_key LIKE ? ESCAPE '\\')")
            values.extend([literal_like(keyword)] * 2)
        speaker = filters.get('speaker', '').strip()
        if speaker == '角色待补全':
            clauses.append("l.speaker='' AND l.speaker_key!='PLAYER'")
        elif speaker:
            clauses.append("(l.speaker LIKE ? ESCAPE '\\' OR (l.speaker_key='PLAYER' AND ? LIKE ? ESCAPE '\\'))")
            values.extend([literal_like(speaker), self.player_name, literal_like(speaker)])
        if filters.get('namespace'):
            clauses.append('l.namespace=?')
            values.append(filters['namespace'])
        if filters.get('source') == 'history':
            clauses.extend(['s.event_count>0', '(SELECT count(*) FROM catalog.lines k WHERE k.text_key=l.text_key)=1'])
        status = filters.get('voice', '')
        if status == 'voiced':
            clauses.append('s.voiced>0')
        elif status == 'silent':
            clauses.append('s.silent>0 AND s.voiced=0')
        elif status == 'unknown':
            clauses.append('COALESCE(s.voiced,0)=0 AND COALESCE(s.silent,0)=0')
        return ' AND '.join(clauses) or '1', values

    def _display(self, row):
        value = dict(row)
        value['identity'] = value['namespace'] + '/' + value['text_key']
        value['original_text'] = value['body']
        value['text'] = rendered_text(value['body'], self.player_name, self.gender)
        value['speaker'] = self.player_name if value['speaker_key'] == 'PLAYER' else value['speaker']
        value['issue'] = text_issue(value['text'])
        value['voice_status'] = voice_status(value)
        return value

    def browse(self, filters=None, *, page=0, page_size=200, all_rows=False):
        filters = filters or {}
        where, values = self._where(filters)
        source = ('FROM catalog.lines l LEFT JOIN log_summary s ON s.text_key=l.text_key '
                  'AND (SELECT count(*) FROM catalog.lines k WHERE k.text_key=l.text_key)=1 WHERE ' + where)
        count = self.db.execute('SELECT count(*) ' + source, values).fetchone()[0]
        order = 's.first_seen, l.namespace,l.text_key' if filters.get('source') == 'history' else 'l.namespace,l.text_key'
        query = ('SELECT l.*,COALESCE(s.event_count,0) event_count,s.first_seen,s.last_seen,'
                 'COALESCE(s.voiced,0) voiced,COALESCE(s.silent,0) silent ' + source + ' ORDER BY ' + order)
        if not all_rows:
            query += ' LIMIT ? OFFSET ?'
            values += [page_size, max(0, page) * page_size]
        return [self._display(row) for row in self.db.execute(query, values)], count

    def unresolved_keys(self):
        return [dict(row) for row in self.db.execute('''
            SELECT s.*, (SELECT count(*) FROM catalog.lines l WHERE l.text_key=s.text_key) candidates
            FROM log_summary s WHERE (SELECT count(*) FROM catalog.lines l WHERE l.text_key=s.text_key)!=1
            ORDER BY s.first_seen,s.text_key
        ''')]

    def provenance(self, key):
        return [dict(row) for row in self.db.execute('''
            SELECT DISTINCT e.time,e.voice,s.path,o.line_number
            FROM log_events e JOIN log_occurrences o USING(event_id) JOIN log_sources s ON s.file_id=o.file_id
            WHERE e.text_key=? ORDER BY e.time,s.path,o.line_number LIMIT 100
        ''', (key,))]

    def import_logs(self, paths, stop=None, progress=lambda text: None):
        paths = list(dict.fromkeys(str(Path(path).resolve()) for path in paths))
        if not paths:
            raise ValueError('请选择游戏日志文件')
        snapshots = []
        for number, path in enumerate(paths, 1):
            check_cancel(stop)
            progress(f'正在解析日志 {number}/{len(paths)} · {Path(path).name}')
            snapshots.append(scan_log(path, stop, progress))
        if not any(item['decoded'] for item in snapshots):
            raise ValueError('所选文件没有可解码的异环日志记录')
        before = self.stats()['events']
        try:
            self.db.execute('BEGIN IMMEDIATE')
            for item in snapshots:
                check_cancel(stop)
                self.db.execute('INSERT OR IGNORE INTO log_files VALUES(?,?,?,?,?,?,?)',
                    (item['signature'], item['size'], item['decoded'], item['invalid'], item['partial'], item['generic'], local_time()))
                self.db.execute('INSERT OR IGNORE INTO log_sources VALUES(?,?)', (item['signature'], item['path']))
                for index, (event_id, key, when, voice, line, event_index) in enumerate(item['events']):
                    if index % 256 == 0:
                        check_cancel(stop)
                    self.db.execute('INSERT OR IGNORE INTO log_events VALUES(?,?,?,?)', (event_id,key,when,voice))
                    self.db.execute('INSERT OR IGNORE INTO log_occurrences VALUES(?,?,?,?)',
                                    (item['signature'],line,event_index,event_id))
            self.db.execute('DELETE FROM log_summary')
            self.db.execute('''INSERT INTO log_summary
                SELECT text_key,count(*),min(time),max(time),sum(voice='voiced'),sum(voice='silent')
                FROM log_events GROUP BY text_key''')
            check_cancel(stop)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return dict(files=len(snapshots), added_events=self.stats()['events']-before,
                    keys=self.db.execute('SELECT count(*) FROM log_summary').fetchone()[0],
                    unresolved=len(self.unresolved_keys()), invalid=sum(item['invalid'] for item in snapshots),
                    partial=sum(item['partial'] for item in snapshots), generic=sum(item['generic'] for item in snapshots))

    def add(self, identities=None, *, filters=None, stop=None, progress=lambda text: None):
        if identities is None:
            rows, _ = self.browse(filters, all_rows=True)
        else:
            rows = []
            for identity in dict.fromkeys(identities):
                namespace, separator, key = identity.partition('/')
                if not separator:
                    raise ValueError('台词编号缺少资源分组')
                row = self.db.execute('SELECT * FROM catalog.lines WHERE namespace=? AND text_key=?', (namespace,key)).fetchone()
                if row:
                    rows.append(self._display(row))
        added = 0
        try:
            self.db.execute('BEGIN IMMEDIATE')
            position = self.db.execute('SELECT COALESCE(max(position),0) FROM work_items').fetchone()[0]
            for index, row in enumerate(rows):
                if index % 256 == 0:
                    check_cancel(stop)
                    progress(f'正在加入整理清单 {index}/{len(rows)}')
                now = local_time()
                cursor = self.db.execute('''INSERT OR IGNORE INTO work_items
                    (identity,namespace,text_key,speaker_key,original_speaker,original_text,speaker,text,
                     position,fingerprint,added_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (row['identity'],row['namespace'],row['text_key'],row['speaker_key'],row['speaker'],row['original_text'],
                     row['speaker'],row['text'],position+1,row['fingerprint'],now,now))
                if cursor.rowcount:
                    position += 1
                    added += 1
            check_cancel(stop)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return dict(added=added, existing=len(rows)-added)

    def work(self, *, page=0, page_size=200, all_rows=False, selected_only=False):
        where = 'WHERE w.selected=1' if selected_only else ''
        count = self.db.execute('SELECT count(*) FROM work_items w ' + where).fetchone()[0]
        query = ('''SELECT w.*,COALESCE(s.event_count,0) event_count,s.first_seen,s.last_seen,
                    COALESCE(s.voiced,0) voiced,COALESCE(s.silent,0) silent
                    FROM work_items w LEFT JOIN log_summary s ON s.text_key=w.text_key
                    AND (SELECT count(*) FROM catalog.lines k WHERE k.text_key=w.text_key)=1 ''' + where + ' ORDER BY w.position')
        values = []
        if not all_rows:
            query += ' LIMIT ? OFFSET ?'
            values = [page_size, max(0,page)*page_size]
        rows = [dict(row) for row in self.db.execute(query, values)]
        for row in rows:
            row['voice_status'] = voice_status(row)
            row['issue'] = text_issue(row['text'])
        return rows, count

    def edit(self, identity, *, speaker, text, note):
        with self.db:
            self.db.execute('UPDATE work_items SET speaker=?,text=?,note=?,updated_at=? WHERE identity=?',
                            (speaker.strip(),text.strip(),note.strip(),local_time(),identity))

    def select(self, identities, value):
        with self.db:
            self.db.executemany('UPDATE work_items SET selected=? WHERE identity=?', [(int(value),identity) for identity in identities])

    def remove(self, identities):
        with self.db:
            self.db.executemany('DELETE FROM work_items WHERE identity=?', [(identity,) for identity in identities])

    def move(self, identity, direction):
        if direction not in (-1, 1):
            raise ValueError('无效的移动方向')
        with self.db:
            current = self.db.execute('SELECT position FROM work_items WHERE identity=?', (identity,)).fetchone()
            if current is None:
                return
            comparator, order = ('<', 'DESC') if direction == -1 else ('>', 'ASC')
            neighbor = self.db.execute(f'SELECT identity,position FROM work_items WHERE position {comparator} ? ORDER BY position {order} LIMIT 1',
                                       (current[0],)).fetchone()
            if neighbor:
                self.db.executemany('UPDATE work_items SET position=? WHERE identity=?',
                                    [(neighbor['position'],identity),(current[0],neighbor['identity'])])

    def export(self, destination, format, stop=None, progress=lambda text: None):
        rows, _ = self.work(all_rows=True, selected_only=True)
        if not rows:
            raise ValueError('请先在整理清单中勾选台词')
        destination = Path(destination).resolve()
        if format not in ('csv','json','txt') or destination.suffix.lower() != '.' + format:
            raise ValueError('请选择 CSV、JSON 或 TXT 文件')
        if destination == self.catalog_path or destination.is_relative_to(self.directory):
            raise ValueError('请将导出文件保存到工具数据目录以外的位置')
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name('.' + uuid4().hex + '.tmp')
        try:
            check_cancel(stop)
            with temporary.open('w', encoding='utf-8-sig' if format in ('csv','txt') else 'utf-8', newline='') as stream:
                if format == 'csv':
                    writer = csv.writer(stream)
                    writer.writerow(['角色','整理后的台词','原文','台词编号','资源分组','角色编号','日志事件数','原声记录','首次记录','最后记录','备注','待处理事项'])
                    for number, row in enumerate(rows):
                        check_cancel(stop)
                        writer.writerow([_cell(value) for value in (row['speaker'],row['text'],row['original_text'],row['text_key'],
                            row['namespace'],row['speaker_key'],row['event_count'],VOICE_LABELS[row['voice_status']],
                            local_time(row['first_seen']) if row['first_seen'] is not None else '',
                            local_time(row['last_seen']) if row['last_seen'] is not None else '',row['note'],row['issue'])])
                elif format == 'json':
                    stream.write('{"version":1,"game_id":"nte","rows":[\n')
                    for number, row in enumerate(rows):
                        check_cancel(stop)
                        stream.write((',' if number else '') + json.dumps(row, ensure_ascii=False) + '\n')
                    stream.write(']}\n')
                else:
                    for number, row in enumerate(rows, 1):
                        check_cancel(stop)
                        stream.write(f"{number}. {row['speaker'] or '角色待补全'} · {row['identity']}\n{row['text']}\n")
                        if row['note']:
                            stream.write(row['note'] + '\n')
                        stream.write('\n')
            check_cancel(stop)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return dict(path=str(destination), rows=len(rows))

    def create_project(self, parent, name, mode='system', options=None, stop=None, progress=lambda text: None):
        rows, _ = self.work(all_rows=True, selected_only=True)
        if not rows:
            raise ValueError('请先在整理清单中勾选台词')
        if mode not in ('system','character'):
            raise ValueError('请选择有效的配音模式')
        parent = Path(parent).resolve()
        parent.mkdir(parents=True, exist_ok=True)
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:32] or '整理台词'
        suffix = uuid4().hex[:8]
        staging = parent / ('.organizer-' + suffix)
        destination = parent / (datetime.now(CHINA).strftime('%Y%m%d-%H%M%S-') + title + '-' + suffix)
        options = options or {}
        project = GenerationProject(staging, mode=mode, game_id='nte')
        held = 0
        try:
            for number, item in enumerate(rows, 1):
                check_cancel(stop)
                voice = 'auto'
                if mode == 'system':
                    preset = options.get('system_character_voices', {}).get(item['speaker'], options.get('system_default_voice', 'default'))
                    voice = 'system:' + preset
                row = DialogueRow(uuid4().hex,'nte_organizer',item['identity'],1,{},item['speaker'],
                                  item['text'].strip(),voice=voice,edited=True)
                row.selected = not row.punctuation_only
                project.rows.append(row)
                row.evidence = dict(game_id='nte',source_kind='nte_localization',catalog_id=item['identity'],
                    namespace=item['namespace'],text_key=item['text_key'],speaker_key=item['speaker_key'],
                    original_text=item['original_text'],catalog_fingerprint=item['fingerprint'],
                    organizer_note=item['note'],voice_status=item['voice_status'])
                row.confirmed = not bool(item['issue'])
                held += int(bool(item['issue']))
                if number % 256 == 0:
                    progress(f'正在建立配音项目 {number}/{len(rows)}')
            check_cancel(stop)
            project.save()
            check_cancel(stop)
            if staging.resolve().parent != parent or destination.resolve().parent != parent:
                raise ValueError('配音项目保存路径无效')
            staging.replace(destination)
            return dict(path=str(destination), rows=len(rows), held=held)
        finally:
            if staging.exists() and staging.resolve().parent == parent and staging.name.startswith('.organizer-'):
                shutil.rmtree(staging)
