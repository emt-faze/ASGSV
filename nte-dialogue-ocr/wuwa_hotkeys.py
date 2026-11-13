"""Window-owned, no-repeat Windows hotkeys; registrations die with the dialog."""
import ctypes
from ctypes import wintypes
from collections import deque
from PySide6 import QtCore


class ReplayPresses:
    """Pair WM_HOTKEY timestamps with sampled down edges of the same key."""
    def __init__(self):
        self.last_sample = None
        self.down = False
        self.native_times = deque(maxlen=8)
        self.sampled_edges = deque(maxlen=8)

    @staticmethod
    def overlaps(tick, edge):
        start, end = edge
        # MSG.time and GetTickCount use the same wrapping millisecond clock.
        return ((tick-start+16) & 0xffffffff) <= ((end-start) & 0xffffffff)+32

    def sample(self, down, tick):
        previous = self.last_sample
        rising = previous is not None and down and not self.down
        self.down, self.last_sample = down, tick
        if not rising:
            return False
        edge = (previous,tick)
        for stamp in reversed(self.native_times):
            if self.overlaps(stamp,edge):
                self.native_times.remove(stamp)
                return False
        self.sampled_edges.append(edge)
        return True

    def native(self, tick):
        for edge in reversed(self.sampled_edges):
            if self.overlaps(tick,edge):
                self.sampled_edges.remove(edge)
                return False
        self.native_times.append(tick)
        return True


class PlaybackHotkeys(QtCore.QAbstractNativeEventFilter):
    def __init__(self, owner, settings, callbacks, *, game_pid=None, on_replay=None):
        super().__init__()
        self.owner, self.registered, self.errors = owner, {}, []
        self.hwnd = int(owner.winId())
        self.game_pid = game_pid
        self.on_replay = on_replay
        self.presses = ReplayPresses()
        self.replay_key = None
        self.timer = None
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        for ident, (action, default) in enumerate((('pause', 'F8'), ('replay', 'F9'), ('skip', 'F10')), 0x4100):
            key = settings.get(action, default).upper()
            if not key.startswith('F') or not key[1:].isdigit() or not 1 <= int(key[1:]) <= 24:
                self.errors.append(key)
                continue
            if self.user32.RegisterHotKey(self.hwnd, ident, 0x4000, 0x70 + int(key[1:]) - 1):
                self.registered[ident] = callbacks[action]
            else:
                self.errors.append(key)
            if action=='replay':
                self.replay_key = 0x70 + int(key[1:]) - 1
        if game_pid is not None and on_replay is not None and self.replay_key is not None:
            self.user32.GetForegroundWindow.restype = wintypes.HWND
            self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
            self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            self.user32.GetAsyncKeyState.restype = ctypes.c_short
            self.tick = ctypes.WinDLL('kernel32').GetTickCount
            self.tick.restype = wintypes.DWORD
            self.timer = QtCore.QTimer(owner)
            self.timer.setTimerType(QtCore.Qt.PreciseTimer)
            self.timer.timeout.connect(self.poll_game_replay)
            self.timer.start(16)
        QtCore.QCoreApplication.instance().installNativeEventFilter(self)
        owner.finished.connect(self.close)

    def nativeEventFilter(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x312 and msg.hWnd == self.hwnd and msg.wParam in self.registered:
            if msg.wParam==0x4101 and self.on_replay is not None:
                if self.presses.native(msg.time):
                    self.on_replay('windows_hotkey')
            else:
                self.registered[msg.wParam]()
            return True, 0
        return False, 0

    def poll_game_replay(self):
        pid = self.game_pid()
        foreground = wintypes.DWORD()
        if pid:
            self.user32.GetWindowThreadProcessId(self.user32.GetForegroundWindow(),ctypes.byref(foreground))
        if not pid or foreground.value!=pid:
            self.presses.last_sample = None
            return
        down = bool(self.user32.GetAsyncKeyState(self.replay_key) & 0x8000)
        if self.presses.sample(down,self.tick()):
            self.on_replay('game_function_key')

    def close(self, *_):
        if self.timer:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None
        for ident in self.registered:
            self.user32.UnregisterHotKey(self.hwnd, ident)
        self.registered.clear()
        QtCore.QCoreApplication.instance().removeNativeEventFilter(self)
