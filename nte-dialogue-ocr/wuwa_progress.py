"""Read one local quest cache, without opening the game or writing to it."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from uuid import uuid4

from game_profiles import wuwa_client
from wuwa_game_accounts import account_key, observed_roles

from dialogue_generation import atomic_text, timestamp

PROGRESS_LABELS = {'completed': '已完成（有记录）', 'pending': '人工确认未完成', 'unknown': '进度未知'}


def cache_path(install_root):
    return wuwa_client(install_root) / 'Saved/LocalStorage/LocalStorage.db'


class MissingProgress(ValueError):
    """No readable progress source. Malformed caches are reported separately."""


def _cache(path):
    if not path.is_file():
        return {}, '', ''
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    try:
        connection.execute('PRAGMA query_only=ON')
        recent = connection.execute('SELECT value FROM LocalStorage WHERE key=?', ('RecentlyLoginUID',)).fetchone()
        row = connection.execute('SELECT value FROM LocalStorage WHERE key=?', ('UserFinishedQuests',)).fetchone()
    finally:
        connection.close()
    last_uid = str(recent[0]).strip('"') if recent else ''
    if not row:
        return {}, '', last_uid
    if not isinstance(row[0], str) or len(row[0]) > 4_000_000:
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
        account = account_key(identity)
        if account in profiles:
            raise ValueError('任务缓存出现重复账号')
        profiles[account] = {str(q) for q in ids}
    return profiles, hashlib.sha256(row[0].encode()).hexdigest(), last_uid


def read_finished_quests(install_root, known=()):
    path = cache_path(install_root)
    profiles, digest, recent = _cache(path)
    observed = observed_roles(install_root)
    roles = set(known) | observed
    supported = set(profiles) | {sdk for sdk, _, _ in observed if sdk}
    by_sdk, by_role = {}, {}
    for sdk, role, suffix in roles:
        if sdk:
            by_sdk.setdefault(sdk, set()).add(role)
            by_role.setdefault(role, set()).add(sdk)
    labels = {key: '本地账号 · ' + key[:6].upper() + '（游戏 UID 待确认）' for key in profiles}
    split = {sdk for sdk, group in by_sdk.items() if len(group) > 1}
    for accounts in by_role.values():
        if len(accounts) > 1:
            split.update(accounts)
    for sdk in split:
        profiles.pop(sdk, None)
        labels.pop(sdk, None)
    owners = {}
    last = account_key('role:' + recent) if recent else ''
    if re.fullmatch(r'[1-9][0-9]{5,19}', recent):
        # This is an in-game UID, but it carries no SDK association on its own.
        observed.add(('', last, recent[-4:]))
        roles.add(('', last, recent[-4:]))
    for sdk, role, suffix in sorted(roles):
        if sdk not in supported and (sdk or (sdk, role, suffix) not in observed):
            continue
        candidates = by_role.get(role, set())
        owner = next(iter(candidates)) if len(candidates) == 1 and candidates.isdisjoint(split) else role
        owners[role] = owner
        profiles.setdefault(owner, set())
        labels[owner] = ('游戏 UID 尾号 ' + suffix + ' · ' + owner[:6].upper()
                         + ('（最近登录）' if role == last else ''))
    if not profiles:
        raise MissingProgress('尚未找到本机账号记录。请在所选游戏目录登录角色进入大世界，再打开账号菜单。')
    return dict(profiles=profiles, labels=labels, path=str(path), sha256=digest,
                owners=owners, split=split, observed=observed)




class QuestProgress:
    def __init__(self, catalog):
        self.catalog, self.db = catalog, catalog.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS game_role_evidence(
                sdk TEXT NOT NULL, role TEXT NOT NULL, suffix TEXT NOT NULL, PRIMARY KEY(sdk,role));
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

    @property
    def selection(self):
        return tuple(self.db.execute("""SELECT
            coalesce((SELECT value FROM meta WHERE key='progress_account'),''),
            coalesce((SELECT value FROM meta WHERE key='account_revision'),'')""").fetchone())

    def require(self, selection):
        if self.selection != selection:
            raise ValueError('游戏账号已切换，原账号的配音任务已停止。')

    def label(self, account):
        label = self.catalog.meta('account_label:'+account)
        if label.startswith('UID 尾号') or not label:
            label = '本地账号 · ' + account[:6].upper() + '（游戏 UID 待确认）'
        alias = self.catalog.meta('account_alias:'+account)
        return alias or label

    def snapshot(self, install_root):
        known = [tuple(row) for row in self.db.execute('SELECT sdk,role,suffix FROM game_role_evidence')]
        snapshot = read_finished_quests(install_root, known)
        fresh = snapshot['observed'] - set(known)
        if fresh:
            with self.db:
                self.db.executemany('INSERT OR IGNORE INTO game_role_evidence VALUES(?,?,?)', fresh)
        return snapshot

    def resolve(self, profiles, explicit=None):
        selected = explicit or self.account
        if not selected and len(profiles) == 1:
            selected = next(iter(profiles))
        if not selected:
            raise ValueError('请在任务库或每日配音计划点击「选择游戏账号」，选择本次配音使用的账号。')
        if selected not in profiles:
            raise ValueError('所选账号不在本机缓存中，请登录该账号或点击「切换游戏账号」。')
        return selected

    def refresh(self, install_root, account=None):
        snapshot = self.snapshot(install_root)
        for key in ('observed','owners','split'):
            snapshot.pop(key)
        profiles = snapshot.pop('profiles')
        labels = snapshot.pop('labels')
        when = timestamp()
        evidence = json.dumps(dict(snapshot, key='UserFinishedQuests', completeness='partial'), ensure_ascii=False)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            selected = self.resolve(profiles, account)
            ids = profiles[selected]
            if selected != self.account:
                self.db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)', (
                    ('progress_account', selected), ('account_revision', uuid4().hex)))
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('account_label:'+selected, labels[selected]))
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

    def refresh_for_preparation(self, install_root, *, require_progress=False):
        try:
            return self.refresh(install_root)
        except MissingProgress:
            if require_progress:
                raise
            return None

    def states(self, account=None):
        return {r['quest_id']: r['status'] for r in self.db.execute(
            'SELECT quest_id,status FROM quest_progress WHERE account=?', (account or self.account,))}

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
