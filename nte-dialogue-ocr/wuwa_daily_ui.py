"""Quest detection, the account's queue and per-user scheduling in one dialog."""
from datetime import datetime
from PySide6 import QtCore as C, QtWidgets as W
from wuwa_catalog import Catalog
from wuwa_daily_plan import config, enqueue, initialize, run, save_meta, today_units, enable_casting
from wuwa_daily_scan import scan
from wuwa_daily_cast import DEFAULT_BASE, DEFAULT_MODEL, test_connection
from wuwa_daily_plan import record_scan
from wuwa_plan_controls import BudgetPane, PLAN_STYLE
from wuwa_daily_scheduler import manage
from wuwa_claim_ui import ClaimPane
from wuwa_account_ui import GameAccountButton
import wuwa_breeze as breeze


class Work(C.QThread):
    success = C.Signal(object)
    failure = C.Signal(str)

    def __init__(self,root,job,parent):
        super().__init__(parent)
        self.root,self.job=root,job

    def run(self):
        try:
            c=Catalog(self.root)
            try:
                initialize(c)
                result=self.job(c)
            finally:
                c.close()
            self.success.emit(result)
        except Exception as exc:
            self.failure.emit(str(exc))


class DailyDialog(W.QDialog):
    scan_changed=C.Signal()
    def __init__(self,c,parent=None):
        super().__init__(parent)
        self.c=c
        initialize(c)
        self.account=c.progress.account
        self.shown_selection=None
        self.worker=None
        self.setWindowTitle('每日配音计划')
        self.setObjectName('dailyPlan')
        self.setStyleSheet(PLAN_STYLE)
        self.resize(830,585)
        layout=W.QVBoxLayout(self)
        account_bar=W.QHBoxLayout()
        self.account_button=GameAccountButton(c,self)
        account_bar.addWidget(self.account_button)
        account_bar.addWidget(W.QLabel('后台固定使用所选账号 · 所有游戏账号共用每日预算'))
        account_bar.addStretch()
        layout.addLayout(account_bar)
        self.tabs=W.QTabWidget()
        layout.addWidget(self.tabs,1)
        detect=W.QWidget()
        box=W.QVBoxLayout(detect)
        self.detect_button=W.QPushButton('扫描本机未完成支线')
        discover=W.QHBoxLayout()
        discover.addWidget(self.detect_button)
        self.index_button=W.QPushButton('下载台词索引')
        self.index_button.setVisible(not c.db.execute("SELECT 1 FROM scopes WHERE kind='quest' LIMIT 1").fetchone())
        self.index_button.setToolTip('从项目指定的公开来源下载台词索引，完成后自动扫描。')
        discover.addWidget(self.index_button)
        discover.addStretch()
        box.addLayout(discover)
        self.results=self.table(['任务名称','进度依据'])
        self.results.setAccessibleName('可加入计划的支线')
        box.addWidget(self.results,1)
        self.scan_note=W.QLabel('进入鸣潮并登录一次，即可从本机游戏记录扫描普通支线。')
        self.scan_note.setWordWrap(True)
        box.addWidget(self.scan_note)
        self.add=W.QPushButton('加入每日计划')
        self.add_all=W.QPushButton('一键加入全部')
        actions=W.QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.add)
        actions.addWidget(self.add_all)
        box.addLayout(actions)
        self.tabs.addTab(detect,'发现支线')
        queue=W.QWidget()
        box=W.QVBoxLayout(queue)
        self.balance=W.QLabel()
        box.addWidget(self.balance)
        self.queue=self.table(['任务名称','执行状态'])
        box.addWidget(self.queue,1)
        bar=W.QHBoxLayout()
        self.remove=W.QPushButton('移出所选任务')
        self.execute=W.QPushButton('立即执行')
        bar.addWidget(self.remove)
        bar.addStretch()
        bar.addWidget(self.execute)
        box.addLayout(bar)
        self.tabs.addTab(queue,'我的计划')
        self.budget_pane=BudgetPane(self)
        self.tabs.addTab(self.budget_pane,'配音与预算')
        options=W.QWidget()
        form=W.QFormLayout(options)
        form.setFieldGrowthPolicy(W.QFormLayout.AllNonFixedFieldsGrow)
        self.when=W.QTimeEdit()
        self.when.setDisplayFormat('HH:mm')
        self.when.setTime(C.QTime.fromString(c.meta('daily_time','09:00'),'HH:mm'))
        form.addRow('每日运行时间',self.when)
        self.schedule_info=W.QLabel('正在读取 Windows 计划…')
        self.schedule_info.setWordWrap(True)
        form.addRow('Windows 后台',self.schedule_info)
        self.enable=W.QPushButton('保存时间并启用')
        self.disable=W.QPushButton('停用后台计划')
        sched=W.QHBoxLayout()
        sched.addWidget(self.enable)
        sched.addWidget(self.disable)
        form.addRow('',sched)
        form.addRow(W.QLabel('关闭工作台后继续执行。错过的计划在下次登录 Windows 后补做。'))
        self.tabs.addTab(options,'后台时间')
        ai=W.QWidget()
        form=W.QFormLayout(ai)
        self.cast_state=W.QLabel()
        self.base=W.QLineEdit(c.meta('daily_cast_endpoint') or DEFAULT_BASE)
        self.base.setPlaceholderText(DEFAULT_BASE)
        self.model=W.QLineEdit(c.meta('daily_cast_model') or DEFAULT_MODEL)
        self.model.setPlaceholderText(DEFAULT_MODEL)
        self.secret=W.QLineEdit()
        self.secret.setEchoMode(W.QLineEdit.Password)
        self.secret.setPlaceholderText('填写或更新密钥，留空沿用此地址保存的密钥')
        for label,field in [('每日选声',self.cast_state),('兼容 Chat Completions 的 API 基址',self.base),
                            ('模型名称',self.model)]:
            form.addRow(label,field)
        self.verify=W.QPushButton('测试连接 / 验证 Key')
        self.verify.setObjectName('verifyKey')
        key_row=W.QHBoxLayout()
        key_row.addWidget(self.secret,1)
        key_row.addWidget(self.verify)
        form.addRow('角色分析 API Key',key_row)
        self.verify.clicked.connect(self.verify_key)
        hint=W.QLabel('先分析当天预算内的角色，再开始配音。模型依据对白选择音色和语气，已有绑定沿用原音色。同一天再次执行会复用档案。模型服务单独计费。')
        hint.setWordWrap(True)
        form.addRow(hint)
        self.save_ai=W.QPushButton('保存并启用每日选声')
        self.save_ai.setObjectName('verifyKey')
        self.disable_ai=W.QPushButton('停用每日选声')
        self.disable_ai.clicked.connect(self.disable_casting)
        actions=W.QHBoxLayout()
        actions.addWidget(self.save_ai)
        actions.addWidget(self.disable_ai)
        form.addRow('',actions)
        self.cast_today=self.table(['角色','音色','表达提示'])
        self.cast_today.setAccessibleName('今日选声')
        self.cast_today.setMinimumHeight(90)
        form.addRow('今日选声',self.cast_today)
        self.tabs.addTab(ai,'角色分析')
        self.claim_pane = ClaimPane(self)
        self.tabs.addTab(self.claim_pane, '每日领积分')
        self.feedback=W.QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setTextInteractionFlags(C.Qt.TextSelectableByMouse)
        layout.addWidget(self.feedback)
        self.account_button.changed.connect(self.refresh)
        self.account_button.problem.connect(self.failed)
        self.detect_button.clicked.connect(self.detect)
        self.index_button.clicked.connect(self.initialize_index)
        self.add.clicked.connect(lambda:self.add_selected(False))
        self.add_all.clicked.connect(lambda:self.add_selected(True))
        self.results.itemChanged.connect(self.selection)
        self.remove.clicked.connect(self.remove_selected)
        self.queue.itemSelectionChanged.connect(self.selection)
        self.execute.clicked.connect(self.execute_now)
        self.enable.clicked.connect(lambda:self.schedule(True))
        self.disable.clicked.connect(lambda:self.schedule(False))
        self.save_ai.clicked.connect(self.save_profile)
        self.poll=C.QTimer(self)
        self.poll.setInterval(2500)
        self.poll.timeout.connect(self.refresh)
        self.refresh()
        def schedules(c):
            return manage(c.root, 'query'), manage(c.root, 'query', kind='claim')

        def loaded(states):
            self.show_schedule(states[0])
            self.claim_pane.show_schedule(states[1])

        self.start(schedules, loaded, '正在检查后台计划…')

    def showEvent(self,event):
        super().showEvent(event)
        self.account_button.detect_single()
        self.poll.start()

    def hideEvent(self,event):
        self.poll.stop()
        super().hideEvent(event)

    @staticmethod
    def table(headers):
        t=W.QTableWidget(0,len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().hide()
        t.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        t.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(0,W.QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    def start(self,job,done,text):
        if self.worker:
            return
        self.feedback.setText(text)
        self.worker=Work(self.c.root,job,self)
        self.worker.success.connect(done)
        self.worker.failure.connect(self.failed)
        self.worker.finished.connect(self.finished_job)
        self.worker.start()
        self.selection()

    def failed(self,msg):
        self.feedback.setText('需要处理 · '+msg)
        if self.schedule_info.text().startswith('正在'):
            self.schedule_info.setText('无法读取系统状态')
        if self.claim_pane.schedule_state.text().startswith('正在'):
            self.claim_pane.schedule_state.setText('无法读取系统计划 · ' + msg)

    def finished_job(self):
        self.worker.deleteLater()
        self.worker=None
        self.refresh()

    def reject(self):
        if self.worker:
            self.feedback.setText('正在完成当前操作，请稍候。')
            return
        super().reject()

    def closeEvent(self,event):
        if self.worker:
            event.ignore()
            return
        super().closeEvent(event)

    def done(self,result):
        self.poll.stop()
        super().done(result)

    def detect(self):
        if self.account_button.ensure():
            selection=self.c.progress.selection
            self.start(lambda c:scan(c,config(c)),lambda data:self.scanned(data,selection),'正在核对所选账号的游戏日志、完成缓存和普通支线索引…')

    def initialize_index(self):
        from wuwa_resources import download_tables
        if not self.account_button.ensure():
            return
        selection=self.c.progress.selection
        def index_then_scan(c):
            cfg=config(c)
            c.import_public(download_tables(c.root, progress=lambda msg:None))
            return scan(c,cfg)
        self.start(index_then_scan,lambda data:self.scanned(data,selection),'正在下载并校验台词索引，首次准备可能需要几分钟…')

    def scanned(self,data,selection):
        account,items=data
        try:
            record_scan(self.c,account,items,selection)
        except ValueError as exc:
            self.failed(str(exc))
            return
        self.index_button.hide()
        self.scan_changed.emit()
        self.show_results(items)
        self.feedback.clear()
        self.refresh()

    def show_results(self,items):
        self.results.setRowCount(0)
        added={r[0] for r in self.c.db.execute('SELECT scope FROM daily_plan WHERE account=?',(self.account,))}
        for scope,name,evidence in items:
            i=self.results.rowCount()
            self.results.insertRow(i)
            entry=W.QTableWidgetItem(name)
            entry.setData(C.Qt.UserRole,scope)
            entry.setCheckState(C.Qt.Unchecked)
            if scope in added:
                entry.setFlags(entry.flags() & ~C.Qt.ItemIsEnabled)
                evidence+=' · 已在计划'
            self.results.setItem(i,0,entry)
            self.results.setItem(i,1,W.QTableWidgetItem(evidence))
        self.scan_note.setText(f'检测到 {len(items)} 个有未完成证据、可匹配台词的普通支线。勾选后加入计划。'
            if items else '本次没有找到符合条件的支线。可进入游戏打开任务列表、追踪支线，再次扫描。')

    def selection(self):
        busy=bool(self.worker)
        self.account_button.setEnabled(not busy)
        self.claim_pane.refresh(busy)
        self.disable_ai.setEnabled(not busy and self.c.meta('daily_cast_enabled')=='1')
        for b in (self.detect_button,self.index_button,self.enable,self.disable,self.save_ai,self.verify,self.budget_pane.save):
            b.setEnabled(not busy)
        for field in (self.base,self.model,self.secret):
            field.setEnabled(not busy)
        self.add.setEnabled(not busy and any(self.results.item(i,0) and self.results.item(i,0).checkState()==C.Qt.Checked
                                             for i in range(self.results.rowCount())))
        self.add_all.setEnabled(not busy and any(self.results.item(i,0) and self.results.item(i,0).flags() & C.Qt.ItemIsEnabled
                                                for i in range(self.results.rowCount())))
        self.remove.setEnabled(not busy and bool(self.queue.selectionModel().selectedRows()))
        self.execute.setEnabled(not busy and self.queue.rowCount()>0)

    def add_selected(self,all_rows=False):
        chosen=[self.results.item(i,0) for i in range(self.results.rowCount())
                if self.results.item(i,0).flags() & C.Qt.ItemIsEnabled and
                (all_rows or self.results.item(i,0).checkState()==C.Qt.Checked)]
        try:
            added=enqueue(self.c,self.account,[(e.data(C.Qt.UserRole),e.text()) for e in chosen])
        except ValueError as exc:
            self.failed(str(exc))
            self.refresh()
            return
        for e in chosen:
            e.setCheckState(C.Qt.Unchecked)
            e.setFlags(e.flags() & ~C.Qt.ItemIsEnabled)
            note=self.results.item(e.row(),1)
            note.setText(note.text()+' · 已在计划')
        self.tabs.setCurrentIndex(1)
        self.feedback.setText(f'已新增 {added} 项 · 当前计划共 {self.c.db.execute("SELECT count(*) FROM daily_plan WHERE account=?",(self.account,)).fetchone()[0]} 项')
        self.refresh()

    def remove_selected(self):
        scopes=[self.queue.item(idx.row(),0).data(C.Qt.UserRole) for idx in self.queue.selectionModel().selectedRows()]
        with self.c.db:
            self.c.db.execute('BEGIN IMMEDIATE')
            if self.account!=self.c.progress.account:
                self.failed('游戏账号已切换，请重新选择任务。')
                self.refresh()
                return
            self.c.db.executemany('DELETE FROM daily_plan WHERE account=? AND scope=?',[(self.account,s) for s in scopes])
        for i in range(self.results.rowCount()):
            e=self.results.item(i,0)
            if e.data(C.Qt.UserRole) in scopes:
                e.setFlags(e.flags() | C.Qt.ItemIsEnabled)
                note=self.results.item(i,1)
                note.setText(note.text().removesuffix(' · 已在计划'))
        self.refresh()

    def refresh(self):
        selection=self.c.progress.selection
        self.account_button.sync()
        if self.shown_selection!=selection:
            self.shown_selection=selection
            self.account=selection[0]
            self.shown=None
            items=list(self.c.db.execute('SELECT scope,name,evidence FROM daily_discovered WHERE account=? ORDER BY name',(self.account,)))
            self.show_results(items)
            if not items:
                self.scan_note.setText('请为所选账号扫描支线。' if self.account else '先选择游戏账号，再扫描该账号的支线。')
        self.cast_state.setText('已启用 · 每次配音前自动预选' if self.c.meta('daily_cast_enabled')=='1' else '未启用 · 填好配置后点击保存')
        self.budget_pane.update_usage(self.c)
        choices=[tuple(r) for r in self.c.db.execute('''SELECT
            coalesce((SELECT speaker FROM lines WHERE speaker_id=d.speaker LIMIT 1),d.speaker),
            coalesce(p.name,d.voice),d.delivery,d.evidence FROM daily_voice_choices d
            LEFT JOIN breeze_presets p ON p.id=d.voice WHERE d.day=? ORDER BY 1''',
            (datetime.now().date().isoformat(),))]
        if choices!=getattr(self,'shown_cast',None):
            self.shown_cast=choices
            self.cast_today.setRowCount(len(choices))
            for index,choice in enumerate(choices):
                for column,text in enumerate(choice[:3]):
                    item=W.QTableWidgetItem(text)
                    item.setToolTip('判断依据 · '+choice[3])
                    self.cast_today.setItem(index,column,item)
        rows=[tuple(r) for r in self.c.db.execute('SELECT scope,name,status FROM daily_plan WHERE account=? ORDER BY rowid',(self.account,))]
        if rows!=getattr(self,'shown',None):
            self.shown=rows
            self.queue.setRowCount(len(rows))
            for i,(s,name,status) in enumerate(rows):
                item=W.QTableWidgetItem(name)
                item.setData(C.Qt.UserRole,s)
                self.queue.setItem(i,0,item)
                detail=W.QTableWidgetItem(status)
                detail.setToolTip(status)
                self.queue.setItem(i,1,detail)
        cap=min(400,int(self.c.meta('breeze_daily_credits','0')))
        legacy=self.c.meta('daily_result','尚未执行') if self.c.meta('daily_account')==self.account else '尚未执行'
        last=self.c.meta('daily_result:'+self.account,legacy)
        self.balance.setText(f'今日共预留 {today_units(self.c)/10:g} / {cap} 积分　·　{last}')
        self.selection()

    def schedule(self,on):
        at=self.when.time().toString('HH:mm')
        def job(c):
            if on:
                config(c)
                if not c.cloud_only() or not breeze.api_key() or int(c.meta('breeze_daily_credits','0'))<=0:
                    raise ValueError('请先在每日计划的「配音与预算」中启用 BreezeBlue、保存密钥并设置每日预算。')
            data=manage(c.root,'register' if on else 'remove',at)
            if bool(data['registered'])!=on:
                raise ValueError('系统计划状态与预期不符')
            values=dict(daily_enabled='1' if on else '0',daily_time=at)
            if on and c.meta('daily_enabled')!='1':
                values['daily_since']=datetime.now().isoformat(timespec='seconds')
            save_meta(c,**values)
            return data
        self.start(job,self.show_schedule,'正在更新 Windows 后台计划…')

    def show_schedule(self,state):
        self.schedule_info.setText('已启用 · 下次 '+state['next'] if state['registered'] else '尚未启用')
        self.feedback.clear()

    def execute_now(self):
        # The standalone process can continue even if the user closes this dialog.
        from wuwa_daily_scheduler import python_path
        from pathlib import Path
        if self.shown_selection!=self.c.progress.selection or not self.account_button.ensure():
            self.refresh()
            return
        args=['-X','utf8',str(Path(__file__).with_name('wuwa_daily_plan.py')),'--root',str(self.c.root),
              '--selection',*self.shown_selection]
        ok,_=C.QProcess.startDetached(str(python_path()),args,str(Path(__file__).parent))
        self.feedback.setText('已启动后台执行，可关闭工作台。' if ok else '启动失败，请检查便携运行环境。')

    def verify_key(self):
        base,model,key=self.base.text().strip(),self.model.text().strip(),self.secret.text().strip()
        self.start(lambda c:test_connection(base,model,key),self.feedback.setText,'正在验证密钥与模型访问权限…')

    def save_profile(self):
        base,model,key=self.base.text(),self.model.text(),self.secret.text()
        def saved(message):
            self.secret.clear()
            self.feedback.setText(message)
        self.start(lambda c:enable_casting(c,base,model,key),saved,'正在验证配置并启用每日选声…')

    def disable_casting(self):
        self.start(lambda c:save_meta(c,daily_cast_enabled='0'),
                   lambda _:self.feedback.setText('每日选声已停用，已保存的配置可继续使用。'),
                   '正在停用每日选声…')

