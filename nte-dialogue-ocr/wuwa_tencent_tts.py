"""Tencent TextToVoice with an account-wide, non-renewing free-pack allowance."""
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from uuid import uuid4
import wave

from wuwa_breeze import MAX_BYTES, NoRedirect, check_audio
from wuwa_update import atomic

CREDENTIAL = 'ASGSV/TencentCloud/TTS'
PACK_PAGE = 'https://console.cloud.tencent.com/tts'
KEY_PAGE = 'https://console.cloud.tencent.com/cam/capi'
MAKER_PAGE = 'https://ttsmaker.com/zh-cn'
TIERS = {'super2': ('超自然大模型 2.0', 20000), 'super1': ('超自然大模型 1.0', 20000),
         'model': ('大模型', 100000), 'standard': ('基础 / 精品', 8000000)}
# Each id is priced by its own pack. Never substitute a voice from another tier.
VOICES = {403013: ('云小璇', '活力女青年', 'super2'), 403010: ('云嘉和', '温和男声', 'super2'),
          403003: ('云小天', '青春校园男声', 'super2'), 403005: ('云雅琴', '沉稳女老师', 'super2'),
          403014: ('云佳洁', '轻熟姐姐', 'super2'), 403008: ('云小洋', '机灵男童', 'super2'),
          603003: ('随和老李', '聊天男声', 'super1'), 603004: ('温柔小柠', '聊天女声', 'super1'),
          601008: ('爱小豪', '聊天男声', 'model'), 601009: ('爱小芊', '聊天女声', 'model'),
          101004: ('智云', '通用男声', 'standard'), 101001: ('智瑜', '情感女声', 'standard')}
SERVICES = {'tts': ('2019-08-23', 'TextToVoice'), 'sts': ('2018-08-13', 'GetCallerIdentity')}


def storage():
    return Path(os.environ['LOCALAPPDATA']) / 'ASGSV' / 'free-tts'


def saved_credentials():
    import win32cred
    import pywintypes
    try:
        item = win32cred.CredRead(CREDENTIAL, win32cred.CRED_TYPE_GENERIC)
    except pywintypes.error as exc:
        if exc.winerror == 1168:
            return None
        raise ValueError('无法访问 Windows 中保存的腾讯云密钥') from None
    return tuple(json.loads(item['CredentialBlob'].decode('utf-16-le')))


def save_credentials(pair):
    import win32cred
    win32cred.CredWrite(dict(Type=win32cred.CRED_TYPE_GENERIC, TargetName=CREDENTIAL,
                             CredentialBlob=json.dumps(pair), Persist=win32cred.CRED_PERSIST_LOCAL_MACHINE))


def sha(data):
    return hashlib.sha256(data).hexdigest()


class TencentError(ValueError):
    def __init__(self, code, request_id=''):
        self.code = code
        super().__init__('腾讯云返回 ' + code + (' · 请求 ' + request_id if request_id else ''))


def signed_request(service, payload, pair, stamp):
    version, action = SERVICES[service]
    host = service + '.tencentcloudapi.com'
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    day = datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m-%d')
    fmt = 'application/json; charset=utf-8'
    canonical = f'POST\n/\n\ncontent-type:{fmt}\nhost:{host}\n\ncontent-type;host\n{sha(body)}'
    scope = day + '/' + service + '/tc3_request'
    message = f'TC3-HMAC-SHA256\n{stamp}\n{scope}\n{sha(canonical.encode())}'
    key = ('TC3' + pair[1]).encode()
    for component in (day, service, 'tc3_request'):
        key = hmac.digest(key, component.encode(), 'sha256')
    signature = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    auth = f'TC3-HMAC-SHA256 Credential={pair[0]}/{scope}, SignedHeaders=content-type;host, Signature={signature}'
    headers = {'Content-Type': fmt, 'Host': host, 'Authorization': auth, 'X-TC-Action': action,
               'X-TC-Version': version, 'X-TC-Timestamp': str(stamp), 'X-TC-Region': 'ap-guangzhou'}
    return Request('https://' + host + '/', headers=headers, data=body, method='POST')


