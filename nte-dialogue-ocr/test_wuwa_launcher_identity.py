import unittest
from game_profiles import wuwa_window_kind


class LauncherIdentity(unittest.TestCase):
    def test_versioned_official_launcher(self):
        cls = 'HwndWrapper[launcher_main;;ceb0ff1a-ace6-4e9f-86d9-db80684880ce]'
        root = r'F:\Wuthering Waves'
        self.assertEqual(wuwa_window_kind(root + r'\3.0.1.0\launcher_main.exe', cls, root), 'launcher')
        for path in (r'C:\Other\3.0.1.0\launcher_main.exe', r'F:\Other\3.0.1.0\launcher_main.exe', root + r'\x\3.0.1.0\launcher_main.exe',
                     root + r'\3.0.1.0\launcher_updater.exe', root + r'\launcher_main.exe'):
            self.assertIsNone(wuwa_window_kind(path, cls, root))
        self.assertIsNone(wuwa_window_kind(root + r'\3.0.1.0\launcher_main.exe', 'UnrealWindow', root))


if __name__ == '__main__':
    unittest.main()
