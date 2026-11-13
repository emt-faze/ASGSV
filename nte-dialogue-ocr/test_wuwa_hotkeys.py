import ctypes
import sys
import unittest
from ctypes import wintypes
from unittest.mock import Mock

from PySide6 import QtCore, QtWidgets

from wuwa_hotkeys import PlaybackHotkeys, ReplayPresses


class ReplayPressTests(unittest.TestCase):
    def test_native_before_and_after_poll_each_replay_once(self):
        key=ReplayPresses()
        self.assertFalse(key.sample(False,1000))
        self.assertTrue(key.native(1006))
        self.assertFalse(key.sample(True,1016))
        self.assertFalse(key.sample(False,1032))
        self.assertTrue(key.sample(True,1048))
        self.assertFalse(key.native(1045))

    def test_game_only_edges_include_no_repeat_and_timer_wrap(self):
        key=ReplayPresses()
        key.sample(False,0xfffffff0)
        self.assertTrue(key.sample(True,2))
        self.assertFalse(key.sample(True,18))
        self.assertFalse(key.native(0xfffffffa))
        key.sample(False,34)
        self.assertTrue(key.sample(True,50))

    def test_entering_game_with_key_held_does_not_replay(self):
        key=ReplayPresses()
        self.assertFalse(key.sample(True,1000))
        self.assertFalse(key.sample(True,1016))
        key.sample(False,1032)
        self.assertTrue(key.sample(True,1048))


@unittest.skipUnless(sys.platform == 'win32', 'Windows hotkey messages')
class NativeHotkeyTests(unittest.TestCase):
    def test_replay_message_dispatches_only_to_registered_window(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        owner = QtWidgets.QDialog()
        replay = Mock()
        listener = PlaybackHotkeys(owner, {'pause':'F22','replay':'F23','skip':'F24'},
                                  {'pause':Mock(), 'replay':replay, 'skip':Mock()})
        try:
            if 0x4101 not in listener.registered:
                self.skipTest('Windows test hotkey unavailable')
            message = wintypes.MSG()
            message.message, message.wParam = 0x312, 0x4101
            pointer = ctypes.addressof(message)
            listener.nativeEventFilter(b'windows_generic_MSG', pointer)
            replay.assert_not_called()
            message.hWnd = listener.hwnd
            listener.nativeEventFilter(b'windows_generic_MSG', pointer)
            replay.assert_called_once_with()
        finally:
            listener.close()
            owner.close()
