import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import wuwa_daily_claim as claim
from wuwa_daily_scheduler import task_name


class DailyGiftTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in [('home', self.root / 'local'), ('api_key', 'test')]:
            self.enterContext(patch.object(claim, name, return_value=value))
        self.browser = self.enterContext(patch.object(claim, 'website_claim'))
        self.ledger = self.enterContext(patch.object(claim, 'read_ledger'))
        self.enterContext(patch.object(claim.time, 'sleep'))
        self.gift = {'description': 'daily claim credits', 'amount': 400,
                     'id': 'example-receipt', 'created_at': datetime.now(timezone.utc).isoformat()}

    def test_separate_windows_users_get_separate_status(self):
        shared_install = self.root / 'shared'
        with patch.object(claim, 'home', return_value=self.root / 'user-A'):
            a = claim.status_path(shared_install)
            claim.write_record(a, {'message': 'user A'})
        with patch.object(claim, 'home', return_value=self.root / 'user-B'):
            b = claim.status_path(shared_install)
            self.assertNotEqual(a, b)
            self.assertEqual(claim.read_record(b), {})

    def test_setup_completes_login_then_verifies_manual_deposit(self):
        self.ledger.return_value = {'topups': [self.gift]}
        self.browser.side_effect = claim.LoginRequired('sign-in')
        with patch.object(claim, 'wait_for_login') as login:
            self.assertEqual(claim.run(self.root, setup=True)['status'], 'credited')
        login.assert_called_once_with()
        self.browser.assert_called_once_with()

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
        self.browser.side_effect = lambda before: (before(), 'submitted')[1]
        self.assertEqual(claim.run(self.root)['status'], 'pending')
        self.assertEqual(claim.run(self.root)['status'], 'pending')
        self.browser.assert_called_once()
        self.ledger.return_value = {'topups': [self.gift]}
        self.assertEqual(claim.run(self.root)['status'], 'credited')
        self.assertEqual(json.loads(claim.status_path(self.root).read_text('utf8'))['receipt'], self.gift['id'])

    def test_api_error_prevents_click(self):
        self.ledger.side_effect = ValueError('HTTP 401')
        result = claim.run(self.root)
        self.assertEqual(result['status'], 'error')
        self.assertIn('401', result['message'])
        self.browser.assert_not_called()
