"""Redeem one verified member offer through the user's ordinary, signed-in browser."""
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
from threading import Thread
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from wuwa_breeze import NoRedirect
from wuwa_claim_browser import MEMBER, select_browser, require_full_reward


def public_key_prefix(key):
    request = Request('https://api.breeze.blue/v1/api-keys/current', headers={'xi-api-key': key})
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise ValueError(f'官网配对接口返回 HTTP {exc.code}，请检查配音密钥。') from None
    except (OSError, URLError) as exc:
        raise ValueError(f'无法连接官网配对接口 · {getattr(exc, "reason", exc)}') from None
    item = data.get('api_key') if isinstance(data, dict) else None
    prefix = item.get('prefix') if isinstance(item, dict) else None
    if not isinstance(prefix, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,32}', prefix) or not key.startswith(prefix) or prefix == key:
        raise ValueError('官网没有返回可核对的公开密钥标识，未操作浏览器。')
    return prefix


def drive_window(proc, before_click, timeout=100):
    """A browser helper cannot redeem until this worker validates the exact offer."""
    output = Queue()
    def read_output():
        try:
            for line in proc.stdout:
                if line.startswith('ASGSV-GIFT '):
                    output.put(line.removeprefix('ASGSV-GIFT '))
        finally:
            output.put(None)
    reader = Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    attempted = False
    confirmed = False
    try:
        while True:
            try:
                raw = output.get(timeout=max(0, deadline - time.monotonic()))
            except Empty:
                raise ValueError('浏览器领取组件超时，请检查 Windows 会话并稍后核对流水。') from None
            if raw is None:
                raise ValueError(f'浏览器领取组件意外退出（{proc.wait(timeout=3)}），未收到核对结果。')
            event = json.loads(raw)
            phase = event.get('event')
            if phase == 'error':
                raise ValueError(event['message'])
            if phase == 'result':
                state = event.get('status')
                allowed = {'already', 'unavailable'}
                if before_click is None:
                    allowed.add('available')
                elif attempted:
                    allowed.update(('unconfirmed', 'acknowledged'))
                if state not in allowed:
                    raise ValueError('领取组件返回了与当前操作不符的结果。')
                return state
            if phase not in ('offer', 'confirm') or (phase == 'confirm') != attempted or confirmed:
                raise ValueError('领取步骤发生变化，已停止操作。')
            labels, button = event['labels'], event['button']
            if not isinstance(labels, list) or len(labels) > 40 or not all(isinstance(v,str) and len(v) <= 150 for v in labels):
                raise ValueError('官网奖励卡格式不符，未授权领取。')
            require_full_reward('\n'.join([*labels, button]), any(MEMBER.fullmatch(v.strip()) for v in labels),
                                button, confirmation=(phase == 'confirm'))
            if before_click is None:
                answer = 'inspect'
            else:
                if not attempted:
                    before_click()
                    attempted = True
                else:
                    confirmed = True
                answer = 'redeem'
            proc.stdin.write(answer + '\n')
            proc.stdin.flush()
    finally:
        # EOF cancels an outstanding confirmation and lets the helper close
        # its own window, including when amount validation or marker I/O fails.
        proc.stdin.close()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        reader.join(timeout=2)
        proc.stdout.close()


def member_window(key, before_click=None):
    browser = select_browser()
    if browser.kind not in ('chrome','msedge'):
        raise ValueError('当前使用独立 Chromium。请在专用领取页登录有 400 积分资格的账号。')
    prefix = public_key_prefix(key)
    helper = Path(__file__).resolve().parents[1] / 'scripts/breezeblue_member_reward.ps1'
    code = helper.read_text(encoding='utf-8-sig')
    ps = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    # Only the public, masked-list prefix crosses into the browser helper.
    system_names = {'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP',
                    'USERPROFILE', 'USERNAME', 'USERDOMAIN', 'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA',
                    'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432',
                    'COMMONPROGRAMFILES', 'COMMONPROGRAMFILES(X86)', 'PSMODULEPATH', 'PROCESSOR_ARCHITECTURE'}
    env = {name: value for name, value in os.environ.items() if name.upper() in system_names}
    env.update(ASGSV_REWARD_BROWSER=str(browser.executable), ASGSV_REWARD_PUBLIC_PREFIX=prefix,
               ASGSV_REWARD_MODE='inspect' if before_click is None else 'claim')
    proc = subprocess.Popen([str(ps), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', code],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env, text=True, encoding='utf8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
    return drive_window(proc, before_click)
