"""BreezeBlue backup for an explicitly paired voice. Credit units are tenths of a credit."""
from datetime import date
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4
import wave
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

import numpy as np
from audio_edges import _decode_pcm
from dialogue_generation import digest, file_digest

MODEL = 'breeze-tts-2-multilingual'
DIRECTION = '自然的中文对白，清晰近讲，跟随人物语气轻重和停顿，避免播音腔。'
CREDENTIAL = 'ASGSV/BreezeBlue/API'
MAX_BYTES = 32 * 1024 * 1024


def api_key():
    import win32cred
    import pywintypes
    try:
        saved = win32cred.CredRead(CREDENTIAL, win32cred.CRED_TYPE_GENERIC)
        blob = saved['CredentialBlob']
        # pywin32 writes Unicode strings as UTF-16LE. Older byte-based
        # writers may already have stored a plain UTF-8 API key.
        return blob.decode('utf-16-le' if b'\x00' in blob else 'utf-8')
    except pywintypes.error as exc:
        if exc.winerror != 1168:
            raise ValueError('无法读取 Windows 中保存的 BreezeBlue 密钥') from None
    return os.environ.get('BREEZE_API_KEY','').strip()


def save_key(key):
    import win32cred
    win32cred.CredWrite(dict(Type=win32cred.CRED_TYPE_GENERIC, TargetName=CREDENTIAL,
        CredentialBlob=key.strip(), Persist=win32cred.CRED_PERSIST_LOCAL_MACHINE))


def voice_id(value):
    value = value.strip()
    if value and not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',value):
        raise ValueError('请填写 BreezeBlue Voice ID')
    return value


def pairing(catalog,speaker_id,local_voice):
    if local_voice.startswith('breeze:'):
        return local_voice.partition(':')[2]
    pair=catalog.db.execute('SELECT voice_id FROM breeze_voices WHERE speaker_id=? AND local_voice=?',
                            (speaker_id,local_voice)).fetchone()
    return pair[0] if pair else ''


def set_pairing(catalog,speaker_id,local_voice,cloud_voice):
    cloud_voice=voice_id(cloud_voice)
    with catalog.db:
        if cloud_voice:
            catalog.db.execute('INSERT OR REPLACE INTO breeze_voices VALUES(?,?,?)',
                               (speaker_id,local_voice,cloud_voice))
        else:
            catalog.db.execute('DELETE FROM breeze_voices WHERE speaker_id=? AND local_voice=?',
                               (speaker_id,local_voice))


class AudioQualityError(ValueError):
    pass


def refresh_presets(catalog):
    key = api_key()
    if not key:
        raise ValueError('请先填写 BreezeBlue 密钥')
    presets = {}
    number = 1
    while True:
        query = dict(language_code='zh', page_size=100, page=number)
        size_before = len(presets)
        request = Request('https://api.breeze.blue/v1/voices?' + urlencode(query), headers={'xi-api-key': key})
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            page = json.load(response)
        for item in page['voices']:
            if item.get('language_code') == 'zh' and item.get('name'):
                vid = voice_id(item['voice_id'])
                presets[vid] = (vid, item['name'], item.get('gender'), item.get('age'))
        if not page.get('has_more'):
            break
        if len(presets) == size_before:
            raise ValueError('云端音色目录分页不完整')
        number += 1
    if not presets:
        raise ValueError('账号没有可用的中文云端音色')
    with catalog.db:
        catalog.db.executemany('INSERT OR REPLACE INTO breeze_presets VALUES(?,?,?,?)', presets.values())
    return len(presets)


def assign_preset(catalog, row):
    sid, speaker = row['speaker_id'], row['speaker']
    binding = catalog.binding(sid)
    if binding:
        return binding
    presets = catalog.db.execute('SELECT * FROM breeze_presets ORDER BY id').fetchall()
    if not presets:
        raise ValueError('请先同步 BreezeBlue 中文音色目录')
    from wuwa_cast_context import for_speaker
    scopes = [r[0] for r in catalog.db.execute(
        "SELECT scope_id FROM scope_lines WHERE line_id=? AND scope_id LIKE 'quest:%'", (row['id'],)
    )] if 'id' in row.keys() else []
    gender, age, _ = for_speaker(catalog, sid, speaker, scopes[0] if len(scopes)==1 else None)
    candidates = [p for p in presets if not gender or p['gender'] == gender] or presets
    matching_age = [p for p in candidates if age and p['age'] == age]
    ordinary = [p for p in candidates if p['age'] in ('young','middle_aged')]
    pool = [p for p in candidates if p['name'] == speaker and (not age or p['age']==age)] or matching_age or ordinary or candidates
    selected = pool[int.from_bytes(hashlib.sha256(sid.encode()).digest()[:8],'big') % len(pool)]
    with catalog.db:
        catalog.db.execute('INSERT OR IGNORE INTO breeze_cast VALUES(?,?)', (sid, selected['id']))
    return catalog.binding(sid)


