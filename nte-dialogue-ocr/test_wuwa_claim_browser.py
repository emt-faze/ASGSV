from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import wuwa_claim_browser as browser


class BrowserSelectionTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.enterContext(patch.object(browser, 'home', return_value=self.root))
        self.system = {kind: browser.ClaimBrowser(kind, self.root / exe)
                       for kind, exe in [('chrome', 'chrome.exe'), ('msedge', 'msedge.exe')]}
        self.enterContext(patch.object(browser, 'installed_browser', side_effect=self.system.get))
        self.enterContext(patch.object(browser, 'default_system_browser', return_value='msedge'))
        self.managed = self.enterContext(patch.object(browser, 'managed_browser', return_value=None))

    def test_legacy_chrome_login_wins_over_default_edge(self):
        (self.root / 'BreezeClaimChrome/Default').mkdir(parents=True)
        selected = browser.select_browser(install=True)
        self.assertEqual(selected.kind, 'chrome')
        self.assertEqual(selected.profile, self.root / 'BreezeClaimChrome')
        self.managed.assert_not_called()

    def test_remember_edge_even_if_default_changes(self):
        self.assertEqual(browser.select_browser().kind, 'msedge')
        with patch.object(browser, 'default_system_browser', return_value='chrome'):
            self.assertEqual(browser.select_browser().kind, 'msedge')
        self.managed.assert_not_called()

    def test_removed_browser_uses_separate_profile(self):
        edge = browser.select_browser()
        del self.system['msedge']
        chrome = browser.select_browser(install=True)
        self.assertEqual(chrome.kind, 'chrome')
        self.assertNotEqual(chrome.profile, edge.profile)
        self.managed.assert_not_called()

    def test_private_download_requires_user_initiated_action(self):
        self.system.clear()
        with self.assertRaises(browser.LoginRequired):
            browser.select_browser()
        self.managed.assert_called_once_with(False)
        installed = browser.ClaimBrowser('chromium', self.root/'managed/chrome.exe')
        self.managed.return_value = installed
        self.assertEqual(browser.select_browser(install=True), installed)
        self.managed.assert_called_with(True)
        self.assertEqual(browser.select_browser(), installed)
        self.managed.assert_called_with(False)
