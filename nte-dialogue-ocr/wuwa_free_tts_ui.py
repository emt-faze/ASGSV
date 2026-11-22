"""Free-provider settings and one-line A/B audition in the existing workbench."""
from contextlib import closing
from pathlib import Path
from threading import Event

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from wuwa_catalog import Catalog
from wuwa_plan_controls import PLAN_STYLE
from wuwa_update_ui import Operation
import wuwa_tencent_tts as tx


class TencentSettings(W.QDialog):
    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.worker = None
        self.setWindowTitle('免费备用源 · 腾讯云')
        self.setObjectName('dailyPlan')
        self.setStyleSheet(PLAN_STYLE)
        self.resize(545, 425)
        form = W.QFormLayout(self)
        help_text = W.QLabel('在腾讯云「语音合成资源包」查看该档剩余字符和到期日。关闭语音合成后付费，并确保同档没有付费包，腾讯云才会在免费包耗尽时停服。更改云端计费设置前请先停用备用源。')
        help_text.setWordWrap(True)
        form.addRow(help_text)
        links = W.QHBoxLayout()
        for label, url in [('领取免费资源包', tx.PACK_PAGE), ('管理腾讯云密钥', tx.KEY_PAGE)]:
            button = W.QPushButton(label)
            button.clicked.connect(lambda _, target=url: G.QDesktopServices.openUrl(C.QUrl(target)))
            links.addWidget(button)
        form.addRow(links)
        self.secret_id, self.secret_key = W.QLineEdit(), W.QLineEdit()
        for widget, label in [(self.secret_id, 'SecretId'), (self.secret_key, 'SecretKey')]:
            widget.setEchoMode(W.QLineEdit.Password)
            widget.setPlaceholderText('仅保存在 Windows 凭据管理器，已保存可留空')
            form.addRow(label, widget)
        self.tier = W.QComboBox()
        for code,(label,_) in tx.TIERS.items():
            self.tier.addItem(label, code)
        self.amount = W.QSpinBox()
        self.amount.setGroupSeparatorShown(True)
        self.amount.setSuffix(' 字符')
        self.expiry = W.QDateEdit(C.QDate.currentDate())
        self.expiry.setDisplayFormat('yyyy-MM-dd')
        self.expiry.setCalendarPopup(True)
        self.expiry.setDateRange(C.QDate.currentDate(), C.QDate.currentDate().addDays(93))
        form.addRow('免费包档位', self.tier)
        form.addRow('控制台剩余字符', self.amount)
        form.addRow('控制台到期日', self.expiry)
        self.confirm = W.QCheckBox('已核对密钥所属账号，本档免费包有效、后付费已关且同档无付费包')
        form.addRow(self.confirm)
        self.note = W.QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(C.Qt.PlainText)
        form.addRow(self.note)
        self.tier.currentIndexChanged.connect(self.load_tier)
        self.load_tier()
        actions = W.QHBoxLayout()
        self.save = W.QPushButton('验证身份并保存')
        self.save.setObjectName('verifyKey')
        self.save.clicked.connect(lambda: self.persist(False))
        self.single = W.QPushButton('仅验证密钥，先试听一句')
        self.single.setToolTip('已领取本档免费包时可用。30 分钟内仅许可一次手动试音，后台任务无法使用此许可。')
        self.single.clicked.connect(lambda: self.persist(True))
        self.disable = W.QPushButton('停用腾讯云')
        self.disable.clicked.connect(self.disable_provider)
        actions.addWidget(self.save)
        actions.addWidget(self.single)
        actions.addWidget(self.disable)
        form.addRow(actions)

    def load_tier(self):
        tier = self.tier.currentData()
        owner = self.catalog.meta('fallback_tencent_owner')
        state = tx.grant_info(owner, tier) if owner else None
        self.amount.setRange(0, tx.TIERS[tier][1])
        self.amount.setValue(state['remaining'] if state else 0)
        self.expiry.setDate(C.QDate.fromString(state['until_date'], 'yyyy-MM-dd') if state else C.QDate.currentDate())
        self.confirm.setChecked(False)
        self.note.setText('请按控制台填写。再次保存只会降低本机剩余额度或缩短期限。到期日当天停用。')

    def disable_provider(self):
        with self.catalog.db:
            self.catalog.db.execute('DELETE FROM meta WHERE key=?', ('fallback_tencent_owner',))
        self.accept()

    def persist(self, single=False):
        if self.worker:
            return
        sid, secret = self.secret_id.text().strip(), self.secret_key.text().strip()
        if bool(sid) != bool(secret):
            self.note.setText('请同时填写 SecretId 和 SecretKey，或两栏留空复用已保存的密钥')
            return
        if not self.confirm.isChecked():
            self.note.setText('请先确认已领取所选档位免费包、后付费已关且同档无付费包，再勾选上方选项。')
            self.confirm.setFocus()
            return
        if not single and self.amount.value() <= 0:
            self.note.setText('「控制台剩余字符」目前为 0。请填实际余量，或点「仅验证密钥，先试听一句」。')
            self.amount.setFocus()
            self.amount.selectAll()
            return
        if not single and self.expiry.date() <= C.QDate.currentDate():
            self.note.setText('「控制台到期日」需要晚于今天。可点日历选择，也可使用单句试听。')
            self.expiry.setFocus()
            return
        tier, amount = self.tier.currentData(), self.amount.value()
        expiry = self.expiry.date().toString('yyyy-MM-dd')
        root = self.catalog.root
        def work(_):
            pair = (sid,secret) if sid else tx.saved_credentials()
            with closing(Catalog(root)) as c:
                if single:
                    return tx.enable_trial(c,pair,tier,True)
                return tx.configure(c,pair,tier,amount,expiry,True)
        self.worker = Operation(work,self)
        self.worker.result.connect(self.saved)
        self.worker.failed.connect(self.note.setText)
        self.worker.finished.connect(self.done_saving)
        self.setEnabled(False)
        self.note.setText('正在使用腾讯云身份接口核对账号…')
        self.worker.start()

    def saved(self, state):
        self.secret_key.clear()
        self.secret_id.clear()
        if state.get('trial'):
            self.note.setText('已保存 · 已开启一次免费手动试听，可关闭此窗口。')
        else:
            self.amount.setValue(state['remaining'])
            self.note.setText('已保存 · 本机剩余上限 '+str(state['remaining'])+' 字符，可关闭此窗口并试听')
        self.confirm.setChecked(False)

    def done_saving(self):
        self.worker.deleteLater()
        self.worker = None
        self.setEnabled(True)

    def reject(self):
        if self.worker is None:
            super().reject()


