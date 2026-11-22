"""Trusted CNB worker. Only checked audio and the public catalog go on the distribution branch."""
import argparse
import base64
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'nte-dialogue-ocr'))
import wuwa_community as pack
import wuwa_community_cnb as cnb
import wuwa_update as update

TITLES = Path(__file__).with_name('titles.json')
FORMAT = 'asgsv-community-catalog-v1'


def load_registry(folder):
    path = folder/'catalog.json'
    if not path.exists():
        return {'format':FORMAT, 'versions':[], 'withdrawn':[]}
    registry = json.loads(path.read_bytes())
    cnb.parse_catalog(update.canonical(registry))
    return registry


def save_registry(folder, registry):
    raw = update.canonical(registry)
    cnb.parse_catalog(raw)
    update.atomic(folder/'catalog.json', raw)


def known_mounts(scope, reference):
    """Only extend compatibility with evidence from the signed official archive for this exact line set."""
    result = [reference['mounts']]
    official = ROOT/'quest-packs'
    from wuwa_quest_download import parse_catalog
    offer = parse_catalog((official/'catalog.json').read_bytes()).get(scope)
    if offer:
        archive = official/offer['asset']
        if update.digest(archive) != offer['sha256']:
            raise RuntimeError('仓库中的官方任务包与签名目录不一致')
        with ZipFile(archive) as z:
            manifest = update.inspect_zip(z)
            raw = z.read('payload/pack.json')
            if manifest['scope'] != scope or pack.hexdigest(raw) != manifest['files']['pack.json'][1]:
                raise RuntimeError('官方任务包元数据校验失败')
            meta = json.loads(raw)
        if {r['id']:r['fingerprint'] for r in meta['lines']} == reference['quests'][scope]:
            for signature in [meta['mounts'], *meta.get('additional_mounts',[])]:
                if signature not in result:
                    result.append(signature)
    return result


def publish(folder, path, issue, author, reference=None, titles=None):
    if type(issue) is not int or issue < 1 or not cnb.AUTHOR.fullmatch(author):
        raise ValueError('CNB 投稿编号或作者身份无效')
    reference = reference or pack.fingerprints()
    checked = pack.inspect_bundle(path, reference, titles or json.loads(TITLES.read_bytes()))
    if checked['author'] != author:
        raise ValueError('投稿内的签名署名与 CNB 账号不一致')
    registry = load_registry(folder)
    if any(t['issue'] == issue or t['content'] == checked['content'] for t in registry['withdrawn']):
        raise ValueError('此版本已由维护者下架')
    for e in registry['versions']:
        if e['issue'] == issue or e['content'] == checked['content']:
            if e['author'] != author or e['content'] != checked['content']:
                raise ValueError('相同录音已由其他投稿公开，或投稿内容已更改')
            return e
    sha = update.digest(path)
    target = folder/'objects'/(sha + '.zip')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    with ZipFile(path) as z:
        sample = z.read('audio/' + checked['sample'] + '.wav')
    update.atomic(folder/'samples'/(checked['sample'] + '.wav'), sample)
    entry = {k:checked[k] for k in ('scope','name','sample','public_key','content')}
    entry.update(issue=issue, author=author, mounts=known_mounts(checked['scope'],reference), lines=len(checked['rows']), sha256=sha,
                 size=path.stat().st_size, sample_size=len(sample))
    registry['versions'].append(entry)
    registry['versions'].sort(key=lambda e: e['issue'], reverse=True)
    save_registry(folder, registry)
    return entry


def withdraw(folder, issue):
    registry = load_registry(folder)
    found = next((e for e in registry['versions'] if e['issue'] == issue), None)
    if not found:
        if any(e['issue'] == issue for e in registry['withdrawn']):
            return
        raise ValueError('目录没有这个投稿编号')
    registry['versions'].remove(found)
    registry['withdrawn'].append({'issue':issue, 'content':found['content']})
    save_registry(folder, registry)
    (folder/'objects'/(found['sha256'] + '.zip')).unlink()
    if not any(e['sample'] == found['sample'] for e in registry['versions']):
        (folder/'samples'/(found['sample'] + '.wav')).unlink()


