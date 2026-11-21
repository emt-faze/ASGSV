"""Use the official daily gift button in a separate, per-user Google Chrome profile."""
import importlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from PySide6.QtCore import QLockFile

from wuwa_claim_components import DIRECTORY, DRIVER, fetch_wheels, verified_wheels

URL = 'https://breezeblue.ai/zh/app/developer'


def home():
    return Path(os.environ['LOCALAPPDATA']) / 'ASGSV'


def profile():
    return home() / 'BreezeClaimChrome'


def chrome():
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe') as key:
                path = Path(winreg.QueryValueEx(key, '')[0].strip('"'))
            if path.is_file() and path.name.lower() == 'chrome.exe':
                return path
        except FileNotFoundError:
            continue
    for var in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        if os.environ.get(var):
            path = Path(os.environ[var]) / 'Google/Chrome/Application/chrome.exe'
            if path.is_file():
                return path
    raise ValueError('未找到 Google Chrome，请从 https://www.google.com/chrome/ 安装后再点一键开启。')


class LoginRequired(ValueError):
    """Interactive sign-in or verification is needed in the dedicated browser."""


def launch_login(url=URL):
    profile().mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([str(chrome()), '--user-data-dir=' + str(profile()), '--no-first-run',
                      '--no-default-browser-check', '--disable-background-mode', '--app=' + url])


def open_login(*, keys=False):
    launch_login(URL + '/api-keys' if keys else URL)
    if keys:
        return '请创建或复制自己的 API Key，关闭该专用窗口，在本页粘贴后点击一键开启。'
    return '请在专用 Chrome 登录与配音密钥相同的账号，看到开发者控制台后关闭这个专用窗口。'


def wait_for_login():
    """Watch only the native sign-in window we opened; never inspect browser storage."""
    import win32con
    import win32gui
    import win32process

    proc = launch_login()
    deadline = time.monotonic() + 600
    ready_since = None
    while proc.poll() is None and time.monotonic() < deadline:
        ready = []

        def inspect(hwnd, _):
            if win32process.GetWindowThreadProcessId(hwnd)[1] != proc.pid or not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if re.fullmatch(r'(?:开发者|Developer)\s*[|·-]\s*Breeze\s*Blue(?:\s*[-–]\s*Google Chrome)?', title, re.I):
                ready.append(hwnd)

        win32gui.EnumWindows(inspect, None)
        if ready:
            if ready_since is None:
                ready_since = time.monotonic()
            elif time.monotonic() - ready_since >= 2:
                win32gui.PostMessage(ready[0], win32con.WM_CLOSE, 0, 0)
                break
        else:
            ready_since = None
        time.sleep(.4)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise LoginRequired('专用窗口仍在等待登录。完成登录后关闭该窗口，再点「立即领取 / 核对到账」。') from exc
    website_claim()  # Confirm the logged-in page before reporting success.


def driver_path():
    return home() / 'claim-tools' / f'playwright-{DRIVER}-py{sys.version_info.major}{sys.version_info.minor}'


def browser_driver():
    folder = driver_path()
    if folder.is_dir() and str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    try:
        return importlib.import_module('playwright.sync_api')
    except ImportError as exc:
        raise ValueError('领取组件未就绪，请点击「保存时间并启用」安装。') from exc


def prepare_browser():
    chrome()
    try:
        browser_driver()
        return
    except ValueError:
        folder = driver_path()
    folder.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(folder) + '.lock')
    if not lock.tryLock(0):
        raise ValueError('领取组件正在安装，请稍后重试。')
    try:
        if folder.exists():
            raise ValueError('领取组件无法加载，请检查该目录下的 Playwright，以及安全软件的隔离记录。')
        with tempfile.TemporaryDirectory(dir=folder.parent) as tmp:
            staging = Path(tmp) / 'package'
            python = Path(sys.executable).with_name('python.exe')
            bundled = Path(__file__).resolve().parents[1] / DIRECTORY
            wheels = verified_wheels(bundled) if bundled.exists() else fetch_wheels(python, Path(tmp) / 'wheels')
            proc = subprocess.run([str(python), '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
                                   '--no-index', '--no-deps', '--no-compile', '--target', str(staging), *map(str, wheels)],
                                  capture_output=True, timeout=240, creationflags=subprocess.CREATE_NO_WINDOW)
            if proc.returncode:
                errors = proc.stderr.decode('utf8', 'replace').splitlines()
                detail = next((line for line in reversed(errors) if 'ERROR:' in line), '')
                raise ValueError('领取组件安装失败。' + detail[:180])
            staging.rename(folder)
        importlib.invalidate_caches()
        browser_driver()
    finally:
        lock.unlock()


GIFT = re.compile(
    r'^(?:领取(?:每日|今日|每天)\s*(?:免费|赠送)?\s*(?:400\s*(?:个)?\s*)?(?:积分|点数|额度|奖励|credits?)|'
    r'(?:每日|今日|每天)\s*(?:免费)?(?:领取|签到)(?:\s*[+＋]?\s*400\s*(?:积分|credits?)?)?|'
    r'claim (?:your )?(?:(?:daily|free|400) )+(?:credits?|reward|gift|bonus))$', re.I)
