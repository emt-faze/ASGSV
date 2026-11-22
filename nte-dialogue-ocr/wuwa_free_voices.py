"""Approved secondary voices for a real line and its current primary cast."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

from dialogue_generation import file_digest
from prepared_playback import PreparedLine
from wuwa_audition import current_line, existing_take
import wuwa_breeze as breeze
from wuwa_catalog import resolve_text
from wuwa_update import atomic
import wuwa_tencent_tts as tx


@dataclass(frozen=True)
class FreeTake:
    provider: str
    voice: str
    path: Path
    sha: str
    spoken: str

    @property
    def label(self):
        if self.provider == 'tencent':
            return '腾讯云 · ' + tx.VOICES[int(self.voice)][0]
        return 'TTSMaker · 网页配音'


def primary_voice(c, sid):
    if c.db.execute('SELECT 1 FROM original_voice_roles WHERE speaker_id=? UNION SELECT 1 FROM original_reference_cast WHERE speaker_id=?', (sid,sid)).fetchone():
        return ''
    binding = c.binding(sid)
    if binding and binding['transcript_reviewed']:
        return breeze.pairing(c, sid, binding['voice'])
    return ''


def check_trial(c, trial, selection, primary):
    current_line(c, trial, selection)
    if not primary or primary_voice(c, trial.sid) != primary:
        raise ValueError('当前角色尚未绑定可用的主音色，或绑定已变化，请刷新后再试')
    return breeze.spoken_text(trial.text, c.root)


def original(c, trial, primary, selection):
    spoken = check_trial(c, trial, selection, primary)
    key = breeze.recipe(trial.text, primary, breeze.line_direction(c,trial.identity,primary), spoken=spoken)
    row = c.db.execute("SELECT sha256 FROM breeze_calls WHERE cache_key=? AND state IN ('ready','validated')",(key,)).fetchone()
    path = c.root/'cache/breezeblue'/(key+'.wav')
    if not (row and path.is_file() and file_digest(path) == row[0]):
        clip = existing_take(c, trial, primary, spoken)
        if not clip:
            raise ValueError('这个角色的本句尚无已完成的主声线录音')
        path = clip.path
    breeze.check_audio(path)
    return path


def tencent_trial(c, trial, primary, voice, selection, stop):
    spoken = check_trial(c, trial, selection, primary)
    def current():
        return not stop.is_set() and check_trial(c,trial,selection,primary) == spoken
    path, hit = tx.synthesize(c, spoken, voice, current, single_trial=True)
    if not current():
        raise ValueError('台词或账号已变化；已生成的录音保留在试听缓存中')
    return FreeTake('tencent', str(voice), path, file_digest(path), spoken), hit


def store_file(c, path):
    breeze.check_audio(path)
    data = path.read_bytes()
    if not 44 < len(data) <= breeze.MAX_BYTES:
        raise ValueError('请选择一条完整的单句录音')
    digest = tx.sha(data)
    dest = c.root/'cache/free-voices'/(digest+'.wav')
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file() or file_digest(dest) != digest:
        atomic(dest, data)
    return dest, digest


def web_trial(c, trial, primary, selection, path):
    spoken = check_trial(c, trial, selection, primary)
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= breeze.MAX_BYTES:
        raise ValueError('所选文件为空或超过单句录音大小上限')
    if path.suffix.lower() == '.wav':
        stored, digest = store_file(c, path)
    elif path.suffix.lower() == '.mp3':
        from dialogue_dubbing import voice_code_path
        voice_code_path(json.loads((c.root/'config.json').read_bytes())['dubbing'])
        from wuwa_dubber.tool_discovery import discover_ffmpeg
        with TemporaryDirectory() as folder:
            dst = Path(folder)/'line.wav'
            try:
                proc = subprocess.run([discover_ffmpeg()[0], '-nostdin','-v','error','-protocol_whitelist','file,pipe',
                    '-f','mp3','-i',str(path.resolve()),'-map','0:a:0','-vn','-ac','1','-ar','24000',
                    '-c:a','pcm_s16le','-fs',str(breeze.MAX_BYTES),str(dst)], timeout=45, capture_output=True,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except FileNotFoundError:
                raise ValueError('本机未找到 FFmpeg，请在 TTSMaker 选择 WAV 下载后再导入') from None
            if proc.returncode or not dst.is_file() or dst.stat().st_size >= breeze.MAX_BYTES:
                raise ValueError('MP3 解码失败或过长，请下载本句的 WAV')
            stored, digest = store_file(c, dst)
    else:
        raise ValueError('请选择 TTSMaker 下载的 WAV 或 MP3')
    return FreeTake('ttsmaker', 'web', stored, digest, spoken)


def accept(c, trial, primary, selection, take):
    from quest_library import prepare_cast
    from wuwa_cast_review import line_problem
    check_trial(c,trial,selection,primary)
    if not take.path.is_file() or file_digest(take.path) != take.sha:
        raise ValueError('试音文件已变化，请重新试听')
    _, digest = store_file(c,take.path)
    prepare_cast(c,trial.scope,[trial])
    with c.db:
        c.db.execute('BEGIN IMMEDIATE')
        if digest != take.sha or take.spoken != check_trial(c,trial,selection,primary):
            raise ValueError('试音时的读音或录音已经变化')
        problem = line_problem(c,trial.identity,trial.sid,primary)
        if problem:
            raise ValueError(problem)
        if take.provider == 'tencent' and int(take.voice) in tx.VOICES:
            c.db.execute('INSERT OR REPLACE INTO free_tts_cast VALUES(?,?,?)',(trial.sid,primary,int(take.voice)))
        elif (take.provider,take.voice) != ('ttsmaker','web'):
            raise ValueError('无法确认该备用音色')
        c.db.execute('INSERT OR REPLACE INTO free_tts_lines VALUES(?,?,?,?,?,?,?,?,?)',
                     (trial.identity,primary,trial.sid,trial.fingerprint,trial.text,take.spoken,take.provider,take.voice,digest))


def configured(c, sid, primary):
    row = c.db.execute('SELECT tencent_voice FROM free_tts_cast WHERE speaker_id=? AND primary_voice=?',(sid,primary)).fetchone()
    return row[0] if row and row[0] in tx.VOICES else None


def accepted(c, row, primary, body, spoken):
    item = c.db.execute('SELECT * FROM free_tts_lines WHERE line_id=? AND primary_voice=?',(row['id'],primary)).fetchone()
    if not item or (item['speaker_id'],item['fingerprint'],item['body'],item['spoken']) != (row['speaker_id'],row['fingerprint'],body,spoken):
        return None
    if item['provider'] == 'tencent':
        if str(configured(c,row['speaker_id'],primary)) != item['voice']:
            return None
    elif (item['provider'],item['voice']) != ('ttsmaker','web'):
        return None
    digest = item['sha256']
    if not re.fullmatch('[0-9a-f]{64}',digest):
        return None
    path = c.root/'cache/free-voices'/(digest+'.wav')
    if not path.is_file() or file_digest(path) != digest:
        return None
    breeze.check_audio(path)
    return FreeTake(item['provider'],item['voice'],path,digest,spoken)


def available(c, row, primary, body, spoken):
    if not primary or not (configured(c,row['speaker_id'],primary) or
            c.db.execute('SELECT 1 FROM free_tts_lines WHERE line_id=? AND primary_voice=?',(row['id'],primary)).fetchone()):
        return False
    if primary_voice(c,row['speaker_id']) != primary:
        return False
    if accepted(c,row,primary,body,spoken):
        return True
    voice = configured(c,row['speaker_id'],primary)
    return bool(voice and tx.available(c,spoken,voice))


def secondary(c, row, primary, body, spoken, current):
    if not current() or primary_voice(c,row['speaker_id']) != primary:
        return None
    take = accepted(c,row,primary,body,spoken)
    if take:
        return take, True
    voice = configured(c,row['speaker_id'],primary)
    if not voice:
        return None
    def still_selected():
        return current() and configured(c,row['speaker_id'],primary) == voice and primary_voice(c,row['speaker_id']) == primary
    path, hit = tx.synthesize(c,spoken,voice,still_selected)
    if not still_selected():
        raise ValueError('备用声线或台词已切换，已保留缓存')
    return FreeTake('tencent',str(voice),path,file_digest(path),spoken), hit


def valid_project(c, row, body, primary, info):
    spoken = breeze.spoken_text(body,c.root)
    if info.get('tts_text') != spoken or primary_voice(c,row['speaker_id']) != primary:
        return False
    if info['provider'] == 'tencent':
        voice = configured(c,row['speaker_id'],primary)
        return bool(voice and info.get('secondary_voice') == str(voice))
    take = accepted(c,row,primary,body,spoken)
    return bool(take and take.provider == info['provider'] and take.sha == info.get('sha256'))


def approved_clips(c, config, only=None):
    from wuwa_cast_review import line_problem
    query = 'SELECT l.*, a.primary_voice FROM free_tts_lines a JOIN lines l ON l.id=a.line_id'
    args = ()
    if only:
        query += ' WHERE a.line_id=?'
        args = (only[0],)
    for row in c.db.execute(query,args):
        primary, sid = row['primary_voice'], row['speaker_id']
        binding = c.binding(sid)
        if (not binding or (only and only[1] != binding['voice']) or primary_voice(c,sid) != primary
                or not c.voice_basis(row,c.local_version(),allow_branch=c.branch_rule_enabled())
                or line_problem(c,row['id'],sid,primary)):
            continue
        body,missing = resolve_text(row['raw_text'],config.get('nickname','漂泊者'),config.get('traveler_gender','female'))
        if missing:
            continue
        take = accepted(c,row,primary,body,breeze.spoken_text(body,c.root))
        if take:
            yield (row['id'],binding['voice']), PreparedLine(row['id'],body,row['speaker'],take.label,take.path,
                take.sha,True,row['id'],binding['voice'],primary)