def read_intake(issue, archive, token, *, title_prefix=cnb.TITLE):
    if not issue.get('invisible'):
        raise ValueError('投稿需由工作台创建为私密 Issue')
    match = re.match(r'\A\x60{3}json\n([^\n]{1,48000})\n\x60{3}(?:\n|\Z)', issue.get('body','').replace('\r\n','\n'))
    if not match:
        raise ValueError('找不到有效的投稿清单')
    data = json.loads(match[1])
    if (not isinstance(data, dict) or set(data) != {'format','sha256','size','files'}
            or data['format'] != 'asgsv-intake-v1' or not isinstance(data['sha256'], str)
            or not update.HASH.fullmatch(data['sha256']) or issue['title'] != title_prefix + data['sha256'][:16]
            or type(data['size']) is not int or not 0 < data['size'] <= pack.MAX_PACK
            or not isinstance(data['files'], list) or len(data['files']) != (data['size'] + cnb.PART - 1)//cnb.PART):
        raise ValueError('投稿附件数量、大小或摘要无效')
    with archive.open('wb') as out:
        for i, f in enumerate(data['files']):
            expected = min(cnb.PART, data['size'] - i*cnb.PART)
            if (not isinstance(f, dict) or set(f) != {'path','sha256','size'} or f['size'] != expected
                    or not isinstance(f['sha256'], str) or not update.HASH.fullmatch(f['sha256'])):
                raise ValueError('投稿分片格式错误')
            path = cnb.file_path(f['path'])
            url = cnb.API + '/' + cnb.REPO + '/-/files/issues/' + quote(path, safe='/')
            req = Request(url, headers={'Authorization':'Bearer '+token, 'Accept':'application/octet-stream'})
            # CNB can redirect file reads to an expiring storage URL. Never forward the token there.
            from urllib.error import HTTPError
            try:
                response = build_opener(cnb.NoRedirect()).open(req, timeout=45)
            except HTTPError as exc:
                if exc.code in (404,410):
                    raise ValueError('投稿的附件已不存在，请重新提交') from None
                if exc.code not in (301,302,303,307,308):
                    raise OSError(f'读取 CNB 附件失败 · HTTP {exc.code}') from None
                location = exc.headers.get('Location','')
                cnb.storage_host(location)
                response = build_opener(cnb.NoRedirect()).open(location, timeout=45)
            with closing(response):
                chunk = cnb.limited(response, expected)
            if len(chunk) != expected or pack.hexdigest(chunk) != f['sha256']:
                raise ValueError('投稿分片不完整')
            out.write(chunk)
    if update.digest(archive) != data['sha256']:
        raise ValueError('投稿附件摘要不一致')


def git(folder, *args, check=True):
    return subprocess.run(['git','-C',str(folder),*args], check=check, capture_output=True, text=True)


def transaction(remote, fn):
    """Reapply to the latest branch on concurrent updates. Never force-push."""
    for _ in range(4):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            git(folder, 'init', '-q')
            git(folder, 'remote','add','origin',remote)
            exists = git(folder, 'ls-remote','--heads','origin','refs/heads/'+pack.BRANCH)
            if exists.stdout.strip():
                git(folder, 'fetch','--depth=1','origin', 'refs/heads/'+pack.BRANCH)
                git(folder, 'checkout','-q','-B', pack.BRANCH,'FETCH_HEAD')
            else:
                git(folder, 'checkout','--orphan',pack.BRANCH)
            git(folder, 'config','user.name','ASGSV Community')
            git(folder, 'config','user.email','community@users.noreply.cnb.cool')
            git(folder, 'lfs','install','--local')
            (folder/'.gitattributes').write_text('objects/*.zip filter=lfs diff=lfs merge=lfs -text\nsamples/*.wav filter=lfs diff=lfs merge=lfs -text\n', encoding='ascii')
            result = fn(folder)
            git(folder,'add','--all')
            if git(folder,'diff','--cached','--quiet',check=False).returncode == 0:
                return result
            git(folder,'commit','-qm','Update community recording catalog')
            push = git(folder,'push','origin','HEAD:refs/heads/'+pack.BRANCH,check=False)
            if push.returncode == 0:
                return result
            if not any(err in push.stderr.lower() for err in ('non-fast-forward','fetch first','stale info','failed to update ref','cannot lock ref')):
                raise RuntimeError('社区目录推送失败，请核对 CNB 分支权限和 Git LFS 配额')
    raise RuntimeError('目录正在被其他任务更新，下次巡检会重新提交')


def process(token, remote):
    pending = []
    page = 1
    while True:
        params = urlencode(dict(state='open', keyword=cnb.TITLE.strip(), page_size=100, page=page))
        rows = cnb.request_json(cnb.ISSUES+'?'+params, token)
        pending.extend(int(r['number']) for r in rows if re.fullmatch(re.escape(cnb.TITLE)+r'[0-9a-f]{16}', r['title']))
        if len(rows) < 100:
            break
        page += 1
    retries = []
    for number in sorted(set(pending))[:30]:
        issue_url = cnb.ISSUES+'/'+str(number)
        issue = cnb.request_json(issue_url, token)
        if issue['state'] != 'open':
            continue
        try:
            with tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp)/'submission.zip'
                read_intake(issue, archive, token)
                # Get the author from CNB, never from the submitted body.
                author = issue['author']['username']
                entry = transaction(remote, lambda folder: publish(folder, archive, number, author))
                status = f'已自动上架 · {entry["name"]} · {entry["lines"]} 句 · 版本 #{entry["issue"]}'
        except (ValueError, KeyError, TypeError) as exc:
            status = '投稿未通过 · ' + str(exc)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            retries.append(number)
            print(f'#{number} 暂缓 · {type(exc).__name__}', flush=True)
            continue
        # Keep the result accessible to the submitter. Network or push errors leave the issue open for retry.
        cnb.request_json(issue_url, token, {'state':'closed', 'body':issue['body']+'\n\nASGSV 处理结果：'+status}, method='PATCH')
        print(f'#{number} {status}', flush=True)
    if retries:
        raise RuntimeError('等待重试的投稿编号：'+', '.join(map(str,retries)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('sync','init','withdraw'))
    parser.add_argument('--issue', type=int)
    args = parser.parse_args()
    remote = f'https://cnb.cool/{cnb.REPO}.git'
    if os.environ.get('CNB_TOKEN'):
        # Restrict this short-lived credential to this CNB checkout, including its LFS endpoint.
        user = os.environ.get('CNB_TOKEN_USER_NAME') or 'cnb'
        header = base64.b64encode((user+':'+os.environ['CNB_TOKEN']).encode()).decode('ascii')
        index = int(os.environ.get('GIT_CONFIG_COUNT','0'))
        os.environ.update({'GIT_CONFIG_COUNT':str(index+1), f'GIT_CONFIG_KEY_{index}':
            f'http.https://cnb.cool/{cnb.REPO}.git.extraHeader', f'GIT_CONFIG_VALUE_{index}':'Authorization: Basic '+header})
    os.environ['GIT_LFS_SKIP_SMUDGE'] = '1'
    if args.action == 'init':
        transaction(remote, lambda folder: save_registry(folder, load_registry(folder)))
    elif args.action == 'withdraw':
        if not args.issue:
            parser.error('withdraw requires --issue')
        transaction(remote, lambda folder: withdraw(folder, args.issue))
    else:
        process(os.environ['CNB_TOKEN'], remote)


if __name__ == '__main__':
    main()

