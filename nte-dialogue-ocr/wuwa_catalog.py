"""Versioned dialogue catalog. Missing audio is never evidence of silence."""
from __future__ import annotations

import hashlib
from functools import lru_cache
import json
from pathlib import Path
import re
import sqlite3
import threading
from uuid import uuid4

from dialogue_generation import DialogueRow, GenerationProject, atomic_text, digest, file_digest, timestamp
from game_profiles import WUWA_ROOT, safe_output
from wuwa_resources import COMMIT, SOURCE

VOICE_STATES = {'voiced', 'unvoiced', 'unknown'}


def walk_dicts(value, path=''):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk_dicts(child, path + '/' + str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_dicts(child, path + '/' + str(index))


def talk_items(actions):
    # Only ShowTalk's dialogue children, never arbitrary UI strings or options.
    for action_path, action in walk_dicts(actions):
        if action.get('Name') != 'ShowTalk':
            continue
        for index, item in enumerate(action.get('Params', {}).get('TalkItems', [])):
            if item.get('Type', 'Talk') not in {'Talk', 'AvgTalk', 'AvgNarration'}:
                continue
            if item.get('TidTalk'):
                yield action_path + '/TalkItems/' + str(index), item


def classify_audio(item, audio):
    key = item.get('PlotLineKey', '')
    if key and audio.get(key, {}).get('FileName'):
        return 'voiced', '公开表存在语音引用（本机版本未验证）：' + key
    return 'unknown', '缺失语音引用不足以证明无原配音'


def ordinary_branch(data):
    tips = {v['QuestType'] for _, v in walk_dicts(data.get('FinishActions', []))
            if isinstance(v.get('QuestType'), str)}
    return (data.get('Type') == 2 and tips == {'Branch'}
            and data.get('DistributeType') in {'System','Interact','Passive','UseItem','PhoneMessage'}
            and not data.get('ActivityId'))


@lru_cache(maxsize=2)
def branch_policy_index(folder, manifest_digest):
    manifest = json.loads((folder/'source.json').read_text(encoding='utf-8'))
    if manifest.get('commit') != COMMIT:
        raise ValueError('任务分类来源版本不匹配')
    def table(name):
        raw = (folder/(name+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest['files'][name]['sha256']:
            raise ValueError('任务分类来源校验失败')
        return json.loads(raw)
    scopes = {'quest:'+str(q['QuestId']) for q in table('quests') if ordinary_branch(q['Data'])}
    audio_keys = {a['Id'] for a in table('audio') if a.get('FileName')}
    eligible = set()
    for state in table('states'):
        for branch, item in talk_items(json.loads(state.get('Actions') or '[]')):
            if (item.get('PlayVoice', False) is not False or item.get('PlotLineKey') in audio_keys
                    or any(item.get(k) for k in ('TalkAkEvent','TalkEndAkEvent','UniversalTone'))):
                continue
            eligible.add((state['StateKey'],branch,str(item.get('Id')),str(item['TidTalk'])))
    return scopes, eligible


def speaker_names(records, localized, texts):
    names = {}
    for record in records:
        identity = str(record['Id'])
        key = localized.get(str(record.get('NameStringKey', -1)), '')
        direct = localized.get(str(record.get('Name', -1)), '')
        names[identity] = texts.get(key, '') or texts.get(direct, direct) or texts.get(f'Speaker_{identity}_Name', '')
    return names


def resolve_text(raw, nickname='漂泊者', gender='female'):
    text = raw.replace('{PlayerName}', nickname)
    # Only established formatting tags are removed. Unknown tokens stay held.
    text = re.sub(r'</?(?:color|size)(?:=[^<>]+)?>', '', text, flags=re.I)
    text = text.replace('<br>', '\n').replace('<br/>', '\n')
    unresolved = re.findall(r'\{[^{}]*\}|<[^<>]*>|\[[^\[\]]*\]|\$\w+', text)
    return text.strip(), unresolved


class Catalog:
    def branch_rule_enabled(self):
        return self.meta('ordinary_side_quests_unvoiced') == '1'

    def set_branch_rule(self, enabled):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                            ('ordinary_side_quests_unvoiced','1' if enabled else '0'))
            if enabled:
                self.db.execute("UPDATE log_generation SET error='' WHERE error='原声未知，待核对'")

    def voice_basis(self, row, version, *, allow_branch=False):
        if not version or row['voice_status']=='voiced':
            return ''
        if row['voice_status']=='unvoiced' and row['reviewed_at'] and row['review_version']==version:
            return row['basis']
        if not allow_branch or row['source_id']!=COMMIT or (row['voice_status']=='unknown' and row['reviewed_at']):
            return ''
        if not hasattr(self,'_branch_policy'):
            try:
                folder=self.root/'sources'/COMMIT
                self._branch_policy=branch_policy_index(folder,file_digest(folder/'source.json'))
            except (OSError,ValueError,KeyError,TypeError):
                self._branch_policy=(set(),set())
        scopes, eligible=self._branch_policy
        key=(row['state_id'],row['branch'],row['line_id'],row['text_id'])
        if key not in eligible:
            return ''
        attached={s[0] for s in self.db.execute(
            "SELECT scope_id FROM scope_lines WHERE line_id=? AND scope_id LIKE 'quest:%'",(row['id'],))}
        if attached and attached.issubset(scopes):
            return '按用户设置的普通支线规则配音（Branch）'
        return ''

    def __init__(self, root=WUWA_ROOT):
        self.root = safe_output(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'dialogue.sqlite', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS lines(
                id TEXT PRIMARY KEY, game_id TEXT NOT NULL CHECK(game_id='wuwa'), source_id TEXT NOT NULL,
                flow_id TEXT NOT NULL, state_id TEXT NOT NULL, branch TEXT NOT NULL, line_id TEXT NOT NULL,
                text_id TEXT NOT NULL, speaker_id TEXT NOT NULL, speaker TEXT NOT NULL, raw_text TEXT NOT NULL,
                plot_key TEXT NOT NULL, voice_status TEXT NOT NULL, basis TEXT NOT NULL, fingerprint TEXT NOT NULL,
                review_version TEXT NOT NULL DEFAULT '', reviewed_at TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS line_flow ON lines(flow_id);
            CREATE INDEX IF NOT EXISTS line_text ON lines(raw_text);
            CREATE INDEX IF NOT EXISTS line_speaker ON lines(speaker);
            CREATE INDEX IF NOT EXISTS line_status ON lines(voice_status);
            CREATE TABLE IF NOT EXISTS scopes(id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL, kind TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scope_lines(scope_id TEXT NOT NULL, line_id TEXT NOT NULL, PRIMARY KEY(scope_id,line_id));
            CREATE INDEX IF NOT EXISTS scopes_for_dialogue_line ON scope_lines(line_id,scope_id);
            CREATE INDEX IF NOT EXISTS scope_line ON scope_lines(line_id);
            CREATE TABLE IF NOT EXISTS voice_bindings(speaker_id TEXT PRIMARY KEY, speaker TEXT NOT NULL,
                voice TEXT NOT NULL, transcript_reviewed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, line_id TEXT NOT NULL, status TEXT NOT NULL,
                basis TEXT NOT NULL, resource_version TEXT NOT NULL, fingerprint TEXT NOT NULL, time TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS log_subtitle ON lines(state_id,line_id);
            CREATE TABLE IF NOT EXISTS binding_origins(speaker_id TEXT PRIMARY KEY,origin TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS breeze_presets(id TEXT PRIMARY KEY,name TEXT NOT NULL,gender TEXT,age TEXT);
            CREATE TABLE IF NOT EXISTS breeze_cast(speaker_id TEXT PRIMARY KEY,voice_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quest_cast_reviews(quest_id TEXT PRIMARY KEY, roles TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS original_voice_roles(speaker_id TEXT PRIMARY KEY,cloud_voice_id TEXT);
            CREATE TABLE IF NOT EXISTS original_reference_cast(speaker_id TEXT PRIMARY KEY,profile TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS voice_rebuilds(speaker_id TEXT PRIMARY KEY,voice TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS log_generation(line_id TEXT NOT NULL, voice TEXT NOT NULL,
                priority INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '', PRIMARY KEY(line_id,voice));
            CREATE TABLE IF NOT EXISTS breeze_voices(speaker_id TEXT NOT NULL,local_voice TEXT NOT NULL,
                voice_id TEXT NOT NULL,PRIMARY KEY(speaker_id,local_voice));
            CREATE TABLE IF NOT EXISTS breeze_calls(cache_key TEXT PRIMARY KEY,day TEXT NOT NULL,
                units INTEGER NOT NULL,state TEXT NOT NULL,sha256 TEXT);
        ''')
        from wuwa_progress import QuestProgress
        self.progress = QuestProgress(self)

    def close(self):
        self.db.close()

    def meta(self, key, default=''):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def local_version(self):
        path = self.root / 'sources/local-inventory.json'
        if not path.exists():
            return ''
        data = json.loads(path.read_text(encoding='utf-8'))
        return digest([(m['path'], m['sha256']) for m in data['mounts']])

    def import_public(self, folder, stop=None, progress=lambda _: None):
        stop = stop or threading.Event()
        folder = Path(folder)
        source = json.loads((folder / 'source.json').read_text(encoding='utf-8'))
        if source.get('commit') != COMMIT:
            raise ValueError('来源提交不匹配')
        def read(name):
            from dialogue_generation import file_digest
            path = folder / (name + '.json')
            if file_digest(path) != source['files'][name]['sha256']:
                raise ValueError('来源文件损坏：' + name)
            return json.loads(path.read_text(encoding='utf-8'))
        progress('建立中文和角色索引')
        texts = {str(x['Id']): x['Content'] for x in read('text')}
        speaker_text = {str(x['Id']): x['Content'] for x in read('speaker_text')}
        speakers = speaker_names(read('speakers'), speaker_text, texts)
        audio = {x['Id']: x for x in read('audio')}
        states = read('states')
        flows = read('flow')
        state_flows = {f"{f['Id']}_{state}": f['Id'] for f in flows for state in f['States']}
        quest_info = {}
        for q in read('quests'):
            data = q['Data']
            qid = str(q['QuestId'])
            quest_info[qid] = (texts.get(data.get('TidName', ''), '') or qid, str(data.get('RegionId', '')))
        quest_states = {}
        for node in read('quest_nodes'):
            qid = str(node['Key']).split('_')[0]
            for _, ref in walk_dicts(node['Data']):
                if 'FlowListName' in ref and 'FlowId' in ref and 'StateId' in ref:
                    state = f"{ref['FlowListName']}_{ref['FlowId']}_{ref['StateId']}"
                    quest_states.setdefault(state, set()).add(qid)
        counts = dict(added=0, changed=0, unchanged=0, missing_text=0)
        seen = set()
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?)', (COMMIT, json.dumps(source, ensure_ascii=False)))
            self.db.execute('DELETE FROM scope_lines WHERE line_id IN (SELECT id FROM lines WHERE source_id=?)', (COMMIT,))
            self.db.execute("DELETE FROM scopes WHERE kind IN ('flow','quest')")
            for number, state in enumerate(states):
                if stop.is_set():
                    raise InterruptedError('索引已取消，保留上次完整版本')
                state_id = state['StateKey']
                flow = state_flows.get(state_id)
                if not flow:
                    continue
                actions = json.loads(state.get('Actions') or '[]')
                for branch, item in talk_items(actions):
                    raw = texts.get(str(item['TidTalk']), '')
                    if not raw:
                        counts['missing_text'] += 1
                        continue
                    sid = str(item.get('WhoId', ''))
                    speaker = speakers.get(sid, '') or ('旁白' if item.get('Type') == 'AvgNarration' else '角色#' + sid)
                    identity = digest(['wuwa', state_id, branch, str(item.get('Id')), item['TidTalk']])
                    seen.add(identity)
                    fingerprint = digest([raw, sid, speaker, item.get('PlotLineKey', '')])
                    old = self.db.execute('SELECT * FROM lines WHERE id=?', (identity,)).fetchone()
                    status, basis = classify_audio(item, audio)
                    review_version = reviewed_at = ''
                    if old and old['fingerprint'] == fingerprint:
                        status, basis, review_version, reviewed_at = old['voice_status'], old['basis'], old['review_version'], old['reviewed_at']
                        counts['unchanged'] += 1
                    else:
                        counts['changed' if old else 'added'] += 1
                    self.db.execute('INSERT OR REPLACE INTO lines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (identity, 'wuwa', COMMIT, flow, state_id, branch, str(item.get('Id', '')), str(item['TidTalk']),
                         sid, speaker, raw, item.get('PlotLineKey', ''), status, basis, fingerprint, review_version, reviewed_at))
                    # Flow labels are provenance identifiers, not verified geographic regions.
                    scope = 'flow:' + flow
                    self.db.execute('INSERT OR IGNORE INTO scopes VALUES(?,?,?,?)', (scope, flow, '区域待关联', 'flow'))
                    self.db.execute('INSERT OR IGNORE INTO scope_lines VALUES(?,?)', (scope, identity))
                    for qid in quest_states.get(state_id, ()):
                        name, region = quest_info.get(qid, (qid, ''))
                        self.db.execute('INSERT OR IGNORE INTO scopes VALUES(?,?,?,?)', ('quest:' + qid, name, '区域ID ' + region, 'quest'))
                        self.db.execute('INSERT OR IGNORE INTO scope_lines VALUES(?,?)', ('quest:' + qid, identity))
                if number % 1000 == 0:
                    progress(f'索引流程 {number}/{len(states)}')
            old_ids = {r[0] for r in self.db.execute('SELECT id FROM lines WHERE source_id=?', (COMMIT,))}
            removed = old_ids - seen
            self.db.executemany('DELETE FROM lines WHERE id=?', ((x,) for x in removed))
            counts['removed'] = len(removed)
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('last_import', json.dumps(counts)))
        return counts

    def import_capture(self, path, name):
        from dialogue_generation import file_digest
        path = Path(path).resolve()
        if not path.is_relative_to((self.root / 'records').resolve()):
            raise ValueError('只能导入鸣潮独立采集目录中的记录')
        if not name.strip():
            raise ValueError('请填写任务或 NPC 名称')
        records = {}
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if value.get('game_id') != 'wuwa':
                raise ValueError('记录不属于鸣潮，未导入')
            if not isinstance(value.get('body'), str) or not value.get('dialogue_id'):
                raise ValueError('台词记录不完整')
            key = value['dialogue_id']
            if key not in records or value.get('revision',0) > records[key].get('revision',0):
                records[key] = value
        source_id = 'capture:' + digest(str(path.parent))
        scope = source_id
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?)', (source_id,json.dumps(dict(
                game_id='wuwa',kind='local_ocr',path=str(path),sha256=file_digest(path),
                resource_fingerprint=self.local_version(),verified_local=False),ensure_ascii=False)))
            self.db.execute('INSERT OR REPLACE INTO scopes VALUES(?,?,?,?)',(scope,name.strip(),'实机采集，区域待核对','capture'))
            for value in records.values():
                speaker = value.get('speaker') or '旁白 / 角色待核对'
                identity = digest([source_id,value['dialogue_id']])
                sid = 'capture:' + digest(speaker)
                raw = value['body'].strip()
                fingerprint = digest([raw,sid,speaker,''])
                old = self.db.execute('SELECT * FROM lines WHERE id=?',(identity,)).fetchone()
                same = old is not None and old['fingerprint'] == fingerprint
                self.db.execute('INSERT OR REPLACE INTO lines VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (identity,'wuwa',source_id,scope,scope,'ocr',value['dialogue_id'],value['dialogue_id'],sid,speaker,
                     raw,'',old['voice_status'] if same else 'unknown',old['basis'] if same else '实机OCR，配音状态未人工核对',
                     fingerprint,old['review_version'] if same else '',old['reviewed_at'] if same else ''))
                self.db.execute('INSERT OR IGNORE INTO scope_lines VALUES(?,?)',(scope,identity))
        return len(records)

    def scopes(self, search='', kind='', region='', offset=0, hide_completed=False, progress_status=''):
        return self.db.execute('''SELECT s.*,count(l.id) total,sum(l.voice_status='unvoiced') ready,
            sum(l.voice_status='voiced') voiced, sum(l.voice_status='unknown') unknown,
            coalesce(p.status,'unknown') progress_status
            FROM scopes s JOIN scope_lines sl ON sl.scope_id=s.id JOIN lines l ON l.id=sl.line_id
            LEFT JOIN quest_progress p ON s.kind='quest' AND p.quest_id=substr(s.id,7) AND p.account=?
            WHERE (s.name LIKE ? OR s.id LIKE ?) AND (?='' OR s.kind=?) AND (?='' OR s.region=?)
            AND (?=0 OR coalesce(p.status,'unknown')!='completed')
            AND (?='' OR coalesce(p.status,'unknown')=?)
            GROUP BY s.id ORDER BY s.kind DESC,s.name LIMIT 1000 OFFSET ?''',
            (self.progress.account, '%' + search + '%', '%' + search + '%', kind, kind, region, region,
             int(hide_completed), progress_status, progress_status, max(0,int(offset)))).fetchall()

    def scope_count(self, search='', kind='', region='', hide_completed=False, progress_status=''):
        return self.db.execute('''SELECT count(*) FROM scopes s
            LEFT JOIN quest_progress p ON s.kind='quest' AND p.quest_id=substr(s.id,7) AND p.account=?
            WHERE (s.name LIKE ? OR s.id LIKE ?) AND (?='' OR s.kind=?) AND (?='' OR s.region=?)
            AND (?=0 OR coalesce(p.status,'unknown')!='completed')
            AND (?='' OR coalesce(p.status,'unknown')=?)
            AND EXISTS(SELECT 1 FROM scope_lines sl JOIN lines l ON l.id=sl.line_id WHERE sl.scope_id=s.id)''',
            (self.progress.account,'%'+search+'%','%'+search+'%',kind,kind,region,region,int(hide_completed),
             progress_status,progress_status)).fetchone()[0]

    def lines(self, scope_ids, status='', speaker=''):
        if not scope_ids:
            return []
        placeholders = ','.join('?' for _ in scope_ids)
        return self.db.execute(f'''SELECT DISTINCT l.* FROM lines l JOIN scope_lines sl ON sl.line_id=l.id
            WHERE sl.scope_id IN ({placeholders}) AND (?='' OR voice_status=?) AND speaker LIKE ?
            ORDER BY flow_id,state_id,branch''', (*scope_ids, status, status, '%' + speaker + '%')).fetchall()

    def review(self, ids, status, basis):
        if status not in VOICE_STATES or len(basis.strip()) < 8:
            raise ValueError('请填写具体核对依据，至少八个字')
        version = self.local_version()
        if status == 'unvoiced' and not version:
            raise ValueError('先读取本机挂载版本，再核对未配音状态')
        with self.db:
            for identity in ids:
                row = self.db.execute('SELECT * FROM lines WHERE id=?', (identity,)).fetchone()
                if not row:
                    raise ValueError('条目已不存在')
                when = timestamp()
                self.db.execute('UPDATE lines SET voice_status=?,basis=?,review_version=?,reviewed_at=? WHERE id=?',
                                (status, basis.strip(), version, when, identity))
                self.db.execute('INSERT INTO reviews(line_id,status,basis,resource_version,fingerprint,time) VALUES(?,?,?,?,?,?)',
                                (identity, status, basis.strip(), version, row['fingerprint'], when))
                if status=='unvoiced':
                    self.db.execute("UPDATE log_generation SET error='' WHERE line_id=?",(identity,))

    def sync_voices(self, data_root):
        names, conflicts = {}, set()
        for path in sorted((Path(data_root) / 'profiles').glob('*/profile.json')):
            p = json.loads(path.read_text(encoding='utf-8-sig'))
            if not p.get('enabled', True):
                continue
            for name in [p['speaker'], *p.get('aliases', [])]:
                if name in names and names[name] != p['speaker']:
                    conflicts.add(name)
                names[name] = p['speaker']
        with self.db:
            for row in self.db.execute('SELECT DISTINCT speaker_id,speaker FROM lines').fetchall():
                name = row['speaker']
                voice = 'character:' + names[name] if name in names and name not in conflicts else 'system:default'
                self.db.execute('INSERT OR IGNORE INTO voice_bindings VALUES(?,?,?,?)', (row['speaker_id'], name, voice, int(voice.startswith('system:'))))
                self.db.execute("UPDATE voice_bindings SET speaker=?,voice=?,transcript_reviewed=? WHERE speaker_id=? AND speaker LIKE '角色#%'",
                                (name, voice, int(voice.startswith('system:')), row['speaker_id']))
        return len(set(names.values()))

    def binding(self, speaker_id):
        if self.cloud_only():
            original = self.db.execute("""SELECT 'character:'||profile AS voice,profile AS label,
                    1 AS transcript_reviewed,speaker_id FROM original_reference_cast WHERE speaker_id=?""",
                    (speaker_id,)).fetchone()
            if original:
                return original
            return self.db.execute("""SELECT 'breeze:'||p.id AS voice, p.name AS label, 1 AS transcript_reviewed,
                c.speaker_id FROM breeze_cast c JOIN breeze_presets p ON p.id=c.voice_id WHERE c.speaker_id=?""",
                (speaker_id,)).fetchone()
        return self.db.execute('SELECT * FROM voice_bindings WHERE speaker_id=?', (speaker_id,)).fetchone()

    def cloud_only(self):
        return self.meta('wuwa_tts_provider') == 'breezeblue'

    def set_binding(self, speaker_id, speaker, voice, reviewed, *, automatic=False):
        if not voice.startswith(('system:', 'character:', 'npc:')):
            raise ValueError('无效声线')
        if voice.startswith('npc:'):
            from wuwa_npc_voices import load_variant
            load_variant(self.root, voice, speaker_id=speaker_id, speaker=speaker)
        with self.db:
            previous = self.binding(speaker_id)
            self.db.execute('INSERT OR REPLACE INTO voice_bindings VALUES(?,?,?,?)', (speaker_id, speaker, voice, int(reviewed)))
            self.db.execute('INSERT OR REPLACE INTO binding_origins VALUES(?,?)',
                            (speaker_id, 'automatic' if automatic else 'manual'))
            if previous and previous['voice'] != voice and not automatic:
                self.db.execute('INSERT OR REPLACE INTO voice_rebuilds VALUES(?,?)',(speaker_id,voice))
            elif previous and reviewed and not previous['transcript_reviewed']:
                self.db.execute("""UPDATE log_generation SET error='' WHERE voice=? AND line_id IN
                                   (SELECT id FROM lines WHERE speaker_id=?)""",(voice,speaker_id))

    def prepare_project(self, scope_ids, config, selected_ids=None, *, live_line=False):
        rows, held = [], 0
        version = self.local_version()
        allow_unknown = bool(config.get('allow_unknown_progress', False))
        if live_line and (not selected_ids or len(selected_ids)!=1):
            raise ValueError('实时生成需要唯一的台词编号')
        allowed = set(selected_ids) if live_line else self.progress.allowed(scope_ids, allow_unknown)
        projects_root = self.root / ('live-generation-projects' if live_line else 'generation-projects')
        for row in self.lines(scope_ids):
            if selected_ids is not None and row['id'] not in selected_ids:
                continue
            if row['id'] not in allowed:
                held += 1
                continue
            text, unresolved = resolve_text(row['raw_text'], config.get('nickname', '漂泊者'), config.get('traveler_gender', 'female'))
            binding = self.binding(row['speaker_id'])
            basis = self.voice_basis(row,version,allow_branch=live_line and self.branch_rule_enabled())
            if basis and not unresolved and not binding and self.cloud_only():
                from wuwa_breeze import assign_preset
                binding = assign_preset(self, row)
            if (not basis
                    or unresolved or not binding or not binding['transcript_reviewed']):
                held += 1
                continue
            source_row = self.db.execute('SELECT metadata FROM sources WHERE id=?', (row['source_id'],)).fetchone()
            source_meta = json.loads(source_row[0]) if source_row else {'unverified_source':row['source_id']}
            source_meta.pop('files', None)
            evidence = dict(game_id='wuwa', source_version=source_meta, flow_id=row['flow_id'], state_id=row['state_id'],
                            branch=row['branch'], line_id=row['line_id'], catalog_id=row['id'],
                            fingerprint=row['fingerprint'], resource_fingerprint=version, raw_text=row['raw_text'],
                            voice_status='unvoiced', voice_basis=basis, resolved_text=text,
                            resolved_speaker=row['speaker'], speaker_id=row['speaker_id'], unresolved=unresolved)
            rows.append(DialogueRow(uuid4().hex, 'wuwa-catalog', row['id'], 1, evidence,
                                    row['speaker'], text, voice=binding['voice'], confirmed=True))
        if not rows:
            raise ValueError(f'没有可生成的台词；{held} 条因任务已完成、进度未知、配音状态、声线或变量而保留')
        selection = dict(game_id='wuwa', scopes=sorted(set(scope_ids)), held=held,
                         progress_account=self.progress.account, allow_unknown_progress=allow_unknown)
        def signature(items):
            return digest(sorted((r.source_id, r.text, r.speaker, r.voice,
                                  digest(r.evidence), r.confirmed) for r in items))
        expected = signature(rows)
        # Reopen the same preparation project instead of duplicating its exports.
        for path in sorted(projects_root.glob('*/selection.json'), reverse=True):
            try:
                previous = json.loads(path.read_text(encoding='utf-8'))
                if previous != selection:
                    continue
                existing = GenerationProject.load(path.parent)
                if existing.game_id == 'wuwa' and signature(existing.rows) == expected:
                    self.validate_project(existing)
                    return existing, held
            except (ValueError, KeyError, OSError):
                continue
        project = GenerationProject.create(projects_root, mode='character', game_id='wuwa')
        project.rows = rows
        project.save()
        guards = []
        for line in self.lines(scope_ids):
            text, _ = resolve_text(line['raw_text'], config.get('nickname', '漂泊者'), config.get('traveler_gender', 'female'))
            guards.append(dict(id=line['id'], text=text, speaker=line['speaker'], voice_status=line['voice_status']))
        atomic_text(project.directory / 'playback-guards.json', json.dumps(dict(game_id='wuwa', lines=guards), ensure_ascii=False))
        atomic_text(project.directory / 'selection.json', json.dumps(selection, ensure_ascii=False, indent=2))
        return project, held

    def validate_project(self, project):
        version = self.local_version()
        config_path = self.root / 'config.json'
        config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
        for row in project.rows:
            e = row.evidence
            current = self.db.execute('SELECT * FROM lines WHERE id=?', (e.get('catalog_id', ''),)).fetchone()
            allow_branch=(project.directory.resolve().parent==(self.root/'live-generation-projects').resolve()
                          and self.branch_rule_enabled())
            if (not current or not self.voice_basis(current,version,allow_branch=allow_branch)
                    or current['fingerprint'] != e.get('fingerprint') or e.get('resource_fingerprint') != version):
                raise ValueError('台词状态或本机资源已变化，请回台词库重新核对并建立项目')
        guards = json.loads((project.directory / 'playback-guards.json').read_text(encoding='utf-8'))
        if guards.get('game_id') != 'wuwa':
            raise ValueError('缺少鸣潮播放排除索引')
        for guard in guards['lines']:
            line = self.db.execute('SELECT * FROM lines WHERE id=?', (guard['id'],)).fetchone()
            if (not line or line['voice_status'] != guard['voice_status'] or line['speaker'] != guard['speaker']
                    or resolve_text(line['raw_text'], config.get('nickname', '漂泊者'), config.get('traveler_gender','female'))[0] != guard['text']):
                raise ValueError('所选范围的配音状态已变化，请重新建立项目')
        selection = json.loads((project.directory / 'selection.json').read_text(encoding='utf-8'))
        if {r['id'] for r in self.lines(selection['scopes'])} != {g['id'] for g in guards['lines']}:
            raise ValueError('所选任务新增或移除了对白，请检查更新差异并重新建立项目')


if __name__ == '__main__':
    catalog = Catalog()
    try:
        print(catalog.import_public(WUWA_ROOT / 'sources' / COMMIT, progress=print))
    finally:
        catalog.close()
