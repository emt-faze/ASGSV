"""Use the official daily gift button in a dedicated, per-user browser profile."""
from dataclasses import dataclass
from decimal import Decimal
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


@dataclass(frozen=True)
class ClaimBrowser:
    kind: str
    executable: Path

    @property
    def label(self):
        return {'chrome': 'Chrome', 'msedge': 'Edge', 'chromium': 'ASGSV 专用 Chromium'}[self.kind]

    @property
    def profile(self):
        folders = {'chrome': 'BreezeClaimChrome', 'msedge': 'BreezeClaimEdge', 'chromium': 'BreezeClaimChromium'}
        return home() / folders[self.kind]

    def launch_options(self):
        return dict(channel=self.kind, executable_path=str(self.executable), chromium_sandbox=True,
                    args=['--disable-background-mode'])


def installed_browser(kind):
    import winreg
    relative = {'chrome': 'Google/Chrome/Application/chrome.exe',
                'msedge': 'Microsoft/Edge/Application/msedge.exe'}[kind]
    name = Path(relative).name
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + name,
                                    0, winreg.KEY_READ | view) as key:
                    value = winreg.QueryValueEx(key, '')[0]
            except (FileNotFoundError, PermissionError):
                continue
            path = Path(os.path.expandvars(value.strip('"')))
            if path.name.lower() == name and path.is_file():
                return ClaimBrowser(kind, path)
    for var in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        if os.environ.get(var):
            path = Path(os.environ[var]) / relative
            if path.is_file():
                return ClaimBrowser(kind, path)
    return None


def default_system_browser():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice') as key:
            progid = winreg.QueryValueEx(key, 'ProgId')[0]
    except (FileNotFoundError, PermissionError):
        return 'chrome'
    return 'msedge' if progid.casefold().startswith('msedgehtm') else 'chrome'


def managed_browser(install):
    if not install and not driver_path().is_dir():
        return None
    ensure_driver()
    with browser_driver().sync_playwright() as pw:
        default = Path(pw.chromium.executable_path)
    # Keep Playwright's platform-specific executable layout inside our own cache.
    revision = next((p for p in default.parents if re.fullmatch(r'chromium-\d+', p.name)), None)
    if revision is None:
        raise ValueError('领取组件未能识别配套的 Chromium 版本，请更新工作台。')
    cache = home() / 'claim-tools' / 'browsers'
    directory = cache / revision.name
    executable = directory / default.relative_to(revision)
    def ready():
        return executable.is_file() and (directory / 'INSTALLATION_COMPLETE').is_file()
    if not ready():
        if not install:
            return None
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        env = dict(get_driver_env(), PLAYWRIGHT_BROWSERS_PATH=str(cache))
        try:
            proc = subprocess.run([*compute_driver_executable(), 'install', '--no-shell', 'chromium'],
                                  env=env, capture_output=True, timeout=600, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('专用 Chromium 下载超时，请检查网络并重试。') from exc
        if proc.returncode or not ready():
            output = (proc.stdout + proc.stderr).decode('utf8', 'replace')
            code = re.search(r'(?:status(?: code)?[ :]+|server returned code )(\d{3})', output, re.I)
            detail = 'HTTP ' + code[1] if code else f'退出码 {proc.returncode}'
            raise ValueError(f'专用 Chromium 安装未完成（{detail}）。请检查网络后重试。')
    return ClaimBrowser('chromium', executable)


def select_browser(*, install=False):
    choice = home() / 'claim-state' / 'browser.txt'
    try:
        previous = choice.read_text('ascii').strip()
    except FileNotFoundError:
        previous = 'chrome' if (home() / 'BreezeClaimChrome' / 'Default').is_dir() else ''
    order = dict.fromkeys((previous, default_system_browser(), 'chrome', 'msedge'))
    selected = None
    for kind in order:
        if kind in ('chrome', 'msedge'):
            selected = installed_browser(kind)
        elif kind == 'chromium':
            selected = managed_browser(False)
        if selected:
            break
    if not selected:
        selected = managed_browser(install)
    if not selected:
        raise LoginRequired('需要准备专用浏览器，请在「每日领积分」点一键开启。')
    if previous != selected.kind or not choice.is_file():
        choice.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='ascii', dir=choice.parent, delete=False) as stream:
            stream.write(selected.kind)
            temp = Path(stream.name)
        try:
            os.replace(temp, choice)
        finally:
            temp.unlink(missing_ok=True)
    return selected


