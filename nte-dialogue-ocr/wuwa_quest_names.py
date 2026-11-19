"""Resolve official titles first, with the quest metadata key as a labeled fallback."""
import json
from wuwa_resources import COMMIT
from dialogue_generation import file_digest


def title(qid, localized='', metadata=''):
    for raw, suffix in ((localized,''),(metadata,'（资源名）')):
        value=str(raw or '').strip()
        if value and not value.isdecimal() and not value.startswith('Quest_'):
            return value+suffix
    return '未命名任务（ID '+str(qid)+'）'


def repair(c):
    missing=list(c.db.execute("SELECT id,name FROM scopes WHERE kind='quest' AND name NOT GLOB '*[^0-9]*'"))
    if not missing:
        return
    folder=c.root/'sources'/COMMIT
    info=folder/'source.json'
    if not info.is_file():
        return
    files=json.loads(info.read_text(encoding='utf8'))['files']
    for name in ('quests','text'):
        if file_digest(folder/(name+'.json'))!=files[name]['sha256']:
            raise ValueError('任务名称索引校验失败，请重新下载台词索引。')
    wanted={scope[6:] for scope,_ in missing}
    meta={str(row['QuestId']):row['Data'] for row in json.loads((folder/'quests.json').read_text(encoding='utf8'))
          if str(row['QuestId']) in wanted}
    tids={data.get('TidName') for data in meta.values()}
    localized={str(r['Id']):r['Content'] for r in json.loads((folder/'text.json').read_text(encoding='utf8'))
               if str(r['Id']) in tids}
    with c.db:
        for scope,_ in missing:
            q=scope[6:]
            data=meta.get(q,{})
            resolved=title(q,localized.get(data.get('TidName')),data.get('Key'))
            c.db.execute('UPDATE scopes SET name=? WHERE id=?',(resolved,scope))

