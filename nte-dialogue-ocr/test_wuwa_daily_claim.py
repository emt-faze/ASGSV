import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import wuwa_daily_claim as claim
from wuwa_claim_ui import result_text
from wuwa_daily_scheduler import task_name


class DailyGiftTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in [('home', self.root / 'local'), ('api_key', 'test')]:
            self.enterContext(patch.object(claim, name, return_value=value))
        self.browser = self.enterContext(patch.object(claim, 'website_claim'))
        self.enterContext(patch.object(claim, 'public_key_prefix', return_value='test_public'))
        self.member = self.enterContext(patch.object(claim, 'member_window'))
        self.member.side_effect = claim.RewardAmountUnclear('已停在领取前')
        self.ledger = self.enterContext(patch.object(claim, 'read_ledger'))
        self.enterContext(patch.object(claim.time, 'sleep'))
        self.gift = {'description': 'daily claim credits', 'amount': 400,
                     'id': 'example-receipt', 'created_at': datetime.now(timezone.utc).isoformat()}

    def accept_member_offer(self, key, before):
        before()
        self.gift['created_at'] = datetime.now(timezone.utc).isoformat()
        return 'acknowledged'

    def test_cooldown_can_pair_only_with_an_actual_full_gift(self):
        self.member.side_effect = None
        self.member.return_value = 'unavailable'
        self.ledger.return_value = {'topups': [dict(self.gift, amount=100)]}
        with self.assertRaises(ValueError):
            claim.probe_member(self.root)
        self.assertFalse(claim.member_binding(self.root, 'test').exists())
        self.ledger.return_value = {'topups': [self.gift]}
        self.assertIn('已连接', claim.probe_member(self.root))
        self.browser.assert_not_called()

    def test_separate_windows_users_get_separate_status(self):
        shared_install = self.root / 'shared'
        with patch.object(claim, 'home', return_value=self.root / 'user-A'):
            a = claim.status_path(shared_install)
            claim.write_record(a, {'message': 'user A'})
        with patch.object(claim, 'home', return_value=self.root / 'user-B'):
            b = claim.status_path(shared_install)
            self.assertNotEqual(a, b)
            self.assertEqual(claim.read_record(b), {})

    def test_setup_checks_deposit_before_opening_browser(self):
        self.ledger.return_value = {'topups': [self.gift]}
        with patch.object(claim, 'wait_for_login') as login:
            self.assertEqual(claim.run(self.root, setup=True)['status'], 'credited')
        login.assert_not_called()
        self.browser.assert_not_called()

    def test_setup_rechecks_deposit_after_sign_in(self):
        self.ledger.side_effect = [{'topups': []}, {'topups': [self.gift]}]
        self.browser.side_effect = claim.LoginRequired('sign-in')
        with patch.object(claim, 'wait_for_login') as login:
            self.assertEqual(claim.run(self.root, setup=True)['status'], 'credited')
        login.assert_called_once_with()
        self.assertEqual(self.browser.call_count, 1)

    def test_day_uses_utc_and_only_daily_topups(self):
        at = datetime.fromisoformat('2026-11-21T08:01:01+08:00')
        old = dict(self.gift, created_at='2026-11-21T07:59:59+08:00')
        new = dict(self.gift, created_at='2026-11-21T08:01:00+08:00')
        purchase = dict(new, description='top up')
        self.assertIsNone(claim.today_receipt({'topups': [old, purchase]}, at))
        self.assertEqual(claim.today_receipt({'topups': [new]}, at)['amount'], 400)
        self.assertNotEqual(task_name(self.root), task_name(self.root, 'claim'))

    def test_manual_claim_prevents_browser_start(self):
        self.ledger.return_value = {'topups': [self.gift]}
        self.assertEqual(claim.run(self.root)['status'], 'credited')
        self.browser.assert_not_called()

    def test_submit_once_while_receipt_is_delayed(self):
        empty = {'topups': []}
        self.ledger.return_value = empty
        self.browser.side_effect = lambda before, *, expected_prefix: (before(), 'acknowledged')[1]
        self.assertEqual(claim.run(self.root)['status'], 'pending')
        self.assertEqual(claim.run(self.root)['status'], 'pending')
        self.browser.assert_called_once()
        self.gift['created_at'] = datetime.now(timezone.utc).isoformat()
        self.ledger.return_value = {'topups': [self.gift]}
        result = claim.run(self.root)
        self.assertEqual((result['status'], result['origin'], result['trigger']), ('credited', 'asgsv', 'workbench'))
        self.assertEqual(json.loads(claim.status_path(self.root).read_text('utf8'))['receipt'], self.gift['id'])

    def test_old_day_only_marker_does_not_lock_the_entire_day(self):
        shared = claim.status_path(self.root).parent
        claim.write_record(shared / 'last-submit.json', {'day': datetime.now(timezone.utc).date().isoformat()})
        self.ledger.side_effect = [{'topups': []}, {'topups': [self.gift]}]
        self.browser.side_effect = lambda before, *, expected_prefix: (before(), 'unconfirmed')[1]
        self.assertEqual(claim.run(self.root)['status'], 'credited')
        self.assertEqual(self.browser.call_count, 1)

    def test_uncertain_click_debounces_then_checks_offer_again(self):
        self.ledger.return_value = {'topups': []}
        self.browser.side_effect = lambda before, *, expected_prefix: (before(), 'unconfirmed')[1]
        self.assertEqual(claim.run(self.root)['status'], 'unconfirmed')
        self.assertEqual(claim.run(self.root)['status'], 'pending')
        guard = next(claim.status_path(self.root).parent.glob('attempt-*.json'))
        value = claim.read_record(guard)
        value['attempted_at'] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        claim.write_record(guard, value)
        self.assertEqual(claim.run(self.root)['status'], 'unconfirmed')
        self.assertEqual(self.browser.call_count, 2)

    def test_different_saved_keys_do_not_share_attempts_or_receipts(self):
        self.ledger.return_value = {'topups': []}
        self.browser.side_effect = lambda before, *, expected_prefix: (before(), 'unconfirmed')[1]
        one = claim.run(self.root)
        with patch.object(claim, 'api_key', return_value='another-test-key'):
            two = claim.run(self.root)
        self.assertEqual(self.browser.call_count, 2)
        self.assertNotEqual(one['account'], two['account'])
        self.assertIn('尚无核对记录', result_text(one, two['account']))

    def test_ambiguous_price_preserves_attempt_and_legacy_ui_is_clear(self):
        self.ledger.return_value = {'topups': []}
        self.browser.side_effect = claim.RewardAmountUnclear('已停在领取前')
        self.assertEqual(claim.run(self.root)['status'], 'amount_unclear')
        self.assertFalse(list(claim.status_path(self.root).parent.glob('attempt-*.json')))
        message = result_text({'status': 'pending', 'message': '领取已提交'}, '')
        self.assertIn('尚未到账', message)
        self.assertNotIn('已提交', message)

    def test_manual_lower_tier_is_reported_at_its_actual_amount(self):
        self.ledger.return_value = {'topups': [dict(self.gift, amount=100)]}
        state = claim.run(self.root)
        self.assertEqual(state['status'], 'credited_other')
        self.assertIn('已到账 100', result_text(state, state['account']))
        self.browser.assert_not_called()

    def test_website_already_without_api_receipt_stays_uncredited(self):
        self.ledger.return_value = {'topups': []}
        self.browser.return_value = 'already'
        result = claim.run(self.root)
        self.assertEqual(result['status'], 'pending')
        self.assertNotIn('receipt', result)

    def test_check_after_delivery_error_finds_a_late_receipt(self):
        self.ledger.side_effect = [{'topups': []}, {'topups': [self.gift]}]
        def interrupted(before, *, expected_prefix):
            before()
            raise ValueError('网页连接中断')
        self.browser.side_effect = interrupted
        self.assertEqual(claim.run(self.root)['status'], 'credited')

    def test_member_window_takes_over_only_before_dedicated_click(self):
        self.browser.side_effect = claim.RewardAmountUnclear('官网有两档')
        self.ledger.side_effect = [{'topups': []}, {'topups': [self.gift]}]
        self.member.side_effect = self.accept_member_offer
        result = claim.run(self.root, scheduled=True)
        self.assertEqual((result['status'], result['origin'], result['channel'], result['trigger']),
                         ('credited', 'asgsv', 'member_window', 'scheduled'))
        self.assertEqual(claim.read_record(claim.member_binding(self.root, 'test'))['route'], 'member_window')
        self.ledger.side_effect = None
        self.ledger.return_value = {'topups': [self.gift]}
        self.assertEqual(claim.run(self.root)['origin'], 'asgsv')
        self.assertEqual(self.member.call_count, 1)

    def test_receipt_predating_worker_remains_an_existing_deposit(self):
        self.ledger.return_value = {'topups': [self.gift]}
        result = claim.run(self.root, scheduled=True)
        self.assertEqual(result['origin'], 'existing')
        self.assertIn('已核对已有流水', result_text(result, result['account']))
        self.member.assert_not_called()
        self.browser.assert_not_called()

    def test_member_probe_pairs_without_issuing_a_claim(self):
        self.ledger.return_value = {'topups': [self.gift]}
        self.member.side_effect = None
        self.member.return_value = 'already'
        self.assertIn('已连接', claim.probe_member(self.root))
        self.member.assert_called_once_with('test')
        self.browser.assert_not_called()
        self.assertFalse(list(claim.status_path(self.root).parent.glob('attempt-*.json')))
        self.ledger.side_effect = [{'topups': []}, {'topups': [self.gift]}]
        self.member.side_effect = self.accept_member_offer
        self.assertEqual(claim.run(self.root)['origin'], 'asgsv')
        self.browser.assert_not_called()

    def test_an_already_message_without_matching_receipt_cannot_pair(self):
        self.ledger.return_value = {'topups': []}
        self.member.side_effect = None
        self.member.return_value = 'already'
        with self.assertRaises(ValueError):
            claim.probe_member(self.root)
        self.assertFalse(claim.member_binding(self.root, 'test').exists())

    def test_no_second_browser_after_a_redemption_attempt(self):
        self.ledger.return_value = {'topups': []}
        def terms_changed(before, *, expected_prefix):
            before()
            raise claim.RewardAmountUnclear('确认面板金额变动')
        self.browser.side_effect = terms_changed
        self.assertEqual(claim.run(self.root)['status'], 'amount_unclear')
        self.member.assert_not_called()

    def test_human_verification_never_switches_browsers(self):
        self.ledger.return_value = {'topups': []}
        self.browser.side_effect = claim.HumanVerificationRequired('官网要求验证')
        self.assertEqual(claim.run(self.root)['status'], 'action_required')
        self.member.assert_not_called()

    def test_bound_account_error_prevents_another_claim_path(self):
        self.ledger.return_value = {'topups': []}
        claim.write_record(claim.member_binding(self.root,'test'), {'route':'member_window'})
        self.member.side_effect = ValueError('当前配音密钥与浏览器不匹配')
        result = claim.run(self.root)
        self.assertEqual(result['status'], 'error')
        self.browser.assert_not_called()
        self.assertNotIn('receipt', result)

    def test_api_error_prevents_click(self):
        self.ledger.side_effect = ValueError('HTTP 401')
        result = claim.run(self.root)
        self.assertEqual(result['status'], 'error')
        self.assertIn('401', result['message'])
        self.browser.assert_not_called()
