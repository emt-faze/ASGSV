"""BreezeBlue daily gift controls for the existing plan dialog."""
from pathlib import Path
import subprocess

from PySide6 import QtCore as C, QtWidgets as W

from wuwa_breeze import api_key, save_key
from wuwa_claim_browser import open_login, prepare_browser
from wuwa_daily_claim import read_ledger, read_record, run, status_path
from wuwa_daily_scheduler import manage, python_path


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
        self.get_key.clicked.connect(lambda: dialog.start(lambda _: open_login(keys=True), dialog.feedback.setText, '正在打开 BreezeBlue 密钥页面…'))
        self.login = W.QPushButton('重新登录专用 Chrome')
        self.enable = W.QPushButton('一键开启每日领取')
        self.disable = W.QPushButton('停用每日领取')
        self.check = W.QPushButton('立即领取 / 核对到账')
        self.enable.setDefault(True)
        self.enable.setObjectName('verifyKey')
        self.login.clicked.connect(lambda: dialog.start(lambda _: open_login(), dialog.feedback.setText, '正在打开专用登录窗口…'))
        self.enable.clicked.connect(lambda: self.schedule(True))
        self.disable.clicked.connect(lambda: self.schedule(False))
        self.check.clicked.connect(lambda: dialog.start(lambda c: run(c.root), self.checked, '正在核对当天的赠送积分…'))
        form = W.QFormLayout(self)
        info = W.QLabel('点「一键开启每日领取」。需要登录时，专用 Chrome 会自动打开，登录后继续核对到账。'
                        '之后只需电脑登录 Windows 并联网，日常 Chrome 和工作台可以关闭。')
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
                prepare_browser()
                if typed:
                    save_key(typed)
            state = manage(c.root, 'register' if enable else 'remove', at, kind='claim')
            if enable:
                if not state['registered']:
                    raise ValueError('Windows 未登记领取计划，请检查当前账号的任务计划程序权限。')
                subprocess.Popen([str(python_path()), '-X', 'utf8', str(Path(__file__).with_name('wuwa_daily_claim.py')),
                                  '--root', str(c.root), '--setup'], creationflags=subprocess.CREATE_NO_WINDOW)
            return state

        def finished(data):
            self.key.clear()
            self.show_schedule(data)
            self.dialog.feedback.setText('后台计划已启用，首次登录和到账核对正在进行。' if data['registered'] else '每日领取已停用。')
        self.dialog.start(change, finished, '正在验证密钥、准备组件并登记 Windows 计划…' if enable else '正在停用每日领取…')

    def checked(self, result):
        self.dialog.feedback.setText(result['message'])
        self.refresh(False)

    def refresh(self, busy):
        try:
            state = read_record(status_path(self.root))
            message = state.get('message', '尚未执行')
            if state.get('checked_at'):
                message = state['checked_at'][:19].replace('T', ' ') + ' · ' + message
            if state.get('receipt'):
                message += '\n流水编号 ' + state['receipt']
        except (OSError, ValueError) as exc:
            message = '无法读取领取记录 · ' + str(exc)
        self.result.setText(message)
        for control in (self.key, self.get_key, self.login, self.enable, self.disable, self.check, self.when):
            control.setEnabled(not busy)
