"""One speaker, one real quest line. Only an explicit confirmation changes the cast."""
from dataclasses import dataclass
import json
from pathlib import Path

import wuwa_breeze as breeze
from dialogue_generation import file_digest
from wuwa_catalog import resolve_text
from wuwa_cast_review import original_problem


class NeedsAudition(ValueError):
    pass


def pending(c, sid):
    return c.db.execute('SELECT * FROM audition_holds WHERE account=? AND speaker_id=?',
                        (c.progress.account, sid)).fetchone()


def hold(c, selection, scope, row, reason, candidate='', expected=''):
    """Persist speaker-level uncertainty; a concurrent user decision always wins."""
    sid = row['speaker_id']
    with c.db:
        c.db.execute('BEGIN IMMEDIATE')
        c.progress.require(selection)
        binding = c.binding(sid)
        current = binding['voice'] if binding else ''
        if current != expected or (current and c.meta('confirmed_cloud:'+sid) == current):
            raise ValueError('角色绑定已变化，请刷新后重试')
        c.db.execute('INSERT OR IGNORE INTO audition_holds VALUES(?,?,?,?,?,?)',
                     (selection[0], sid, scope, row['id'], reason, candidate))
    raise NeedsAudition(reason+' · 请在任务库试听确认')


def editable(c, sid, voice):
    if c.db.execute('SELECT 1 FROM original_reference_cast WHERE speaker_id=?',(sid,)).fetchone():
        raise ValueError('此角色已绑定原声参考')
    issue = original_problem(c, sid, voice)
    if issue:
        raise ValueError(issue)
    if not c.db.execute('SELECT 1 FROM breeze_presets WHERE id=?',(voice,)).fetchone():
        raise ValueError('请在每日配音计划中同步云端音色')


@dataclass(frozen=True)
class TrialLine:
    scope: str
    sid: str
    identity: str
    fingerprint: str
    text: str


def own_line(c, scope, sid, preferred=''):
    cfg = json.loads((c.root/'config.json').read_bytes())
    version = c.local_version()
    allow_branch = c.branch_rule_enabled()
    waiting = pending(c,sid)
    binding = c.binding(sid)
    candidate = (waiting['candidate'] if waiting else '') or (binding['voice'].removeprefix('breeze:') if binding else '')
    options = []
    rows = [row for row in c.lines([scope]) if row['speaker_id']==sid]
    for row in sorted(rows,key=lambda row:row['id']!=preferred):
        if not c.voice_basis(row,version,allow_branch=allow_branch):
            continue
        text, unknown = resolve_text(row['raw_text'], cfg.get('nickname','漂泊者'),cfg.get('traveler_gender','female'))
        spoken = breeze.spoken_text(text, c.root)
        if unknown or not 1 <= len(spoken) <= 1000 or not any(ch.isalnum() for ch in spoken):
            continue
        trial = TrialLine(scope,sid,row['id'],row['fingerprint'],text)
        if row['id'] == preferred:
            return trial
        cached = False
        if candidate:
            key = breeze.recipe(text,candidate,breeze.line_direction(c,row['id'],candidate),spoken=spoken)
            cached = (c.root/'cache/breezeblue'/(key+'.wav')).is_file() and c.db.execute(
                "SELECT 1 FROM breeze_calls WHERE cache_key=? AND state IN ('ready','validated')",(key,)).fetchone() is not None
        rank = (not cached, not 8 <= len(spoken) <= 48, abs(len(spoken)-22), row['id'])
        options.append((rank,trial))
    if not options:
        raise ValueError('该角色暂无可试音的已核实无原声台词，请先探测游戏资源并更新台词索引')
    return min(options,key=lambda item:item[0])[1]


