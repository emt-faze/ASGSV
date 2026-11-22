"""CNB transport. Readers use public repository paths; only submissions need an author session."""
import json
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import wuwa_community as pack
import wuwa_update as update

REPO = update.CNB_REPOSITORY
API = 'https://api.cnb.cool'
ISSUES = '/' + REPO + '/-/issues'
PUBLIC = f'https://cnb.cool/{REPO}/-/git/raw/{pack.BRANCH}/'
PART = 4 * 1024**2
MAX_CATALOG = 4 * 1024**2
TITLE = 'ASGSV 配音投稿 '
AUTHOR = re.compile(r'[\w.-]{1,80}', re.ASCII)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        storage_host(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header('Authorization')
        return redirected


def limited(stream, limit):
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('远端返回的数据超过大小限制')
    return data


def request_json(path, token, data=None, *, method=None):
    if hasattr(token, 'request'):
        return token.request(path, data)
    headers = {'Accept':'application/json', 'User-Agent':'ASGSV-Community', 'Authorization':'Bearer ' + token}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = Request(API + path, headers=headers, data=update.canonical(data) if data is not None else None, method=method)
    try:
        with build_opener(NoRedirect()).open(req, timeout=30) as response:
            raw = limited(response, MAX_CATALOG)
    except HTTPError as exc:
        detail = {401:'请重新登录 CNB', 403:'此 CNB 账号或令牌没有该操作所需的 Issue 权限',
                  413:'CNB 拒绝了过大的附件', 429:'CNB 请求过于频繁，请稍后重试'}.get(exc.code, exc.reason)
        raise RuntimeError(f'CNB HTTP {exc.code} · {detail}') from None
    return json.loads(raw) if raw else None


def public_file(relative, headers=None, timeout=30):
    if not (relative == 'catalog.json' or re.fullmatch(r'(?:objects/[a-f0-9]{64}\.zip|samples/[a-f0-9]{64}\.wav)', relative)):
        raise ValueError('无效的社区资源名称')
    return build_opener(PublicRedirect()).open(Request(PUBLIC + relative,
            headers={'User-Agent':'ASGSV-Community', **(headers or {})}), timeout=timeout)


def media(entry, *, sample=False, headers=None, timeout=40):
    """Serve a materialized raw file or resolve a Git LFS pointer anonymously."""
    sha = entry['sample' if sample else 'sha256']
    size = entry['sample_size' if sample else 'size']
    relative = 'samples/'+sha+'.wav' if sample else 'objects/'+sha+'.zip'
    try:
        stream = public_file(relative, headers, timeout)
    except HTTPError as exc:
        if exc.code not in (403, 404, 416):
            raise
        exc.close()
    else:
        if stream.status == 206:
            if stream.headers.get('Content-Range','').endswith('/'+str(size)):
                return stream
        elif not stream.peek(45).startswith(b'version https://git-lfs.github.com/spec/v1'):
            return stream
        else:
            with stream:
                pointer = limited(stream, 2048).decode('ascii').splitlines()
            if 'oid sha256:'+sha not in pointer or 'size '+str(size) not in pointer:
                raise ValueError('CNB LFS 指针与社区目录不一致')
        stream.close()
    endpoint = 'https://cnb.cool/'+REPO+'.git/info/lfs/objects/batch'
    mime = 'application/vnd.git-lfs+json'
    req = Request(endpoint, data=update.canonical({'operation':'download','transfers':['basic'],
                'ref':{'name':'refs/heads/'+pack.BRANCH}, 'objects':[{'oid':sha,'size':size}]}),
                headers={'Content-Type':mime,'Accept':mime})
    with build_opener(NoRedirect()).open(req, timeout=timeout) as response:
        objects = json.loads(limited(response, 65536))['objects']
    if len(objects)!=1 or objects[0].get('oid')!=sha or objects[0].get('size')!=size or 'error' in objects[0]:
        raise ValueError('CNB 未提供此版本的公开 LFS 录音')
    action = objects[0]['actions']['download']
    storage_host(action['href'])
    download_headers = {k:v for k,v in action.get('header',{}).items()
                        if k.lower() == 'authorization' or k.lower().startswith('x-cos-')}
    return build_opener(NoRedirect()).open(Request(action['href'], headers={**download_headers, **(headers or {})}),timeout=timeout)


def valid_mounts(mounts):
    return (isinstance(mounts, list) and 0 < len(mounts) <= 64 and
            all(isinstance(sig,list) and 0 < len(sig) <= 128 and
                all(isinstance(item,list) and len(item)==2 and isinstance(item[0],str)
                    and update.safe_name(item[0]) and isinstance(item[1],str) and update.HASH.fullmatch(item[1])
                    for item in sig) for sig in mounts))


def parse_catalog(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('format') != 'asgsv-community-catalog-v1' or not isinstance(data.get('versions'), list):
        raise ValueError('社区目录格式无效')
    versions, issues, archived = [], set(), set()
    for e in data['versions']:
        if (not isinstance(e, dict) or set(e) != {'scope','name','lines','author','issue','size','sha256','sample',
                'sample_size','public_key','content','mounts'} or not valid_mounts(e['mounts']) or not isinstance(e['scope'], str)
                or not re.fullmatch(r'quest:[0-9]{1,20}', e['scope']) or type(e['issue']) is not int
                or not 0 < e['issue'] < 2**63 or e['issue'] in issues or not isinstance(e['author'], str)
                or not AUTHOR.fullmatch(e['author']) or not isinstance(e['name'], str) or not 0 < len(e['name']) <= 200
                or any(not isinstance(e[k], str) or not update.HASH.fullmatch(e[k]) for k in ('sha256','content','sample','public_key'))
                or e['sha256'] in archived or type(e['lines']) is not int or not 0 < e['lines'] <= pack.MAX_LINES
                or type(e['size']) is not int or not 0 < e['size'] <= pack.MAX_PACK
                or type(e['sample_size']) is not int or not 0 < e['sample_size'] <= pack.MAX_AUDIO):
            raise ValueError('社区目录的版本记录无效')
        issues.add(e['issue'])
        archived.add(e['sha256'])
        versions.append(e)
    return versions


def catalog():
    with public_file('catalog.json', {'Cache-Control':'no-cache'}) as response:
        raw = limited(response, MAX_CATALOG)
    return parse_catalog(raw)


def check_listed(offer):
    if offer not in catalog():
        raise ValueError('该版本已下架或目录已更新，请刷新后重新选择')


def download_offer(install, root, entry, stop, progress):
    check_listed(entry)
    artifact = dict(entry, asset=entry['sha256'] + '.zip')
    def open_asset(_, filename, *, headers, timeout, tag=None):
        return media(entry, headers=headers, timeout=timeout)
    path = update.download(install, {}, artifact, progress, source='cnb', stop=stop, open_file=open_asset)
    check_listed(entry)
    if stop.is_set():
        raise InterruptedError('社区配音下载已取消')
    progress('核对投稿签名、全任务台词和 PCM 录音')
    return pack.install_bundle(root, path, entry)


def preview(root, entry):
    check_listed(entry)
    sha = entry['sample']
    dst = root / 'updates/community-samples' / (sha + '.wav')
    if not dst.exists() or update.digest(dst) != sha:
        with media(entry, sample=True) as response:
            raw = limited(response, entry['sample_size'])
        if len(raw) != entry['sample_size'] or pack.hexdigest(raw) != sha or pack.clean_wav(raw) != raw:
            raise ValueError('试听片段校验失败')
        update.atomic(dst, raw)
    return dst


def storage_host(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443)
            or not (host == 'cnb.cool' or host.endswith('.cnb.cool') or host.endswith('.myqcloud.com'))):
        raise ValueError('CNB 返回了未识别的附件存储地址')


def file_path(path):
    if (not isinstance(path, str) or not 1 <= len(path) <= 512 or '\\' in path
            or path.startswith('/') or not re.fullmatch(r'[A-Za-z0-9_./-]+', path)
            or any(p in ('.','..','') for p in path.split('/'))):
        raise ValueError('CNB 附件路径无效')
    return path


def submit(token, archive, stop, progress, *, title_prefix=TITLE):
    size = archive.stat().st_size
    if not 0 < size <= pack.MAX_PACK:
        raise ValueError('待上传文件大小无效')
    sha = update.digest(archive)
    files = []
    links = []
    with archive.open('rb') as src:
        while chunk := src.read(PART):
            if stop.is_set():
                raise InterruptedError('已取消投稿')
            name = f'{sha}-{len(files):03}.bin'
            form = {'name':name,'size':len(chunk),'content_type':'application/octet-stream'}
            group = request_json(ISSUES + '/asset-groups', token, {'file_assets':[form]})
            asset = group['file_upload_urls'][0]
            storage_host(asset['upload_url'])
            path = file_path(asset['path'])
            req = Request(asset['upload_url'], method='PUT', data=chunk, headers={'Content-Type':'application/octet-stream'})
            try:
                with build_opener(NoRedirect()).open(req, timeout=90) as res:
                    if res.status not in (200, 201, 204):
                        raise OSError(f'附件上传未完成 · HTTP {res.status}')
            except HTTPError as exc:
                raise OSError(f'CNB 附件上传失败 · HTTP {exc.code}') from None
            files.append(dict(path=path, size=len(chunk), sha256=pack.hexdigest(chunk)))
            links.append(asset['asset_link'])
            progress(f'上传录音 {min(size, len(files) * PART) * 100 // size}%')
    if stop.is_set():
        raise InterruptedError('已取消投稿')
    payload = {'format':'asgsv-intake-v1','sha256':sha,'size':size,'files':files}
    body = '\n'.join(['\x60\x60\x60json', update.canonical(payload).decode(), '\x60\x60\x60', *links])
    issue = request_json(ISSUES, token, {'title':title_prefix + sha[:16], 'invisible':True, 'body':body})
    number = int(issue['number'])
    if not issue.get('invisible') and not request_json(ISSUES+'/'+str(number),token).get('invisible'):
        raise ValueError('CNB 未确认私密投稿状态，请检查投稿 #'+str(number))
    return number


def report(token, entry, reason):
    reason = reason.strip()
    if not 2 <= len(reason) <= 1000:
        raise ValueError('请用 2 至 1000 字说明问题，可注明台词或角色')
    issue = request_json(ISSUES, token, {'title':f'ASGSV 配音举报 #{entry["issue"]}', 'invisible':True,
        'body':f'任务：{entry["name"]}\n投稿编号：{entry["issue"]}\n音频包：{entry["sha256"]}\n\n{reason}'})
    return int(issue['number'])


def protected_file(root, name, data=None):
    import win32crypt
    path = root / 'community-author' / name
    if data is not None:
        update.atomic(path, win32crypt.CryptProtectData(data, 'ASGSV CNB', None, None, None, 1))
        return data
    if path.is_file():
        return win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 1)[1]


def signing_key(root):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    with update.locked(root/'community-author/key.lock', 5000):
        secret = protected_file(root, 'signing-key')
        if secret is None:
            secret = protected_file(root, 'signing-key', Ed25519PrivateKey.generate().private_bytes_raw())
        return Ed25519PrivateKey.from_private_bytes(secret)



