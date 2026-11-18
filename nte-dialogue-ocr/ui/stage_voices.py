"""Speaker-specific preset selection and non-billable auditions for the live stage."""
import json
from pathlib import Path

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from dialogue_dubbing import voice_paths
from system_voices import PRESETS
from ui.icons import get_icon


def presets(catalog, config, speaker_id):
    if catalog.cloud_only():
        for vid, name in catalog.db.execute('SELECT id,name FROM breeze_presets ORDER BY name,id'):
            yield name, 'breeze:' + vid, None
        return
    for key, (name, *_) in PRESETS.items():
        yield name, 'system:' + key, None
    profiles = voice_paths(config['dubbing'])[1] / 'profiles'
    from wuwa_npc_voices import choices
    from wuwa_dubber.models import VoiceProfile
    for path in sorted(profiles.glob('*/profile.json')):
        try:
            values = json.loads(path.read_text(encoding='utf-8-sig'))
            profile = VoiceProfile.from_dict(values)
            sample = profile.resolve_reference(profiles)
            if values.get('enabled', True) and profile.prompt_text and sample.is_file():
                yield profile.speaker, 'character:' + profile.speaker, sample
        except (ValueError, OSError, KeyError):
            continue
    for name, key, profile in choices(catalog.root, speaker_id=speaker_id):
        yield name, key, profile.resolve_reference(profiles)


def bind_current(catalog, row, voice):
    """Write one explicit choice. LogWorker.decision observes the committed binding."""
    sid = row['speaker_id']
    if not catalog.cloud_only():
        catalog.set_binding(sid, row['speaker'], voice, True)
        return
    provider, _, vid = voice.partition(':')
    if provider != 'breeze' or not catalog.db.execute('SELECT 1 FROM breeze_presets WHERE id=?',(vid,)).fetchone():
        raise ValueError('此音色不在当前云端预设库中')
    from wuwa_cast_review import line_problem, original_problem
    with catalog.db:
        locked = catalog.db.execute('SELECT profile FROM original_reference_cast WHERE speaker_id=?',(sid,)).fetchone()
        if locked:
            raise ValueError('该角色已绑定原声参考：' + locked[0])
        problem = original_problem(catalog,sid,vid) or line_problem(catalog,row['id'],sid,vid)
        if problem:
            raise ValueError(problem)
        catalog.db.execute('INSERT OR REPLACE INTO breeze_cast VALUES(?,?)',(sid,vid))


class SampleLookup(C.QObject):
    resolved = C.Signal(str,str)

    def __init__(self, voice):
        super().__init__()
        self.voice = voice

    def run(self):
        from urllib.request import Request, build_opener
        from wuwa_breeze import api_key, voice_id, NoRedirect
        try:
            secret = api_key()
            if not secret:
                raise ValueError('请先在设置中填写 BreezeBlue 密钥')
            req = Request('https://api.breeze.blue/v1/voices/'+voice_id(self.voice),
                          headers={'xi-api-key':secret})
            with build_opener(NoRedirect()).open(req,timeout=10) as response:
                address = json.load(response).get('preview_url') or ''
            if not address.startswith('https://'):
                raise ValueError('该预设未提供试听音频')
            self.resolved.emit(address,'')
        except (ValueError,OSError) as exc:
            self.resolved.emit('',str(exc))


class VoiceMenu(W.QFrame):
    selected = C.Signal(str)

    def __init__(self, parent, entries, current):
        super().__init__(parent, C.Qt.Popup)
        self.setObjectName('stagePopup')
        self.setAttribute(C.Qt.WA_DeleteOnClose)
        self.setFixedSize(310, 344)
        self.lookup = None
        self.sapi = None
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        layout = W.QVBoxLayout(self)
        self.search = W.QLineEdit()
        self.search.setPlaceholderText('搜索音色')
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.list = W.QTreeWidget()
        self.list.setRootIsDecorated(False)
        self.list.setHeaderLabels(['音色', '试听'])
        self.list.setAccessibleName('音色预设')
        self.list.header().setStretchLastSection(False)
        self.list.header().setSectionResizeMode(0,W.QHeaderView.Stretch)
        self.list.setColumnWidth(1,44)
        play = get_icon('play', '#ACC5CD', 15)
        mark = get_icon('check', '#D8B467', 14)
        for name, key, sample in entries:
            item = W.QTreeWidgetItem(self.list, [name,''])
            item.setData(0,C.Qt.UserRole,key)
            item.setData(1,C.Qt.UserRole,str(sample) if sample else '')
            item.setIcon(1,play)
            item.setToolTip(1,'试听 ' + name)
            if key == current:
                item.setIcon(0,mark)
                self.list.setCurrentItem(item)
        layout.addWidget(self.list,1)
        self.note = W.QLabel('点名称应用，点播放图标试听' if self.list.topLevelItemCount() else '暂无可用音色，请先在设置中同步音色库')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.player.errorOccurred.connect(lambda error,msg: self.note.setText('试听失败：'+msg))
        self.list.itemClicked.connect(self.choose)
        self.search.textChanged.connect(self.filter)
        self.listen_key = G.QShortcut(G.QKeySequence('Space'),self.list)
        self.listen_key.activated.connect(lambda:self.choose(self.list.currentItem(),1))
        self.apply_key = G.QShortcut(G.QKeySequence('Return'),self.list)
        self.listen_key.setContext(C.Qt.WidgetShortcut)
        self.apply_key.setContext(C.Qt.WidgetShortcut)
        self.apply_key.activated.connect(lambda:self.choose(self.list.currentItem(),0))

    def filter(self,text):
        for i in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(i)
            item.setHidden(text.casefold() not in item.text(0).casefold())

    def choose(self,item,column):
        if item is None or item.isHidden():
            return
        self.stop_sample()
        key = item.data(0,C.Qt.UserRole)
        if column == 0:
            self.selected.emit(key)
            return
        self.note.setText('试听 · ' + item.text(0))
        if key.startswith('breeze:'):
            self.lookup = SampleLookup(key[7:])
            self.lookup.resolved.connect(self.sample_ready)
            C.QThreadPool.globalInstance().start(self.lookup.run)
        elif key.startswith('system:'):
            import win32com.client
            try:
                self.sapi = win32com.client.Dispatch('SAPI.SpVoice')
                _,token,rate,pitch = PRESETS[key[7:]]
                if token:
                    voice = win32com.client.Dispatch('SAPI.SpObjectToken')
                    voice.SetId(token)
                    self.sapi.Voice = voice
                self.sapi.Rate = rate
                self.sapi.Speak(f'<pitch absmiddle="{pitch}">你好，这里是角色音色试听。</pitch>',9)
            except Exception as exc:
                self.note.setText('本机语音不可用：'+str(exc))
        else:
            path = Path(item.data(1,C.Qt.UserRole))
            if path.is_file():
                self.player.setSource(C.QUrl.fromLocalFile(str(path)))
                self.player.play()
            else:
                self.note.setText('未找到该音色的参考录音')

    def sample_ready(self, address, error):
        if self.lookup is None or self.sender() is not self.lookup or not self.isVisible():
            return
        self.lookup = None
        if error:
            self.note.setText('试听不可用：' + error)
        else:
            self.player.setSource(C.QUrl(address))
            self.player.play()

    def stop_sample(self):
        self.lookup = None
        self.player.stop()
        if self.sapi:
            self.sapi.Speak('',3)
            self.sapi = None

    def hideEvent(self,event):
        self.stop_sample()
        super().hideEvent(event)
