"""Signed WuWa program and content updates for portable Windows installations."""
import base64
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from http.client import IncompleteRead
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from uuid import uuid4
import wave
from zipfile import ZipFile

PUBLIC_KEY = '97753d092ff8c22f13f7f853e4880ce5452324b3b5629246b578c1e5239f68e0'
RELEASES = 'https://github.com/emt-faze/ASGSV/releases/'
FEED = RELEASES + 'latest/download/wuwa-updates.json'
DEFAULT_SOURCE = 'cnb'
SOURCES = {'cnb':'CNB 国内源', 'github':'GitHub 官方源'}
# Portable builds bind this value to the publisher's public CNB repository.
CNB_REPOSITORY = 'asgsv-studio/ASGSV'
SOURCE_FILE = '.asgsv-update/source.txt'
HASH = re.compile(r'[a-f0-9]{64}')
TABLES = {'flow','states','text','speakers','speaker_text','quests','quest_nodes','audio'}
MANIFEST = 'FILE_MANIFEST.json'
VERSION = 'nte-dialogue-ocr/GENERATOR_VERSION'


def canonical(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def signed_body(raw):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        envelope = json.loads(raw)
        body = envelope['body']
        signature = base64.b64decode(envelope['signature'], validate=True)
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(PUBLIC_KEY)).verify(signature, canonical(body))
    except (ValueError, TypeError, KeyError, InvalidSignature) as exc:
        raise ValueError('更新签名无效，请重新获取官方更新包') from exc
    if body.get('schema') != 1:
        raise ValueError('更新格式需要更高版本的更新器')
    return body


def semver(s):
    if not isinstance(s,str) or not re.fullmatch(r'\d+\.\d+\.\d+', s):
        raise ValueError('程序版本号无效')
    return tuple(map(int,s.split('.')))


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
        temp=Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            f.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def safe_name(name):
    if not isinstance(name,str) or '\\' in name or len(name)>240:
        return False
    return all(p not in ('','.','..') and not p.endswith(('.',' ')) and
               not PureWindowsPath(p).is_reserved() and not re.search(r'[<>:"|?*\x00-\x1f]',p)
               for p in name.split('/'))


def program_name(name):
    if not safe_name(name):
        return False
    if name in ('README.md',VERSION):
        return True
    return bool(re.fullmatch(r'nte-dialogue-ocr/(?:[a-zA-Z_]\w*\.py|ui/\w+\.py|docs/[^/]+\.md)|'
                             r'scripts/[\w.-]+\.(?:py|ps1|bat)|wuwa-dubber/src/wuwa_dubber/(?:\w+/)*\w+\.py',name))


def target(root,name):
    p = root/name
    if p.resolve()!=root.resolve()/name:
        raise ValueError('更新路径经过重定向 · '+name)
    return p


@contextmanager
def locked(path,timeout=0):
    from PySide6.QtCore import QLockFile
    path.parent.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(path))
    lock.setStaleLockTime(0)
    if not lock.tryLock(timeout):
        raise RuntimeError('正在等待其他工作台或后台任务退出 · '+path.name)
    try:
        yield
    finally:
        lock.unlock()


def selected_source(root):
    path = Path(root)/SOURCE_FILE
    try:
        name = path.read_text(encoding='utf8').strip()
    except FileNotFoundError:
        return DEFAULT_SOURCE
    return name if name in SOURCES else DEFAULT_SOURCE


def save_source(root, name):
    if name not in SOURCES:
        raise ValueError('未知更新源')
    atomic(Path(root)/SOURCE_FILE, name.encode('ascii'))


def cnb_releases(repository):
    parts = repository.split('/')
    if len(parts) < 2 or any(not re.fullmatch(r'[\w.-]+', p) or p in ('.', '..') for p in parts):
        raise ValueError('此构建缺少有效的 CNB 发布仓库。请导入网盘签名更新包或切换更新源。')
    from urllib.parse import quote
    return 'https://cnb.cool/' + quote(repository) + '/-/releases/'


class CnbRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        dst = urlsplit(newurl)
        if dst.scheme != 'https' or dst.netloc not in ('cnb.cool', 'asset.cnb.cool'):
            raise ValueError('CNB 附件跳转到了未识别的站点。请使用网盘签名更新包。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def release_page(source, tag):
    if source == 'cnb':
        return cnb_releases(CNB_REPOSITORY) + 'tag/' + tag
    if source == 'github':
        return RELEASES + 'tag/' + tag
    raise ValueError('未知更新源')


def open_asset(source, filename, *, tag=None, headers=None, timeout):
    headers={'User-Agent':'ASGSV-WuWa-Updater', **(headers or {})}
    if source == 'cnb':
        # The public latest/download route works anonymously. The release API can require login.
        path = ('download/' + tag + '/') if tag else 'latest/download/'
        url = cnb_releases(CNB_REPOSITORY) + path + filename
        return build_opener(CnbRedirect()).open(Request(url, headers=headers), timeout=timeout)
    if source == 'github':
        url = RELEASES+'download/'+tag+'/'+filename if tag else FEED
        return urlopen(Request(url,headers=headers),timeout=timeout)
    raise ValueError('未知更新源')


def read_feed(source=DEFAULT_SOURCE):
    for attempt in range(2):
        try:
            with open_asset(source,'wuwa-updates.json',headers={'Cache-Control':'no-cache'},timeout=12) as stream:
                body=signed_body(stream.read(1024*1024))
            break
        except (OSError,IncompleteRead) as exc:
            if attempt or isinstance(exc,(FileNotFoundError,PermissionError)) or (isinstance(exc,HTTPError) and exc.code not in (408,502,503,504)):
                raise
            time.sleep(.35)
    if not re.fullmatch(r'wuwa-v\d+\.\d+\.\d+', body['tag']):
        raise ValueError('更新渠道不匹配')
    for e in body['packages']:
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+\.zip',e['asset']) or not HASH.fullmatch(e['sha256'])
                or not 0<e['size']<=2**31 or e['kind'] not in ('app','index','quest')):
            raise ValueError('更新清单中的附件无效')
    return body


def program_offer(feed,version):
    candidates=[e for e in feed['packages'] if e['kind']=='app' and e['base']==version
                and semver(e['version'])>semver(version)]
    return max(candidates,key=lambda e:semver(e['version']),default=None)


def download(install,feed,e,progress,*,source=None,stop=None):
    source=selected_source(install) if source is None else source
    directory=install/'.asgsv-update/downloads'
    directory.mkdir(parents=True,exist_ok=True)
    dest=directory/(e['sha256']+'.zip')
    if dest.is_file() and digest(dest)==e['sha256']:
        return dest
    with locked(directory/(e['sha256']+'.lock')):
        temp=dest.with_suffix('.part')
        error=''
        for attempt in range(3):
            try:
                if stop is not None and stop.is_set():
                    raise InterruptedError('下载已取消')
                offset=temp.stat().st_size if temp.exists() else 0
                if offset>=e['size']:
                    temp.unlink()
                    offset=0
                headers={'Range':f'bytes={offset}-'} if offset else {}
                with open_asset(source,e['asset'],tag=feed['tag'],headers=headers,timeout=45) as src:
                    if src.status!=206:
                        offset=0
                    elif not src.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
                        raise ValueError('断点位置不匹配')
                    with temp.open('ab' if offset else 'wb') as dst:
                        marked=-1
                        while chunk:=src.read(256*1024):
                            if stop is not None and stop.is_set():
                                raise InterruptedError('下载已取消')
                            offset+=len(chunk)
                            if offset>e['size']:
                                raise ValueError('附件超出签名清单大小')
                            dst.write(chunk)
                            percent=100*offset//e['size']
                            if percent!=marked:
                                progress(f'下载 {e["name"]} · {percent}%')
                                marked=percent
                if offset!=e['size']:
                    raise OSError('下载尚未完成')
                if digest(temp)!=e['sha256']:
                    temp.unlink()
                    raise ValueError('下载文件校验失败')
                os.replace(temp,dest)
                return dest
            except (OSError,ValueError) as exc:
                if isinstance(exc,(FileNotFoundError,PermissionError,InterruptedError)):
                    raise
                error=str(exc)
                progress(f'第 {attempt+1} 次下载未完成 · {exc}')
    raise OSError('无法取得更新附件 · '+error)


