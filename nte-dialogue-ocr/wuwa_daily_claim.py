"""One daily website gift, verified against the dubbing account's credit ledger."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
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
from wuwa_claim_browser import HumanVerificationRequired, LoginRequired, RewardAmountUnclear, home, wait_for_login, website_claim
from wuwa_member_reward import member_window, public_key_prefix


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
    rows = ledger.get('topups') if isinstance(ledger, dict) else None
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


def account_scope(key):
    return hashlib.sha256(('asgsv-daily-gift:' + key).encode('utf8')).hexdigest()[:24]


def member_binding(root, key):
    return status_path(root).parent / f'member-{account_scope(key)}.json'


def probe_member(root):
    """Verify the usual browser in read-only mode, even after today's credit arrived."""
    key = api_key(allow_environment=False)
    if not key:
        raise ValueError('请先保存用于配音的 BreezeBlue API Key。')
    shared = status_path(root).parent
    shared.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(shared / 'claim.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise ValueError('已有领取操作正在执行，请稍后再检测。')
    try:
        receipt = today_receipt(read_ledger(key), datetime.now(timezone.utc))
        result = member_window(key)
        if result == 'available':
            detail = '当前奖励卡明确可领取 400 积分。'
        elif result in ('already', 'unavailable') and receipt and receipt['amount'] == 400:
            detail = '已匹配官网密钥，官网显示已领或下一轮倒计时。配音账号本轮已有 400 积分流水。'
        else:
            raise ValueError('浏览器尚未提供明确的 400 积分领取资格或对应到账记录。')
        write_record(member_binding(root, key), {'route': 'member_window'})
        return '已连接浏览器会员账号 · ' + detail + ' 后续领取由 ASGSV 执行。'
    finally:
        lock.unlock()


def run(root, *, setup=False, scheduled=False):
    path = status_path(root)
    shared = path.parent
    shared.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(shared / 'claim.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        return dict(status='busy', message='另一项领取核对正在进行，请稍候查看结果。')

    account = ''
    def record(status, message, **extra):
        result = dict(status=status, message=message, account=account,
                      checked_at=datetime.now().astimezone().isoformat(timespec='seconds'), **extra)
        write_record(path, result)
        return result

    try:
        key = api_key(allow_environment=False)
        if not key:
            raise ValueError('请先在「配音与预算」保存 BreezeBlue API Key，领取时用它核对到账。')
        account = account_scope(key)

        def verify():
            ledger = read_ledger(key)
            return today_receipt(ledger, datetime.now(timezone.utc))

        intent = shared / f'attempt-{account}.json'
        previous = read_record(intent)
        attempt = {}
        outcome = None
        channel = 'dedicated'

        def confirmed(receipt):
            amount = receipt['amount']
            paid_at = datetime.fromisoformat(receipt['credited_at'])
            proof = attempt or previous
            if (proof.get('state') == 'acknowledged' and
                    proof.get('channel') in ('dedicated', 'member_window') and
                    proof.get('trigger') in ('scheduled', 'workbench') and
                    proof.get('day') == paid_at.astimezone(timezone.utc).date().isoformat()):
                # The ledger can become visible on a later run. Correlate the
                # provider's deposit time, never merely the day or the amount.
                start = datetime.fromisoformat(proof['attempted_at']).replace(microsecond=0)
                if start <= paid_at <= start + timedelta(minutes=15):
                    proof.update(state='verified', receipt=receipt['receipt'])
                    write_record(intent, proof)
            ours = proof.get('state') == 'verified' and proof.get('receipt') == receipt['receipt']
            origin = 'asgsv' if ours else 'existing'
            label = 'ASGSV 已自动领取并核对到账' if ours else '已核对已有赠送流水'
            message = f'{label} · {amount:g} 积分。'
            if amount != 400:
                message += '本期目标为 400 积分。'
            return record('credited' if amount == 400 else 'credited_other', message, origin=origin,
                          channel=proof.get('channel', '') if ours else '',
                          trigger=proof.get('trigger', '') if ours else '', **receipt)

        record('checking', '正在核对当前配音账号的每日赠送流水…')
        receipt = verify()
        if receipt:
            return confirmed(receipt)

        # A bounded, account-scoped guard covers a real redemption attempt.
        now = datetime.now(timezone.utc)
        if previous.get('day') == now.date().isoformat():
            started = datetime.fromisoformat(previous['attempted_at'])
            delay = 15 * 60 if previous['state'] == 'acknowledged' else 5 * 60
            retry_at = started + timedelta(seconds=delay)
            if now < retry_at:
                return record('pending', '尚未到账 · 当前配音账号没有本轮赠送流水。可随时核对，'
                              f'{retry_at.astimezone():%H:%M:%S} 后可重新检查官网是否仍可领取。')

        def mark_attempt():
            start = datetime.now(timezone.utc)
            attempt.update(day=start.date().isoformat(), attempted_at=start.isoformat(), state='attempting',
                           channel=channel, trigger='scheduled' if scheduled else 'workbench')
            write_record(intent, attempt)
            record('checking', '正在领取官网每日奖励，随后将核对实际到账金额…')

        def claim_from_member_window():
            nonlocal channel
            channel = 'member_window'
            record('browser', 'ASGSV 正在核对浏览器会员账号和 400 积分奖励卡…')
            result = member_window(key, mark_attempt)
            if result in ('already', 'acknowledged'):
                write_record(member_binding(root, key), {'route': 'member_window'})
            return result

        def claim_from_browser():
            if read_record(member_binding(root, key)).get('route') == 'member_window':
                return claim_from_member_window()
            try:
                return website_claim(mark_attempt, expected_prefix=public_key_prefix(key))
            except RewardAmountUnclear:
                if attempt:
                    raise
                return claim_from_member_window()

        record('browser', 'ASGSV 正在检查官网的每日奖励和领取条件…')
        failure = None
        try:
            try:
                outcome = claim_from_browser()
            except LoginRequired:
                if not setup or attempt:
                    raise
                record('login', '请在专用浏览器登录自己的 BreezeBlue 账号，登录后将继续核对。')
                wait_for_login()
                receipt = verify()
                if receipt:
                    return confirmed(receipt)
                outcome = claim_from_browser()
        except ValueError as exc:
            failure = exc
        if attempt:
            attempt['state'] = 'acknowledged' if outcome == 'acknowledged' else 'unconfirmed'
            write_record(intent, attempt)
        if attempt or outcome in ('already', 'unavailable'):
            record('checking', '正在等待当前配音账号的赠送流水…')
            for delay in ((0, 2, 5, 8) if attempt or outcome == 'already' else (0,)):
                if delay:
                    time.sleep(delay)
                receipt = verify()
                if receipt:
                    return confirmed(receipt)
        if failure:
            if isinstance(failure, RewardAmountUnclear):
                return record('amount_unclear', str(failure))
            if isinstance(failure, (LoginRequired, HumanVerificationRequired)):
                return record('action_required', '需要本人操作 · ' + str(failure))
            raise failure
        if outcome in ('already', 'acknowledged'):
            return record('pending', '尚未到账 · 官网显示已领取，当前配音账号没有本轮赠送流水。'
                          '稍后可再次核对；若持续如此，请检查专用浏览器和 API Key 是否属于同一账号。')
        if outcome == 'unavailable':
            return record('unavailable', '尚未到账 · 官网目前显示下一轮倒计时，没有可用的领取按钮。')
        if outcome == 'unconfirmed':
            return record('unconfirmed', '尚未到账 · 已点击「立即领取」，但官网没有确认，当前配音账号也没有赠送流水。'
                          '可随时核对，5 分钟后可重试。')
        raise ValueError('官网的领取结果无法识别，请打开专用领取页核对。')
    except Exception as exc:
        return record('error', '无法确认到账 · ' + (str(exc) or type(exc).__name__))
    finally:
        lock.unlock()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--scheduled', action='store_true')
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--check-member', action='store_true', help='只检测会员领取通道，不领取')
    args = parser.parse_args()
    if args.check_member:
        try:
            message = probe_member(args.root)
        except (ValueError, OSError) as exc:
            if sys.stdout:
                print(str(exc))
            return 1
        if sys.stdout:
            print(message)
        return 0
    result = run(args.root, setup=args.setup, scheduled=args.scheduled)
    if sys.stdout:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('credited', 'credited_other', 'busy') else 1


if __name__ == '__main__':
    raise SystemExit(main())