def call(service, payload, pair):
    req = signed_request(service, payload, pair, int(time.time()))
    try:
        with build_opener(NoRedirect()).open(req, timeout=45) as response:
            raw = response.read(MAX_BYTES * 2 + 1)
    except HTTPError as exc:
        raise ValueError(f'腾讯云 HTTP {exc.code}，请核对控制台；本次不自动重发。') from None
    except (OSError, URLError) as exc:
        raise ValueError('腾讯云连接中断，结果待核对；本次不自动重发。') from exc
    if len(raw) > MAX_BYTES * 2:
        raise ValueError('腾讯云返回的数据过大')
    response = json.loads(raw).get('Response')
    if not isinstance(response, dict):
        raise ValueError('腾讯云响应缺少 Response')
    if 'Error' in response:
        raise TencentError(str(response['Error'].get('Code', 'Unknown')), str(response.get('RequestId', '')))
    return response


def account(pair):
    if not pair or len(pair) != 2 or not all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', x) for x in pair):
        raise ValueError('请在「免费备用源」填写腾讯云 SecretId 和 SecretKey')
    identity = call('sts', {}, pair).get('AccountId')
    if not isinstance(identity, str) or not re.fullmatch(r'[0-9]{4,24}', identity):
        raise ValueError('腾讯云没有返回可核对的主账号标识')
    return sha(('ASGSV-TTS:' + identity).encode())


@contextmanager
def ledger():
    folder = storage()
    folder.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder/'tencent.sqlite', timeout=15)) as db:
        db.row_factory = sqlite3.Row
        db.executescript('''
            CREATE TABLE IF NOT EXISTS grants(owner TEXT, tier TEXT, until_date TEXT NOT NULL,
                remaining INTEGER NOT NULL CHECK(remaining>=0), PRIMARY KEY(owner,tier));
            CREATE TABLE IF NOT EXISTS auditions(owner TEXT, tier TEXT, expires INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner,tier));
            CREATE TABLE IF NOT EXISTS calls(owner TEXT, recipe TEXT, session TEXT NOT NULL,
                state TEXT NOT NULL, sha TEXT NOT NULL DEFAULT '', PRIMARY KEY(owner,recipe));
        ''')
        yield db


def grant_info(owner, tier):
    with ledger() as db:
        row = db.execute('SELECT * FROM grants WHERE owner=? AND tier=?', (owner, tier)).fetchone()
        return dict(row) if row else None


def configure(c, pair, tier, amount, expiry, free_only):
    if not free_only:
        raise ValueError('请先在腾讯云控制台关闭 TTS 后付费，并确认此档没有可抵扣的付费包')
    if tier not in TIERS or not isinstance(amount, int) or not 0 <= amount <= TIERS[tier][1]:
        raise ValueError('请填写该档免费资源包在控制台显示的剩余字符')
    # The date is the console expiry day in Beijing. Stop at its start so a
    # partial last day cannot overrun the provider's exact cutoff.
    today = datetime.fromtimestamp(time.time(), timezone.utc).astimezone(BEIJING).date()
    days = (date.fromisoformat(expiry) - today).days
    if not 0 < days <= 93:
        raise ValueError('请按控制台填写免费包到期日（未来三个月内），到期日当天停止')
    owner = account(pair)
    with ledger() as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''INSERT INTO grants VALUES(?,?,?,?) ON CONFLICT(owner,tier) DO UPDATE SET
                 remaining=min(remaining,excluded.remaining), until_date=min(until_date,excluded.until_date)''',
                   (owner, tier, expiry, amount))
    save_credentials(pair)
    with c.db:
        c.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('fallback_tencent_owner', owner))
    return grant_info(owner, tier)


BEIJING = timezone(timedelta(hours=8))


def enable_trial(c, pair, tier, free_only):
    """One interactive request, never usable by a job. A recorded grant always takes precedence."""
    if not free_only or tier not in TIERS:
        raise ValueError('请先确认该档免费资源包已生效、后付费已关闭且没有同档付费包')
    owner = account(pair)
    with ledger() as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''INSERT INTO auditions(owner,tier,expires) VALUES(?,?,?)
                    ON CONFLICT(owner,tier) DO UPDATE SET expires=excluded.expires WHERE used=0''',
                   (owner,tier,int(time.time())+1800))
        used = db.execute('SELECT used FROM auditions WHERE owner=? AND tier=?',(owner,tier)).fetchone()[0]
        if used:
            raise ValueError('该档单句试听已使用；可重听缓存，继续合成请登记免费包实际余量')
    save_credentials(pair)
    with c.db:
        c.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('fallback_tencent_owner',owner))
    return {'trial':True}


