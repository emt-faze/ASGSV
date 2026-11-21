"""Optional dialogue-based casting via a user-configured chat completions endpoint."""
import hashlib
from time import perf_counter
import json
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from wuwa_breeze import NoRedirect
from wuwa_cast_prompt import SYSTEM_PROMPT, casting_input, dialogue_excerpt, evidence_for


DEFAULT_BASE = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-flash'


class CastingServiceError(ValueError):
    """The whole casting pass can be retried once the service is available."""


def test_connection(base, model, key):
    base=endpoint(base)
    secret=key.strip() or credential(base)
    if not model.strip():
        raise ValueError('请填写要验证的模型名称。')
    headers={'Accept':'application/json','Content-Type':'application/json'}
    if secret:
        headers['Authorization']='Bearer '+secret
    payload=dict(model=model.strip(),messages=[dict(role='user',content='OK')],max_tokens=1,stream=False)
    req=Request(base+'/chat/completions',data=json.dumps(payload).encode(),headers=headers)
    started=perf_counter()
    try:
        with build_opener(NoRedirect()).open(req,timeout=20) as response:
            raw=response.read(65537)
        if len(raw)>65536 or not json.loads(raw)['choices'][0]['message']:
            raise ValueError('服务未返回有效的聊天响应')
    except HTTPError as exc:
        reasons={401:'密钥无效或已撤销',403:'凭据无权访问',404:'接口路径或模型不存在',402:'账户余额不足',429:'请求受到限流'}
        raise ValueError(f'HTTP {exc.code} · {reasons.get(exc.code,exc.reason)}') from None
    except (URLError,TimeoutError,OSError) as exc:
        raise ValueError('网络连接失败 · '+str(getattr(exc,'reason',exc))) from None
    except (ValueError,TypeError,KeyError,IndexError) as exc:
        raise ValueError('聊天探测响应格式无效 · '+str(exc)) from None
    latency=round((perf_counter()-started)*1000)
    if not secret:
        raise ValueError(f'HTTP 200 · 服务允许匿名调用。请填写 API Key 后验证鉴权（{latency} ms）。')
    return f'连接成功 · HTTP 200 · {model} · {latency} ms'


def endpoint(value):
    url = value.strip().rstrip('/')
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or any(c.isspace() for c in url)):
        raise ValueError('请输入模型服务的 HTTPS API 基址，例如 https://api.deepseek.com/v1。')
    return url


def credential(base, new=None):
    import win32cred
    import pywintypes
    target = 'ASGSV/Casting/' + hashlib.sha256(endpoint(base).encode()).hexdigest()
    if new is not None:
        win32cred.CredWrite(dict(Type=win32cred.CRED_TYPE_GENERIC, TargetName=target,
            CredentialBlob=new.strip(), Persist=win32cred.CRED_PERSIST_LOCAL_MACHINE))
        return
    try:
        blob = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)['CredentialBlob']
        return blob.decode('utf-16-le' if b'\0' in blob else 'utf8')
    except pywintypes.error as exc:
        if exc.winerror == 1168:
            return ''
        raise ValueError('无法读取此模型服务的 Windows 凭据') from None


def infer(base, model, speaker, dialogue, voices, requirements):
    secret = credential(base)
    if not secret:
        raise CastingServiceError('请在每日计划的「角色分析」中保存该模型服务的 API Key。')
    brief = casting_input(speaker,dialogue,voices,requirements)
    payload = dict(model=model, messages=[dict(role='system',content=SYSTEM_PROMPT),
        dict(role='user',content=json.dumps(brief,ensure_ascii=False,separators=(',',':')))])
    req = Request(endpoint(base)+'/chat/completions', data=json.dumps(payload).encode(),
                  headers={'Authorization':'Bearer '+secret, 'Content-Type':'application/json'})
    try:
        with build_opener(NoRedirect()).open(req, timeout=35) as resp:
            data = resp.read(200001)
        if len(data)>200000:
            raise ValueError
        reply = json.loads(data)['choices'][0]['message']['content'].strip()
        fence = chr(96)*3
        if reply.startswith(fence) and reply.endswith(fence):
            reply = reply[len(fence):-len(fence)].strip().removeprefix('json').strip()
        return json.loads(reply)
    except HTTPError as exc:
        raise CastingServiceError(f'角色分析服务 HTTP {exc.code}，请检查密钥、模型名称或账户额度。') from None
    except (URLError, TimeoutError):
        raise CastingServiceError('角色分析服务连接超时或不可达，请检查 API 基址和网络。') from None
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        raise CastingServiceError('角色分析服务返回格式无效。') from None


