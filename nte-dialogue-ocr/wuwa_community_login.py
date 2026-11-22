"""Use CNB's own device-login client; never read its token store or inherit a release token."""
import hashlib
import queue
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import threading
from urllib.request import Request, urlopen

from wuwa_community_cnb import ISSUES, REPO
from wuwa_update import atomic, digest, locked

NODE = 'v24.22.3'
NODE_SHA = '86fc4785327db5f5c7558dc6156c543b433c313e813a222e088086222ab4afa6'
CLI = '1.24.13'
TAR_SHA = '46f8877a7d7ac0f2956847a6a24a669599329e0edb8e0d8db25beb7525155a46'
JS_SHA = '29303fe40a9b0399d32ddd49e56532bb59f5659581ef448e3d9fc67796a2456c'


def acquire(path, urls, sha, limit, stop, progress):
    if path.is_file() and digest(path) == sha:
        return
    errors = []
    for url in urls:
        tmp = path.with_suffix('.partial')
        try:
            with urlopen(Request(url, headers={'User-Agent':'ASGSV-Community'}), timeout=25) as src, tmp.open('wb') as dst:
                count = 0
                sha256 = hashlib.sha256()
                while chunk := src.read(256*1024):
                    if stop.is_set():
                        raise InterruptedError('登录组件下载已取消')
                    count += len(chunk)
                    if count > limit:
                        raise ValueError('登录组件超过大小限制')
                    sha256.update(chunk)
                    dst.write(chunk)
                    if count % (2*1024**2) < len(chunk):
                        progress(f'准备 CNB 登录组件 · {count // 1024**2} MiB')
            if sha256.hexdigest() != sha:
                raise ValueError('登录组件 SHA-256 校验失败')
            os.replace(tmp, path)
            return
        except InterruptedError:
            raise
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        finally:
            tmp.unlink(missing_ok=True)
    raise OSError('无法获取官方登录组件 · ' + ' / '.join(errors))


class Author:
    def __init__(self, root, stop, progress):
        self.directory = root/'community-author'/'tools'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.stop, self.progress = stop, progress
        self.js = self.directory/f'cnb-{CLI}.cjs'
        self.node = self.directory/f'node-{NODE}.exe'

    def prepare(self):
        with locked(self.directory/'components.lock', 3000):
            if not self.js.exists() or digest(self.js) != JS_SHA:
                archive = self.directory/f'cnb-{CLI}.tgz'
                tail = f'/@cnbcool/cnb-cli/-/cnb-cli-{CLI}.tgz'
                acquire(archive, ['https://registry.npmmirror.com'+tail,'https://registry.npmjs.org'+tail],
                        TAR_SHA, 4*1024**2, self.stop, self.progress)
                with tarfile.open(archive, 'r:gz') as tar:
                    script = tar.extractfile('package/dist/index.js').read(4*1024**2)
                    if hashlib.sha256(script).hexdigest() != JS_SHA:
                        raise ValueError('CNB 客户端校验失败')
                    atomic(self.js, script)
                    atomic(self.directory/'CNB-CLI-LICENSE.md', tar.extractfile('package/LICENSE.md').read())
            installed = shutil.which('node.exe')
            if installed and digest(Path(installed)) == NODE_SHA:
                self.node = Path(installed)
            else:
                suffix = f'/{NODE}/win-x64/node.exe'
                acquire(self.node, ['https://nodejs.org/dist'+suffix, 'https://npmmirror.com/mirrors/node'+suffix],
                        NODE_SHA, 160*1024**2, self.stop, self.progress)

    def run(self, args, timeout=75):
        # The tool must use the identity explicitly authorized in the player's browser.
        needed = {'SYSTEMROOT','WINDIR','SYSTEMDRIVE','COMSPEC','PATH','PATHEXT','USERPROFILE',
                  'HOMEDRIVE','HOMEPATH','APPDATA','LOCALAPPDATA','TEMP','TMP','PROGRAMFILES',
                  'PROGRAMFILES(X86)','PROGRAMW6432','LANG','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY'}
        env = {k:v for k,v in os.environ.items() if k.upper() in needed}
        env.update(CNB_API_ENDPOINT='https://api.cnb.cool', NO_COLOR='1')
        with subprocess.Popen([str(self.node), str(self.js), *args], cwd=self.directory, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as proc:
            if args[0] == 'login':
                return self.wait_for_login(proc, timeout)
            deadline = time.monotonic()+timeout
            while True:
                if self.stop.is_set() or time.monotonic() >= deadline:
                    proc.kill()
                    proc.communicate()
                    raise InterruptedError('CNB 操作已取消或超时')
                try:
                    raw = proc.communicate(timeout=.3)[0].decode('utf8','replace')
                    break
                except subprocess.TimeoutExpired:
                    continue
            if proc.returncode:
                code = re.search(r'\b(4\d\d|5\d\d)\b',raw)
                detail = 'HTTP '+code[1] if code else '请检查网络和浏览器授权状态'
                raise RuntimeError('CNB 操作失败 · '+detail)
        return raw

    def wait_for_login(self, proc, timeout):
        lines = queue.Queue()
        def read():
            try:
                for raw in proc.stdout:
                    lines.put(raw.decode('utf8','replace').strip())
            finally:
                lines.put(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        deadline = time.monotonic()+timeout
        try:
            while not self.stop.is_set() and time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=.25)
                except queue.Empty:
                    continue
                if line is None:
                    if proc.wait(timeout=3) != 0:
                        raise RuntimeError('CNB 登录未完成，请检查网络或重新授权')
                    return ''
                if re.fullmatch(r'https://cnb\.cool/[^\s]+', line):
                    self.progress('请在浏览器完成授权。若浏览器未打开，可复制此链接：'+line)
            raise InterruptedError('CNB 授权已取消或超时')
        finally:
            if proc.poll() is None:
                proc.kill()
            reader.join(timeout=2)

    def request(self, path, data=None):
        if path == '/user':
            args = ['users','get-user-info']
        elif path == ISSUES+'/asset-groups':
            args = ['issues','post-asset-group','--repo',REPO,'--data',json.dumps(data)]
        elif path == ISSUES and data:
            with tempfile.TemporaryDirectory(dir=self.directory) as tmp:
                body = Path(tmp)/'issue.md'
                body.write_text(data['body'], encoding='utf8')
                return self.json_command(['issues','create-issue','--repo',REPO,'--invisible',
                                          '--title',data['title'],'--body-file',str(body)])
        elif path.startswith(ISSUES+'/') and path.rsplit('/',1)[1].isdecimal():
            args = ['issues','get-issue','--repo',REPO,'--number',path.rsplit('/',1)[1]]
        else:
            raise ValueError('不支持的 CNB 投稿操作')
        return self.json_command(args)

    def json_command(self, args):
        raw = self.run([*args,'--verbose'])
        result = json.loads(raw)
        if isinstance(result, dict) and 'data' in result and ('status' in result or 'headers' in result):
            result = result['data']
        if not isinstance(result, dict):
            raise ValueError('CNB 官方客户端返回了无法识别的结果')
        return result

    def login(self):
        self.progress('请在浏览器完成 CNB 授权，完成后会自动返回')
        self.run(['login','--host','cnb.cool'], timeout=600)
        return self.request('/user')['username']

    def current(self):
        from wuwa_community_cnb import AUTHOR
        name = self.request('/user')['username']
        if not AUTHOR.fullmatch(name):
            raise ValueError('CNB 返回的投稿账号标识无效')
        return name

