"""Exercise real nonmember intake with a private test Issue that the publisher ignores."""
import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile
from threading import Event
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener

import publish
from publish import cnb, update


def token_for(name):
    value = os.environ.get(name)
    if not value and os.name=='nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as env:
            try:
                value = winreg.QueryValueEx(env,name)[0]
            except FileNotFoundError:
                value = None
    if not value:
        raise ValueError('缺少本机凭据 '+name+'，尚未执行线上测试')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submit-private-check', action='store_true', required=True)
    parser.parse_args()
    author = token_for('ASGSV_CNB_TEST_AUTHOR_TOKEN')
    auditor = token_for('CNB_TOKEN')
    if secrets.compare_digest(author,auditor):
        raise ValueError('普通账号与维护者不能使用同一个令牌')
    name = cnb.request_json('/user',author)['username']
    # Independent membership lookup includes inherited group roles. Only this GET uses the auditor.
    path = '/'+cnb.REPO+'/-/members/'+quote(name,safe='')+'/access-level'
    try:
        membership=cnb.request_json(path,auditor)
    except RuntimeError as exc:
        if 'HTTP 404' not in str(exc):
            raise
        membership=[]
    if not isinstance(membership,list) or any(r.get('access_level') not in ('Unknown','Anonymous') for r in membership):
        raise ValueError('此账号具有仓库或上级组织角色，请使用非成员账号验收')
    prefix='ASGSV 私密投稿权限验收 '
    with tempfile.TemporaryDirectory() as tmp:
        sample=Path(tmp)/'transport.bin'
        # Two actual upload requests cross the 4 MiB chunk boundary without uploading a player's recordings.
        sample.write_bytes(os.urandom(cnb.PART + 4096))
        number=cnb.submit(author,sample,Event(),print,title_prefix=prefix)
        ticket=cnb.ISSUES+'/'+str(number)
        try:
            issue=cnb.request_json(ticket,author)
            if issue['author']['username']!=name or not issue['invisible']:
                raise ValueError('CNB 返回的投稿身份或可见性不符')
            decoded=Path(tmp)/'readback.bin'
            publish.read_intake(issue,decoded,author,title_prefix=prefix)
            if update.digest(sample)!=update.digest(decoded):
                raise ValueError('读回的附件与上传内容不一致')
            req=Request(cnb.API+ticket,headers={'Accept':'application/json'})
            try:
                with build_opener(cnb.NoRedirect()).open(req,timeout=15) as response:
                    raise ValueError(f'匿名用户可以读取私密验收单 · HTTP {response.status}')
            except HTTPError as exc:
                if exc.code not in (401,403,404):
                    raise
            print(json.dumps(dict(nonmember=name,private_issue=number,anonymous_read_denied=True,
                bytes=decoded.stat().st_size,sha256=update.digest(decoded)),ensure_ascii=False))
        finally:
            cnb.request_json(ticket,author,{'state':'closed'},method='PATCH')


if __name__=='__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from None

