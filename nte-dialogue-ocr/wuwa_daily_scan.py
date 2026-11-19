"""Read quest-level evidence and match it to the installed dialogue index."""
import gzip
import hashlib
import json
import re
from pathlib import Path
from functools import lru_cache

from wuwa_log_stream import TABLE, format_of
from wuwa_progress import read_finished_quests
from wuwa_voice_pack import mount_signature

MANIFEST = Path(__file__).parent / 'assets/daily-quest-fingerprints.json.gz'
QUEST_ID = re.compile(r'\[任务id:\s*(\d+)\]', re.I)
STATE = re.compile(r'\[StateId:\s*(Progress|Finish|Delete)\]')
LOGIN = b'LoginProcedure-LoginRequest-'
PENDING = '上线下发进行中的任务'
AVAILABLE = '下发可接任务'


@lru_cache(maxsize=1)
def verified_index():
    with gzip.open(MANIFEST, 'rt', encoding='utf8') as f:
        return json.load(f)


def log_events(path):
    with path.open('rb') as stream:
        kind = format_of(stream.read(512))
        if kind is None:
            return
        encoded, offset = kind
        stream.seek(offset)
        tail = b''
        # A file may be growing. Scan only the bytes present when opened.
        left = max(0, path.stat().st_size - offset)
        while left:
            chunk = stream.read(min(1 << 20, left))
            if not chunk:
                break
            left -= len(chunk)
            lines = (tail + (chunk.translate(TABLE) if encoded else chunk)).split(b'\n')
            tail = lines.pop()
            for line in lines:
                if b'[Quest]' in line or (b'[Login]' in line and (LOGIN in line or b'[uId: ' in line)):
                    yield line.decode('utf8', 'replace')


def reduce_events(lines, active, excluded, session=None):
    for line in lines:
        if session is not None and '[Login]' in line and '[uId: ' in line:
            uid = re.search(r'\[uId: (\d+)\]',line)
            if uid:
                key = hashlib.sha256(('wuwa-local-account:'+uid[1]).encode()).hexdigest()
                if key != session.get('account'):
                    active.clear()
                    excluded.clear()
                session['account'] = key
            continue
        if '[Login]' in line and 'LoginProcedure-LoginRequest-登录成功' in line:
            active.clear()
            excluded.clear()
            continue
        if '[Quest]' not in line:
            continue
        field = QUEST_ID.search(line)
        if not field:
            continue
        key = field[1]
        state = STATE.search(line) if ' 任务状态更新 ' in line else None
        if state and state[1] in ('Finish', 'Delete'):
            active.pop(key, None)
            excluded.add(key)
        elif (PENDING + ' [' in line or (state and state[1] == 'Progress')
              or ('点击追踪按钮' in line and '[追踪结果: Success]' in line)):
            active[key] = '游戏记录 · 进行中'
            excluded.discard(key)
        elif AVAILABLE + ' [' in line and key not in excluded:
            active[key] = '游戏记录 · 可接取'


def scan(catalog, config):
    install = Path(config['install_root'])
    profiles = read_finished_quests(install)['profiles']
    folder = install / 'Wuthering Waves Game/Client/Saved/Logs'
    files = [p for p in folder.glob('Client-backup-*.log') if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime)
    current = folder / 'Client.log'
    if current.is_file():
        files.append(current)
    active, excluded, session = {}, set(), {}
    for file in files[-12:]:
        try:
            reduce_events(log_events(file), active, excluded, session)
        except FileNotFoundError:
            continue  # A rotation during reading is normal.
    account = session.get('account') or (next(iter(profiles)) if len(profiles)==1 else '')
    if account not in profiles:
        raise ValueError('无法确认日志对应的游戏账号，请重新登录要扫描的账号后重试。')
    finished = profiles[account]
    if catalog.progress.account == account:
        for q, state in catalog.progress.states().items():
            if state == 'completed':
                finished.add(q)
            if state == 'pending' and q not in excluded:
                active.setdefault(q, '玩家已确认未完成')
    from wuwa_quest_names import repair, title
    repair(catalog)
    names = dict(catalog.db.execute("SELECT id,name FROM scopes WHERE kind='quest'"))
    if not names:
        raise ValueError('尚无台词索引。请在鸣潮设置中点击「更新台词索引」，完成后重新扫描。')
    verified = verified_index()
    quests = []
    for q, evidence in active.items():
        scope = 'quest:' + q
        if q not in finished and scope in verified['quests'] and scope in names:
            quests.append((scope, title(q,names[scope]), evidence))
    quests.sort(key=lambda item: ('可接取' in item[2], item[1], item[0]))
    return account, quests


def matched_lines(inventory):
    index = verified_index()
    return index['quests'] if mount_signature(inventory) == index['mounts'] else {}