def inspect_zip(z):
    if len(z.infolist())>10000 or z.getinfo('update.json').file_size>4*1024**2:
        raise ValueError('更新包清单过大')
    m=signed_body(z.read('update.json'))
    files=m['files']
    names=z.namelist()
    if (not files or len({n.casefold() for n in names})!=len(names)
            or set(names)!={'update.json',*('payload/'+n for n in files)}):
        raise ValueError('更新包文件清单不一致')
    kind=m['kind']
    total=0
    for n,(size,sha) in files.items():
        if not safe_name(n) or not HASH.fullmatch(sha) or size<0 or size!=z.getinfo('payload/'+n).file_size:
            raise ValueError('更新包条目无效 · '+n)
        if kind=='app':
            valid=program_name(n)
        elif kind=='index':
            valid=n in {t+'.json' for t in TABLES|{'source'}}
        elif kind=='quest':
            valid=n=='pack.json' or re.fullmatch(r'audio/[a-f0-9]{64}\.wav',n)
        else:
            valid=False
        if not valid:
            raise ValueError('更新包包含非授权文件 · '+n)
        total+=size
    if total>2**31:
        raise ValueError('更新内容过大')
    if kind=='app':
        if VERSION not in files or semver(m['version'])<=semver(m['base']):
            raise ValueError('程序升级版本范围无效')
    else:
        semver(m['minimum'])
        if type(m['revision'])!=int or m['revision']<1:
            raise ValueError('资源修订号无效')
        if kind=='quest' and not re.fullmatch(r'quest:\d+',m['scope']):
            raise ValueError('任务编号无效')
        if kind=='index' and set(files)!={t+'.json' for t in TABLES|{'source'}}:
            raise ValueError('台词索引表不完整')
    return m


def extract(z,m,folder):
    for name,(_,sha) in m['files'].items():
        dst=target(folder,name)
        dst.parent.mkdir(parents=True,exist_ok=True)
        with z.open('payload/'+name) as src,dst.open('xb') as f:
            shutil.copyfileobj(src,f,256*1024)
        if digest(dst)!=sha:
            raise ValueError('更新包文件受损 · '+name)


def quest_state(root):
    p=root/'updates/quests.json'
    return json.loads(p.read_bytes()) if p.exists() else {}


def revision(root,m):
    if m['kind']=='quest':
        return quest_state(root).get(m['scope'],{}).get('revision',0)
    db=root/'dialogue.sqlite'
    if not db.exists():
        return 0
    con=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
    try:
        row=con.execute("SELECT value FROM meta WHERE key='index_revision'").fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def validate_quest(folder,m):
    data=json.loads((folder/'pack.json').read_bytes())
    seen=set()
    audio=set()
    if data.get('format')!=1 or not data.get('mounts') or not data.get('lines'):
        raise ValueError('任务包缺少适用游戏版本或台词')
    for r in data['lines']:
        for k in ('id','state','number','speaker','speaker_id','quest_name','text','label','voice','fingerprint'):
            if not isinstance(r[k],str) or not r[k]:
                raise ValueError('任务配音信息不完整 · '+k)
        k=r['state'],r['number']
        n='audio/'+r['sha256']+'.wav'
        if r['quest']!=m['scope'] or k in seen or n not in m['files'] or m['files'][n][1]!=r['sha256']:
            raise ValueError('任务配音与台词不一致')
        seen.add(k)
        if n not in audio:
            with wave.open(str(folder/n),'rb') as w:
                if w.getnframes()<1 or not w.readframes(1):
                    raise ValueError('任务录音为空')
            audio.add(n)
    if set(m['files'])!={'pack.json',*audio}:
        raise ValueError('配音包存在未引用的文件')


