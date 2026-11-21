"""Read quest-level evidence and match it to the installed dialogue index."""
import gzip
import json
import re
from pathlib import Path
from functools import lru_cache

from wuwa_game_accounts import LoginSession, log_events, log_files
from wuwa_voice_pack import mount_signature

MANIFEST = Path(__file__).parent / 'assets/daily-quest-fingerprints.json.gz'
QUEST_ID = re.compile(r'\[任务id:\s*(\d+)\]', re.I)
STATE = re.compile(r'\[StateId:\s*(Progress|Finish|Delete)\]')
PENDING = '上线下发进行中的任务'
AVAILABLE = '下发可接任务'


@lru_cache(maxsize=1)
def verified_index():
    with gzip.open(MANIFEST, 'rt', encoding='utf8') as f:
        return json.load(f)


def reduce_events(lines, active, excluded, session=None, *, target=None, owners=None, split=()):
    """Only attribute quest events to the selected login or a confirmed in-game UID."""
    identity = LoginSession()
    owner = None
    for line in lines:
        event = identity.feed(line)
        if event:
            if event == 'sdk':
                owner = None  # Login setup is not evidence of a successful switch.
                continue
            if event == 'login':
                owner = identity.sdk if identity.sdk not in split else None
            else:
                previous = owner
                owner = (owners or {}).get(identity.role, identity.role)
                if previous == owner:
                    continue
            if target is None or owner == target:
                active.clear()
                excluded.clear()
                if session is not None:
                    session['seen'] = True
            continue
        if '[Quest]' not in line:
            continue
        if target is not None and owner != target:
            continue
        field = QUEST_ID.search(line)
        if not field:
            continue
        if session is not None:
            session['seen'] = True
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
    snapshot = catalog.progress.snapshot(install)
    profiles = snapshot['profiles']
    account = catalog.progress.resolve(profiles)
    if not catalog.progress.account:
        account = catalog.progress.refresh(install)['account']
    selection = catalog.progress.selection
    if selection[0] != account:
        raise ValueError('游戏账号已切换，请重新扫描。')
    active, excluded, session = {}, set(), {}
    for file in log_files(install):
        try:
            reduce_events(log_events(file), active, excluded, session, target=account,
                          owners=snapshot['owners'], split=snapshot['split'])
        except FileNotFoundError:
            continue  # A rotation during reading is normal.
    finished = profiles[account]
    for q, state in catalog.progress.states(account).items():
        if state == 'completed':
            finished.add(q)
        if state == 'pending' and q not in excluded:
            active.setdefault(q, '玩家已确认未完成')
    if not session.get('seen'):
        # A different player can rotate all of this player's logs out of the window.
        # Only snapshots already written by the account-scoped scanner qualify.
        if catalog.meta('scanned:'+account):
            for scope,reason in catalog.db.execute(
                    'SELECT scope,evidence FROM daily_discovered WHERE account=?',(account,)):
                active.setdefault(scope.removeprefix('quest:'), '上次扫描 · '+reason.removeprefix('上次扫描 · '))
        if not active:
            raise ValueError('未找到所选账号的支线记录。请用该账号进入游戏，打开任务列表后重新扫描。')
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
    catalog.progress.require(selection)
    return account, quests


def matched_lines(inventory, catalog=None, scopes=None):
    index = verified_index()
    signature = mount_signature(inventory)
    if not signature and not inventory.get('base_paks'):
        return {}
    if signature and signature == index['mounts']:
        return index['quests']
    # Pre-recordings can be prepared from a released index on another build.
    # Match the quest, stable line ID and content fingerprint. Live pack playback
    # keeps its separate build checks. Callers also verify original voice evidence.
    if catalog is None or not scopes:
        return {}
    approved = {}
    for scope in scopes:
        reference = index['quests'].get(scope)
        if not reference:
            continue
        if any(reference.get(r['id']) == r['fingerprint'] for r in catalog.lines([scope])):
            approved[scope] = reference  # Consumers compare each actual fingerprint before synthesis.
    return approved