def wav_recipe(spoken, voice):
    rate = 16000 if VOICES[voice][2] == 'standard' else 24000
    return sha(json.dumps(['text-to-voice-v1', voice, spoken, rate], ensure_ascii=False).encode()), rate


def available(c, spoken, voice):
    owner = c.meta('fallback_tencent_owner')
    if voice not in VOICES or not re.fullmatch(r'[0-9a-f]{64}', owner):
        return False
    if not spoken.strip() or len(spoken) > (500 if spoken.isascii() else 150):
        return False
    recipe,_ = wav_recipe(spoken,voice)
    with ledger() as db:
        call_state = db.execute('SELECT state,sha FROM calls WHERE owner=? AND recipe=?',(owner,recipe)).fetchone()
        if call_state:
            path = storage()/'wav'/owner/(recipe+'.wav')
            return bool(call_state['state']=='ready' and path.is_file() and sha(path.read_bytes())==call_state['sha'])
        return bool(db.execute('SELECT 1 FROM grants WHERE owner=? AND tier=? AND until_date>? AND remaining>=?',
            (owner,VOICES[voice][2],datetime.now(BEIJING).date().isoformat(),len(spoken))).fetchone())


def synthesize(c, spoken, voice, current, *, single_trial=False):
    if voice not in VOICES:
        raise ValueError('未识别的腾讯云音色')
    if not spoken.strip() or not 1 <= len(spoken) <= (500 if spoken.isascii() else 150):
        raise ValueError('腾讯云单句上限为 150 个中文字符或 500 个英文字符，请选择较短的一句')
    tier = VOICES[voice][2]
    recipe, rate = wav_recipe(spoken,voice)
    owner = c.meta('fallback_tencent_owner')
    if not re.fullmatch(r'[0-9a-f]{64}', owner):
        raise ValueError('腾讯云尚未登记免费资源包，请在「配音与预算 → 免费备用源」配置')
    folder = storage()/'wav'/owner
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(recipe+'.wav')
    with ledger() as db:
        def cached():
            prev = db.execute('SELECT state,sha FROM calls WHERE owner=? AND recipe=?', (owner,recipe)).fetchone()
            if not prev:
                return False
            if prev['state'] != 'ready':
                raise ValueError('腾讯云这句已有未核实的提交，请先核对控制台，已阻止重复请求')
            if not path.is_file() or sha(path.read_bytes()) != prev['sha']:
                raise ValueError('已生成的腾讯云缓存缺失或已改变，请检查原文件')
            check_audio(path)
            return True
        if cached():
            return path, True
        grant = grant_info(owner, tier)
        one = (not grant and single_trial and db.execute(
            'SELECT 1 FROM auditions WHERE owner=? AND tier=? AND used=0 AND expires>?',
            (owner,tier,int(time.time()))).fetchone())
        if not one and (not grant or grant['until_date'] <= datetime.now(BEIJING).date().isoformat() or grant['remaining'] < len(spoken)):
            raise ValueError('腾讯云「'+TIERS[tier][0]+'」免费资源包未登记、已到期或余量不足，已停止')
        pair = saved_credentials()
        if account(pair) != owner:
            raise ValueError('保存的密钥与登记免费包的腾讯云主账号不同')
        session = str(uuid4())
        if not current():
            raise ValueError('试听已取消或台词已切换')
        with db:
            db.execute('BEGIN IMMEDIATE')
            if cached():
                return path, True
            day = datetime.now(BEIJING).date().isoformat()
            before = db.execute('SELECT remaining,until_date FROM grants WHERE owner=? AND tier=?',(owner,tier)).fetchone()
            changed = db.execute('''UPDATE grants SET remaining=remaining-? WHERE owner=? AND tier=?
                    AND until_date>? AND remaining>=?''', (len(spoken), owner, tier, day, len(spoken))).rowcount
            if not changed and one:
                changed = db.execute('''UPDATE auditions SET used=1 WHERE owner=? AND tier=? AND used=0 AND expires>?
                    AND NOT EXISTS(SELECT 1 FROM grants WHERE owner=? AND tier=?)''',
                    (owner,tier,int(time.time()),owner,tier)).rowcount
            if not changed:
                raise ValueError('腾讯云「'+TIERS[tier][0]+'」免费余量或单句试听额度不足，已停止')
            db.execute("INSERT INTO calls(owner,recipe,session,state) VALUES(?,?,?,'submitted')", (owner, recipe, session))
            if not current() or c.meta('fallback_tencent_owner') != owner:
                raise ValueError('台词、账号或备用源设置已切换，未发送请求')
        try:
            reply = call('tts', dict(Text=spoken, SessionId=session, VoiceType=voice, Codec='wav',
                                     SampleRate=rate, PrimaryLanguage=1), pair)
        except TencentError as exc:
            if exc.code in ('UnsupportedOperation.PkgExhausted', 'UnsupportedOperation.NoFreeAccount'):
                with db:
                    db.execute('UPDATE grants SET remaining=0 WHERE owner=? AND tier=?', (owner,tier))
                    db.execute('DELETE FROM calls WHERE owner=? AND recipe=?', (owner, recipe))
                raise ValueError('腾讯云已确认该档免费包用尽，已停止') from None
            if (exc.code.startswith(('AuthFailure','UnauthorizedOperation','InvalidParameter'))
                    or exc.code in ('LimitExceeded.AccessLimit','UnsupportedOperation.AuthorizationFailed',
                                    'UnsupportedOperation.ServerNotOpen','UnsupportedOperation.ForbiddenUse')):
                # The service explicitly rejected this request before synthesis. Never retry here.
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('DELETE FROM calls WHERE owner=? AND recipe=? AND session=?',(owner,recipe,session))
                    if before is None:
                        db.execute('UPDATE auditions SET used=0 WHERE owner=? AND tier=?',(owner,tier))
                    else:
                        # Keep a concurrently lowered or exhausted cap intact.
                        db.execute('''UPDATE grants SET remaining=? WHERE owner=? AND tier=? AND remaining=? AND until_date=?''',
                            (before['remaining'],owner,tier,before['remaining']-len(spoken),before['until_date']))
            raise
        if reply.get('SessionId') != session or not reply.get('RequestId'):
            raise ValueError('腾讯云会话回执与本句不符，已保留预留记录')
        data = base64.b64decode(reply['Audio'], validate=True)
        if not 44 < len(data) <= MAX_BYTES:
            raise ValueError('腾讯云未返回完整 WAV')
        scratch = folder/(session+'.wav')
        try:
            atomic(scratch, data)
            check_audio(scratch)
            with wave.open(str(scratch)) as f:
                if (f.getframerate(),f.getnchannels(),f.getsampwidth()) != (rate,1,2):
                    raise ValueError('腾讯云输出与请求的 PCM WAV 格式不符')
            scratch.replace(path)
            with db:
                db.execute("UPDATE calls SET state='ready',sha=? WHERE owner=? AND recipe=? AND session=?",
                           (sha(data),owner,recipe,session))
        finally:
            scratch.unlink(missing_ok=True)
        return path, False
