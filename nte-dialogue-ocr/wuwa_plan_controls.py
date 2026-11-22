"""Daily-plan controls shared by installed and portable workspaces."""
from PySide6 import QtWidgets as W
from wuwa_daily_plan import save_meta,today_units
import wuwa_breeze as breeze

PLAN_STYLE = """
QDialog#dailyPlan {background:#10181C;color:#F2F7F8;}
QDialog#dailyPlan QLabel, QDialog#dailyPlan QCheckBox {color:#E5EDF0;}
QDialog#dailyPlan QLineEdit, QDialog#dailyPlan QAbstractSpinBox {
    background:#101B20;color:#F5F8FA;border:1px solid #79939E;border-radius:6px;
    padding:5px 8px;selection-color:#091317;selection-background-color:#E5C474;
    placeholder-text-color:#B0C2CA;font-size:14px;}
QDialog#dailyPlan QAbstractSpinBox QLineEdit {background:transparent;border:0;padding:0;color:#F5F8FA;}
QDialog#dailyPlan QAbstractSpinBox:focus,QDialog#dailyPlan QLineEdit:focus {border-color:#F0D589;}
QDialog#dailyPlan QAbstractSpinBox:disabled,QDialog#dailyPlan QLineEdit:disabled {
    color:#B7C8D0;background:#1D292F;border-color:#718894;}
QDialog#dailyPlan QPushButton {color:#E6EEF2;background:#1C2A30;border:1px solid #647E89;border-radius:6px;padding:5px 10px;}
QDialog#dailyPlan QPushButton:hover {border-color:#E4C27B;}
QDialog#dailyPlan QPushButton:disabled {color:#AEBDC3;background:#1C2529;border-color:#637780;}
QDialog#dailyPlan QPushButton#verifyKey {color:#101719;background:#E4C27B;font-weight:600;}
QDialog#dailyPlan QProgressBar {background:#1B2A30;color:#FAFBFC;border:1px solid #718A94;border-radius:5px;min-height:12px;}
QDialog#dailyPlan QProgressBar::chunk {background:#57B5AA;border-radius:4px;}
"""


class BudgetPane(W.QWidget):
    def __init__(self,dialog):
        super().__init__()
        self.dialog=dialog
        c=dialog.c
        form=W.QFormLayout(self)
        self.cloud=W.QCheckBox('优先使用 BreezeBlue 云端合成')
        self.cloud.setChecked(c.cloud_only())
        self.ordinary=W.QCheckBox('启用普通支线无原声规则')
        self.ordinary.setChecked(c.branch_rule_enabled())
        self.budget=W.QSpinBox()
        self.budget.setRange(0,400)
        self.budget.setSuffix(' 积分')
        self.budget.setValue(int(c.meta('breeze_daily_credits','0')))
        self.key=W.QLineEdit()
        self.key.setEchoMode(W.QLineEdit.Password)
        self.key.setPlaceholderText('填写 BreezeBlue API Key，留空保留现有密钥')
        self.usage=W.QLabel()
        self.meter=W.QProgressBar()
        self.meter.setTextVisible(False)
        self.save=W.QPushButton('保存配音与预算')
        for label,widget in [('配音服务',self.cloud),('普通支线',self.ordinary),('BreezeBlue 密钥',self.key),
                            ('每日预算',self.budget),('今日账本',self.usage),('',self.meter),('',self.save)]:
            form.addRow(label,widget)
        self.backup = W.QPushButton('免费备用源 · 腾讯云与 TTSMaker')
        self.backup.setToolTip('登记腾讯云免费资源包；TTSMaker 在单句试音中打开')
        form.addRow('备用配音', self.backup)
        self.backup.clicked.connect(self.configure_backup)
        self.save.clicked.connect(self.persist)
        self.update_usage(c)

    def update_usage(self,c):
        units=today_units(c)
        cap=int(c.meta('breeze_daily_credits','0'))
        self.usage.setText(f'所有游戏账号今日共预留 {units/10:g} / {min(cap,400)} 积分')
        self.meter.setRange(0,max(1,min(cap,400)*10))
        self.meter.setValue(min(units,self.meter.maximum()))

    def configure_backup(self):
        from wuwa_free_tts_ui import TencentSettings
        TencentSettings(self.dialog.c, self).exec()

    def persist(self):
        key=self.key.text().strip()
        cloud,rule,limit=self.cloud.isChecked(),self.ordinary.isChecked(),self.budget.value()
        def work(c):
            if cloud and not (key or breeze.api_key()):
                raise ValueError('请先填写 BreezeBlue API Key。')
            if cloud and (key or not c.db.execute('SELECT 1 FROM breeze_presets LIMIT 1').fetchone()):
                breeze.refresh_presets(c,key=key or None)
            if key:
                breeze.save_key(key)
            save_meta(c,breeze_daily_credits=str(limit),wuwa_tts_provider='breezeblue' if cloud else 'hybrid',
                      ordinary_side_quests_unvoiced='1' if rule else '0')
            c.set_branch_rule(rule)
            return '已保存配音服务、普通支线规则和每日预算'
        def saved(msg):
            self.key.clear()
            self.dialog.feedback.setText(msg)
        self.dialog.start(work,saved,'正在检查配音配置并保存…')