class LoginRequired(ValueError):
    """Interactive sign-in or verification is needed in the dedicated browser."""


def launch_login(url=URL):
    browser = select_browser(install=True)
    browser.profile.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([str(browser.executable), '--user-data-dir=' + str(browser.profile), '--no-first-run',
                      '--no-default-browser-check', '--disable-background-mode', '--app=' + url])


def open_login(*, keys=False):
    launch_login(URL + '/api-keys' if keys else URL)
    if keys:
        return '请创建或复制自己的 API Key，关闭该专用窗口，在本页粘贴后点击一键开启。'
    return '请在专用领取页查看「每日奖励」，按官网提示登录或完成验证。关闭该窗口后可核对到账。'


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
            if re.fullmatch(r'(?:开发者|Developer)\s*[|·-]\s*Breeze\s*Blue(?:\s*[-–]\s*(?:Google Chrome(?: for Testing)?|Microsoft Edge|Chromium))?', title, re.I):
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
    # The caller checks the ledger and the signed-in page after this window closes.


def driver_path():
    return home() / 'claim-tools' / f'playwright-{DRIVER}-py{sys.version_info.major}{sys.version_info.minor}'


def browser_driver():
    folder = driver_path()
    if not folder.is_dir():
        raise ValueError('领取组件尚未安装。')
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    try:
        return importlib.import_module('playwright.sync_api')
    except ImportError as exc:
        raise ValueError('领取组件未就绪，请点击「一键开启每日领取」安装。') from exc


def prepare_browser():
    ensure_driver()
    return select_browser(install=True)


def ensure_driver():
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


# The profile entry opens an offer. Only the redemption button inside that
# offer starts an attempt; other credits and purchases share this page.
OFFER = re.compile(r'(?=.*(?:每日|今日|每天|daily))(?=.*(?:积分|credits?|奖励|reward))', re.I | re.S)
PROFILE = re.compile(r'^(?:修改资料|edit profile)$', re.I)
ENTRY = re.compile(r'^(?:领取每日积分|claim daily credits)$', re.I)
CONFIRM = re.compile(r'^(?:确认领取|确定领取|confirm claim|confirm redemption)$', re.I)
REDEEM = re.compile(r'^(?:立即领取|确认领取|确定领取|领取|签到|(?:立即)?领取\s*(?:每日|今日|免费|赠送)?\s*[+＋]?\s*\d+\s*(?:个)?\s*(?:积分|点数)|claim(?: now)?|claim (?:my |your )?(?:daily reward|free credits)|confirm claim|confirm redemption)$', re.I)
RECEIVED = re.compile(r'^(?:[+＋]?\s*\d[\d,]*\s*积分已到账(?:[。！!]|$)|[✓✔🎉\s]*(?:领取成功|签到成功|(?:今日|今天|本轮)已(?:经)?(?:领取|签到|领完)|已领取(?:今日|今天|本轮|每日)奖励|(?:每日奖励|积分)已(?:领取|到账))|(?:daily (?:reward|credits?) )?(?:already claimed|successfully claimed|claimed today)|you(?: have| already|\x27ve) (?:already )?claimed (?:your |the )?daily)', re.I)
REJECTION = re.compile(r'^(?:领取失败|签到失败|积分领取失败|无法领取|请求过于频繁|操作过于频繁|网络(?:异常|错误)|(?:failed|unable) to claim|claim failed|too many requests|network error)', re.I)
CHALLENGE = re.compile(r'验证您是(?:人类|真人)|(?:请|需要)完成人机验证|verify (?:that )?you are human|human verification', re.I)
RESET = re.compile(r'(?:后可领取下一轮|下次领取倒计时|距下次领取|next claim in|until (?:the )?next claim)', re.I)


class HumanVerificationRequired(ValueError):
    """The provider asks the account holder to complete an interactive step."""


class RewardAmountUnclear(ValueError):
    """The offer does not establish that this account can redeem exactly 400 credits."""


class AmbiguousGift(ValueError):
    """The page has not presented a unique daily offer."""


def visible(locator):
    return [item for item in locator.all() if item.is_visible()]


