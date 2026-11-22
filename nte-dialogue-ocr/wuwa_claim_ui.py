"""BreezeBlue daily gift controls for the existing plan dialog."""
from pathlib import Path
import subprocess

from PySide6 import QtCore as C, QtWidgets as W

from wuwa_breeze import api_key, save_key
from wuwa_claim_browser import open_login, prepare_browser
from wuwa_daily_claim import account_scope, probe_member, read_ledger, read_record, run, status_path
from wuwa_daily_scheduler import manage, python_path


def result_text(state, scope):
    if not state:
        return '尚未核对'
    checked = state.get('checked_at', '')[:19].replace('T', ' ')
    prefix = checked + ' · ' if checked else ''
    if state.get('account') and state['account'] != scope:
        return '当前配音密钥尚无核对记录，请点击「立即领取 / 核对到账」。'
    if state.get('status') in ('credited', 'credited_other'):
        if not state.get('account') or not state.get('receipt') or not state.get('credited_at'):
            return prefix + '历史记录需要重新核对，请点击「立即领取 / 核对到账」。'
        paid = state['credited_at'][:19].replace('T', ' ')
        target = '（本期目标 400 积分）' if state['amount'] != 400 else ''
        origin = 'ASGSV 自动领取' if state.get('origin') == 'asgsv' else '已核对已有流水'
        return f"{prefix}{origin} · 已到账 {state['amount']:g} 积分{target}\n入账时间 {paid} · 流水尾号 {state['receipt'][-6:]}"
    if state.get('status') == 'pending' and not state.get('account'):
        return prefix + '尚未到账 · 上次操作没有对应的赠送流水，可重新领取或核对。'
    return prefix + state.get('message', '尚未核对')


class ClaimPane(W.QWidget):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.root = dialog.c.root
        self.schedule_state = W.QLabel('正在读取 Windows 计划…')
        self.result = W.QLabel()
        for label in (self.schedule_state, self.result):
            label.setTextFormat(C.Qt.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(C.Qt.TextSelectableByMouse)
        self.when = W.QTimeEdit(C.QTime(8, 45))
        self.when.setDisplayFormat('HH:mm')
        self.key = W.QLineEdit()
        self.key.setEchoMode(W.QLineEdit.Password)
        self.key.setPlaceholderText('已保存的密钥自动复用，首次使用可在这里粘贴')
        self.get_key = W.QPushButton('获取密钥')
        self.get_key.clicked.connect(lambda: dialog.start(lambda _: open_login(keys=True), dialog.feedback.setText, '正在准备浏览器并打开 BreezeBlue 密钥页面…'))
        self.login = W.QPushButton('打开官网领取页')
        self.enable = W.QPushButton('一键开启每日领取')
        self.disable = W.QPushButton('停用每日领取')
        self.check = W.QPushButton('立即领取 / 核对到账')
        self.probe = W.QPushButton('检测自动领取通道')
        self.probe.setToolTip('ASGSV 自行核对常用浏览器中的会员账号和奖励卡，本次检测不领取积分。')
        self.enable.setDefault(True)
        self.enable.setObjectName('verifyKey')
        self.login.clicked.connect(lambda: dialog.start(lambda _: open_login(), dialog.feedback.setText, '正在打开官网专用领取页…'))
        self.enable.clicked.connect(lambda: self.schedule(True))
        self.disable.clicked.connect(lambda: self.schedule(False))
        self.check.clicked.connect(lambda: dialog.start(lambda c: run(c.root), self.checked, 'ASGSV 正在领取并核对当天赠送积分…'))
        self.probe.clicked.connect(lambda: dialog.start(lambda c: probe_member(c.root), dialog.feedback.setText,
                                                       'ASGSV 正在临时打开官网，检测会员账号…'))
        form = W.QFormLayout(self)
        info = W.QLabel('仅在官网明确本账号可领 400 积分时领取。额度不明确时会保留本次机会。'
                        '显示「已到账」时可查看实际金额、入账时间和流水尾号。'
                        '专用浏览器只显示双档时，工具会临时打开常用浏览器的官网窗口，核对配音账号后自动领取并关窗。'
                        '启用 Windows 计划后，日常浏览器和工作台均可提前关闭。')
        info.setWordWrap(True)
        form.addRow(info)
        key_row = W.QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(self.get_key)
        form.addRow('BreezeBlue API Key', key_row)
        form.addRow('每日领取时间', self.when)
        form.addRow('Windows 后台', self.schedule_state)
        actions = W.QHBoxLayout()
        actions.addWidget(self.enable)
        actions.addWidget(self.disable)
        actions.addStretch()
        form.addRow('', actions)
        form.addRow('最近一次核对', self.result)
        other = W.QHBoxLayout()
        other.addWidget(self.check)
        other.addWidget(self.probe)
        other.addWidget(self.login)
        other.addStretch()
        form.addRow('', other)
        self.refresh(False)

    def show_schedule(self, data):
        if data['registered']:
            self.schedule_state.setText(f"{data['state']} · 下次 {data['next']}")
            scheduled = C.QTime.fromString(data.get('time', ''), 'HH:mm')
            if scheduled.isValid():
                self.when.setTime(scheduled)
        else:
            self.schedule_state.setText('尚未启用')

    def schedule(self, enable):
        at = self.when.time().toString('HH:mm')
        typed = self.key.text().strip() if enable else ''

        def change(c):
            if enable:
                key = typed or api_key(allow_environment=False)
                if not key:
                    raise ValueError('请在本页填入自己的 BreezeBlue API Key，然后点击一键开启。')
                read_ledger(key)
                browser = prepare_browser()
                if typed:
                    save_key(typed)
            state = manage(c.root, 'register' if enable else 'remove', at, kind='claim')
            if enable:
                state['browser'] = browser.label
                if not state['registered']:
                    raise ValueError('Windows 未登记领取计划，请检查当前账号的任务计划程序权限。')
                subprocess.Popen([str(python_path()), '-X', 'utf8', str(Path(__file__).with_name('wuwa_daily_claim.py')),
                                  '--root', str(c.root), '--setup'], creationflags=subprocess.CREATE_NO_WINDOW)
            return state

        def finished(data):
            self.key.clear()
            self.show_schedule(data)
            self.dialog.feedback.setText(f"已启用 · {data['browser']}，正在检查登录及到账。" if data['registered'] else '每日领取已停用。')
        self.dialog.start(change, finished, '正在验证密钥并准备浏览器；首次下载专用 Chromium 可能需要几分钟…' if enable else '正在停用每日领取…')

    def checked(self, result):
        self.dialog.feedback.setText(result['message'])
        self.refresh(False)

    def refresh(self, busy):
        try:
            state = read_record(status_path(self.root))
            key = api_key(allow_environment=False) if state.get('account') else ''
            message = result_text(state, account_scope(key) if key else '')
        except (OSError, ValueError) as exc:
            message = '无法读取领取记录 · ' + str(exc)
        self.result.setText(message)
        for control in (self.key, self.get_key, self.login, self.enable, self.disable, self.check, self.probe, self.when):
            control.setEnabled(not busy)