def voice_for(c, scope, row, members, *, day=None):
    from wuwa_audition import NeedsAudition, hold, pending
    from wuwa_cast_context import scene_turns, name_cues, summarize
    from wuwa_cast_review import original_problem, reviewed_roles
    sid = row['speaker_id']
    selection = c.progress.selection
    binding = c.binding(sid)
    expected = binding['voice'] if binding else ''
    waiting = pending(c,sid)
    if waiting:
        raise NeedsAudition(waiting['reason']+' · 请在任务库试听确认')
    voice = ''
    reviewed = (reviewed_roles(c,scope) or {}).get(sid)
    if reviewed == 'original':
        raise ValueError('此角色沿用原声')
    if binding:
        voice = binding['voice'].removeprefix('breeze:') if binding['voice'].startswith('breeze:') else ''
        if not voice or original_problem(c,sid,voice):
            raise ValueError('角色保留原声或本地声线，等待可用录音')
    if not voice and (original_problem(c,sid,'') or c.db.execute('SELECT 1 FROM original_voice_roles WHERE speaker_id=?',(sid,)).fetchone()):
        raise ValueError('角色沿用原声，等待原声音色')
    if reviewed == 'pending':
        hold(c,selection,scope,row,'此角色的声线有待确认',voice,expected)
    if voice and not day:
        return voice
    name = row['speaker']
    turns = scene_turns(members,sid,name)
    cues = name_cues(name)+[cue for turn in turns for cue in turn['cues']]
    gender,age,_ = summarize(cues)
    requirements = {}
    automatic_review = c.meta('cast_source:'+scope)=='automatic'
    if reviewed in ('male','female') and not automatic_review:
        gender = reviewed
        requirements['gender'] = gender
    if voice:
        presets = [dict(p) for p in c.db.execute('SELECT id,name,gender,age FROM breeze_presets WHERE id=?',(voice,))
                   if automatic_review or reviewed in (None,'any',p['gender'])]
    else:
        presets = [dict(p) for p in c.db.execute('SELECT id,name,gender,age FROM breeze_presets ORDER BY id')
                   if (not gender or p['gender']==gender) and (not age or p['age']==age)]
    if not presets:
        if not binding and c.db.execute('SELECT 1 FROM breeze_presets LIMIT 1').fetchone():
            hold(c,selection,scope,row,'现有音色与角色条件不匹配',expected=expected)
        raise ValueError('没有符合已确认条件的云端预设，请同步音色或手动选声')
    if c.meta('daily_cast_enabled') != '1':
        if not gender:
            hold(c,selection,scope,row,'角色性别证据不足',expected=expected)
        pool = [p for p in presets if p['name']==row['speaker']] or [
            p for p in presets if age or p['age'] in ('young','middle_aged')] or presets
        index=int.from_bytes(hashlib.sha256(sid.encode()).digest()[:8],'big') % len(pool)
        with c.db:
            c.db.execute('BEGIN IMMEDIATE')
            c.progress.require(selection)
            if pending(c,sid):
                raise NeedsAudition('该角色正在等待任务库试音确认')
            c.db.execute('INSERT OR IGNORE INTO breeze_cast VALUES(?,?)',(sid,pool[index]['id']))
        return c.binding(sid)['voice'].removeprefix('breeze:')
    dialogue = dialogue_excerpt(turns,name,row['id'])
    conflicts = [field for field,index in (('gender',1),('age',2)) if len({cue[index] for cue in cues if cue[index]})>1]
    if voice:
        requirements.update(voice_id=voice,gender=presets[0]['gender'],age=presets[0]['age'])
    base, model = c.meta('daily_cast_endpoint') or DEFAULT_BASE, c.meta('daily_cast_model') or DEFAULT_MODEL
    signature = hashlib.sha256(json.dumps([SYSTEM_PROMPT,day,base,model,scope,sid,name,dialogue,
                                           requirements,conflicts,presets],sort_keys=True).encode()).hexdigest()
    prior = c.db.execute('SELECT signature,profile FROM daily_profiles WHERE speaker=?',(sid,)).fetchone()
    if prior and prior[0]==signature:
        result = json.loads(prior[1])
    else:
        result = infer(base,model,row['speaker'],dialogue,presets,requirements)
        if not isinstance(result,dict):
            raise CastingServiceError('角色分析服务没有返回有效档案')
        with c.db:
            c.db.execute('INSERT OR REPLACE INTO daily_profiles VALUES(?,?,?)',(sid,signature,json.dumps(result,ensure_ascii=False)))
    choice = next((p for p in presets if p['id']==result.get('voice_id')),None)
    citations,issue = evidence_for(result,dialogue,requirements,conflicts)
    if not voice and choice and automatic_review and reviewed in ('male','female') and reviewed!=choice['gender']:
        issue = '本次证据与此前自动判断的角色性别冲突'
    conf = result.get('confidence')
    if (result.get('decision')!='cast' or not choice or type(conf) not in (int,float) or not .75<=conf<=1 or issue
        or not isinstance(result.get('personality'),str) or not result['personality'].strip()
        or not choice['gender'] or not choice['age']
        or (choice['gender'],choice['age'])!=(result.get('gender'),result.get('age'))):
        # A previously confirmed voice stays fixed even if today's acting analysis is inconclusive.
        if voice and c.meta('confirmed_cloud:'+sid) == 'breeze:'+voice:
            choice = presets[0]
            result = {'personality':'沿用已确认的角色声线，自然表达'}
            citations = ['用户已确认音色']
        else:
            reason = '角色分析证据不足 · '+issue if issue else '角色分析证据不足或音色不匹配'
            hold(c,selection,scope,row,reason,choice['id'] if choice else '',expected)
    with c.db:
        c.db.execute('BEGIN IMMEDIATE')
        c.progress.require(selection)
        if pending(c,sid):
            raise NeedsAudition('该角色正在等待任务库试音确认')
        c.db.execute('INSERT OR IGNORE INTO breeze_cast VALUES(?,?)',(sid,choice['id']))
        binding = c.binding(sid)
        if not binding or binding['voice'] != 'breeze:'+choice['id']:
            raise ValueError('分析期间角色声线已变化，请重新执行')
        if day:
            c.db.execute('INSERT OR REPLACE INTO daily_voice_choices VALUES(?,?,?,?,?,?,?)',
                (day,sid,base,model,choice['id'],result['personality'].strip()[:80],
                 '\n'.join(dict.fromkeys(quote.strip() for quote in citations))))
    return choice['id']

