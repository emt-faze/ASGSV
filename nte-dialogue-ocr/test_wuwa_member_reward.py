"""Exercise the native Windows helper on an isolated Chrome with every request intercepted."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

import wuwa_claim_browser as site
from wuwa_member_reward import drive_window

PREFIX = 'bb_fixture_12'
HELPER = Path(__file__).resolve().parents[1] / 'scripts/breezeblue_member_reward.ps1'


@unittest.skipUnless(sys.platform == 'win32', 'Requires native Windows browser accessibility.')
class NativeMemberRewardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            driver = site.browser_driver()
        except ValueError as exc:
            raise unittest.SkipTest(str(exc))
        browser = site.installed_browser('chrome') or site.installed_browser('msedge')
        if browser is None:
            raise unittest.SkipTest('Requires Chrome or Edge.')
        pw = driver.sync_playwright().start()
        cls.addClassCleanup(pw.stop)
        cls.browser = pw.chromium.launch(headless=False, **browser.launch_options(),
                                         ignore_default_args=['--hide-scrollbars'])
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.ctx = self.browser.new_context(service_workers='block')
        self.addCleanup(self.ctx.close)
        self.page = self.ctx.new_page()
        self.url = site.URL + '/api-keys#asgsv-reward-' + uuid.uuid4().hex
        self.started = []

    def offer(self, card, *, key=PREFIX, paid=False):
        action = '<span>400 积分已到账</span>' if paid else (
            '<button onclick="window.attempts++;this.parentElement.innerHTML='
            '&quot;<p>400 积分已到账</p>&quot;">立即领取</button>')
        html = f'''<!doctype html><meta charset="utf-8"><title>ASGSV 本地领取验收</title>
        <script>window.attempts=0</script>
        <table><tr><th>密钥标识</th></tr><tr><td>{key}••••••</td></tr></table>
        <button aria-label="修改资料" onclick="document.querySelector('[role=menu]').hidden=false">修改资料</button>
        <div role="menu" hidden><button aria-expanded="true">领取每日积分</button><section>
        {card}{action}</section><button>语言</button></div>'''
        def serve(route):
            if route.request.url.split('#')[0] == self.url.split('#')[0]:
                route.fulfill(body=html,content_type='text/html; charset=utf-8')
            else:
                route.abort()
        self.ctx.route('**/*', serve)
        self.page.goto(self.url)

    def helper(self, inspect=False):
        # Exercise the real implementation on this test's already-open isolated
        # window. The production launcher exclusively opens the fixed HTTPS URL.
        script = '. {\n' + HELPER.read_text(encoding='utf-8-sig') + '\n}\n' + r'''
$url=$env:ASGSV_TEST_REWARD_URL
try {
    $win=@(BrowserWindows | Where-Object { SameAddress $_ $url })
    if ($win.Count -ne 1) {throw '未定位到独立的验收窗口。'}
    $result=VisitReward $win[0] $url $env:ASGSV_REWARD_PUBLIC_PREFIX $env:ASGSV_REWARD_MODE
    Event-Json @{event='result'; status=$result}
} catch {Event-Json @{event='error';message=$_.Exception.Message}}
'''
        ps = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        proc = subprocess.Popen([str(ps),'-NoProfile','-NonInteractive','-Command',script],
                   env=dict(os.environ,ASGSV_TEST_REWARD_URL=self.url,ASGSV_REWARD_PUBLIC_PREFIX=PREFIX,
                            ASGSV_REWARD_MODE='inspect' if inspect else 'claim'),
                   stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                   text=True,encoding='utf8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
        return drive_window(proc, None if inspect else lambda: self.started.append(True), timeout=65)

    def test_program_redeems_verified_member_offer_exactly_once(self):
        self.offer('<p>+400 积分</p><b>Discord 成员</b><p>每天回来一次，保持余额充足</p>')
        self.assertEqual(self.helper(), 'acknowledged')
        self.assertEqual(self.page.evaluate('window.attempts'), 1)
        self.assertEqual(self.started, [True])

    def test_two_tiers_are_not_redeemed(self):
        self.offer('<p>+400 积分</p><p>加入 Discord 可领完整奖励</p><p>+100 积分</p><p>标准每日奖励</p>')
        with self.assertRaises(site.RewardAmountUnclear):
            self.helper()
        self.assertEqual(self.page.evaluate('window.attempts'), 0)
        self.assertEqual(self.started, [])

    def test_different_account_never_reaches_redemption(self):
        self.offer('<p>+400 积分</p><b>Discord 成员</b>', key='bb_different_1')
        with self.assertRaisesRegex(ValueError,'未能匹配'):
            self.helper()
        self.assertEqual(self.page.evaluate('window.attempts'), 0)
        self.assertEqual(self.started, [])

    def test_readiness_check_cannot_redeem(self):
        self.offer('<p>+400 积分</p><b>Discord 成员</b>')
        self.assertEqual(self.helper(inspect=True), 'available')
        self.assertEqual(self.page.evaluate('window.attempts'), 0)

    def test_already_credited_never_clicks(self):
        self.offer('', paid=True)
        self.assertEqual(self.helper(), 'already')
        self.assertEqual(self.started, [])
        self.assertEqual(self.page.evaluate('window.attempts'), 0)