def gift_offer(page, names=REDEEM):
    """Locate one redemption button inside a visible daily reward offer."""
    dialogs = [d for d in visible(page.get_by_role('dialog')) if OFFER.search(d.inner_text(timeout=1800))]
    if len(dialogs) > 1:
        raise AmbiguousGift('官网同时显示了多个每日奖励面板，请打开专用领取页核对。')
    if dialogs:
        matches = [(button, dialogs[0]) for button in visible(dialogs[0].get_by_role('button', name=names))]
    else:
        matches = []
        for button in visible(page.get_by_role('button', name=names)):
            region = button
            for _ in range(5):
                region = region.locator('xpath=parent::*[not(self::html or self::body or self::main)]')
                if not region.count():
                    break
                label = region.inner_text(timeout=1800)
                if len(label) > 600 or region.get_by_role('navigation').count():
                    break
                if OFFER.search(label):
                    if len(visible(region.get_by_role('button', name=names))) == 1:
                        matches.append((button, region))
                    break
    if len(matches) > 1:
        raise AmbiguousGift('官网同时显示了多个领取按钮，请打开专用领取页核对。')
    return matches[0] if matches else None


AMOUNTS = re.compile(r'(?<![\d.,])(?:[+＋]\s*)?(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*(?:个\s*)?(?:积分|credits?\b)', re.I)
MEMBER = re.compile(r'^\s*Discord\s*(?:成员|會員|会员|member)\s*$', re.I)
PERSONAL_AMOUNT = re.compile(r'(?:本(?:次|轮)|你(?:今日|今天)|您(?:今日|今天))\s*(?:可(?:以)?|将)?领(?:取)?\s*[+＋]?\s*400\s*积分|(?:you can claim|your reward is)\s*[+]?\s*400\s*credits?', re.I)
PRICED_BUTTON = re.compile(r'^(?:立即|确认|确定)?领取\s*(?:每日|今日|免费|赠送)?\s*[+＋]?\s*400\s*(?:个)?\s*积分$|^claim\s*[+]?\s*400\s*credits?$', re.I)


def require_full_reward(text, member, action, *, confirmation=False):
    amounts = {Decimal(s.replace(',', '').replace('，', '')) for s in AMOUNTS.findall(text)}
    if amounts == {400} and (member or PERSONAL_AMOUNT.search(text) or PRICED_BUTTON.fullmatch(action.strip()) or confirmation):
        return
    shown = '、'.join(format(n, 'f') for n in sorted(amounts, reverse=True))
    detail = f'领取卡列出 {shown} 积分' if shown else '领取卡没有标明金额'
    raise RewardAmountUnclear(f'已停在领取前 · {detail}，无法确认本账号本次可领 400 积分。'
                            '请在官网奖励卡核对「Discord 成员」和单一 +400 后重试。')


def check_reward_amount(offer, *, confirmation=False):
    button, region = offer
    require_full_reward(region.inner_text(timeout=1800), bool(visible(region.get_by_text(MEMBER))),
                        button.get_attribute('aria-label') or button.inner_text(), confirmation=confirmation)


def website_notice(page):
    if visible(page.get_by_text(CHALLENGE)) or visible(page.locator(
            'iframe[src*="challenges.cloudflare.com"], iframe[title*="human verification" i], '
            'iframe[title*="人机验证"]')):
        raise HumanVerificationRequired('官网要求本人验证。请打开专用领取页，完成验证后核对到账。')
    refused = visible(page.get_by_text(REJECTION))
    if refused:
        raise ValueError('官网提示：' + ' '.join(refused[0].inner_text().split())[:140])
    if visible(page.get_by_text(RECEIVED)):
        return 'already'
    return None


