"""Browser regressions for the visible Chinese reward panels (no external requests)."""
import sys
import unittest

import wuwa_claim_browser as reward


@unittest.skipUnless(sys.platform == 'win32', 'The desktop claim browser is supported on Windows.')
class RewardPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            driver = reward.browser_driver()
        except ValueError as exc:
            raise unittest.SkipTest(str(exc))
        browser = reward.installed_browser('chrome') or reward.installed_browser('msedge') or reward.managed_browser(False)
        if browser is None:
            raise unittest.SkipTest('需要已安装的领取浏览器')
        pw = driver.sync_playwright().start()
        cls.addClassCleanup(pw.stop)
        cls.browser = pw.chromium.launch(headless=True, **browser.launch_options())
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.context = self.browser.new_context(service_workers='block')
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.marks = []

    def show(self, body):
        html = '<meta charset="utf-8"><script>window.clicks=0</script>' + body
        self.page.route('**/*', lambda r: r.fulfill(body=html, content_type='text/html; charset=utf-8')
                        if r.request.url == reward.URL else r.abort())
        self.page.goto(reward.URL)

    def test_masked_key_list_confirms_only_the_dubbing_account(self):
        self.page.route('**/*', lambda r: r.fulfill(body='<table><tr><td>bb_fixture28••••</td></tr></table>',
                                                    content_type='text/html; charset=utf-8'))
        self.page.goto(reward.URL + '/api-keys')
        reward.match_dedicated_account(self.page, 'bb_fixture28', timeout=1)
        for other in ('bb_fixture', 'bb_wrong12'):
            with self.subTest(prefix=other), self.assertRaisesRegex(ValueError, '未能匹配'):
                reward.match_dedicated_account(self.page, other, timeout=.3)

    def test_account_lookup_requires_the_official_key_list(self):
        self.show('<table><tr><td>bb_fixture28</td></tr></table>')
        with self.assertRaises(reward.LoginRequired):
            reward.match_dedicated_account(self.page, 'bb_fixture28')

    def run_page(self, read_only=False):
        return reward.claim_on_page(self.page, None if read_only else lambda: self.marks.append(True),
                                    offer_timeout=1.7, feedback_timeout=.9)

    def test_screenshot_member_card_redeems_and_header_stays_untouched(self):
        self.show('''<button onclick="window.wrong=true">领取每日积分</button>
            <section><p>+400 积分</p><b>Discord 成员</b><p>每天回来一次，保持余额充足</p>
            <button onclick="window.clicks++;this.parentNode.innerHTML='400 积分已到账'">立即领取</button></section>''')
        self.assertEqual(self.run_page(), 'acknowledged')
        self.assertEqual(self.page.evaluate('window.clicks'), 1)
        self.assertFalse(self.page.evaluate('Boolean(window.wrong)'))
        self.assertEqual(self.marks, [True])

    def test_two_tiers_with_active_button_and_timer_never_clicks(self):
        self.show('''<div role="dialog">每日奖励 <p>+400 积分，加入我们的 Discord 社区</p>
            <p>+100 积分，不加入 Discord 的标准每日奖励</p>
            <button onclick="window.clicks++">立即领取</button>20h 53m 后可领取下一轮</div>''')
        with self.assertRaisesRegex(reward.RewardAmountUnclear, '400、100'):
            self.run_page()
        self.assertEqual(self.marks, [])
        self.assertEqual(self.page.evaluate('window.clicks'), 0)

    def test_personal_offer_can_resolve_the_generic_welcome_dialog(self):
        self.show('''<div role="dialog">每日奖励 +400 积分 / +100 积分
            <button onclick="this.parentNode.remove()">关闭每日奖励</button>
            <button onclick="window.wrong=true">立即领取</button></div>
            <button onclick="document.querySelector('section').hidden=false">修改资料</button>
            <section hidden>领取每日积分 +400 积分 <b>Discord 成员</b>
            <button onclick="window.clicks++;this.parentNode.innerHTML='领取成功'">立即领取</button></section>''')
        self.assertEqual(self.run_page(), 'acknowledged')
        self.assertFalse(self.page.evaluate('Boolean(window.wrong)'))
        self.assertEqual(self.page.evaluate('window.clicks'), 1)

    def test_no_response_does_not_become_success_and_click_is_not_repeated(self):
        self.show('''<div role="dialog">每日奖励 +400 积分 <b>Discord 成员</b>
            <button onclick="window.clicks++">立即领取</button><p>20h 53m 后可领取下一轮</p></div>''')
        self.assertEqual(self.run_page(), 'unconfirmed')
        self.assertEqual(self.page.evaluate('window.clicks'), 1)

    def test_new_explicit_confirmation_completes_once(self):
        self.show('''<div role="dialog">每日奖励 +400 积分 <b>Discord 成员</b>
            <button onclick="window.clicks++;this.hidden=true;this.nextElementSibling.hidden=false">立即领取</button>
            <button hidden onclick="window.clicks++;this.parentNode.innerHTML='今日已领取'">确认领取</button></div>''')
        self.assertEqual(self.run_page(), 'acknowledged')
        self.assertEqual(self.page.evaluate('window.clicks'), 2)
        self.assertEqual(self.marks, [True])

    def test_advertised_400_alone_does_not_prove_personal_eligibility(self):
        self.show('''<div role="dialog">每日奖励 +400 积分
            <button onclick="window.clicks++">立即领取</button></div>''')
        with self.assertRaises(reward.RewardAmountUnclear):
            self.run_page()
        self.assertEqual(self.page.evaluate('window.clicks'), 0)

    def test_new_confirmation_with_a_different_amount_is_never_accepted(self):
        self.show('''<div role="dialog">每日奖励 <b>Discord 成员</b><span>+400 积分</span>
            <button onclick="window.clicks++;this.hidden=true;this.previousElementSibling.textContent='+100 积分';this.nextElementSibling.hidden=false">立即领取</button>
            <button hidden onclick="window.clicks++">确认领取</button></div>''')
        with self.assertRaisesRegex(reward.RewardAmountUnclear, '100'):
            self.run_page()
        self.assertEqual(self.page.evaluate('window.clicks'), 1)

    def test_button_explicitly_offering_400_is_enough(self):
        self.show('''<div role="dialog">每日奖励
            <button onclick="window.clicks++;this.parentNode.innerHTML='400 积分已到账'">领取 400 积分</button></div>''')
        self.assertEqual(self.run_page(), 'acknowledged')
        self.assertEqual(self.page.evaluate('window.clicks'), 1)

    def test_lower_amount_never_spends_the_daily_opportunity(self):
        self.show('''<section>每日奖励 +100 积分 <button onclick="window.clicks++">立即领取</button></section>''')
        with self.assertRaisesRegex(reward.RewardAmountUnclear, '100'):
            self.run_page()
        self.assertEqual(self.marks, [])

    def test_unrelated_coupon_claim_is_never_clicked(self):
        self.show('''<section>新手优惠券 +400 积分<button onclick="window.clicks++">立即领取</button></section>''')
        with self.assertRaisesRegex(ValueError, '没有显示可用'):
            self.run_page()
        self.assertEqual(self.page.evaluate('window.clicks'), 0)

    def test_visible_human_verification_stops_before_click(self):
        self.show('''<div role="dialog">每日奖励 +400 积分 <b>Discord 成员</b>
            <button onclick="window.clicks++">立即领取</button><p>验证您是真人</p></div>''')
        with self.assertRaises(reward.HumanVerificationRequired):
            self.run_page()
        self.assertEqual(self.marks, [])

    def test_provider_rejection_is_reported(self):
        self.show('''<div role="dialog">每日奖励 +400 积分 <b>Discord 成员</b>
            <button onclick="window.clicks++;this.parentNode.innerHTML='领取失败，请稍后再试'">立即领取</button></div>''')
        with self.assertRaisesRegex(ValueError, '领取失败'):
            self.run_page()

    def test_read_only_inspection_does_not_click(self):
        self.show('''<div role="dialog">每日奖励 +400 积分 <b>Discord 成员</b>
            <button onclick="window.clicks++">立即领取</button></div>''')
        self.assertEqual(self.run_page(read_only=True), 'available')
        self.assertEqual(self.page.evaluate('window.clicks'), 0)