class FreeAudition(W.QDialog):
    def __init__(self, catalog, line, speaker, parent=None):
        super().__init__(parent)
        import wuwa_free_voices as voices
        self.c, self.line = catalog, line
        self.selection = catalog.progress.selection
        self.primary = voices.primary_voice(catalog,line.sid)
        self.stop = Event()
        self.worker = self.take = None
        self.heard = False
        self.setWindowTitle('免费备用声线 · '+speaker)
        self.setObjectName('dailyPlan')
        self.setStyleSheet(PLAN_STYLE)
        self.resize(540,350)
        box = W.QVBoxLayout(self)
        title = W.QLabel(speaker+'　'+line.text)
        title.setTextFormat(C.Qt.PlainText)
        title.setWordWrap(True)
        box.addWidget(title)
        self.presets = W.QComboBox()
        for voice,(name,kind,tier) in tx.VOICES.items():
            self.presets.addItem(f'{name} · {kind}　{tx.TIERS[tier][0]}',voice)
        paired = voices.configured(catalog,line.sid,self.primary)
        if paired:
            self.presets.setCurrentIndex(self.presets.findData(paired))
        box.addWidget(self.presets)
        self.edit = W.QWidget()
        commands = W.QGridLayout(self.edit)
        commands.setContentsMargins(0,0,0,0)
        controls = [('A · 播放现有录音',self.play_a),('B · 腾讯云试听这一句',self.play_b),
                    ('复制本句并打开 TTSMaker',self.open_maker),('导入 TTSMaker 单句录音',self.pick_file),
                    ('腾讯云免费资源设置',self.settings)]
        for i,(label,action) in enumerate(controls):
            button = W.QPushButton(label)
            commands.addWidget(button,i//2,i%2)
            button.clicked.connect(action)
        box.addWidget(self.edit)
        self.notice = W.QLabel('试听按本句计量；对比满意后才会记为备用。TTSMaker 使用免费网页版。')
        self.notice.setTextFormat(C.Qt.PlainText)
        self.notice.setWordWrap(True)
        box.addWidget(self.notice)
        box.addStretch()
        self.apply = W.QPushButton('确认这条备用录音')
        self.apply.setEnabled(False)
        self.apply.clicked.connect(self.confirm)
        box.addWidget(self.apply)
        self.player = QMediaPlayer(self)
        self.out = QAudioOutput(self)
        self.player.setAudioOutput(self.out)
        self.player.positionChanged.connect(self.position)
        self.player.errorOccurred.connect(self.playback_error)
        self.presets.currentIndexChanged.connect(self.reset_take)

    def reset_take(self,*_):
        self.player.stop()
        self.take = None
        self.heard = False
        self.apply.setEnabled(False)
        self.apply.setText('确认这条备用录音')

    def settings(self):
        TencentSettings(self.c,self).exec()

    def run(self, job, message, callback):
        if self.worker:
            return
        self.reset_take()
        self.stop.clear()
        root=self.c.root
        def work(_):
            with closing(Catalog(root)) as catalog:
                return job(catalog)
        self.worker = Operation(work,self)
        self.worker.result.connect(callback)
        self.worker.failed.connect(self.notice.setText)
        self.worker.finished.connect(self.finished_work)
        self.edit.setEnabled(False)
        self.presets.setEnabled(False)
        self.notice.setText(message)
        self.worker.start()

    def finished_work(self):
        self.worker.deleteLater()
        self.worker = None
        if self.stop.is_set():
            self.reject()
        else:
            self.edit.setEnabled(True)
            self.presets.setEnabled(True)

    def play_a(self):
        from wuwa_free_voices import original
        self.run(lambda c: original(c,self.line,self.primary,self.selection),
                 '正在读取已校验的原录音…',self.a_ready)

    def a_ready(self,path):
        if not self.stop.is_set() and self.c.progress.selection == self.selection:
            self.notice.setText('A · 正在播放现有主声线')
            self.play(path)

    def play_b(self):
        from wuwa_free_voices import tencent_trial
        voice = self.presets.currentData()
        self.run(lambda c: tencent_trial(c,self.line,self.primary,voice,self.selection,self.stop),
                 '正在检查该档免费额度及本句缓存…', self.b_ready)

    def open_maker(self):
        from wuwa_free_voices import check_trial
        try:
            spoken = check_trial(self.c,self.line,self.selection,self.primary)
        except ValueError as exc:
            self.notice.setText(str(exc))
            return
        W.QApplication.clipboard().setText(spoken)
        opened = G.QDesktopServices.openUrl(C.QUrl(tx.MAKER_PAGE))
        self.notice.setText('已复制本句。到免费网页粘贴、选声并下载 WAV，再点「导入」。' if opened
                            else '已复制本句，请在浏览器打开 '+tx.MAKER_PAGE+'，下载 WAV 后导入。')

    def pick_file(self):
        from wuwa_free_voices import web_trial
        name,_ = W.QFileDialog.getOpenFileName(self,'选择本句的 TTSMaker 录音','','单句音频 (*.wav *.mp3)')
        if name:
            self.run(lambda c: (web_trial(c,self.line,self.primary,self.selection,Path(name)),True),
                     '正在校验录音格式和音频内容…',self.b_ready)

    def b_ready(self,result):
        if self.stop.is_set() or self.c.progress.selection != self.selection:
            self.notice.setText('账号或台词已切换，请重新开启试音')
            return
        self.take, hit = result
        if self.take.provider == 'ttsmaker':
            detail = '已导入本句'
            self.apply.setText('采用为本句备用录音')
        else:
            detail = '复用缓存' if hit else '本句生成完成'
            self.apply.setText('设为该角色的腾讯云备用声线')
        self.notice.setText('B · '+self.take.label+' · '+detail)
        self.play(self.take.path)

    def play(self,path):
        self.player.setSource(C.QUrl.fromLocalFile(str(path)))
        self.player.play()

    def position(self,msec):
        if msec > 0 and self.take and self.player.source() == C.QUrl.fromLocalFile(str(self.take.path)):
            self.heard = True
            self.apply.setEnabled(True)

    def playback_error(self,_,message):
        self.heard = False
        self.apply.setEnabled(False)
        self.notice.setText('播放失败 · '+message)

    def confirm(self):
        import sqlite3
        from wuwa_free_voices import accept
        if self.worker or not self.take or not self.heard:
            return
        try:
            accept(self.c,self.line,self.primary,self.selection,self.take)
        except (ValueError,OSError,sqlite3.Error) as exc:
            self.notice.setText(str(exc))
            return
        self.player.stop()
        super().accept()

    def reject(self):
        self.player.stop()
        if self.worker:
            self.stop.set()
            self.notice.setText('正在结束单句试音，已提交的录音将保留在缓存中')
            return
        super().reject()
