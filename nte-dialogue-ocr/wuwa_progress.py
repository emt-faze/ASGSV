"""Read one local quest cache, without opening the game or writing to it."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from dialogue_generation import atomic_text, timestamp

PROGRESS_LABELS = {'completed': '已完成（有记录）', 'pending': '人工确认未完成', 'unknown': '进度未知'}


def read_finished_quests(install_root):
    path = Path(install_root).resolve() / 'Wuthering Waves Game/Client/Saved/LocalStorage/LocalStorage.db'
    if not path.is_file():
        raise ValueError('未找到本机任务缓存；未将任何任务判为未完成')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    try:
        connection.execute('PRAGMA query_only=ON')
        row = connection.execute('SELECT value FROM LocalStorage WHERE key=?', ('UserFinishedQuests',)).fetchone()
    finally:
        connection.close()
    if not row or not isinstance(row[0], str) or len(row[0]) > 4_000_000:
        raise ValueError('本机任务缓存缺失或格式不受支持')
    value = json.loads(row[0])
    if not isinstance(value, dict) or value.get('___MetaType___') != '___Map___' or not isinstance(value.get('Content'), list):
        raise ValueError('任务缓存 Map 格式不受支持')
    profiles = {}
    for pair in value['Content']:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('任务缓存账号条目格式不受支持')
        identity, quests = pair
        if not isinstance(identity, str) or not identity.strip() or not isinstance(quests, dict):
            raise ValueError('任务缓存账号或任务集合无效')
        if quests.get('___MetaType___') != '___Set___' or not isinstance(quests.get('Content'), list):
            raise ValueError('任务缓存 Set 格式不受支持')
        ids = quests['Content']
        if any(type(q) is not int or q <= 0 for q in ids):
            raise ValueError('任务 ID 格式不受支持')
        # Keep the game-specific cache identity opaque; never log raw account IDs.
        account = hashlib.sha256(('wuwa-local-account:' + identity).encode()).hexdigest()
        if account in profiles:
            raise ValueError('任务缓存出现重复账号')
        profiles[account] = {str(q) for q in ids}
    return dict(profiles=profiles, path=str(path), sha256=hashlib.sha256(row[0].encode()).hexdigest())


class QuestProgress:
    def __init__(self, catalog):
        self.catalog, self.db = catalog, catalog.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS quest_progress(
                account TEXT NOT NULL, quest_id TEXT NOT NULL, status TEXT NOT NULL,
                source TEXT NOT NULL, evidence TEXT NOT NULL, observed_at TEXT NOT NULL,
                PRIMARY KEY(account,quest_id));
            CREATE TABLE IF NOT EXISTS quest_progress_audit(
                id INTEGER PRIMARY KEY, account TEXT NOT NULL, quest_id TEXT NOT NULL,
                status TEXT NOT NULL, source TEXT NOT NULL, evidence TEXT NOT NULL, observed_at TEXT NOT NULL);
        ''')

    @property
    def account(self):
        return self.catalog.meta('progress_account')

    def refresh(self, install_root, account=None):
        snapshot = read_finished_quests(install_root)
        profiles = snapshot.pop('profiles')
        selected = account or self.account
        if not selected:
            if len(profiles) != 1:
                raise ValueError('本地任务缓存不是单一账号，请先选择账号；没有合并进度')
            selected = next(iter(profiles))
        if selected not in profiles:
            raise ValueError('已绑定账号不在本地任务缓存中，暂停更新；没有切换账号或合并进度')
        when = timestamp()
        evidence = json.dumps(dict(snapshot, key='UserFinishedQuests', completeness='partial'), ensure_ascii=False)
        ids = profiles[selected]
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('progress_account', selected))
            for qid in ids:
                previous = self.db.execute('SELECT status,source FROM quest_progress WHERE account=? AND quest_id=?',
                                           (selected, qid)).fetchone()
                # An explicit current task-list review can override a historical
                # completion entry for repeatable tasks. Refresh must preserve it.
                if previous and previous['source'] == 'manual':
                    continue
                self.db.execute('INSERT OR REPLACE INTO quest_progress VALUES(?,?,?,?,?,?)',
                                (selected, qid, 'completed', 'local_cache', evidence, when))
            known = {r[0].partition(':')[2] for r in self.db.execute("SELECT id FROM scopes WHERE kind='quest'")}
            report = dict(game_id='wuwa', account=selected, observed_at=when, source=snapshot,
                          recorded=len(ids), matched=len(ids & known), unmatched=len(ids - known),
                          completeness='partial', absent_status='unknown')
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('progress_last_scan', json.dumps(report)))
        # Preserve old positive evidence when the cache shrinks. Absence is not a reset.
        atomic_text(self.catalog.root / 'reports/quest-progress.json', json.dumps(report, ensure_ascii=False, indent=2))
        return report

    def states(self):
        return {r['quest_id']: r['status'] for r in self.db.execute(
            'SELECT quest_id,status FROM quest_progress WHERE account=?', (self.account,))}

    def review(self, scope_ids, status, basis):
        if not self.account:
            raise ValueError('先读取本机任务进度并绑定账号')
        if status not in PROGRESS_LABELS or len(basis.strip()) < 4:
            raise ValueError('请选择任务进度并填写核对依据')
        ids = []
        for scope in scope_ids:
            row = self.db.execute('SELECT kind FROM scopes WHERE id=?', (scope,)).fetchone()
            if not row or row[0] != 'quest':
                raise ValueError('只能核对任务，不能将 NPC 流程直接视作已完成任务')
            ids.append(scope.partition(':')[2])
        with self.db:
            for qid in ids:
                values = (self.account, qid, status, 'manual', basis.strip(), timestamp())
                self.db.execute('INSERT OR REPLACE INTO quest_progress VALUES(?,?,?,?,?,?)', values)
                self.db.execute('INSERT INTO quest_progress_audit(account,quest_id,status,source,evidence,observed_at) VALUES(?,?,?,?,?,?)', values)

    def line_states(self, scope_ids):
        if not scope_ids:
            return {}
        marks = ','.join('?' for _ in scope_ids)
        rows = self.db.execute(f'''SELECT DISTINCT chosen.line_id, q.scope_id FROM scope_lines chosen
            LEFT JOIN scope_lines q ON q.line_id=chosen.line_id AND q.scope_id LIKE 'quest:%'
            WHERE chosen.scope_id IN ({marks})''', tuple(scope_ids))
        memberships = {}
        for line, quest in rows:
            memberships.setdefault(line, set())
            if quest:
                memberships[line].add(quest)
        states, selected = self.states(), set(scope_ids)
        result = {}
        for line, quests in memberships.items():
            relevant = (quests & selected) or quests
            values = [states.get(q.partition(':')[2], 'unknown') for q in relevant]
            if values and all(v == 'completed' for v in values):
                result[line] = 'completed'
            elif 'pending' in values:
                result[line] = 'pending'
            else:
                result[line] = 'unknown'
        return result

    def allowed(self, scope_ids, allow_unknown=False):
        return {line for line, status in self.line_states(scope_ids).items()
                if status == 'pending' or (status == 'unknown' and allow_unknown)}

    def validate_generation(self, project):
        selection = json.loads((project.directory / 'selection.json').read_text(encoding='utf-8'))
        if selection.get('progress_account', '') != self.account:
            raise ValueError('任务进度账号已变化，请重新选择任务建立项目')
        allowed = self.allowed(selection['scopes'], selection.get('allow_unknown_progress', False))
        if any(r.evidence.get('catalog_id') not in allowed for r in project.rows if r.selected):
            raise ValueError('项目包含已完成或未获许可的进度未知任务，请回台词库重新建立项目')
