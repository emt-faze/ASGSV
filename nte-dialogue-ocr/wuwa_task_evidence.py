"""Audit one pending task against mounted local tables, without approving it."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from flatbuffers import encode, number_types, packer
from flatbuffers.table import Table

from dialogue_generation import atomic_text, file_digest
from game_profiles import WUWA_ROOT
from wuwa_catalog import talk_items, walk_dicts
from wuwa_npc_audio import inventory_archives, archive_stats
from wuwa_local_inspect import valid_snapshot


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)


def fb_string(blob, slot):
    if len(blob) < 8:
        raise ValueError('Truncated FlatBuffer')
    table = Table(blob, encode.Get(packer.uoffset, blob, 0))
    offset = table.Offset(slot)
    return table.String(table.Pos + offset).decode('utf-8') if offset else ''


def fb_int(blob, slot):
    table = Table(blob, encode.Get(packer.uoffset, blob, 0))
    offset = table.Offset(slot)
    return table.Get(number_types.Int32Flags, table.Pos + offset) if offset else 0


class LocalAssets:
    def __init__(self, root=WUWA_ROOT):
        self.root = Path(root)
        self.archives, self.mounts = inventory_archives(r'F:\Wuthering Waves')
        self.before = archive_stats(self.archives)
        self.reports = []
        for path in (self.root / 'sources/local-dialogue').glob('*.result.json'):
            request = json.loads(path.with_name(path.name.replace('.result.', '.request.')).read_text(encoding='utf-8'))
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('game_id') != 'wuwa' or not valid_snapshot(data, request, self.before, self.mounts):
                continue
            self.reports.append((path, request, data))
        self.used = {}

    def asset(self, virtual_path):
        actual = {a['path']: a for a in self.archives}
        candidates = []
        for path, request, data in self.reports:
            covers = (virtual_path in request['exports'] or any(q.casefold() in virtual_path.casefold() for q in request['queries']))
            if not covers or set(request['archives']) != set(actual):
                continue
            if any(a['status'] != 'index_readable' or (a.get('truncated') and virtual_path not in request['exports']) for a in data['archives']):
                continue
            for archive in data['archives']:
                for file in archive['files']:
                    if file['virtual_path'].casefold() == virtual_path.casefold():
                        item = actual[archive['archive']]
                        candidates.append((item['priority'], item['order'], archive['archive']))
        if not candidates:
            raise ValueError('No complete mounted lookup: ' + virtual_path)
        priority, order, archive = max(candidates)
        if len({a for p, o, a in candidates if (p, o) == (priority, order)}) != 1:
            raise ValueError('Ambiguous overlay: ' + virtual_path)
        exports = []
        for report_path, _, data in self.reports:
            for entry in data['archives']:
                if entry['archive'] != archive:
                    continue
                for f in entry.get('files', []):
                    if f['virtual_path'] == virtual_path and f.get('output') and not f.get('error'):
                        exports.append((report_path, f))
        if not exports or len({f['sha256'] for _, f in exports}) != 1:
            raise ValueError('Missing or conflicting local export: ' + virtual_path)
        report, file = exports[0]
        output = Path(file['output'])
        if file_digest(output) != file['sha256']:
            raise ValueError('Local export hash mismatch')
        self.used[virtual_path] = dict(file, archive=archive, report=str(report), report_sha256=file_digest(report))
        return output

    def assert_unchanged(self):
        current, mounts = inventory_archives(r'F:\Wuthering Waves')
        if mounts != self.mounts or archive_stats(current) != self.before:
            raise ValueError('Game resources changed during audit')


def state_references(value):
    return {f"{v['FlowListName']}_{v['FlowId']}_{v['StateId']}"
            for _, v in walk_dicts(value) if all(k in v for k in ('FlowListName', 'FlowId', 'StateId'))}


def audio_decision(item, actions, runtime_proven):
    # Absence alone is insufficient: require the current runtime's boolean gate,
    # the complete local state and the absence of alternate voice/event paths.
    if not runtime_proven or item.get('Type', 'Talk') != 'Talk':
        return 'unknown'
    if item.get('PlayVoice') is True:
        return 'voiced'
    if 'PlayVoice' in item and item['PlayVoice'] is not False:
        return 'unknown'
    if any(item.get(k) for k in ('TalkAkEvent', 'TalkEndAkEvent', 'UniversalTone')):
        return 'unknown'
    if any(v.get('Name') not in {'SetPlotMode', 'ShowTalk', 'JumpTalk', 'FinishTalk', 'FinishState',
                               'BeginFlowTemplate', 'CloseFlowTemplate'}
           for _, v in walk_dicts(actions) if 'Name' in v and ('Params' in v or 'ActionId' in v)):
        return 'unknown'
    return 'unvoiced'


def audit_task(quest_id, root=WUWA_ROOT):
    root = Path(root)
    assets = LocalAssets(root)
    base = 'Client/Content/Aki/'
    scripts = {
        'JavaScript/Core/Define/Config/FlowState.js': '82c88ff76ce980df59614a774cb19055b4e361e0c02c30bad2c22782e0820bf7',
        'JavaScript/Core/Define/Config/QuestData.js': '2a50acd496efde025c3fe812ce9b88624612b363901a6cce6d2e50c963467691',
        'JavaScript/Core/Define/Config/QuestNodeData.js': 'd0b374049d45a0833cc8b67e4d64d1be4a343ee45f78b210ae625a9f484453ea',
        'JavaScript/Core/Define/Config/Speaker.js': 'b18e6a283825f623fd12cf45257ce86f7928a4fd7f60085d9773336d1bfb52c1',
        'JavaScript/Game/Module/Plot/Flow/FlowConfig.js': '37809c389ba866c79427ee49c59d8e79539c7c7939fd48cd7d48460ec4b9d735',
        'JavaScript/Game/Module/Plot/PlotView/PlotTextLogic.js': '9ea2b0535bbd56bf794e9417f900b0e466e242a82177a1968146c0c9cae497e6',
        'JavaScript/Game/Module/Plot/PlotView/PlotComponent/PlotTextAudioComponent.js': '4927f9a5a8b27b8306b6174df7c1eab4c603adb78b463ecd55d67eeafb605723',
    }
    for name, sha in scripts.items():
        if file_digest(assets.asset(base + name)) != sha:
            raise ValueError('Runtime/schema version changed; review required')
    databases = {k: assets.asset(base + 'ConfigDB/' + v) for k, v in {
        'states': 'db_flowState.db', 'quests': 'db_QuestData.db', 'nodes': 'db_QuestNodeData.db',
        'audio': 'db_plot_audio.db', 'speakers': 'db_speaker.db',
        'names': 'zh-Hans/lang_speaker.db', 'text': 'zh-Hans/lang_multi_text.db'}.items()}
    with closing(readonly(databases['quests'])) as db:
        row = db.execute('SELECT BinData FROM questdata WHERE QuestId=?', (int(quest_id),)).fetchone()
        if not row or fb_int(row[0], 4) != int(quest_id):
            raise ValueError('Local quest identity missing')
        quest = json.loads(fb_string(row[0], 6))
    if quest.get('Type') != 2:
        raise ValueError('Only side tasks are in the first-task scope')
    with closing(readonly(databases['nodes'])) as db:
        nodes = [json.loads(fb_string(b, 6)) for (b,) in db.execute(
            'SELECT BinData FROM questnodedata WHERE substr(Key,1,?)=?', (len(str(quest_id))+1, str(quest_id)+'_'))]
    states = state_references([quest, *nodes])
    if not states:
        raise ValueError('No directly linked task states')
    result = dict(game_id='wuwa', quest_id=str(quest_id), status='audited_not_applied',
                  coverage='all_direct_task_nodes_and_recursive_explicit_flow_references', lines=[],
                  state_ids=[], blockers=[], assets={}, original_voice_review_written=False)
    with closing(readonly(databases['states'])) as db, closing(readonly(databases['text'])) as textdb, \
            closing(readonly(databases['speakers'])) as speakers, closing(readonly(databases['names'])) as names, \
            closing(readonly(databases['audio'])) as audiodb, closing(readonly(root / 'dialogue.sqlite')) as catalog:
        def text(identity):
            record = textdb.execute('SELECT Content,RedirectDbIndex FROM MultiText WHERE Id=?', (identity,)).fetchone()
            if not record or not record[0]:
                raise ValueError('Missing/redirected local Chinese text: ' + identity)
            return record[0]
        result['quest_name'] = text(quest['TidName'])
        pending, visited = sorted(states), set()
        while pending:
            state = pending.pop(0)
            if state in visited:
                continue
            visited.add(state)
            if len(visited) > 100:
                raise ValueError('Task flow limit exceeded')
            record = db.execute('SELECT BinData FROM flowstate WHERE StateKey=?', (state,)).fetchone()
            if not record or fb_string(record[0], 4) != state:
                raise ValueError('Missing local state: ' + state)
            actions = json.loads(fb_string(record[0], 16))
            pending.extend(sorted(state_references(actions) - visited))
            modes = {v.get('Params', {}).get('Mode') for _, v in walk_dicts(actions) if v.get('Name') == 'SetPlotMode'}
            if modes != {'LevelC'}:
                result['blockers'].append('Non-LevelC state: ' + state)
            for branch, item in talk_items(actions):
                speaker_id = str(item.get('WhoId', ''))
                s = speakers.execute('SELECT BinData FROM speaker WHERE Id=?', (speaker_id,)).fetchone()
                if not s:
                    raise ValueError('Missing local speaker')
                name_key = fb_int(s[0], 10)
                name = names.execute('SELECT Content FROM Speaker WHERE Id=?', (name_key,)).fetchone()
                if not name:
                    raise ValueError('Missing local speaker name')
                raw_text = text(item['TidTalk'])
                rows = catalog.execute('SELECT id,raw_text,speaker_id,speaker FROM lines WHERE state_id=? AND branch=? AND text_id=?',
                    (state, branch, item['TidTalk'])).fetchall()
                if len(rows) != 1 or (rows[0][1], rows[0][2], rows[0][3]) != (raw_text, speaker_id, name[0]):
                    result['blockers'].append('Catalog differs: ' + item['TidTalk'])
                status = audio_decision(item, actions, True)
                # Conflicting local audio rows require manual investigation, even
                # when the currently observed runtime gate would suppress them.
                audio_ref = audiodb.execute('SELECT Id FROM plotaudio WHERE Id=?', (item['TidTalk'],)).fetchone()
                if status == 'unvoiced' and audio_ref:
                    status = 'unknown'
                result['lines'].append(dict(catalog_id=rows[0][0] if len(rows)==1 else '',
                    state_id=state, branch=branch, text_id=item['TidTalk'], speaker_id=speaker_id,
                    speaker=name[0], text=raw_text, voice_status=status,
                    runtime_play_voice=bool(item.get('PlayVoice')), local_audio_reference=bool(audio_ref),
                    item=item))
        scoped = {x[0] for x in catalog.execute('SELECT line_id FROM scope_lines WHERE scope_id=?', ('quest:'+str(quest_id),))}
        if scoped != {x['catalog_id'] for x in result['lines']}:
            result['blockers'].append('Task catalog coverage differs from current local task graph')
        result['state_ids'] = sorted(visited)
    assets.assert_unchanged()
    result['assets'] = assets.used
    result['mounts'] = assets.mounts
    result['archive_stats'] = assets.before
    result['all_dialogue_unvoiced'] = bool(result['lines']) and not result['blockers'] and all(x['voice_status']=='unvoiced' for x in result['lines'])
    result['basis'] = 'Current mounted local task graph and Chinese text cross-checked; current runtime gates full dialogue audio on PlayVoice, which evaluates false; no alternate audio paths or conflicting local audio rows in audited states.'
    from dialogue_generation import digest
    out = root / 'reports' / ('task-local-audit-' + str(quest_id) + '-' + digest(result) + '.json')
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if out.exists() and out.read_text(encoding='utf-8') != content:
        raise ValueError('Immutable audit file differs; preserved')
    if not out.exists():
        atomic_text(out, content)
    return out, result


if __name__ == '__main__':
    import sys
    path, result = audit_task(sys.argv[1])
    print(json.dumps(dict(path=str(path), task=result['quest_name'], lines=len(result['lines']),
        unvoiced=result['all_dialogue_unvoiced'], blockers=result['blockers']), ensure_ascii=False))