def current_line(c, trial, selection):
    c.progress.require(selection)
    row = c.db.execute('''SELECT l.* FROM lines l JOIN scope_lines sl ON sl.line_id=l.id
        WHERE sl.scope_id=? AND l.id=? AND l.speaker_id=?''',(trial.scope,trial.identity,trial.sid)).fetchone()
    if not row or row['fingerprint'] != trial.fingerprint or not c.voice_basis(
            row,c.local_version(),allow_branch=c.branch_rule_enabled()):
        raise ValueError('任务台词或原声依据已变化，请重新选择试音台词')
    cfg = json.loads((c.root/'config.json').read_bytes())
    text, unknown = resolve_text(row['raw_text'],cfg.get('nickname','漂泊者'),cfg.get('traveler_gender','female'))
    if unknown or text != trial.text:
        raise ValueError('台词内容已变化，请重新试听')


def existing_take(c, trial, voice, spoken):
    from wuwa_voice_pack import load_pack, installed_previews
    from wuwa_log_generation import projects, prepared_line
    bound = 'breeze:'+voice
    key = breeze.recipe(trial.text,voice,breeze.line_direction(c,trial.identity,voice),spoken=spoken)
    def match(item):
        entry,clip = item
        return (clip.binding_voice == bound and clip.text == trial.text and entry['id'] == trial.identity
                and entry['speaker_id'] == trial.sid and entry['fingerprint'] == trial.fingerprint
                and clip.path.is_file() and file_digest(clip.path) == clip.sha256)
    installed = installed_previews(c.root).get(trial.scope, (0, {}))[1].get(trial.identity)
    if installed and match(installed):
        return installed[1]
    for item in load_pack(c.root,allow_missing_audio=True).values():
        if match(item):
            return item[1]
    for project in projects(c.root):
        for entry in project.rows:
            if (entry.source_id == trial.identity and entry.voice == bound and entry.text == trial.text
                    and entry.evidence.get('speaker_id') == trial.sid and entry.evidence.get('fingerprint') == trial.fingerprint
                    and entry.audio and entry.audio.get('cloud_voice') == voice
                    and entry.audio.get('cache_key') == key):
                clip = prepared_line(project,entry)
                if clip:
                    return clip
    return None


@dataclass(frozen=True)
class Take:
    line: TrialLine
    voice: str
    path: Path
    sha: str
    reused: bool
    direction: str
    spoken: str


def audition(c, trial, voice, selection, stop):
    current_line(c,trial,selection)
    editable(c,trial.sid,voice)
    old = c.binding(trial.sid)
    prior_binding = old['voice'] if old else ''
    direction = breeze.line_direction(c,trial.identity,voice)
    spoken = breeze.spoken_text(trial.text,c.root)
    key = breeze.recipe(trial.text,voice,direction,spoken=spoken)
    cached = c.db.execute('SELECT state FROM breeze_calls WHERE cache_key=?',(key,)).fetchone()
    path = c.root/'cache/breezeblue'/(key+'.wav')
    existing = None if cached and path.is_file() else existing_take(c,trial,voice,spoken)
    def still_current():
        current_line(c,trial,selection)
        binding = c.binding(trial.sid)
        return not stop.is_set() and (binding['voice'] if binding else '') == prior_binding
    if not still_current():
        raise ValueError('已取消本次试音')
    path, reused = breeze.cached_wav(c,trial.text,voice,still_current,direction,
        spoken=spoken,max_daily_credits=400,reuse=existing)
    return Take(trial,voice,path,file_digest(path),reused,direction,spoken)


def confirm_take(c, take, selection, previous):
    from quest_library import choose_cloud_voice
    with c.db:
        c.db.execute('BEGIN IMMEDIATE')
        current_line(c,take.line,selection)
        binding = c.binding(take.line.sid)
        if (binding['voice'] if binding else '') not in (previous,'breeze:'+take.voice):
            raise ValueError('此角色已在其他窗口换声，请重新打开试音')
        if (not take.path.is_file() or file_digest(take.path)!=take.sha or
                breeze.spoken_text(take.line.text,c.root)!=take.spoken or
                breeze.line_direction(c,take.line.identity,take.voice)!=take.direction):
            raise ValueError('录音或演绎设置已变化，请重新试听')
        choose_cloud_voice(c,take.line.sid,take.voice)
