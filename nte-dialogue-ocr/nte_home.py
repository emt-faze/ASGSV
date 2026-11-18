"""NTE workbench with the shared live dashboard and integrated text preparation."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from PySide6 import QtCore, QtWidgets as W

from dialogue_dubbing import voice_paths
from dialogue_generation import GenerationProject
from nte_live_panel import NteLivePanel


class NteHomePanel(W.QWidget):
    project_requested = QtCore.Signal(object, str)
    stopped = QtCore.Signal()
    busy_changed = QtCore.Signal(bool)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.settings_process = None
        self.organizer = None
        self.pending_project = None
        self.pending_catalog = None
        layout = W.QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.workspace_tabs = W.QTabWidget()
        self.live = NteLivePanel(self.root,self)
        self.live.project_selected.connect(self.show_project)
        self.live.stopped.connect(self.work_stopped)
        self.live.running_changed.connect(lambda _:self.busy_changed.emit(self.busy()))
        self.workspace_tabs.addTab(self.live,'实时配音')
        self.task_library = W.QWidget()
        self.workspace_tabs.addTab(self.task_library,'任务库与配音准备')
        layout.addWidget(self.workspace_tabs,1)
        body = W.QVBoxLayout(self.task_library)
        body.setContentsMargins(0,0,0,0)
        self.advanced = W.QWidget()
        advanced = W.QVBoxLayout(self.advanced)
        advanced.setContentsMargins(0,0,0,0)
        self.readiness = W.QLabel()
        self.readiness.setWordWrap(True)
        advanced.addWidget(self.readiness)
        extra = W.QHBoxLayout()
        self.collect_button = self.button(extra,'采集游戏台词',lambda:self.request_project('collect'),W.QStyle.SP_DesktopIcon)
        self.settings_button = self.button(extra,'游戏连接与字幕校准',lambda:self.open_live(dubbing=False),W.QStyle.SP_FileDialogDetailedView)
        extra.addStretch()
        advanced.addLayout(extra)
        self.advanced.hide()
        body.addWidget(self.advanced)
        projects = W.QHBoxLayout()
        projects.addWidget(W.QLabel('台词项目'))
        self.projects = W.QComboBox()
        self.projects.setSizeAdjustPolicy(W.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.projects.setMinimumContentsLength(18)
        self.projects.currentIndexChanged.connect(self.update_project)
        projects.addWidget(self.projects,1)
        self.button(projects,'刷新项目',lambda:self.refresh(),W.QStyle.SP_BrowserReload)
        self.prepare_button = self.button(projects,'生成与试听',lambda:self.request_project('prepare'),W.QStyle.SP_FileIcon)
        self.prepare_button.setObjectName('primary')
        body.addLayout(projects)
        self.organizer_layout = W.QVBoxLayout()
        self.organizer_layout.setContentsMargins(0,0,0,0)
        body.addLayout(self.organizer_layout,1)
        playback = W.QHBoxLayout()
        self.live_button = self.button(playback,'连接异环',lambda:self.open_live(dubbing=True),W.QStyle.SP_MediaPlay)
        self.button(playback,'字幕校准',lambda:self.open_live(dubbing=False),W.QStyle.SP_DesktopIcon)
        self.project_summary = W.QLabel()
        self.project_summary.setWordWrap(True)
        playback.addWidget(self.project_summary,1)
        self.play_button = self.button(playback,'开始配音',lambda:self.request_project('playback'),W.QStyle.SP_MediaPlay)
        body.addLayout(playback)
        self.status = W.QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        self.workspace_tabs.currentChanged.connect(self.tab_changed)
        self.refresh()

    def button(self, layout, text, callback, icon):
        button = W.QPushButton(text)
        button.setIcon(self.style().standardIcon(icon))
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def message(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def tab_changed(self, index):
        if index != 1 or self.organizer is not None:
            return
        try:
            from nte_organizer.organizer_ui import OrganizerWindow
            self.organizer = OrganizerWindow(parent=self.task_library,project_opener=self.open_organized_project)
            self.organizer.catalog_preparer = self.prepare_catalog
            self.organizer.project_created.connect(lambda path:self.refresh(project_path=path))
            self.organizer.stopped.connect(self.work_stopped)
            self.organizer.busy_changed.connect(self.organizer_busy_changed)
            self.organizer_layout.addWidget(self.organizer)
        except Exception as exc:
            self.message('台词整理页未打开 · '+str(exc))

    def prepare_catalog(self, game_root, force):
        if self.live.running():
            self.pending_catalog = (game_root, force)
            if force:
                self.organizer.status.setText('正在停止实时配音，随后提取游戏台词…')
                self.live.stop_live()
            else:
                self.organizer.status.setText('将在实时配音停止后自动检查游戏目录与台词库')
            return
        self.organizer.start_catalog_update(game_root, force)

    def organizer_busy_changed(self, busy):
        self.live.start.setEnabled(not busy and not self.live.running())
        self.busy_changed.emit(self.busy())

    def show_project(self, path):
        self.refresh(project_path=path)
        self.workspace_tabs.setCurrentIndex(1)

    def open_organized_project(self, path):
        self.refresh(project_path=path)
        self.defer_project(Path(path),'prepare')

    def refresh(self, *, project_path=None):
        previous = str(Path(project_path).resolve()) if project_path else self.projects.currentData()
        projects_root = self.root/'generation-projects'
        last = projects_root/'last-project.txt'
        try:
            if not previous and last.is_file():
                previous = str(Path(last.read_text(encoding='utf-8').strip()).resolve())
            config = json.loads((self.root/'config.json').read_text(encoding='utf-8-sig'))
            _,data = voice_paths(config.get('dubbing',{}))
            models = self.root/'assets/ocr-v6'
            manifest = json.loads((models/'models.json').read_text(encoding='utf-8'))
            available = sum((models/name).is_file() and (models/name).stat().st_size==spec['bytes']
                            for name,spec in manifest.items())
            launcher = Path(config.get('launcher_path',''))
            launcher_status = '已找到异环启动器' if launcher.is_file() else '请在连接设置中选择异环启动器'
            voices = len(list((data/'profiles').glob('*/profile.json')))
            self.readiness.setText(f'{launcher_status} · 字幕模型 {available}/{len(manifest)} · 角色声线 {voices} 条')
        except (OSError,ValueError,KeyError) as exc:
            self.readiness.setText('异环配置需要检查 · '+str(exc))
        paths = sorted(projects_root.glob('*/project.json'),key=lambda path:path.stat().st_mtime,reverse=True)
        if previous and (Path(previous)/'project.json').is_file():
            candidate = Path(previous)/'project.json'
            if candidate not in paths:
                paths.insert(0,candidate)
        self.projects.blockSignals(True)
        self.projects.clear()
        for path in paths:
            try:
                project = GenerationProject.load(path)
                if project.game_id=='nte':
                    self.projects.addItem(f'{path.parent.name} · {len(project.rows)} 条',str(path.parent.resolve()))
            except (OSError,ValueError,KeyError,TypeError):
                continue
        self.projects.setCurrentIndex(max(0,self.projects.findData(previous)))
        self.projects.blockSignals(False)
        self.update_project()

    def update_project(self):
        self.play_button.setEnabled(False)
        path = self.projects.currentData()
        if not path:
            self.project_summary.setText('可从整理清单建立配音项目')
            self.live.set_project(None)
            return
        try:
            project = GenerationProject.load(path)
            playable = sum(row.status=='done' and not row.review_reason() and project.audio_path(row) is not None
                           for row in project.rows)
            mode = '角色声线' if project.mode=='character' else '系统中文声'
            summary = f'{len(project.rows)} 条台词 · {playable} 条完整配音 · {mode}'
            self.project_summary.setText(summary)
            self.play_button.setEnabled(playable>0)
            self.live.set_project(path,summary)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.project_summary.setText('项目读取失败 · '+str(exc))
            self.live.set_project(None)

    def request_project(self, action):
        path = self.projects.currentData()
        self.defer_project(Path(path) if path else None,action)

    def defer_project(self, path, action):
        if self.busy():
            self.pending_project = (path,action)
            self.stop(keep_pending=True)
            return
        if self.organizer and not self.organizer.save_editor():
            self.message('请先完成整理清单的保存')
            return
        self.project_requested.emit(path,action)

    def open_live(self, *, dubbing):
        if dubbing:
            self.workspace_tabs.setCurrentIndex(0)
            self.live.start_live()
            return
        if self.settings_process is not None and self.settings_process.poll() is None:
            self.message('游戏连接与字幕校准窗口已打开')
            return
        try:
            self.settings_process = subprocess.Popen([sys.executable,'-s',str(self.root/'app.py')],cwd=self.root,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.message('已打开游戏连接与字幕校准窗口')
        except OSError as exc:
            self.message('连接窗口未打开 · '+str(exc))

    def busy(self):
        return self.live.running() or bool(self.organizer and self.organizer.busy())

    def stop(self, *, keep_pending=False):
        self.pending_catalog = None
        if not keep_pending:
            self.pending_project = None
        self.live.stop_live()
        if self.organizer and self.organizer.busy():
            self.organizer.cancel_job()

    def work_stopped(self):
        if self.busy():
            return
        if self.pending_catalog and not getattr(self.window(),'closing',False):
            pending,self.pending_catalog = self.pending_catalog,None
            self.organizer.start_catalog_update(*pending)
            return
        pending,self.pending_project = self.pending_project,None
        self.stopped.emit()
        if pending and not getattr(self.window(),'closing',False):
            self.project_requested.emit(*pending)

    def shutdown(self):
        if self.busy():
            self.stop()
            return False
        if self.organizer and not self.organizer.shutdown():
            return False
        self.live.shutdown()
        return True

    def closeEvent(self, event):
        if self.shutdown():
            event.accept()
        else:
            event.ignore()