def claim_on_page(page, before_click, *, offer_timeout=20, feedback_timeout=8):
    until = time.monotonic() + offer_timeout
    menu_opened = False
    offer_opened = False
    saw_reset = False
    unclear = None
    while time.monotonic() < until:
        address = urlsplit(page.url)
        if address.hostname != 'breezeblue.ai' or '/auth/' in address.path or '/login' in address.path:
            raise LoginRequired('请在专用领取页登录与配音密钥相同的 BreezeBlue 账号。')
        if website_notice(page):
            return 'already'
        offer = gift_offer(page)
        button = offer[0] if offer else None
        if button is not None and button.is_enabled():
            try:
                check_reward_amount(offer)
            except RewardAmountUnclear as exc:
                unclear = exc
                # The welcome dialog can advertise two tiers. Check the signed-in
                # account menu for a personal offer before deciding eligibility.
                if not menu_opened:
                    close = visible(page.get_by_role('button', name=re.compile(r'^(?:关闭每日奖励|close daily reward)$', re.I)))
                    if len(close) == 1:
                        close[0].click(timeout=3500)
                    profile = visible(page.get_by_role('button', name=PROFILE))
                    if len(profile) == 1:
                        profile[0].click(timeout=3500)
                        menu_opened = True
                page.wait_for_timeout(300)
                continue
            if before_click is None:
                return 'available'
            button.click(trial=True, timeout=3500)
            # A second click is allowed only on a newly presented, distinctly
            # labelled confirmation, never on a lingering redemption button.
            confirm_visible = gift_offer(page, CONFIRM) is not None
            check_reward_amount(offer)
            before_click()
            button.click(timeout=3500)
            until_feedback = time.monotonic() + feedback_timeout
            while time.monotonic() < until_feedback:
                if website_notice(page):
                    return 'acknowledged'
                if not confirm_visible:
                    confirmation = gift_offer(page, CONFIRM)
                    if confirmation and confirmation[0].is_enabled():
                        check_reward_amount(confirmation, confirmation=True)
                        confirm_visible = True
                        confirmation[0].click(timeout=3500)
                page.wait_for_timeout(250)
            return 'unconfirmed'
        # A countdown for the *next* round also appears with an active claim.
        saw_reset = bool(visible(page.get_by_text(RESET)))
        if button is None and not menu_opened:
            profile = visible(page.get_by_role('button', name=PROFILE))
            if len(profile) == 1:
                profile[0].click(timeout=3500)
                menu_opened = True
                continue
        if button is None and menu_opened and not offer_opened:
            entries = visible(page.get_by_role('button', name=ENTRY))
            if len(entries) == 1 and (entries[0].get_attribute('aria-haspopup') or
                                       entries[0].get_attribute('aria-expanded') == 'false'):
                entries[0].click(timeout=3500)
                offer_opened = True
                continue
        page.wait_for_timeout(300)
    if unclear is not None:
        raise unclear
    if saw_reset:
        return 'unavailable'
    raise ValueError('官网没有显示可用的每日奖励领取按钮。请打开专用领取页查看账号的领取条件。')


def match_dedicated_account(page, prefix, *, timeout=15):
    """Match the saved dubbing key to its masked row on the official key list."""
    if not isinstance(prefix, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,32}', prefix):
        raise ValueError('缺少可核对的官网密钥标识，已停止领取。')
    masked = re.compile(r'(?<![A-Za-z0-9_-])' + re.escape(prefix) + r'(?![A-Za-z0-9_-])')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        address = urlsplit(page.url)
        if (address.scheme, address.netloc, address.path.rstrip('/')) != ('https', 'breezeblue.ai', '/zh/app/developer/api-keys'):
            raise LoginRequired('请在专用领取页登录与配音密钥相同的 BreezeBlue 账号。')
        website_notice(page)
        matches = visible(page.get_by_role('table').get_by_role('cell').filter(has_text=masked))
        if len(matches) == 1:
            return
        if len(matches) > 1:
            raise ValueError('官网密钥列表中出现多个相同标识，已停止领取。')
        page.wait_for_timeout(250)
    raise ValueError('专用浏览器的官网密钥列表未能匹配当前配音密钥，已停止领取。')


def website_claim(before_click=None, *, expected_prefix=None):
    browser = select_browser()
    if not (browser.profile / 'Default').is_dir():
        raise LoginRequired('请在专用领取页登录 BreezeBlue。')
    ensure_driver()
    driver = browser_driver()
    with driver.sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(str(browser.profile), headless=True,
                        timeout=30000, **browser.launch_options())
        except driver.Error as exc:
            raise ValueError('专用浏览器未能启动。请关闭专用登录窗口后重试。') from exc
        try:
            page = ctx.new_page()
            address = URL + '/api-keys' if before_click is not None else URL
            reply = page.goto(address, wait_until='domcontentloaded', timeout=45000)
            if reply and reply.status >= 400:
                raise ValueError(f'开发者页面返回 HTTP {reply.status}，请在专用领取页检查连接或登录状态。')
            if before_click is not None:
                match_dedicated_account(page, expected_prefix)
            return claim_on_page(page, before_click)
        except driver.Error as exc:
            detail = str(exc).splitlines()[0][:150]
            raise ValueError('无法完成网页领取 · ' + detail) from exc
        finally:
            ctx.close()
