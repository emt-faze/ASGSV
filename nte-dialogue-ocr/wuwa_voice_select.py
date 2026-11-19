"""Searchable cloud voice picker; identity stays attached to the selected row."""
from PySide6 import QtCore as C, QtWidgets as W
from quest_library import choose_cloud_voice

LABELS={'male':'男声','female':'女声','young':'青年','middle_aged':'中年','old':'老年','child':'儿童'}

class CloudVoiceDialog(W.QDialog):
    def __init__(self,c,row,parent=None):
        super().__init__(parent)
        self.c,self.row=c,row
        self.setWindowTitle('选择音色 · '+row['speaker'])
        self.resize(470,450)
        layout=W.QVBoxLayout(self)
        self.search=W.QLineEdit()
        self.search.setPlaceholderText('搜索音色名称、已绑定角色、年龄、性别或 Voice ID')
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.list=W.QListWidget()
        layout.addWidget(self.list,1)
        self.notice=W.QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        controls=W.QDialogButtonBox(W.QDialogButtonBox.Ok|W.QDialogButtonBox.Cancel)
        self.apply=controls.button(W.QDialogButtonBox.Ok)
        self.apply.setText('使用所选音色')
        controls.button(W.QDialogButtonBox.Cancel).setText('取消')
        layout.addWidget(controls)
        binding=c.binding(row['speaker_id'])
        current=binding['voice'] if binding else ''
        roles={}
        for vid,speaker in c.db.execute('SELECT DISTINCT b.voice_id,l.speaker FROM breeze_cast b JOIN lines l ON l.speaker_id=b.speaker_id'):
            roles.setdefault(vid,[]).append(speaker)
        selected=None
        for vid,name,gender,age in c.db.execute('SELECT id,name,gender,age FROM breeze_presets ORDER BY name,id'):
            tags=' · '.join(LABELS[k] for k in (gender,age) if k in LABELS)
            item=W.QListWidgetItem(name+('　'+tags if tags else ''))
            item.setData(C.Qt.UserRole,vid)
            terms=' '.join([name,vid,tags,gender or '',age or '',*roles.get(vid,[])])
            item.setData(C.Qt.UserRole+1,terms.casefold())
            item.setToolTip(terms)
            self.list.addItem(item)
            if current=='breeze:'+vid:
                selected=item
        if selected:
            self.list.setCurrentItem(selected)
        self.search.textChanged.connect(self.filter)
        self.list.itemDoubleClicked.connect(self.accept)
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
        self.apply.setEnabled(bool(visible))
        self.notice.setText(f'{len(visible)} 个可选音色' if visible else '没有匹配音色，可更换关键词或在每日计划中同步预设。')

    def accept(self,*_):
        item=self.list.currentItem()
        if item and not item.isHidden():
            try:
                choose_cloud_voice(self.c,self.row['speaker_id'],item.data(C.Qt.UserRole))
            except ValueError as exc:
                self.notice.setText(str(exc))
                return
            super().accept()

