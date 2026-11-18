"""Navigation and voice browser for the ASGSV Qt Widgets workbench."""
from PySide6 import QtCore as C, QtWidgets as W

from ui.icons import bind_button_icon
from wuwa_workflow import voice_label


class Sidebar(W.QFrame):
    requested = C.Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName('workbenchSidebar')
        self.setFixedWidth(176)
        self.collapsed = False
        layout = W.QVBoxLayout(self)
        layout.setContentsMargins(6, 16, 7, 12)
        layout.setSpacing(4)
        self.buttons = {}
        self.captions = []
        for group, entries in (
            ('工作台', (('实时配音', 'tacet_voice'), ('任务库', 'quest_wave'), ('角色音色', 'echo_role'))),
            ('系统', (('快捷键', 'keyboard'), ('运行日志', 'terminal'), ('设置', 'settings'))),
        ):
            if self.captions:
                layout.addSpacing(16)
            caption = W.QLabel(group)
            caption.setObjectName('navCaption')
            layout.addWidget(caption)
            self.captions.append(caption)
            for title, icon in entries:
                button = self.make_button(title, icon)
                button.setCheckable(True)
                button.setAutoExclusive(True)
                button.clicked.connect(lambda checked=False, key=title: self.requested.emit(key))
                layout.addWidget(button)
                self.buttons[title] = button
        layout.addStretch()
        self.fold = self.make_button('收起侧栏', 'sidebar_collapse')
        self.fold.clicked.connect(self.toggle_width)
        layout.addWidget(self.fold)
        self.motion = C.QVariantAnimation(self)
        self.motion.setDuration(180)
        self.motion.setEasingCurve(C.QEasingCurve.OutCubic)
        self.motion.valueChanged.connect(self.setFixedWidth)

    def make_button(self, title, icon):
        button = W.QToolButton()
        button.setObjectName('navItem')
        button.setText(title)
        button.setAccessibleName(title)
        button.setToolTip(title)
        button.setCursor(C.Qt.PointingHandCursor)
        button.setToolButtonStyle(C.Qt.ToolButtonTextBesideIcon)
        button.setSizePolicy(W.QSizePolicy.Expanding, W.QSizePolicy.Fixed)
        button.setFixedHeight(38)
        bind_button_icon(button, icon, normal_color='#B7C7C9', size=18)
        return button

    def toggle_width(self):
        self.collapsed = not self.collapsed
        style = C.Qt.ToolButtonIconOnly if self.collapsed else C.Qt.ToolButtonTextBesideIcon
        for button in (*self.buttons.values(), self.fold):
            button.setToolButtonStyle(style)
        for title, button in self.buttons.items():
            button.setToolTip('打开' + title if self.collapsed else title)
        for caption in self.captions:
            caption.setVisible(not self.collapsed)
        title = '展开侧栏' if self.collapsed else '收起侧栏'
        self.fold.setText(title)
        self.fold.setToolTip(title)
        self.fold.setAccessibleName(title)
        self.motion.stop()
        self.motion.setStartValue(self.width())
        self.motion.setEndValue(54 if self.collapsed else 176)
        self.motion.start()


class RoleVoices(W.QWidget):
    edit_requested = C.Signal(object)

    def __init__(self, catalog):
        super().__init__()
        self.catalog = catalog
        layout = W.QVBoxLayout(self)
        title = W.QLabel('角色音色')
        title.setObjectName('sectionTitle')
        layout.addWidget(title)
        self.search = W.QLineEdit()
        self.search.setPlaceholderText('搜索角色名称')
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.list = W.QListWidget()
        self.list.setAccessibleName('角色列表')
        layout.addWidget(self.list, 1)
        row = W.QHBoxLayout()
        self.voice = W.QLabel()
        row.addWidget(self.voice, 1)
        self.edit = W.QPushButton('设置角色声线')
        row.addWidget(self.edit)
        layout.addLayout(row)
        self.list.currentItemChanged.connect(self.show_voice)
        self.search.textChanged.connect(self.filter_roles)
        self.edit.clicked.connect(self.edit_current)
        self.list.itemActivated.connect(self.edit_current)

    def refresh(self):
        previous = self.list.currentItem()
        selected = previous.data(C.Qt.UserRole) if previous else None
        self.list.clear()
        # A speaker can occur in many lines. Keep separate identities for namesakes.
        for speaker_id, speaker in self.catalog.db.execute(
            'SELECT speaker_id, MIN(speaker) FROM lines GROUP BY speaker_id ORDER BY MIN(speaker)'
        ):
            item = W.QListWidgetItem(speaker or '角色待确认', self.list)
            item.setData(C.Qt.UserRole, speaker_id)
            if speaker_id == selected:
                self.list.setCurrentItem(item)
        self.filter_roles(self.search.text())

    def filter_roles(self, query):
        query = query.strip().casefold()
        first = None
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(query not in item.text().casefold())
            if first is None and not item.isHidden():
                first = item
        current = self.list.currentItem()
        if not current or current.isHidden():
            self.list.setCurrentItem(first)
        self.show_voice()

    def show_voice(self):
        item = self.list.currentItem()
        self.edit.setEnabled(item is not None)
        if item is None:
            self.voice.setText('没有匹配的角色')
            return
        binding = self.catalog.binding(item.data(C.Qt.UserRole))
        self.voice.setText(voice_label(binding) if binding else '尚未绑定声线')

    def edit_current(self):
        item = self.list.currentItem()
        if item:
            self.edit_requested.emit(dict(speaker_id=item.data(C.Qt.UserRole), speaker=item.text()))
            self.show_voice()
