"""Standalone native UI for offline NTE dialogue preparation."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading

from PySide6 import QtCore, QtGui, QtWidgets as W

from .bootstrap import ASGSV, ROOT, VERSION, load_settings, save_settings
from .organizer_core import Cancelled, Organizer, VOICE_LABELS, local_time
from generator_ui_support import configure_dialog, fit_window_to_screen, set_help

GROUP_NAMES = {
    'ST_Dialogue_CityMark_Chat': '城市 NPC 对话',
    'ST_Dialogue_WorldHide_Chat': '世界隐藏对话',
    'ST_MainDialogue': '主线对话', 'ST_Dialogue_Side': '支线对话',
    'ST_SideQuest': '支线任务', 'ST_SideQuest_QQ': '支线任务 QQ',
    'ST_AmbianceDialogue': '环境对话', 'ST_Dialogue_Liking': '羁遇对话',
    'ST_Dialogue_Legend': '番外对话', 'ST_Likeability': '好感相关资源',
    'ST_Bubble_CityMark_Chat': '城市 NPC 气泡文本',
}


class Job(QtCore.QThread):
    progress = QtCore.Signal(str)
    result = QtCore.Signal(object)
    error = QtCore.Signal(str)

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action = action
        self.stop = threading.Event()

    def cancel(self):
        self.stop.set()

    def run(self):
        try:
            self.result.emit(self.action(self.stop, self.progress.emit))
        except Cancelled:
            self.result.emit({'cancelled': True})
        except Exception as exc:
            self.error.emit(str(exc))


class LineModel(QtCore.QAbstractTableModel):
    def __init__(self, *, work=False, on_check=None, parent=None):
        super().__init__(parent)
        self.rows = []
        self.work = work
        self.on_check = on_check
        self.columns = [('speaker','角色'),('text','台词'),('namespace','资源分组'),('text_key','台词编号'),
                        ('voice_status','原声记录'),('event_count','日志事件数')]
        if work:
            self.columns = [('selected','勾选'),('speaker','角色'),('text','整理后的台词'),('text_key','台词编号'),
                            ('voice_status','原声记录'),('issue','待处理事项')]

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return len(self.columns)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole and orientation == QtCore.Qt.Horizontal:
            return self.columns[section][1]
        return None

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = self.columns[index.column()][0]
        if key == 'selected':
            if role == QtCore.Qt.CheckStateRole:
                return QtCore.Qt.Checked if row['selected'] else QtCore.Qt.Unchecked
            return None
        value = row.get(key, '')
        if key == 'voice_status':
            value = VOICE_LABELS.get(value, '')
        if key == 'speaker' and not value:
            value = '角色待补全'
        if key == 'namespace':
            value = GROUP_NAMES.get(value, value)
        if role == QtCore.Qt.DisplayRole:
            return str(value).replace('\r',' ').replace('\n',' ')
        if role == QtCore.Qt.ToolTipRole:
            return str(value)
        if role == QtCore.Qt.ForegroundRole:
            if key == 'issue' and value:
                return QtGui.QBrush(QtGui.QColor('#fbbf24'))
            if key == 'voice_status':
                if value == '日志含原声事件':
                    return QtGui.QBrush(QtGui.QColor('#34d399'))
                elif value == '原声状态待确认':
                    return QtGui.QBrush(QtGui.QColor('#fbbf24'))
                elif value == '日志标记无原声':
                    return QtGui.QBrush(QtGui.QColor('#f87171'))
            if key == 'speaker' and value == '角色待补全':
                return QtGui.QBrush(QtGui.QColor('#fb923c'))
        if role == QtCore.Qt.BackgroundRole:
            if key == 'voice_status':
                if value == '日志含原声事件':
                    return QtGui.QBrush(QtGui.QColor('#0d3323'))
                elif value == '原声状态待确认':
                    return QtGui.QBrush(QtGui.QColor('#3b2b11'))
                elif value == '日志标记无原声':
                    return QtGui.QBrush(QtGui.QColor('#3b1319'))
            if key == 'speaker' and value == '角色待补全':
                return QtGui.QBrush(QtGui.QColor('#381e11'))
            if key == 'issue' and value:
                return QtGui.QBrush(QtGui.QColor('#3b2b11'))
        if role == QtCore.Qt.TextAlignmentRole:
            if key in ('selected', 'voice_status', 'event_count'):
                return int(QtCore.Qt.AlignCenter)
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and self.work and index.column() == 0:
            flags |= QtCore.Qt.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        if self.work and index.isValid() and index.column() == 0 and role == QtCore.Qt.CheckStateRole:
            checked = value in (QtCore.Qt.Checked, QtCore.Qt.Checked.value)
            row = self.rows[index.row()]
            if self.on_check and not self.on_check(row['identity'], checked):
                return False
            row['selected'] = int(checked)
            self.dataChanged.emit(index, index, [QtCore.Qt.CheckStateRole])
            return True
        return False


class SettingsDialog(W.QDialog):
    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setWindowTitle('数据与称谓设置')
        self.resize(730, 290)
        self.options = dict(options)
        layout = W.QFormLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        self.catalog = W.QLineEdit(options['catalog_path'])
        self.catalog.setMinimumHeight(32)
        catalog_row = W.QHBoxLayout()
        catalog_row.addWidget(self.catalog)
        choose = W.QPushButton('选择原文库…')
        choose.setMinimumHeight(32)
        choose.clicked.connect(self.choose_catalog)
        catalog_row.addWidget(choose)
        layout.addRow('中文原文库', catalog_row)
        self.logs = W.QLineEdit(options['log_directory'])
        self.logs.setMinimumHeight(32)
        log_row = W.QHBoxLayout()
        log_row.addWidget(self.logs)
        choose_logs = W.QPushButton('选择文件夹…')
        choose_logs.setMinimumHeight(32)
        choose_logs.clicked.connect(self.choose_logs)
        log_row.addWidget(choose_logs)
        layout.addRow('游戏日志文件夹', log_row)
        self.player = W.QLineEdit(options['player_name'])
        self.player.setMinimumHeight(32)
        layout.addRow('玩家称呼', self.player)
        self.gender = W.QComboBox()
        self.gender.setMinimumHeight(32)
        for label, value in (('保留原文中的称谓分支', None), ('使用男性称谓', 0), ('使用女性称谓', 1)):
            self.gender.addItem(label, value)
        self.gender.setCurrentIndex(max(0,self.gender.findData(options.get('gender'))))
        layout.addRow('主角称谓分支', self.gender)
        note = W.QLabel('称谓设置用于读取原文。整理清单内可以逐条编辑角色与台词。')
        note.setWordWrap(True)
        note.setStyleSheet("""
            background: #e8f5f2;
            border: 1px solid #b8e2d8;
            border-radius: 6px;
            padding: 8px 12px;
            color: #0f766e;
            font-size: 12px;
        """)
        layout.addRow(note)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.Save | W.QDialogButtonBox.Cancel)
        save_btn = buttons.button(W.QDialogButtonBox.Save)
        if save_btn:
            save_btn.setObjectName('primary')
            save_btn.setText('保存设置')
            save_btn.setMinimumHeight(34)
        cancel_btn = buttons.button(W.QDialogButtonBox.Cancel)
        if cancel_btn:
            cancel_btn.setText('取消')
            cancel_btn.setMinimumHeight(34)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        configure_dialog(self, 'organizer_settings')
        fit_window_to_screen(self, 730, 320)

    def choose_catalog(self):
        path, _ = W.QFileDialog.getOpenFileName(self,'选择异环原文库',self.catalog.text(),'SQLite 数据库 (*.sqlite *.db)')
        if path:
            self.catalog.setText(path)

    def choose_logs(self):
        path = W.QFileDialog.getExistingDirectory(self,'选择游戏日志文件夹',self.logs.text())
        if path:
            self.logs.setText(path)

    def values(self):
        return dict(self.options,catalog_path=self.catalog.text().strip(),log_directory=self.logs.text().strip(),
                    player_name=self.player.text().strip() or '鉴定师',gender=self.gender.currentData())


class OrganizerWindow(W.QWidget):
    project_created = QtCore.Signal(object)
    stopped = QtCore.Signal()
    busy_changed = QtCore.Signal(bool)

    def __init__(self, options=None, data_directory=None, parent=None, *, project_opener=None):
        super().__init__(parent)
        auto_prepare = options is None and data_directory is None
        self.persist_settings = options is None
        self.project_opener = project_opener
        self.catalog_preparer = None
        self.store_closed = False
        self.operation_active = False
        self.options = dict(options or load_settings())
        self.data_directory = Path(data_directory or ROOT / 'data')
        self.store = self.new_store()
        self.worker = self.updater = None
        self.closing = self.loading_editor = False
        self.editor_id = None
        self.editor_dirty = False
        self.pages = [0,0]
        self.totals = [0,0]
        self.setWindowTitle('异环台词整理 v' + VERSION)
        if parent is None:
            self.setMinimumSize(900,600)
        icon = ASGSV.parent / 'wuwa-dubber/assets/wuwa-dubber.ico'
        if icon.is_file():
            self.setWindowIcon(QtGui.QIcon(str(icon)))
        self.body = W.QWidget()
        frame = W.QVBoxLayout(self)
        frame.setContentsMargins(0,0,0,0)
        frame.addWidget(self.body,1)
        outer = W.QVBoxLayout(self.body)
        outer.setContentsMargins(16, 14, 16, 14)
        if parent is not None:
            outer.setContentsMargins(0,0,0,0)
        outer.setSpacing(10)
        installation_row = W.QHBoxLayout()
        installation_row.addWidget(W.QLabel('游戏安装目录'))
        self.game_directory = W.QLineEdit()
        self.game_directory.setReadOnly(True)
        self.game_directory.setPlaceholderText('自动查找已安装的异环客户端')
        installation_row.addWidget(self.game_directory, 1)
        self.extract_button = W.QPushButton('自动识别并提取')
        self.extract_button.setObjectName('primary')
        self.extract_button.clicked.connect(lambda: self.update_catalog())
        installation_row.addWidget(self.extract_button)
        self.choose_game_button = W.QPushButton('选择目录…')
        self.choose_game_button.clicked.connect(self.choose_game_directory)
        installation_row.addWidget(self.choose_game_button)
        outer.addLayout(installation_row)
        toolbar = W.QHBoxLayout()
        toolbar.setSpacing(8)
        for label, action in (('读取本机历史日志',self.import_local_logs),('选择日志文件…',self.choose_logs),
                              ('查看未定位编号',self.show_unresolved),
                              ('数据设置',self.settings),('使用帮助',self.help)):
            button = W.QPushButton(label)
            button.setMinimumHeight(34)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        toolbar.addStretch()
        outer.addLayout(toolbar)
        self.summary = W.QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("""
            background: #141722;
            border: 1px solid #283044;
            border-left: 4px solid #6366f1;
            border-radius: 8px;
            padding: 10px 16px;
            color: #e2e8f0;
            font-weight: 600;
            font-size: 13px;
        """)
        outer.addWidget(self.summary)
        self.tabs = W.QTabWidget()
        outer.addWidget(self.tabs,1)
        self.models = [LineModel(parent=self),LineModel(work=True,on_check=self.check_item,parent=self)]
        self.tables = []
        self.page_labels = []
        self.previous_buttons = []
        self.next_buttons = []
        self._build_browse()
        self._build_work()
        self.status_bar = W.QStatusBar(self)
        self.status_bar.setSizeGripEnabled(parent is None)
        frame.addWidget(self.status_bar)
        self.status_bar.setStyleSheet("QStatusBar { background: #0c0e14; border-top: 1px solid #23293b; color: #94a3b8; font-size: 12px; }")
        self.status = W.QLabel('可在游戏关闭时检索原文与解析日志')
        self.status.setWordWrap(True)
        self.status_bar.addWidget(self.status,1)
        self.progress = W.QProgressBar()
        self.progress.setMaximumWidth(160)
        self.progress.setRange(0,0)
        self.progress.hide()
        self.status_bar.addPermanentWidget(self.progress)
        self.cancel_button = W.QPushButton('取消当前操作')
        self.cancel_button.setMinimumHeight(26)
        self.cancel_button.clicked.connect(self.cancel_job)
        self.cancel_button.hide()
        self.status_bar.addPermanentWidget(self.cancel_button)
        self.search_timer = QtCore.QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.apply_filter)
        self.editor_timer = QtCore.QTimer(self)
        self.editor_timer.setSingleShot(True)
        self.editor_timer.timeout.connect(self.save_editor)
        self.keyword.textChanged.connect(self.queue_filter)
        self.speaker.currentTextChanged.connect(self.queue_filter)
        for widget in (self.group,self.source,self.voice):
            widget.currentIndexChanged.connect(self.apply_filter)
        self.dialogue_only.toggled.connect(self.scope_changed)
        for widget in (self.editor_speaker,self.editor_text,self.editor_note):
            widget.textChanged.connect(self.editor_changed)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.load_facets()
        self.refresh_all()
        configure_dialog(self, 'organizer')
        set_help(self.keyword, '输入台词正文、台词编号或任务码，稍后自动更新结果。', '搜索台词')
        set_help(self.speaker, '选择或输入角色名称，与其他条件一起筛选。', '角色筛选')
        set_help(self.group, '选择台词所在的资源分组。', '资源分组')
        set_help(self.source, '选择原文库全部条目，或历史日志中已定位的条目。', '数据来源')
        set_help(self.voice, '按日志中的原声记录筛选，待确认表示尚无充分的原声状态记录。', '原声状态')
        set_help(self.extract_button, '自动查找完整的异环客户端，读取中文资源并更新配音共用的台词库。', '提取游戏台词')
        set_help(self.choose_game_button, '选择异环安装目录或 Client 子目录，然后提取台词。', '选择游戏目录')
        if auto_prepare:
            QtCore.QTimer.singleShot(0, lambda: self.update_catalog(force=False) if not self.store_closed else None)
        if parent is None:
            fit_window_to_screen(self, 1250, 850)
        else:
            self.selection_count.setStyleSheet("""
                background: #1e1b38;
                border: 1px solid #4338ca;
                border-radius: 6px;
                padding: 5px 14px;
                color: #a78bfa;
                font-weight: 700;
                font-size: 12px;
            """)
            self.open_project.setText('打开生成与试听')
            for splitter in (self.browse_splitter,self.work_splitter):
                splitter.setOrientation(QtCore.Qt.Horizontal)
                splitter.setStretchFactor(0,2)
                splitter.setStretchFactor(1,1)
                splitter.setSizes([760,400])
            for column in (2,3,5):
                self.tables[0].setColumnHidden(column,True)
            self.tables[1].setColumnHidden(3,True)
            self.tables[0].setColumnWidth(0,100)
            self.tables[1].setColumnWidth(1,100)
            self.tables[1].setColumnWidth(5,100)

    def new_store(self):
        return Organizer(self.data_directory,self.options['catalog_path'],
                         player_name=self.options['player_name'],gender=self.options.get('gender'))

    def _table(self, number, layout):
        table = W.QTableView()
        table.setModel(self.models[number])
        table.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        table.setSelectionMode(W.QAbstractItemView.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setMinimumHeight(150)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(38)
        table.setShowGrid(True)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setHighlightSections(False)
        for column in range(self.models[number].columnCount()):
            table.horizontalHeader().setSectionResizeMode(column,W.QHeaderView.Interactive)
            table.setColumnWidth(column,140)
        text_column = 2 if number else 1
        table.horizontalHeader().setSectionResizeMode(text_column,W.QHeaderView.Stretch)
        if number:
            table.setColumnWidth(0,55)
            table.setColumnWidth(1,120)
            table.setColumnWidth(3,180)
            table.setColumnWidth(4,110)
            table.setColumnWidth(5,130)
        else:
            table.setColumnWidth(0,120)
            table.setColumnWidth(2,160)
            table.setColumnWidth(3,180)
            table.setColumnWidth(4,110)
            table.setColumnWidth(5,90)
        table.selectionModel().currentRowChanged.connect(lambda current, previous: self.current_changed(number,current))
        self.tables.append(table)
        layout.addWidget(table,1)
        navigation = W.QHBoxLayout()
        navigation.setContentsMargins(0, 4, 0, 0)
        navigation.setSpacing(8)
        previous = W.QPushButton('上一页')
        previous.setMinimumHeight(30)
        previous.setCursor(QtCore.Qt.PointingHandCursor)
        previous.clicked.connect(lambda: self.turn_page(number,-1))
        label = W.QLabel()
        label.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 500; padding: 0 4px;")
        following = W.QPushButton('下一页')
        following.setMinimumHeight(30)
        following.setCursor(QtCore.Qt.PointingHandCursor)
        following.clicked.connect(lambda: self.turn_page(number,1))
        navigation.addWidget(previous)
        navigation.addWidget(label)
        navigation.addWidget(following)
        navigation.addStretch()
        layout.addLayout(navigation)
        self.previous_buttons.append(previous)
        self.next_buttons.append(following)
        self.page_labels.append(label)
        return table

    def _build_browse(self):
        page = W.QWidget()
        layout = W.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        search = W.QHBoxLayout()
        search.setSpacing(8)
        self.keyword = W.QLineEdit()
        self.keyword.setPlaceholderText('搜索台词、台词编号或任务码，例如 WJ1062')
        self.keyword.setClearButtonEnabled(True)
        self.keyword.setMinimumHeight(34)
        search.addWidget(self.keyword,2)
        self.speaker = W.QComboBox()
        self.speaker.setEditable(True)
        self.speaker.setInsertPolicy(W.QComboBox.NoInsert)
        self.speaker.setMinimumWidth(150)
        self.speaker.setMinimumHeight(34)
        self.speaker.completer().setFilterMode(QtCore.Qt.MatchContains)
        search.addWidget(self.speaker)
        self.group = W.QComboBox()
        self.group.setMinimumWidth(200)
        self.group.setMinimumHeight(34)
        search.addWidget(self.group)
        layout.addLayout(search)
        filters = W.QHBoxLayout()
        filters.setSpacing(8)
        self.source = W.QComboBox()
        self.source.setMinimumHeight(32)
        self.source.addItem('原文库全部条目','catalog')
        self.source.addItem('历史日志中已定位的台词','history')
        self.voice = W.QComboBox()
        self.voice.setMinimumHeight(32)
        self.voice.addItem('全部原声状态','')
        for value,label in VOICE_LABELS.items():
            self.voice.addItem(label,value)
        self.dialogue_only = W.QCheckBox('只看对话相关资源')
        self.dialogue_only.setChecked(True)
        filters.addWidget(self.source)
        filters.addWidget(self.voice)
        filters.addWidget(self.dialogue_only)
        filters.addStretch()
        reset = W.QPushButton('清空筛选')
        reset.setMinimumHeight(32)
        reset.setCursor(QtCore.Qt.PointingHandCursor)
        reset.clicked.connect(self.reset_filters)
        filters.addWidget(reset)
        layout.addLayout(filters)
        self.browse_splitter = W.QSplitter(QtCore.Qt.Vertical)
        self.browse_splitter.setChildrenCollapsible(False)
        self.browse_splitter.setHandleWidth(6)
        results = W.QWidget()
        results_layout = W.QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(6)
        self._table(0, results_layout)
        self.browse_splitter.addWidget(results)
        self.preview = W.QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(70)
        self.preview.setPlaceholderText('选中一条台词，查看完整正文、资源编号与日志来源')
        self.preview.setStyleSheet("""
            QPlainTextEdit {
                background: #141722;
                border: 1px solid #252b3d;
                border-radius: 8px;
                padding: 8px 10px;
                color: #e2e8f0;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                font-size: 12px;
            }
        """)
        self.browse_splitter.addWidget(self.preview)
        self.browse_splitter.setStretchFactor(0, 1)
        self.browse_splitter.setStretchFactor(1, 0)
        self.browse_splitter.setSizes([500, 100])
        layout.addWidget(self.browse_splitter, 1)
        actions = W.QHBoxLayout()
        actions.setSpacing(10)
        self.add_selected = W.QPushButton('加入选中条目')
        self.add_selected.setObjectName('primary')
        self.add_selected.setMinimumHeight(36)
        self.add_selected.setCursor(QtCore.Qt.PointingHandCursor)
        self.add_selected.setStyleSheet("""
            QPushButton#primary {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0891b2, stop:0.5 #06b6d4, stop:1 #2563eb);
                border: 1px solid #0e7490;
                font-weight: 700;
                padding: 0 16px;
            }
            QPushButton#primary:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0e7490, stop:0.5 #0891b2, stop:1 #1d4ed8);
            }
        """)
        self.add_selected.clicked.connect(self.add_highlighted)
        self.add_filtered = W.QPushButton('加入当前筛选全部')
        self.add_filtered.setMinimumHeight(36)
        self.add_filtered.setCursor(QtCore.Qt.PointingHandCursor)
        self.add_filtered.clicked.connect(self.add_filter_results)
        actions.addWidget(self.add_selected)
        actions.addWidget(self.add_filtered)
        actions.addStretch()
        layout.addLayout(actions)
        self.tabs.addTab(page,'原文库与日志')

    def _build_work(self):
        page = W.QWidget()
        layout = W.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        actions = W.QHBoxLayout()
        actions.setSpacing(8)
        for label, action in (('勾选本页',lambda: self.select_page(True)),('取消本页勾选',lambda: self.select_page(False)),
                              ('上移',lambda: self.move(-1)),('下移',lambda: self.move(1)),('移出选中条目',self.remove)):
            button = W.QPushButton(label)
            button.setMinimumHeight(32)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.clicked.connect(action)
            actions.addWidget(button)
        actions.addStretch()
        self.selection_count = W.QLabel()
        self.selection_count.setStyleSheet("""
            background: #1e1b38;
            border: 1px solid #4338ca;
            border-radius: 6px;
            padding: 5px 14px;
            color: #a78bfa;
            font-weight: 700;
            font-size: 12px;
        """)
        actions.addWidget(self.selection_count)
        layout.addLayout(actions)
        self.work_splitter = W.QSplitter(QtCore.Qt.Vertical)
        self.work_splitter.setChildrenCollapsible(False)
        self.work_splitter.setHandleWidth(6)
        results = W.QWidget()
        results_layout = W.QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(6)
        self._table(1, results_layout)
        self.work_splitter.addWidget(results)
        editor = W.QGroupBox('编辑当前台词 · 自动保存')
        form = W.QFormLayout(editor)
        form.setContentsMargins(10, 8, 10, 8)
        form.setSpacing(8)
        self.editor_speaker = W.QLineEdit()
        self.editor_speaker.setMinimumHeight(32)
        form.addRow('角色',self.editor_speaker)
        self.editor_text = W.QPlainTextEdit()
        self.editor_text.setMinimumHeight(55)
        self.editor_text.setMaximumHeight(85)
        form.addRow('台词',self.editor_text)
        self.editor_note = W.QLineEdit()
        self.editor_note.setMinimumHeight(32)
        form.addRow('备注',self.editor_note)
        self.original = W.QPlainTextEdit()
        self.original.setReadOnly(True)
        self.original.setMinimumHeight(80)
        self.original.setMaximumHeight(120)
        self.original.setStyleSheet("""
            color: #cbd5e1;
            font-size: 12px;
            background: #141722;
            border-radius: 6px;
            padding: 8px 12px;
            border: 1px solid #283044;
        """)
        form.addRow(self.original)
        self.editor_scroll = W.QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setFrameShape(W.QFrame.NoFrame)
        self.editor_scroll.setMinimumHeight(100)
        self.editor_scroll.setWidget(editor)
        self.work_splitter.addWidget(self.editor_scroll)
        self.work_splitter.setStretchFactor(0, 1)
        self.work_splitter.setStretchFactor(1, 1)
        self.work_splitter.setSizes([420, 160])
        layout.addWidget(self.work_splitter, 1)
        footer = W.QHBoxLayout()
        footer.setSpacing(10)
        for label, format in (('导出 CSV…','csv'),('导出 TXT…','txt'),('导出 JSON…','json')):
            button = W.QPushButton(label)
            button.setMinimumHeight(36)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.clicked.connect(lambda checked=False, kind=format: self.export(kind))
            footer.addWidget(button)
        footer.addStretch()
        self.project_button = W.QPushButton('建立配音项目…')
        self.project_button.setObjectName('primary')
        self.project_button.setMinimumHeight(38)
        self.project_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.project_button.setStyleSheet("""
            QPushButton#primary {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4f46e5, stop:0.5 #6366f1, stop:1 #7c3aed);
                border: 1px solid #4338ca;
                font-weight: 700;
                padding: 0 18px;
            }
            QPushButton#primary:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4338ca, stop:0.5 #4f46e5, stop:1 #6d28d9);
            }
        """)
        self.project_button.clicked.connect(self.create_project)
        footer.addWidget(self.project_button)
        self.open_project = W.QPushButton('在配音工具中打开')
        self.open_project.setMinimumHeight(38)
        self.open_project.setCursor(QtCore.Qt.PointingHandCursor)
        self.open_project.setStyleSheet("font-weight: 600; padding: 0 14px;")
        self.open_project.clicked.connect(self.open_in_generator)
        footer.addWidget(self.open_project)
        layout.addLayout(footer)
        self.tabs.addTab(page,'整理清单')

    def load_facets(self):
        previous_speaker, previous_group = self.speaker.currentText(), self.group.currentData()
        self.speaker.blockSignals(True)
        self.group.blockSignals(True)
        self.speaker.clear()
        self.speaker.addItems(['全部角色','角色待补全',*self.store.speakers()])
        self.speaker.setCurrentText(previous_speaker or '全部角色')
        self.group.clear()
        self.group.addItem('全部资源分组','')
        for group in self.store.groups(self.dialogue_only.isChecked()):
            self.group.addItem(GROUP_NAMES.get(group,group),group)
        self.group.setCurrentIndex(max(0,self.group.findData(previous_group)))
        self.group.blockSignals(False)
        self.speaker.blockSignals(False)

    def filters(self):
        speaker = self.speaker.currentText().strip()
        return dict(keyword=self.keyword.text(),speaker='' if speaker=='全部角色' else speaker,
                    namespace=self.group.currentData(),source=self.source.currentData(),voice=self.voice.currentData(),
                    dialogue_only=self.dialogue_only.isChecked())

    def apply_filter(self, *args):
        if self.worker and self.worker.isRunning():
            return
        self.search_timer.stop()
        self.pages[0] = 0
        self.refresh(0)

    def queue_filter(self, *args):
        self.add_selected.setEnabled(False)
        self.add_filtered.setEnabled(False)
        self.search_timer.start(300)

    def scope_changed(self):
        self.load_facets()
        self.apply_filter()

    def reset_filters(self):
        self.keyword.clear()
        self.speaker.setCurrentIndex(0)
        self.group.setCurrentIndex(0)
        self.source.setCurrentIndex(0)
        self.voice.setCurrentIndex(0)
        self.dialogue_only.setChecked(True)
        self.apply_filter()

    def refresh(self, number):
        try:
            if number:
                if not self.save_editor():
                    return
                rows,total = self.store.work(page=self.pages[1])
            else:
                rows,total = self.store.browse(self.filters(),page=self.pages[0])
            if not rows and total and self.pages[number] > 0:
                self.pages[number] = (total-1)//200
                return self.refresh(number)
            self.totals[number] = total
            self.models[number].replace(rows)
            count = max(1,(total+199)//200)
            self.page_labels[number].setText(f'共 {total:,} 条 · 第 {self.pages[number]+1}/{count} 页')
            self.previous_buttons[number].setEnabled(self.pages[number]>0)
            self.next_buttons[number].setEnabled(self.pages[number]+1<count)
            if rows:
                self.tables[number].selectRow(0)
            if not number:
                self.add_filtered.setText(f'加入筛选结果（{total:,} 条）')
                set_help(self.add_filtered, f'将当前筛选匹配的全部 {total:,} 条台词加入整理清单，范围包含所有分页。')
                self.add_selected.setEnabled(bool(rows))
                self.add_filtered.setEnabled(bool(total))
            self.refresh_stats()
        except Exception as exc:
            self.show_error(str(exc))

    def refresh_all(self):
        self.refresh(0)
        self.refresh(1)

    def refresh_stats(self):
        stats = self.store.stats()
        self.summary.setText(f"中文资源 {stats['resources']:,} 条 · 对话相关资源 {stats['dialogue']:,} 条 · 已解析日志事件 {stats['events']:,} 条")
        self.selection_count.setText(f"清单 {stats['work']:,} 条 · 已勾选 {stats['selected']:,} 条")
        self.tabs.setTabText(1,f"整理清单（{stats['work']:,}）")
        self.open_project.setEnabled(bool(self.options.get('last_project') and (Path(self.options['last_project'])/'project.json').is_file()))

    def turn_page(self, number, direction):
        self.pages[number] = max(0,self.pages[number]+direction)
        self.refresh(number)

    def highlighted(self, number):
        return [self.models[number].rows[index.row()]['identity'] for index in self.tables[number].selectionModel().selectedRows()]

    def current_changed(self, number, index):
        if number:
            if not self.save_editor():
                return
            self.editor_timer.stop()
            self.loading_editor = True
            self.editor_id = None
            row = self.models[1].rows[index.row()] if index.isValid() and index.row()<len(self.models[1].rows) else None
            self.editor_speaker.setText(row['speaker'] if row else '')
            self.editor_text.setPlainText(row['text'] if row else '')
            self.editor_note.setText(row['note'] if row else '')
            self.original.setPlainText(('原文  '+row['original_text']+'\n'+row['identity']) if row else '')
            self.original.setToolTip(row['original_text'] if row else '')
            self.editor_id = row['identity'] if row else None
            for widget in (self.editor_speaker,self.editor_text,self.editor_note):
                widget.setEnabled(row is not None)
            self.editor_dirty = False
            self.loading_editor = False
            return
        if not index.isValid() or index.row()>=len(self.models[0].rows):
            self.preview.clear()
            return
        row = self.models[0].rows[index.row()]
        lines = [row['speaker'] or '角色待补全',row['text'],'',row['identity'],VOICE_LABELS[row['voice_status']]]
        if row['issue']:
            lines.append(row['issue'])
        for evidence in self.store.provenance(row['text_key'])[:3]:
            when = local_time(evidence['time']) if evidence['time'] is not None else '时间未记录'
            lines.append(f"{when} · {Path(evidence['path']).name} 第 {evidence['line_number']} 行")
        self.preview.setPlainText('\n'.join(lines))

    def editor_changed(self):
        if not self.loading_editor and self.editor_id:
            self.editor_dirty = True
            self.editor_timer.start(400)

    def save_editor(self):
        if not self.editor_dirty or not self.editor_id:
            return True
        try:
            speaker,text,note = self.editor_speaker.text(),self.editor_text.toPlainText(),self.editor_note.text()
            self.store.edit(self.editor_id,speaker=speaker,text=text,note=note)
            from .organizer_core import text_issue
            for number,row in enumerate(self.models[1].rows):
                if row['identity']==self.editor_id:
                    row.update(speaker=speaker.strip(),text=text.strip(),note=note.strip(),issue=text_issue(text))
                    self.models[1].dataChanged.emit(self.models[1].index(number,1),self.models[1].index(number,5))
                    break
            self.editor_dirty = False
            self.status.setText('修改已自动保存')
            return True
        except Exception as exc:
            self.show_error(str(exc))
            return False

    def tab_changed(self, number):
        self.save_editor()
        if number==1:
            self.refresh(1)

    def check_item(self, identity, checked):
        try:
            self.store.select([identity],checked)
            self.refresh_stats()
            return True
        except Exception as exc:
            self.show_error(str(exc))
            return False

    def select_page(self, checked):
        self.store.select([row['identity'] for row in self.models[1].rows],checked)
        self.refresh(1)

    def remove(self):
        if not self.save_editor():
            return
        self.store.remove(self.highlighted(1))
        self.refresh(1)

    def move(self, direction):
        ids = self.highlighted(1)
        if len(ids)!=1:
            self.status.setText('请选中一条台词后上移或下移')
            return
        if not self.save_editor():
            return
        self.store.move(ids[0],direction)
        self.refresh(1)
        for index,row in enumerate(self.models[1].rows):
            if row['identity']==ids[0]:
                self.tables[1].selectRow(index)
                break

    def start_job(self, action, done):
        if self.worker and self.worker.isRunning():
            return
        if not self.save_editor():
            return
        self.search_timer.stop()
        self.worker = Job(action,self)
        self.worker.progress.connect(self.status.setText)
        self.worker.result.connect(lambda result: self.status.setText('操作已取消') if result.get('cancelled') else done(result))
        self.worker.error.connect(self.show_error)
        self.worker.finished.connect(self.job_finished)
        self.set_busy(True)
        self.worker.start()

    def set_busy(self, busy):
        self.operation_active = bool(busy)
        self.body.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        self.busy_changed.emit(bool(busy))

    def cancel_job(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
        if self.updater and self.updater.state()!=QtCore.QProcess.NotRunning:
            self.update_cancelled = True
            self.updater.kill()
        self.cancel_button.setEnabled(False)
        self.status.setText('正在取消…')

    def job_finished(self):
        self.set_busy(False)
        self.stopped.emit()
        if self.closing:
            self.close()

    def import_local_logs(self):
        paths = sorted(Path(self.options['log_directory']).glob('HT*.log'))
        if not paths:
            self.show_error('没有找到异环日志，请在数据设置中选择日志文件夹')
            return
        self.import_logs(paths)

    def choose_logs(self):
        paths,_ = W.QFileDialog.getOpenFileNames(self,'选择异环历史日志',self.options['log_directory'],'日志文件 (*.log *.txt)')
        if paths:
            self.import_logs(paths)

    def import_logs(self, paths):
        def action(stop,progress):
            with self.new_store() as store:
                return store.import_logs(paths,stop,progress)
        def done(result):
            self.refresh_all()
            self.status.setText(f"已解析 {result['files']} 个文件 · 新增 {result['added_events']} 条事件 · 台词编号 {result['keys']} 个 · 待定位 {result['unresolved']} 个 · 异常行 {result['invalid']} 条")
        self.start_job(action,done)

    def add_highlighted(self):
        identities = self.highlighted(0)
        if not identities:
            self.status.setText('请先选中需要整理的台词')
            return
        self.add_items(identities=identities)

    def add_filter_results(self):
        self.add_items(filters=self.filters())

    def add_items(self, identities=None, filters=None):
        def action(stop,progress):
            with self.new_store() as store:
                return store.add(identities,filters=filters,stop=stop,progress=progress)
        def done(result):
            self.refresh(1)
            self.status.setText(f"已加入 {result['added']} 条 · 清单已有 {result['existing']} 条")
        self.start_job(action,done)

    def export(self, format):
        if not self.save_editor():
            return
        path,_ = W.QFileDialog.getSaveFileName(self,'导出已勾选台词',str(ROOT/'exports'/('异环台词.'+format)),f'{format.upper()} 文件 (*.{format})')
        if not path:
            return
        if Path(path).suffix.lower()!='.'+format:
            path += '.'+format
        def action(stop,progress):
            with self.new_store() as store:
                return store.export(path,format,stop,progress)
        self.start_job(action,lambda result: self.status.setText(f"已导出 {result['rows']} 条 · {result['path']}"))

    def create_project(self):
        if not self.save_editor():
            return
        dialog = W.QDialog(self)
        dialog.setWindowTitle('建立配音项目')
        dialog.resize(460, 200)
        form = W.QFormLayout(dialog)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(12)
        name = W.QLineEdit('整理台词')
        name.setMinimumHeight(32)
        mode = W.QComboBox()
        mode.setMinimumHeight(32)
        mode.addItem('系统中文声','system')
        mode.addItem('角色声线','character')
        form.addRow('项目名称',name)
        form.addRow('生成模式',mode)
        buttons = W.QDialogButtonBox(W.QDialogButtonBox.Ok|W.QDialogButtonBox.Cancel)
        ok_btn = buttons.button(W.QDialogButtonBox.Ok)
        if ok_btn:
            ok_btn.setObjectName('primary')
            ok_btn.setText('确定建立')
            ok_btn.setMinimumHeight(34)
        cancel_btn = buttons.button(W.QDialogButtonBox.Cancel)
        if cancel_btn:
            cancel_btn.setText('取消')
            cancel_btn.setMinimumHeight(34)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        configure_dialog(dialog, 'organizer_settings')
        if dialog.exec()!=W.QDialog.Accepted:
            return
        project_name, project_mode = name.text(),mode.currentData()
        self.build_project(project_name,project_mode)

    def build_project(self, name, mode):
        def action(stop,progress):
            config = json.loads((ASGSV/'config.json').read_text('utf-8-sig'))
            with self.new_store() as store:
                return store.create_project(ASGSV/'generation-projects',name,mode,config.get('dubbing',{}),stop,progress)
        def done(result):
            self.options['last_project'] = result['path']
            save_settings(self.options)
            self.refresh_stats()
            self.status.setText(f"配音项目已建立 · {result['rows']} 条 · 待核对 {result['held']} 条 · 可点击“在配音工具中打开”")
            self.project_created.emit(Path(result['path']))
        self.start_job(action,done)

    def open_in_generator(self):
        path = self.options.get('last_project')
        if not path or not (Path(path)/'project.json').is_file():
            self.show_error('请先建立配音项目')
            return
        if self.project_opener:
            self.project_opener(Path(path))
            return
        try:
            subprocess.Popen([sys.executable,str(ASGSV/'dialogue_generator_ui.py'),'--project',path,'--game','nte'],
                cwd=ASGSV,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except OSError as exc:
            self.show_error(str(exc))

    def show_unresolved(self):
        rows = self.store.unresolved_keys()
        dialog = W.QDialog(self)
        dialog.setWindowTitle('日志中待定位的台词编号')
        dialog.resize(850,520)
        layout = W.QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        text = W.QPlainTextEdit()
        text.setReadOnly(True)
        text.setStyleSheet("""
            QPlainTextEdit {
                background: #F8FAFB;
                border: 1px solid #D5DEE6;
                border-radius: 8px;
                padding: 10px;
                color: #172B3A;
                font-family: 'Consolas', 'Segoe UI', monospace;
                font-size: 12px;
            }
        """)
        lines = [f'共 {len(rows)} 个编号']
        for row in rows:
            detail = '原文库中暂无对应条目' if row['candidates']==0 else f"对应 {row['candidates']} 个资源分组，请检索编号后核对"
            lines.append(f"{row['text_key']} · {row['event_count']} 条日志事件 · {detail}")
        text.setPlainText('\n'.join(lines))
        layout.addWidget(text)
        close = W.QPushButton('关闭')
        close.setMinimumHeight(34)
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def settings(self):
        if not self.save_editor():
            return
        dialog = SettingsDialog(self.options,self)
        if dialog.exec()!=W.QDialog.Accepted:
            return
        options = dialog.values()
        try:
            store = Organizer(self.data_directory,options['catalog_path'],player_name=options['player_name'],gender=options['gender'])
            try:
                save_settings(options)
            except Exception:
                store.close()
                raise
            self.store.close()
            self.store,self.options = store,options
            self.load_facets()
            self.pages = [0,0]
            self.refresh_all()
            self.status.setText('数据与称谓设置已保存')
        except Exception as exc:
            self.show_error(str(exc))

    def choose_game_directory(self):
        path = W.QFileDialog.getExistingDirectory(self, '选择异环游戏安装目录', self.game_directory.text())
        if path:
            self.update_catalog(game_root=path)

    def update_catalog(self, *, game_root=None, force=True):
        if self.busy() or self.store_closed or not self.save_editor():
            return
        if self.catalog_preparer:
            self.catalog_preparer(game_root, force)
        else:
            self.start_catalog_update(game_root, force)

    def start_catalog_update(self, game_root=None, force=True):
        if self.busy() or self.store_closed or not self.save_editor():
            return
        self.search_timer.stop()
        try:
            self.update_log = self.data_directory/'catalog-update.log'
            self.update_log.write_text('', encoding='utf-8')
            self.store.detach_catalog()
        except Exception as exc:
            self.show_error(str(exc))
            return
        self.update_cancelled = False
        self.update_result = None
        self.update_error = ''
        self.update_buffer = b''
        self.updater = QtCore.QProcess(self)
        self.updater.setWorkingDirectory(str(ASGSV))
        self.updater.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.updater.readyReadStandardOutput.connect(self.read_catalog_progress)
        self.updater.finished.connect(self.catalog_updated)
        self.updater.errorOccurred.connect(self.update_failed)
        self.set_busy(True)
        self.status.setText('正在自动识别异环安装目录…')
        arguments = ['-X','utf8',str(ASGSV/'nte_resources.py'),'--auto',
                     '--config',str(ASGSV/'config.json'),'--output',self.options['catalog_path']]
        if game_root is not None:
            arguments.extend(['--game-root',str(game_root)])
        if not force:
            arguments.append('--ensure')
        self.updater.start(sys.executable,arguments)

    def read_catalog_progress(self):
        chunk = bytes(self.updater.readAllStandardOutput())
        with self.update_log.open('ab') as stream:
            stream.write(chunk)
        self.update_buffer += chunk
        while b'\n' in self.update_buffer:
            line,self.update_buffer = self.update_buffer.split(b'\n',1)
            try:
                event = json.loads(line.decode('utf-8'))
            except (UnicodeError,ValueError):
                continue
            if not isinstance(event,dict):
                continue
            if 'progress' in event and not self.update_cancelled:
                self.status.setText(event['progress'])
            if 'error' in event:
                self.update_error = event['error']
            if 'result' in event:
                self.update_result = event['result']

    def update_failed(self, error):
        if error==QtCore.QProcess.FailedToStart:
            self.update_error = '资源读取程序未启动 · ' + self.updater.errorString()
            self.catalog_updated(1,QtCore.QProcess.CrashExit)

    def catalog_updated(self, code, status):
        if self.updater is None:
            return
        self.read_catalog_progress()
        process,self.updater = self.updater,None
        process.deleteLater()
        try:
            self.store.attach_catalog()
            self.load_facets()
            self.refresh_all()
            if code==0 and self.update_result:
                result = self.update_result
                self.game_directory.setText(result['game_root'])
                self.game_directory.setToolTip('识别来源 · '+result['source'])
                self.options['catalog_path'] = result['catalog_path']
                if self.persist_settings:
                    save_settings(self.options)
                message = '台词提取完成' if result['updated'] else '台词库已是当前游戏版本'
                self.status.setText(f"{message} · 中文资源 {result['entries']:,} 条 · 对话相关 {result['dialogue']:,} 条")
            elif self.update_cancelled:
                self.status.setText('提取已取消')
            else:
                self.show_error(self.update_error or '提取未完成，详情见 '+str(self.update_log))
        except Exception as exc:
            self.show_error(str(exc))
        self.set_busy(False)
        self.stopped.emit()
        if self.closing:
            self.close()

    def help(self):
        W.QMessageBox.information(self,'使用帮助',
            '在“原文库与日志”中按角色、分组或关键词查找台词，然后加入整理清单。\n\n'
            '首次打开准备页会自动查找异环目录并检查资源版本，缺少台词库或游戏更新后会提取中文台词。'
            '也可点击“自动识别并提取”重新读取，或使用“选择目录”指定安装位置。\n\n'
            '“读取本机历史日志”会解析已保存的 HT 日志，并将有唯一原文匹配的编号加入日志筛选结果。'
            '日志里的通用对话动作只用于统计。待定位编号可以单独查看。\n\n'
            '整理清单中的修改自动保存。勾选需要的条目后，可以导出 CSV、TXT、JSON，或建立配音项目。'
            '资源库按分组和编号排序，日志筛选按首次记录排序。\n\n'
            '带有变量的台词会显示待处理事项，建立配音项目后仍可核对。原声状态来源于已导入的日志记录。')

    def show_error(self, message):
        if hasattr(self,'status'):
            self.status.setText('操作未完成 · '+message)

    def busy(self):
        return bool(self.operation_active or (self.worker and self.worker.isRunning()) or
                    (self.updater and self.updater.state()!=QtCore.QProcess.NotRunning))

    def shutdown(self):
        if self.store_closed:
            return True
        if self.busy():
            self.cancel_job()
            return False
        if not self.save_editor():
            return False
        self.search_timer.stop()
        self.editor_timer.stop()
        self.store.close()
        self.store_closed = True
        return True

    def closeEvent(self, event):
        if (self.worker and self.worker.isRunning()) or (self.updater and self.updater.state()!=QtCore.QProcess.NotRunning):
            self.closing = True
            self.cancel_job()
            event.ignore()
            return
        self.save_editor()
        if self.editor_dirty:
            event.ignore()
            return
        if self.shutdown():
            event.accept()
        else:
            event.ignore()