DONE = re.compile(r'(?:今日已领取|今天已领取|今日已签到|今日已领完|already claimed today|claimed today)', re.I)
SHORT = re.compile(r'^(?:领取|立即领取|签到|领取\s*(?:免费|赠送)?\s*(?:400\s*(?:个)?\s*)?(?:积分|点数|额度|奖励|credits?)|claim(?: now)?)$', re.I)
GIFT_AREA = re.compile(r'(?=.*(?:每日|每天|今日|daily))(?=.*(?:400|赠送积分|免费积分|free credits))', re.I | re.S)


class AmbiguousGift(ValueError):
    """The page has not presented a unique daily offer yet."""


def gift_button(page):
    """Short labels such as '领取' must belong to a visible daily credits offer."""
    primary = page.get_by_role('button', name=re.compile(r'^(?:领取每日积分|claim daily credits)$', re.I))
    if primary.count() == 1 and primary.is_visible():
        return primary
    buttons = page.get_by_role('button', name=GIFT)
    matches = [b for b in buttons.all() if b.is_visible()]
    for button in page.get_by_role('button', name=SHORT).all():
        if not button.is_visible():
            continue
        context = button
        for _ in range(4):
            context = context.locator('xpath=parent::*[not(self::html or self::body)]')
            if not context.count():
                break
            summary = context.inner_text(timeout=1500)
            if len(summary) > 520:
                break
            if GIFT_AREA.search(summary) and context.get_by_role('button', name=SHORT).count() == 1:
                matches.append(button)
                break
    if len(matches) > 1:
        raise AmbiguousGift('网页上有多个领取入口，请在专用 Chrome 核对当日赠送。')
    return matches[0] if matches else None


def cooling_down(page):
    caption = page.get_by_text(re.compile(r'^(下次领取倒计时|next claim in)$', re.I))
    if not caption.count() or not caption.first.is_visible():
        return False
    duration = caption.first.locator('..').inner_text(timeout=1500)
    return any(int(n) > 0 for n in re.findall(r'(\d+)\s*(?:[hms]\b|小时|分钟|秒)', duration, re.I))


def website_claim(before_click=None):
    if not (profile() / 'Default').is_dir():
        raise LoginRequired('请先登录 BreezeBlue 的专用 Chrome 窗口。')
    driver = browser_driver()
    with driver.sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(str(profile()), channel='chrome',
                      executable_path=str(chrome()), headless=True, chromium_sandbox=True,
                      args=['--disable-background-mode'], timeout=30000)
        except driver.Error as exc:
            raise ValueError('专用 Chrome 未能启动。请关闭专用登录窗口后重试。') from exc
        try:
            page = ctx.new_page()
            reply = page.goto(URL, wait_until='domcontentloaded', timeout=45000)
            if reply and reply.status >= 400:
                raise ValueError(f'开发者页面返回 HTTP {reply.status}，请在专用 Chrome 检查连接或登录状态。')
            menu_opened = False
            ambiguous = False
            ready_since = None
            until = time.monotonic() + 20
            while time.monotonic() < until:
                url = urlsplit(page.url)
                if url.hostname != 'breezeblue.ai' or '/auth/' in url.path or '/login' in url.path:
                    raise LoginRequired('专用账户需要重新登录，请打开专用 Chrome。')
                if page.get_by_text(re.compile(r'验证您是人类|验证您是真人|verify you are human', re.I)).first.is_visible():
                    raise LoginRequired('网站要求人工验证，请在专用 Chrome 完成。')
                if page.get_by_text(DONE).first.is_visible() or cooling_down(page):
                    return 'already'
                try:
                    button = gift_button(page)
                    ambiguous = False
                except AmbiguousGift:
                    ambiguous = True
                    ready_since = None
                    page.wait_for_timeout(300)
                    continue
                if button is None and not menu_opened:
                    avatar = page.get_by_role('button', name=re.compile(r'^(修改资料|edit profile)$', re.I))
                    if avatar.count() == 1 and avatar.is_visible():
                        avatar.click(timeout=3500)
                        menu_opened = True
                        continue
                if button is not None and button.is_enabled():
                    if ready_since is None:
                        ready_since = time.monotonic()
                    if time.monotonic() - ready_since < 1.2:
                        page.wait_for_timeout(250)
                        continue
                    if before_click is None:
                        return 'available'
                    button.click(trial=True, timeout=3500)
                    before_click()
                    button.click(timeout=3500)
                    # Some site versions open a separate daily-gift confirmation.
                    confirmed = False
                    deadline = time.monotonic() + 6
                    while time.monotonic() < deadline:
                        if page.get_by_text(DONE).first.is_visible() or cooling_down(page):
                            break
                        if not confirmed:
                            dialog = page.get_by_role('dialog').filter(has_text=GIFT_AREA)
                            if dialog.count() == 1 and dialog.is_visible():
                                final = gift_button(dialog)
                                if final is not None and final.is_enabled():
                                    final.click(timeout=3500)
                                    confirmed = True
                        page.wait_for_timeout(250)
                    return 'submitted'
                ready_since = None
                page.wait_for_timeout(400)
            if ambiguous:
                raise AmbiguousGift('网页持续显示多个领取入口，请在专用 Chrome 核对。')
            raise ValueError('未找到可领取的每日赠送按钮。请在专用 Chrome 检查页面或登录状态。')
        except driver.Error as exc:
            detail = str(exc).splitlines()[0][:150]
            raise ValueError('无法完成网页领取 · ' + detail) from exc
        finally:
            ctx.close()
