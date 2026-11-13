"""Conservative WuWa startup: authenticated window, fresh OCR, foreground only.

Unknown screens deliberately require the player. No quest or movement input.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import time

import numpy as np

from dialogue_generation import atomic_text


def classify_screen(detections, size, launcher=False):
    width, height = size
    labels = []
    for points, raw, confidence in detections or []:
        points = np.asarray(points, dtype=float)
        if points.shape != (4, 2) or not np.isfinite(points).all() or float(confidence) < .9:
            continue
        label = re.sub(r'\s+', '', str(raw))
        x, y = points.mean(axis=0)
        if not (0 <= x < width and 0 <= y < height):
            continue
        labels.append((label, float(confidence), [float(x), float(y)]))
    blocked = ('验证码', '登录', '短信验证', '同意并', '选择服务器', '确认服务器', '选择区服', '用户协议', '隐私政策',
               '开始更新', '立即更新', '更新游戏', '下载游戏', '正在更新', '正在下载', '重新连接', '网络错误')
    if any(any(word in label for word in blocked) for label, _, _ in labels):
        return dict(state='takeover', reason='登录、更新、验证或选服页面，需要手动处理')
    if any(word in label for label, _, _ in labels for word in ('正在加载', '加载中')):
        return dict(state='loading')
    matches = []
    for label, confidence, (x, y) in labels:
        if confidence < .96:
            continue
        if launcher and label in {'开始游戏', '启动游戏'} and .6*width < x < .97*width and .68*height < y < .96*height:
            matches.append(dict(state='launcher_ready', center=[x, y], label=label))
        if not launcher and label in {'点击进入游戏', '点击进入', '点击开始游戏'} and .25*width < x < .75*width and .60*height < y < .95*height:
            matches.append(dict(state='entry_ready', center=[x, y], label=label))
    if len(matches) == 1:
        return matches[0]
    return dict(state='unknown', reason='页面未可靠识别；未发送点击')


def foreground_click(window, center, size, captured_at, stop):
    import ctypes
    from ctypes import wintypes
    import win32api
    import win32gui
    from capture_windows import _assert_window_identity
    if stop.is_set() or time.monotonic() - captured_at > 1.5:
        return False
    _assert_window_identity(window)
    if win32gui.GetForegroundWindow() != window.hwnd or win32gui.IsIconic(window.hwnd):
        return False
    _, _, width, height = win32gui.GetClientRect(window.hwnd)
    if tuple(size) != (width, height):
        return False
    x, y = center
    if not (0 < x < width and 0 < y < height):
        return False
    win32api.SetCursorPos(win32gui.ClientToScreen(window.hwnd, (int(x), int(y))))
    if stop.is_set() or win32gui.GetForegroundWindow() != window.hwnd:
        return False
    class Mouse(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('data', wintypes.DWORD),
                    ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class Union(ctypes.Union):
        _fields_ = [('mouse', Mouse)]
    class Input(ctypes.Structure):
        _fields_ = [('type', wintypes.DWORD), ('value', Union)]
    events = (Input * 2)()
    events[0].value.mouse.flags, events[1].value.mouse.flags = 2, 4
    send = ctypes.windll.user32.SendInput
    send.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    send.restype = wintypes.UINT
    count = send(2, events, ctypes.sizeof(Input))
    if count == 1:
        release = Input()
        release.value.mouse.flags = 4
        send(1, ctypes.byref(release), ctypes.sizeof(Input))
    return count == 2


def launch_and_monitor(config, root, stop, progress, timeout=120):
    import win32gui
    from capture_windows import list_game_windows
    from capture_session import snapshot
    from ocr_engines import RapidModel
    from wuwa_resources import inventory
    inventory(config['install_root'], root)
    root = Path(root)
    windows = lambda: list_game_windows(game_id='wuwa', install_root=config['install_root'])
    if not windows():
        launcher = Path(config['install_root']) / 'launcher.exe'
        if not launcher.is_file():
            raise ValueError('官方启动器不存在')
        subprocess.Popen([str(launcher)], cwd=str(launcher.parent))
        progress('官方启动器已启动；等待已验证的前台窗口')
    engine = RapidModel('small')
    began, previous, count, clicks = time.monotonic(), None, 0, 0
    report = []
    try:
        while not stop.wait(.4) and time.monotonic() - began < timeout:
            candidates = windows()
            if not candidates:
                continue
            window = candidates[0]
            if win32gui.GetForegroundWindow() != window.hwnd:
                progress('等待鸣潮窗口处于前台；未发送输入')
                continue
            frame = snapshot(window, backend='wgc')
            captured = time.monotonic()
            detections, _ = engine(frame, use_cls=False)
            size = (frame.shape[1], frame.shape[0])
            result = classify_screen(detections, size, window.is_launcher)
            if result['state'] == 'unknown' and not window.is_launcher:
                from wuwa_scene import dialogue_layout
                if dialogue_layout(frame, root):
                    result = dict(state='dialogue_ready')
            report.append(dict(elapsed=time.monotonic()-began, window=window.process_path, **result))
            state = result['state']
            if state == 'dialogue_ready':
                key = (window.hwnd,window.pid,state)
                count = count+1 if previous == key else 1
                previous = key
                if count >= 3:
                    return '已识别游戏内对话界面，启动操作结束；未操作角色或剧情选项'
                continue
            if state in {'unknown', 'takeover'}:
                progress(result['reason'])
                # No automatic screenshot retention of login/account pages.
                return result['reason'] + '；启动操作已暂停，可手动进入世界后校准字幕'
            if state == 'loading':
                previous, count = None, 0
                continue
            key = (window.hwnd, window.pid, state, tuple(round(v / 6) for v in result['center']))
            count = count + 1 if key == previous else 1
            previous = key
            if count >= 3:
                if clicks >= 2:
                    return '已达到启动点击上限，请手动接管'
                if foreground_click(window, result['center'], size, captured, stop):
                    clicks += 1
                    progress('已点击 ' + result['label'] + '；等待画面变化')
                    previous, count = None, 0
                    stop.wait(3)
        return '启动已取消' if stop.is_set() else '启动等待超时，请手动接管'
    finally:
        atomic_text(root / 'launch-last.json', json.dumps(dict(events=report, clicks=clicks, world_ready_verified=False), ensure_ascii=False, indent=2))