def install_content(install,archive,progress=print,*,content_root=None,stop=None):
    from wuwa_catalog import Catalog
    root=install/'game-data/wuwa' if content_root is None else content_root
    store=root/'updates'
    with locked(store/'content.lock'),ZipFile(archive) as z:
        m=inspect_zip(z)
        if m['kind']=='app' or semver((install/VERSION).read_text().strip())<semver(m['minimum']):
            raise ValueError('请先升级工作台程序')
        current=revision(root,m)
        if m['revision']<current:
            raise ValueError('本机资源版本更新，已取消导入')
        if m['revision']==current:
            return '已是当前资源版本 · '+m['name']
        name=hashlib.sha256(canonical(m)).hexdigest()
        folder=store/name
        progress('校验并展开 · '+m['name'])
        with tempfile.TemporaryDirectory(dir=store) as tmp:
            stage=Path(tmp)/'payload'
            extract(z,m,stage)
            if m['kind']=='quest':
                validate_quest(stage,m)
            if folder.exists():
                if any(not (folder/n).is_file() or digest(folder/n)!=v[1] for n,v in m['files'].items()):
                    raise ValueError('本机资源快照受损 · '+name)
            else:
                os.replace(stage,folder)
        if stop is not None and stop.is_set():
            raise InterruptedError('资源更新已取消')
        if m['kind']=='quest':
            state=quest_state(root)
            state[m['scope']]={'folder':name,'revision':m['revision']}
            atomic(store/'quests.json',canonical(state))
        else:
            c=Catalog(root)
            try:
                c.import_public(folder,stop=stop,progress=progress,revision=m['revision'])
            finally:
                c.close()
        return f'已更新 {m["name"]} · 修订 {m["revision"]}'


def update_index(install, content_root, stop, progress):
    source = selected_source(install)
    progress('正在从' + SOURCES[source] + '检查中文台词索引…')
    feed = read_feed(source)
    entries = [e for e in feed['packages'] if e['kind'] == 'index']
    if not entries:
        raise ValueError('当前发布尚未提供台词索引，可从更新中心导入网盘索引包。')
    entry = max(entries, key=lambda e: e['revision'])
    if semver(portable_version(install)) < semver(entry['minimum']):
        raise ValueError('请先升级工作台至 v' + entry['minimum'] + ' 或更高版本')
    if revision(content_root, entry) >= entry['revision']:
        return '中文台词索引已是最新版本'
    path = download(install, feed, entry, progress, source=source, stop=stop)
    return install_content(install, path, progress, content_root=content_root, stop=stop)


def portable_version(root):
    return (root/VERSION).read_text().strip()


def update_resources(install, source, progress):
    progress('正在检查资源版本…')
    feed = read_feed(source)
    version = semver(portable_version(install))
    latest = {}
    for entry in feed['packages']:
        if entry['kind'] == 'app':
            continue
        scope = (entry['kind'], entry.get('scope', ''))
        if scope not in latest or entry['revision'] > latest[scope]['revision']:
            latest[scope] = entry
    total = len(latest)
    if not total:
        return '此版本尚未发布资源包'
    installed = current = 0
    pending = []
    for position, key in enumerate(sorted(latest), start=1):
        entry = latest[key]
        if revision(install / 'game-data/wuwa', entry) >= entry['revision']:
            current += 1
            continue
        if version < semver(entry['minimum']):
            pending.append(entry['name'])
            continue
        progress(f'正在更新 {position}/{total} · {entry["name"]}')
        archive = download(install, feed, entry, progress, source=source)
        install_content(install, archive, progress)
        installed += 1
    result = f'已更新 {installed} 项资源，已有 {current} 项是最新版本'
    if pending:
        result += '。升级程序后可更新 ' + '、'.join(pending)
    return result


def stage_program(root,archive,progress=print):
    parent=root/'.asgsv-update'
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as tmp:
        copy=Path(tmp)/'update.zip'
        shutil.copyfile(archive,copy)
        with ZipFile(copy) as z:
            m=inspect_zip(z)
            if m['kind']!='app' or portable_version(root)!=m['base']:
                raise ValueError('此程序包需要 v'+str(m.get('base','?'))+'，当前 v'+portable_version(root))
            progress('验证程序文件 · v'+m['version'])
            extract(z,m,Path(tmp)/'verify')
        path=parent/(digest(copy)+'.zip')
        os.replace(copy,path)
        return path


