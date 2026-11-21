"""One daily website gift, verified against the dubbing account's credit ledger."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from PySide6.QtCore import QLockFile

from wuwa_breeze import NoRedirect, api_key
from wuwa_claim_browser import LoginRequired, home, wait_for_login, website_claim


def write_record(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf8', delete=False, dir=path.parent) as f:
        temporary = Path(f.name)
        try:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        except Exception:
            f.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_record(path):
    try:
        data = json.loads(path.read_text('utf8'))
        if not isinstance(data, dict):
            raise ValueError('记录格式异常')
        return data
    except FileNotFoundError:
        return {}


def status_path(root):
    return home() / 'claim-state' / 'latest.json'


def today_receipt(ledger, now):
    rows = ledger['topups']
    if not isinstance(rows, list):
        raise ValueError('积分流水格式已变化，请检查 BreezeBlue 接口。')
    for row in rows:
        if row.get('description') != 'daily claim credits':
            continue
        stamp = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('积分流水缺少时区，无法核实到账日。')
        if stamp > now or stamp.astimezone(timezone.utc).date() != now.astimezone(timezone.utc).date():
            continue
        amount = float(row['amount'])
        if not math.isfinite(amount) or amount <= 0 or not row.get('id'):
            raise ValueError('每日赠送流水缺少有效的金额或编号。')
        return dict(amount=amount, receipt=str(row['id']),
                    credited_at=stamp.astimezone().isoformat(timespec='seconds'))
    return None


def read_ledger(key):
    request = Request('https://api.breeze.blue/v1/balance', headers={'xi-api-key': key})
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        raise ValueError(f'读取积分流水返回 HTTP {exc.code}。请检查 BreezeBlue 密钥及服务状态。') from None
    except (OSError, URLError) as exc:
        raise ValueError(f'无法连接积分流水接口 · {getattr(exc, "reason", exc)}') from None


def run(root, *, setup=False):
    path = status_path(root)
    shared = home() / 'claim-state'
    shared.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(shared / 'claim.lock'))
    if not lock.tryLock(0):
        return dict(status='busy', message='已有领取操作正在执行。')

    def record(status, message, **extra):
        result = dict(status=status, message=message, checked_at=datetime.now().astimezone().isoformat(timespec='seconds'), **extra)
        write_record(path, result)
        return result

    intent = shared / 'last-submit.json'
    try:
        key = api_key(allow_environment=False)
        if not key:
            raise ValueError('请先在「配音与预算」保存 BreezeBlue API Key，领取时用它核对到账。')

        if setup:
            record('checking', '正在检查专用浏览器 的登录状态…')
            try:
                website_claim()
            except LoginRequired:
                record('login', '请在专用浏览器 登录自己的 BreezeBlue 账号。登录后会自动继续，也可手动关闭该窗口。')
                wait_for_login()

        def verify():
            return today_receipt(read_ledger(key), datetime.now(timezone.utc))

        def confirmed(receipt):
            amount = receipt['amount']
            return record('credited', f'每日赠送已到账 {amount:g} 积分', **receipt)

        record('checking', '正在核对当前配音账号的每日赠送流水…')
        receipt = verify()
        if receipt:
            return confirmed(receipt)
        day = datetime.now(timezone.utc).date().isoformat()
        previous = read_record(intent)
        if previous.get('day') == day:
            return record('pending', '本领取周期已经提交，当前账号还没有到账流水。请核对专用浏览器 与配音密钥是否属于同一账号。')

        def mark_submitted():
            write_record(intent, dict(day=datetime.now(timezone.utc).date().isoformat()))
            record('pending', '领取请求待核验，正在等待账户流水…')

        record('browser', '正在用专用浏览器 检查每日赠送…')
        outcome = website_claim(mark_submitted)
        for delay in (0, 2, 5, 8):
            if delay:
                time.sleep(delay)
            receipt = verify()
            if receipt:
                return confirmed(receipt)
        detail = '网页显示已经领取' if outcome == 'already' else '领取已提交'
        return record('pending', detail + '，当前配音账号尚无对应流水。请稍后复核，并确认登录账号与 API Key 一致。')
    except Exception as exc:
        return record('error', str(exc) or type(exc).__name__)
    finally:
        lock.unlock()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--scheduled', action='store_true')
    parser.add_argument('--setup', action='store_true')
    args = parser.parse_args()
    result = run(args.root, setup=args.setup)
    if sys.stdout:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('credited', 'busy') else 1


if __name__ == '__main__':
    raise SystemExit(main())
