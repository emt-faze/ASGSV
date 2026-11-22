"""Nonmodal community audio library. Network, disk inspection and signing run off the GUI thread."""
import json
from pathlib import Path
import tempfile
from threading import Event
from urllib.error import HTTPError

from PySide6 import QtCore as C, QtWidgets as W
from PySide6.QtCore import Qt

import wuwa_community as pack
import wuwa_community_cnb as cnb
from wuwa_community_login import Author
from wuwa_update import atomic, canonical
from wuwa_update_ui import Operation


class CommunityDialog(W.QDialog):
    installed = C.Signal(str)
    listen = C.Signal(object)

    def __init__(self, library, scope):
        super().__init__(library)
        from wuwa_identity import configure_home
        configure_home(self)
        self.root, self.install = library.catalog.root, library.install_root
        self.scope = scope
        self.name = library.quests[scope].name if scope in library.quests else ''
        self.worker = None
        self.stopping = False
        self.stop = Event()
        self.entries, self.shown = [], []
        self.setWindowTitle('ASGSV · 社区配音')
        self.resize(720, 420)
        layout = W.QVBoxLayout(self)
        filters = W.QHBoxLayout()
        self.search = W.QLineEdit()
        self.search.setPlaceholderText('搜索任务或投稿作者')
        self.search.setAccessibleName('搜索社区配音')
        self.only = W.QCheckBox('当前任务')
        self.only.setChecked(bool(scope))
        self.only.setEnabled(bool(scope))
        self.refresh = W.QPushButton('刷新')
        for widget in (self.search, self.only, self.refresh):
            filters.addWidget(widget)
        layout.addLayout(filters)
        self.table = W.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['任务', '投稿作者', '已配齐', '大小'])
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, W.QHeaderView.Stretch)
        self.table.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(W.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        self.note = W.QLabel()
        self.note.setTextFormat(Qt.PlainText)
        self.note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.read_actions = W.QHBoxLayout()
        self.sample = W.QPushButton('试听同一句')
        self.download = W.QPushButton('下载并启用')
        self.download.setObjectName('primary')
        self.restore = W.QPushButton('恢复原配音')
        self.report_button = W.QPushButton('举报此版')
        for btn in (self.download,self.sample,self.restore,self.report_button):
            self.read_actions.addWidget(btn)
        self.read_actions.addStretch()
        layout.addLayout(self.read_actions)
        author_bar = W.QHBoxLayout()
        self.login = W.QPushButton('登录 CNB')
        self.share = W.QPushButton('分享我的配音')
        self.share.setToolTip('公开分享自己为《'+self.name+'》生成的完整录音' if scope else '先在任务库选中要分享的任务')
        self.status = W.QPushButton('我的投稿状态')
        for btn in (self.login,self.share,self.status):
            author_bar.addWidget(btn)
        author_bar.addStretch()
        layout.addLayout(author_bar)
        self.search.textChanged.connect(self.render)
        self.only.toggled.connect(self.render)
        self.table.itemSelectionChanged.connect(self.buttons)
        self.refresh.clicked.connect(self.load)
        self.sample.clicked.connect(self.play_sample)
        self.download.clicked.connect(self.get_version)
        self.restore.clicked.connect(self.restore_original)
        self.report_button.clicked.connect(self.report_version)
        self.login.clicked.connect(self.login_author)
        self.share.clicked.connect(self.share_current)
        self.status.clicked.connect(self.check_submission)
        self.buttons()
        self.load()

    @property
    def profile(self):
        path = self.root/'community-author/profile.json'
        return json.loads(path.read_bytes()) if path.is_file() else {}

    def save_profile(self, data):
        atomic(self.root/'community-author/profile.json', canonical(data))

    def run(self, work, done, label):
        if self.worker:
            return
        self.stop.clear()
        self.note.setText(label)
        self.worker = Operation(work, self)
        self.worker.message.connect(self.note.setText)
        self.worker.failed.connect(self.note.setText)
        self.worker.result.connect(done)
        self.worker.finished.connect(self.finished_work)
        self.buttons()
        self.worker.start()

    def finished_work(self):
        self.worker.deleteLater()
        self.worker = None
        if self.stopping:
            super().reject()
        else:
            self.buttons()

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.stopping = True
            self.stop.set()
            self.note.setText('正在结束当前操作…')
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)

    def load(self):
        def fetch(_):
            try:
                return cnb.catalog()
            except HTTPError as exc:
                if exc.code == 404:
                    raise ValueError('CNB 社区目录尚未上线。如需官方成品，可在任务库下载') from None
                raise
        self.run(fetch, self.loaded, '正在匿名读取 CNB 社区目录…')

    def loaded(self, entries):
        self.entries = entries
        self.render()
        self.note.setText(f'共 {len(entries)} 个公开版本 · 下载和试听无需登录' if entries else '还没有通过检查的投稿')

    def render(self, *_):
        search = self.search.text().strip().casefold()
        self.shown = [e for e in self.entries if (not self.only.isChecked() or e['scope']==self.scope)
                      and search in (e['name']+' '+e['author']).casefold()]
        self.table.setRowCount(len(self.shown))
        for row, e in enumerate(self.shown):
            values = (e['name'],f'{e["author"]} · #{e["issue"]}',f'{e["lines"]} 句',f'{e["size"]/1048576:.1f} MiB')
            for col, v in enumerate(values):
                item = W.QTableWidgetItem(v)
                item.setToolTip('已检查台词完整性与录音文件 · 当前版本尚无实机验证记录')
                self.table.setItem(row,col,item)
        if self.shown:
            self.table.selectRow(0)
        self.buttons()

    def current(self):
        i = self.table.currentRow()
        return self.shown[i] if 0 <= i < len(self.shown) else None

    def buttons(self):
        idle = not self.worker
        entry = self.current()
        for button in (self.sample,self.download,self.report_button):
            button.setEnabled(bool(entry and idle))
        scope = entry['scope'] if entry else self.scope
        selected = pack.selections(self.root).get(scope)
        active = bool(selected and entry and selected['community'] == entry['issue'])
        self.download.setText('已启用此版' if active else '下载并启用')
        self.download.setEnabled(bool(entry and idle and not active))
        self.restore.setEnabled(bool(selected and idle))
        self.share.setEnabled(bool(self.scope and idle))
        self.refresh.setEnabled(idle)
        self.login.setEnabled(idle)
        profile = self.profile
        self.login.setText('切换 CNB 作者 · '+profile['name'] if profile.get('name') else '登录 CNB')
        self.status.setEnabled(bool(profile.get('issue') and idle))

    def author(self, progress):
        client = Author(self.root,self.stop,progress)
        client.prepare()
        return client

    def login_author(self):
        self.run(lambda p:self.author(p).login(), self.logged_in, '正在准备 CNB 官方登录组件…')

    def logged_in(self, name):
        self.save_profile(self.profile if self.profile.get('name')==name else {'name':name})
        self.note.setText('已登录 CNB · '+name)

    def share_current(self):
        profile = self.profile
        if not profile.get('name'):
            self.note.setText('请先点击「登录 CNB」。下载和试听可以直接使用。')
            return
        question = f'将《{self.name}》的完整录音以 {profile["name"]} 署名公开分享？\n请确认这些录音可公开分享，且语音中使用了通用称呼。'
        if W.QMessageBox.question(self,'分享我的配音',question) != W.QMessageBox.Yes:
            return
        def upload(progress):
            client = self.author(progress)
            if client.current() != profile['name']:
                raise ValueError('CNB 当前账号已改变，请先点击「切换 CNB 作者」核对署名')
            with tempfile.TemporaryDirectory() as temp:
                archive = Path(temp)/'submission.zip'
                pack.export_local(self.root,self.scope,archive,cnb.signing_key(self.root),profile['name'],progress)
                return cnb.submit(client,archive,self.stop,progress)
        def submitted(number):
            self.save_profile(dict(name=profile['name'],issue=number))
            self.note.setText(f'已提交 #{number} · 自动检查通过后公开。「我的投稿状态」可查看结果。')
        self.run(upload, submitted, '正在核对自己的任务录音…')

    def check_submission(self):
        number = self.profile.get('issue')
        def lookup(p):
            issue = self.author(p).request(cnb.ISSUES+'/'+str(number))
            return (issue['body'].rsplit('\nASGSV 处理结果：',1)[-1][:300] if '\nASGSV 处理结果：' in issue['body']
                    else '等待自动检查' if issue['state']=='open' else '投稿已关闭')
        self.run(lookup, lambda s:self.note.setText(f'投稿 #{number} · {s}'), '正在读取投稿结果…')

    def play_sample(self):
        entry = self.current()
        if entry:
            def play(path):
                self.listen.emit(path)
                self.note.setText(f'正在试听 {entry["author"]} · 《{entry["name"]}》')
            self.run(lambda _:cnb.preview(self.root,entry),play,'正在校验试听片段…')

    def get_version(self):
        entry = self.current()
        if entry:
            def done(message):
                self.note.setText(message)
                self.installed.emit(entry['scope'])
            self.run(lambda p:cnb.download_offer(self.install,self.root,entry,self.stop,p),done,'正在下载所选作者的版本…')

    def restore_original(self):
        entry = self.current()
        scope = entry['scope'] if entry else self.scope
        if scope:
            def done(_):
                self.note.setText('已恢复本机及官方配音')
                self.installed.emit(scope)
            self.run(lambda _:pack.select_version(self.root,scope),done,'正在恢复配音…')

    def report_version(self):
        entry = self.current()
        if not entry:
            return
        if not self.profile.get('name'):
            self.note.setText('请先登录 CNB 后提交举报。')
            return
        reason, ok = W.QInputDialog.getMultiLineText(self,'举报此版本','哪位角色、哪句台词出了问题？')
        if ok:
            self.run(lambda p:cnb.report(self.author(p),entry,reason),
                     lambda number:self.note.setText(f'举报 #{number} 已提交给维护者'),
                     '正在提交举报…')