def restore(root,tx):
    journal=json.loads((tx/'journal.json').read_bytes())
    for n,present in reversed(journal):
        if n!=MANIFEST and not program_name(n):
            raise ValueError('回滚清单异常')
        dst=target(root,n)
        if present:
            atomic(dst,target(tx/'backup',n).read_bytes())
        else:
            dst.unlink(missing_ok=True)
    (tx/'journal.json').unlink()


def notify_healthy(root):
    token=os.environ.pop('ASGSV_UPDATE_HEALTH','')
    if re.fullmatch('[a-f0-9]{32}',token):
        atomic(root/'.asgsv-update'/('ready-'+token),portable_version(root).encode())


def start_verified(root,version):
    token=uuid4().hex
    ready=root/'.asgsv-update'/('ready-'+token)
    cmd=[str(root/'runtime/python/pythonw.exe'),'-I','-X','utf8',str(root/'scripts/launch_desktop.py')]
    child=subprocess.Popen(cmd,cwd=root,env=dict(os.environ,ASGSV_UPDATE_HEALTH=token))
    try:
        until=time.monotonic()+90
        while time.monotonic()<until and child.poll() is None:
            if ready.exists() and ready.read_text()==version:
                return
            time.sleep(.2)
        raise RuntimeError('新版工作台启动自检未通过')
    except BaseException:
        if child.poll() is None:
            child.terminate()
            child.wait(15)
        raise
    finally:
        ready.unlink(missing_ok=True)


def apply_program(root,archive,progress=print):
    root=root.resolve()
    state=root/'.asgsv-update'
    tx=state/'transaction'
    entry=root/'game-data/entry.lock'
    daily=root/'game-data/wuwa/daily-plan.lock'
    # Keep the daily scheduler excluded until the new desktop confirms startup.
    with locked(state/'apply.lock'),locked(daily,120000):
        with ExitStack() as locks:
            locks.enter_context(locked(entry,120000))
            if (tx/'journal.json').exists():
                restore(root,tx)
            if tx.exists():
                shutil.rmtree(tx)
            tx.mkdir()
            with ZipFile(archive) as z:
                m=inspect_zip(z)
                if m['kind']!='app' or portable_version(root)!=m['base']:
                    raise ValueError('程序更新基础版本不匹配')
                extract(z,m,tx/'new')
            if (tx/'new'/VERSION).read_text().strip()!=m['version']:
                raise ValueError('程序包版本文件不一致')
            manifest=json.loads((root/MANIFEST).read_bytes())
            if manifest['version']!=m['base']:
                raise ValueError('便携版文件清单与程序版本不匹配')
            changes=[]
            for n,(_,new_sha) in m['files'].items():
                dst=target(root,n)
                prior=manifest['files'].get(n)
                actual=digest(dst) if dst.is_file() else None
                if actual!=new_sha:
                    if actual!=prior:
                        raise ValueError('保留本机修改，升级已中止 · '+n)
                    changes.append(n)
                manifest['files'][n]=new_sha
            manifest['version']=m['version']
            atomic(tx/'new'/MANIFEST,canonical(manifest))
            changes.append(MANIFEST)
            journal=[]
            for n in changes:
                dst=target(root,n)
                if dst.exists():
                    backup=tx/'backup'/n
                    backup.parent.mkdir(exist_ok=True,parents=True)
                    shutil.copyfile(dst,backup)
                journal.append((n,dst.exists()))
            atomic(tx/'journal.json',canonical(journal))
            released=False
            try:
                for n in changes:
                    atomic(target(root,n),(tx/'new'/n).read_bytes())
                locks.close()
                released=True
                progress('启动新版工作台并核对版本')
                start_verified(root,m['version'])
            except BaseException:
                with locked(entry,15000) if released else ExitStack():
                    restore(root,tx)
                raise
            (tx/'journal.json').unlink()
            return '程序升级成功 · v'+m['version']


if __name__=='__main__' and sys.argv[1:2]==['--check-feed']:
    try:
        source=sys.argv[2] if len(sys.argv)>2 else selected_source(Path(__file__).resolve().parents[1])
        payload=canonical(read_feed(source))
    except Exception as exc:
        print(f'{type(exc).__name__} · {exc}',file=sys.stderr)
        raise SystemExit(1)
    sys.stdout.buffer.write(payload)