@lru_cache(maxsize=256)
def _check(path,size,mtime_ns):
    try:
        with wave.open(str(path),'rb') as handle:
            frames, rate = handle.getnframes(), handle.getframerate()
            width, channels = handle.getsampwidth(), handle.getnchannels()
            if frames < rate*.1 or frames*width*channels > MAX_BYTES or not 1<=channels<=2:
                raise AudioQualityError('音频长度或声道异常')
            raw=handle.readframes(frames)
            if len(raw)!=frames*width*channels:
                raise AudioQualityError('音频数据不完整')
        data=_decode_pcm(raw,width,channels)
        if np.max(np.abs(data))<.001 or np.std(data,axis=0).max()<.00015:
            raise AudioQualityError('音频近乎静音')
        if np.mean(np.abs(data)>=.999)>.02:
            raise AudioQualityError('音频严重削波')
    except (EOFError,wave.Error) as exc:
        raise AudioQualityError('音频格式无效') from exc


def check_audio(path):
    info=path.stat()
    _check(path,info.st_size,info.st_mtime_ns)


def line_direction(catalog,line_id,cloud_voice):
    return catalog.meta(f'breeze_direction:{line_id}:{cloud_voice}', DIRECTION)


def spoken_text(text):
    return re.sub(r'（\*[^*（）]+\*）', '', text).strip()


def recipe(text,cloud_voice,direction=DIRECTION):
    inputs = dict(provider='BreezeBlue',voice_id=cloud_voice,text=text,model=MODEL,
                  language='zh',direction=direction,format='wav_24000')
    if spoken_text(text) != text:
        inputs['spoken_text'] = spoken_text(text)
    return digest(inputs)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):
        return None


def fetch_wav(text,cloud_voice,key,direction=DIRECTION):
    req=Request('https://api.breeze.blue/v1/text-to-speech/'+voice_id(cloud_voice)+'?output_format=wav_24000',
        data=json.dumps(dict(text=spoken_text(text),model_id=MODEL,language_code='zh',instructions=direction)).encode('utf-8'),
        headers={'xi-api-key':key,'Content-Type':'application/json','Accept':'audio/wav'},method='POST')
    with build_opener(NoRedirect()).open(req,timeout=55) as response:
        if response.status!=200:
            raise ValueError('云端响应不是完整音频')
        data=response.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES:
            raise ValueError('云端音频超出大小限制')
        return data


def cached_wav(catalog,text,cloud_voice,allow_charge,direction=DIRECTION):
    key=recipe(text,cloud_voice,direction)
    folder=catalog.root/'cache/breezeblue'
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/(key+'.wav')
    db=catalog.db
    # BEGIN IMMEDIATE serializes the budget and duplicate-request checks across workers.
    db.execute('BEGIN IMMEDIATE')
    try:
        prior=db.execute('SELECT state,sha256 FROM breeze_calls WHERE cache_key=?',(key,)).fetchone()
        if prior:
            if prior[0] in ('validated','ready') and path.is_file() and file_digest(path)==prior[1]:
                check_audio(path)
                if prior[0]=='validated':
                    db.execute("UPDATE breeze_calls SET state='ready' WHERE cache_key=?",(key,))
                db.commit()
                return path,True
            raise ValueError('同一云端请求已有记录，请核对云端历史后处理，已避免重复扣额')
        limit=int(catalog.meta('breeze_daily_credits','0'))*10
        today=date.today().isoformat()
        spent=db.execute('SELECT COALESCE(SUM(units),0) FROM breeze_calls WHERE day=?',(today,)).fetchone()[0]
        # Reserve the documented maximum weight (2 units) for each character.
        units=2*len(text)
        if not allow_charge():
            raise ValueError('字幕或声线已变化，已跳过云端补录')
        if not limit or spent+units>limit:
            raise ValueError('BreezeBlue 今日预算不足')
        if not 1<=len(text)<=1000:
            raise ValueError('台词超出云端单次长度')
        secret=api_key()
        if not secret:
            raise ValueError('请在鸣潮设置中填写 BreezeBlue API Key')
        db.execute("INSERT INTO breeze_calls VALUES(?,?,?,'submitted',NULL)",(key,today,units))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    staging=folder/('.'+uuid4().hex+'.wav')
    try:
        try:
            if not allow_charge():
                raise ValueError('台词已切换，已跳过云端补录')
        except Exception:
            with db:
                db.execute('DELETE FROM breeze_calls WHERE cache_key=?',(key,))
            raise
        data=fetch_wav(text,cloud_voice,secret,direction)
        staging.write_bytes(data)
        check_audio(staging)
        with wave.open(str(staging),'rb') as handle:
            if (handle.getframerate(),handle.getnchannels(),handle.getsampwidth())!=(24000,1,2):
                raise ValueError('云端返回了非预期的音频格式')
        sha=hashlib.sha256(data).hexdigest()
        with db:
            db.execute("UPDATE breeze_calls SET state='validated',sha256=? WHERE cache_key=?",(sha,key))
        staging.replace(path)
        with db:
            db.execute("UPDATE breeze_calls SET state='ready',sha256=? WHERE cache_key=?",(sha,key))
        return path,False
    except HTTPError as exc:
        if exc.code in (400,401,402,403,404,422,429):
            with db:
                db.execute("DELETE FROM breeze_calls WHERE cache_key=?",(key,))
        raise ValueError(f'BreezeBlue HTTP {exc.code}，请检查密钥、音色或额度') from None
    finally:
        staging.unlink(missing_ok=True)

