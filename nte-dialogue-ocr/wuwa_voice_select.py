"""Searchable cloud voice picker; identity stays attached to the selected row."""
from contextlib import closing
import sqlite3
from threading import Event
from PySide6 import QtCore as C, QtWidgets as W
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from quest_library import choose_cloud_voice
from wuwa_update_ui import Operation
from wuwa_audition import audition, confirm_take
from wuwa_breeze import spoken_text
from wuwa_catalog import Catalog

LABELS={'male':'男声','female':'女声','young':'青年','middle_aged':'中年','old':'老年','child':'儿童'}

class CloudVoiceDialog(W.QDialog):
    def __init__(self,c,row,parent=None,*,trial=None,preferred='',reason=''):
        super().__init__(parent)
        self.c,self.row=c,row
        self.trial = trial
        self.selection = c.progress.selection
        self.worker = self.take = None
        self.heard = False
        self.last_voice = ''
        self.stop = Event()
        self.setWindowTitle(('角色试音 · ' if trial else '选择音色 · ')+row['speaker'])
        self.resize(470,450)
        layout=W.QVBoxLayout(self)
        if trial:
            self.line_label = W.QLabel(row['speaker']+'　'+trial.text)
            self.line_label.setTextFormat(C.Qt.PlainText)
            self.line_label.setWordWrap(True)
            layout.addWidget(self.line_label)
            maximum = len(spoken_text(trial.text,c.root))*2/10
            hint = W.QLabel((reason+'。' if reason else '')+
                            f'同一句对白对比音色。已有录音免费重听，新录这句最多预留 {maximum:g} 积分，计入每日预算。')
            hint.setTextFormat(C.Qt.PlainText)
            hint.setWordWrap(True)
            layout.addWidget(hint)
        self.search=W.QLineEdit()
        self.search.setPlaceholderText('搜索音色名称、已绑定角色、年龄、性别或 Voice ID')
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.list=W.QListWidget()
        layout.addWidget(self.list,1)
        self.notice=W.QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        if trial:
            actions = W.QHBoxLayout()
            self.listen = W.QPushButton('试听这句')
            self.swap = W.QPushButton('换一个声线重听')
            self.listen.clicked.connect(self.start_sample)
            self.swap.clicked.connect(self.swap_sample)
            actions.addWidget(self.listen)
            actions.addWidget(self.swap)
            layout.addLayout(actions)
            self.player = QMediaPlayer(self)
            self.output = QAudioOutput(self)
            self.player.setAudioOutput(self.output)
            self.player.positionChanged.connect(self.played)
            self.player.errorOccurred.connect(self.playback_error)
        controls=W.QDialogButtonBox(W.QDialogButtonBox.Ok|W.QDialogButtonBox.Cancel)
        self.apply=controls.button(W.QDialogButtonBox.Ok)
        self.apply.setText('确认并记住' if trial else '使用所选音色')
        controls.button(W.QDialogButtonBox.Cancel).setText('取消')
        layout.addWidget(controls)
        binding=c.binding(row['speaker_id'])
        current=binding['voice'] if binding else ''
        self.previous = current
        roles={}
        for vid,speaker in c.db.execute('SELECT DISTINCT b.voice_id,l.speaker FROM breeze_cast b JOIN lines l ON l.speaker_id=b.speaker_id'):
            roles.setdefault(vid,[]).append(speaker)
        selected=None
        presets = list(c.db.execute('SELECT id,name,gender,age FROM breeze_presets ORDER BY name,id'))
        if trial:
            from wuwa_cast_context import quest_suggestion
            from wuwa_cast_review import reviewed_roles
            gender, age, _ = quest_suggestion(c.lines([trial.scope]),trial.sid,row['speaker'])
            reviewed = (reviewed_roles(c,trial.scope) or {}).get(trial.sid)
            if reviewed in ('male','female'):
                gender = reviewed
            first = preferred or current.removeprefix('breeze:')
            reference = next((p for p in presets if p['id']==first),None)
            if reference:
                gender = gender or reference['gender']
                age = age or reference['age']
            presets.sort(key=lambda v:(v['id'] != first,
                                       bool(gender and v['gender']!=gender),bool(age and v['age']!=age)))
        for vid,name,gender,age in presets:
            tags=' · '.join(LABELS[k] for k in (gender,age) if k in LABELS)
            item=W.QListWidgetItem(name+('　'+tags if tags else ''))
            item.setData(C.Qt.UserRole,vid)
            terms=' '.join([name,vid,tags,gender or '',age or '',*roles.get(vid,[])])
            item.setData(C.Qt.UserRole+1,terms.casefold())
            item.setToolTip(terms)
            self.list.addItem(item)
            if current=='breeze:'+vid:
                selected=item
        if selected and not trial:
            self.list.setCurrentItem(selected)
        self.search.textChanged.connect(self.filter)
        self.list.itemDoubleClicked.connect(self.start_sample if trial else self.accept)
        if trial:
            self.list.currentItemChanged.connect(self.candidate_changed)
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        self.filter('')
        self.search.setFocus()

    def filter(self,text):
        terms=text.casefold().split()
        visible=[]
        for i in range(self.list.count()):
            item=self.list.item(i)
            match=all(t in item.data(C.Qt.UserRole+1) for t in terms)
            item.setHidden(not match)
            if match:
                visible.append(item)
        if self.list.currentItem() not in visible:
            self.list.setCurrentItem(visible[0] if visible else None)
        if self.trial:
            self.update_sample_actions()
        else:
            self.apply.setEnabled(bool(visible))
        self.notice.setText(f'{len(visible)} 个可选音色' if visible else '没有匹配音色，可更换关键词或在每日计划中同步预设。')

    def selected_voice(self):
        item = self.list.currentItem()
        return item.data(C.Qt.UserRole) if item and not item.isHidden() else ''

    def update_sample_actions(self):
        voice = self.selected_voice()
        idle = self.worker is None
        self.listen.setEnabled(idle and bool(voice))
        count = sum(not self.list.item(i).isHidden() for i in range(self.list.count()))
        self.swap.setEnabled(idle and count > 1)
        self.apply.setEnabled(bool(idle and self.heard and self.take and self.take.voice == voice))

    def candidate_changed(self,*_):
        self.take = None
        self.heard = False
        self.player.stop()
        self.notice.setText('试听所选音色后可确认')
        self.update_sample_actions()

    def swap_sample(self):
        visible = [self.list.item(i) for i in range(self.list.count()) if not self.list.item(i).isHidden()]
        if len(visible) < 2:
            return
        if self.selected_voice() == self.last_voice or not self.last_voice:
            self.list.setCurrentItem(visible[(visible.index(self.list.currentItem())+1) % len(visible)])
        self.start_sample()

    def start_sample(self,*_):
        voice = self.selected_voice()
        if not voice or self.worker:
            return
        self.candidate_changed()
        self.stop.clear()
        self.last_voice = voice
        root,trial,selection,stop = self.c.root,self.trial,self.selection,self.stop
        def work(_):
            with closing(Catalog(root)) as c:
                return audition(c,trial,voice,selection,stop)
        self.worker = Operation(work,self)
        self.worker.result.connect(self.sample_ready)
        self.worker.failed.connect(self.notice.setText)
        self.worker.finished.connect(self.sample_finished)
        self.search.setEnabled(False)
        self.list.setEnabled(False)
        self.update_sample_actions()
        self.notice.setText('正在查找同声线录音，缺少时生成这一句…')
        self.worker.start()

    def sample_ready(self,take):
        if self.stop.is_set() or self.selected_voice() != take.voice:
            return
        if self.c.progress.selection != self.selection:
            self.notice.setText('游戏账号已切换，请重新打开角色试音')
            return
        self.take = take
        self.notice.setText('正在播放 · '+('复用已有录音' if take.reused else '已生成本句试音'))
        self.player.setSource(C.QUrl.fromLocalFile(str(take.path)))
        self.player.play()

    def sample_finished(self):
        self.worker.deleteLater()
        self.worker = None
        if self.stop.is_set():
            self.reject()
            return
        self.search.setEnabled(True)
        self.list.setEnabled(True)
        self.update_sample_actions()

    def played(self,position):
        if (position > 0 and not self.heard and self.take and self.selected_voice()==self.take.voice
                and self.player.source()==C.QUrl.fromLocalFile(str(self.take.path))):
            self.heard = True
            self.update_sample_actions()

    def playback_error(self,_,message):
        self.heard = False
        self.notice.setText('播放失败 · '+message)
        self.update_sample_actions()

    def reject(self):
        if self.trial:
            self.player.stop()
            if self.worker:
                self.stop.set()
                self.notice.setText('正在结束试音，已提交的录音会保留在缓存中')
                return
        super().reject()

    def accept(self,*_):
        item=self.list.currentItem()
        if item and not item.isHidden():
            try:
                if self.trial:
                    if self.worker or not self.heard or not self.take or self.take.voice != self.selected_voice():
                        return
                    confirm_take(self.c,self.take,self.selection,self.previous)
                    self.player.stop()
                else:
                    choose_cloud_voice(self.c,self.row['speaker_id'],item.data(C.Qt.UserRole))
            except (ValueError,OSError,sqlite3.Error) as exc:
                self.notice.setText(str(exc))
                return
            super().accept()

