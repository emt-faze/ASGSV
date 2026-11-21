"""Preflight window for interactive workbench launches."""
import json
from datetime import datetime
from pathlib import Path
import re
import sys

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from asgsv_identity import application_icon
import wuwa_update as update
from wuwa_update_ui import Operation, launch_apply, source_picker


class Stage(W.QFrame):
    def __init__(self, root):
        super().__init__()
        self.art = G.QPixmap(str(root/'nte-dialogue-ocr/assets/dialogue-coast.jpg'))
        self.setMinimumHeight(190)

    def paintEvent(self, event):
        p = G.QPainter(self)
        p.setRenderHint(G.QPainter.Antialiasing)
        p.setRenderHint(G.QPainter.SmoothPixmapTransform)
        rect = C.QRectF(self.rect()).adjusted(.5,.5,-.5,-.5)
        shape = G.QPainterPath()
        shape.addRoundedRect(rect,12,12)
        p.setClipPath(shape)
        p.fillRect(rect,G.QColor('#152228'))
        if not self.art.isNull():
            image = C.QRectF(self.art.rect())
            scale = max(rect.width()/image.width(),rect.height()/image.height())
            crop = C.QRectF(0,0,rect.width()/scale,rect.height()/scale)
            crop.moveCenter(image.center())
            p.drawPixmap(rect,self.art,crop)
        shade = G.QLinearGradient(rect.topLeft(),rect.topRight())
        shade.setColorAt(0,G.QColor(10,18,22,250))
        shade.setColorAt(.56,G.QColor(10,18,22,224))
        shade.setColorAt(1,G.QColor(12,24,28,185))
        p.fillRect(rect,shade)
        p.setPen(G.QPen(G.QColor('#35484d'),1))
        p.drawPath(shape)


STYLE = """
QDialog#startup { background: #0d1519; }
QWidget { color: #e9f0ef; }
QLabel { background: transparent; border: none; }
QLabel#brand { font-size: 18px; font-weight: 700; }
QLabel#product, QLabel#detail, QLabel#stageNote { font-size: 13px; color: #d3e1e3; }
QLabel#version { font: 12px "Consolas"; color: #e6cf93; background: #262722;
    border: 1px solid #5a5038; border-radius: 10px; padding: 3px 9px; }
QLabel#section { font-size: 13px; color: #e9cb80; font-weight: 600; }
QLabel#headline { font-size: 29px; font-weight: 700; color: #f3f7f5; }
QLabel#percent { font: bold 13px "Consolas"; color: #e9cb80; }
QProgressBar { background: #27383e; border: 0; border-radius: 3px; }
QProgressBar::chunk { background: #d8b467; border-radius: 3px; }
QPushButton { color: #dde8e8; background: #1a272c; border: 1px solid #34494f;
    border-radius: 7px; padding: 8px 16px; font-size: 13px; }
QPushButton:hover, QPushButton:focus { border-color: #d8b467; }
QPushButton#primary { color: #101719; background: #dfbe75; border: 1px solid #e4c782; font-weight: 700; }
QPushButton#primary:hover, QPushButton#primary:focus { background: #efd498; }
QPushButton#details { background: transparent; color: #a9bdc2; padding: 6px 0; border: 0; }
QPushButton#details:hover, QPushButton#details:focus { color: #efd498; text-decoration: underline; }
QPlainTextEdit { background: #101b20; color: #d1dfe1; border: 1px solid #2b3c43;
   border-radius: 6px; padding: 5px; font-family: "Consolas","Microsoft YaHei UI"; font-size: 12px; }
"""


class StartupUpdate(W.QDialog):
    """Only Accepted hands off to GameHome; closing the launcher exits the app."""
    def __init__(self, root):
        super().__init__()
        self.root = Path(root)
        self.version = update.portable_version(self.root)
        self.feed = self.offer = self.worker = self.probe = None
        self.phase = 'checking'
        self.advance = C.QTimer(self, singleShot=True)
        self.advance.timeout.connect(self.accept)
        self.setObjectName('startup')
        self.setWindowTitle('ASGSV · 启动检查')
        self.setWindowIcon(application_icon())
        face=G.QFont()
        face.setFamilies(['Microsoft YaHei UI','Microsoft YaHei','Segoe UI'])
        face.setPointSize(10)
        self.setFont(face)
        self.setStyleSheet(STYLE)
        outer = W.QVBoxLayout(self)
        outer.setContentsMargins(30,24,30,22)
        outer.setSpacing(16)
        header = W.QHBoxLayout()
        icon = W.QLabel()
        icon.setPixmap(application_icon().pixmap(C.QSize(44,44),self.devicePixelRatioF()))
        header.addWidget(icon)
        header.addSpacing(6)
        identity = W.QVBoxLayout()
        identity.setSpacing(0)
        header.addLayout(identity)
        self.label('ASGSV','brand',identity)
        self.label('鸣潮配音工作台','product',identity)
        header.addStretch()
        header.addWidget(W.QLabel('更新源'))
        self.source=source_picker(self.root)
        self.source.activated.connect(self.switch_source)
        self.source.installEventFilter(self)
        header.addWidget(self.source)
        badge=self.label('v'+self.version,'version',header)
        header.setAlignment(badge,C.Qt.AlignVCenter)
        outer.addLayout(header)
        stage = Stage(self.root)
        outer.addWidget(stage,1)
        card = W.QVBoxLayout(stage)
        card.setContentsMargins(26,18,26,22)
        card.setSpacing(8)
        self.label('程序更新','section',card)
        card.addStretch()
        self.title = self.label('正在检查更新','headline',card)
        self.note = self.label('正在验证更新源的发布签名。','detail',card)
        self.note.setWordWrap(True)
        card.addStretch()
        line = W.QHBoxLayout()
        self.status = self.label('读取版本清单','stageNote',line)
        line.addStretch()
        self.percent = self.label('','percent',line)
        outer.addLayout(line)
        self.bar = W.QProgressBar()
        self.bar.setAccessibleName('程序更新进度')
        self.bar.setFixedHeight(6)
        self.bar.setTextVisible(False)
        self.bar.setRange(0,0)
        outer.addWidget(self.bar)
        self.log = W.QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(100)
        self.log.setFixedHeight(83)
        self.log.hide()
        outer.addWidget(self.log)
        actions = W.QHBoxLayout()
        self.details = W.QPushButton('查看详情')
        self.details.setObjectName('details')
        self.details.setCheckable(True)
        self.details.toggled.connect(self.show_details)
        actions.addWidget(self.details)
        actions.addStretch()
        self.enter = W.QPushButton('进入工作台')
        self.enter.clicked.connect(self.accept)
        actions.addWidget(self.enter)
        self.primary = W.QPushButton()
        self.primary.setObjectName('primary')
        self.primary.clicked.connect(self.act)
        self.primary.hide()
        actions.addWidget(self.primary)
        outer.addLayout(actions)
        for button in (self.details,self.enter,self.primary):
            button.setAutoDefault(False)
        self.fit(760,412)
        C.QTimer.singleShot(0,self.check)

    def fit(self,width,height):
        area=self.screen().availableGeometry()
        self.resize(min(width,area.width()-32),min(height,area.height()-48))

    @staticmethod
    def label(text,name,layout):
        widget = W.QLabel(text)
        widget.setTextFormat(C.Qt.PlainText)
        widget.setObjectName(name)
        layout.addWidget(widget)
        return widget

    def show_details(self,show):
        self.log.setVisible(show)
        self.details.setText('收起详情' if show else '查看详情')
        if show:
            self.advance.stop()
            if self.phase=='current':
                self.note.setText('所选更新源暂无更高版本。')
        self.fit(self.width(),510 if show else 412)

    def record(self,text):
        self.log.appendPlainText(datetime.now().strftime('%H:%M:%S')+'  '+text)

    def busy(self):
        self.bar.setRange(0,0)
        self.percent.clear()

    def eventFilter(self,watched,event):
        if watched is self.source and event.type() in (C.QEvent.MouseButtonPress,C.QEvent.KeyPress):
            self.advance.stop()
        return super().eventFilter(watched,event)

    def switch_source(self):
        self.advance.stop()
        self.feed=self.offer=None
        self.cancel_probe()
        self.check()

    def cancel_probe(self):
        proc,self.probe=self.probe,None
        if proc is not None:
            proc.finished.disconnect(self.checked)
            proc.errorOccurred.disconnect(self.process_error)
            proc.kill()
            proc.waitForFinished(1000)
            proc.deleteLater()

    def check(self):
        if self.phase=='closed' or self.probe is not None:
            return
        self.phase='checking'
        self.primary.hide()
        self.busy()
        self.title.setText('正在检查更新')
        source=self.source.currentData()
        self.note.setText('正在连接'+update.SOURCES[source]+'并验证发布签名。')
        self.status.setText('读取版本清单')
        self.record('检查官方稳定版清单')
        # A separate cancellable process uses the same proxy, TLS and signing path
        # as the in-app updater. Skipping never waits for a network timeout.
        proc=C.QProcess(self)
        self.probe=proc
        proc.finished.connect(self.checked)
        proc.errorOccurred.connect(self.process_error)
        proc.start(sys.executable,['-X','utf8',str(Path(update.__file__).resolve()),'--check-feed',source])

    def process_error(self,error):
        if error==C.QProcess.FailedToStart and self.probe:
            message=self.probe.errorString()
            self.probe.deleteLater()
            self.probe=None
            self.failure(message)

    def checked(self,code,exit_status):
        proc=self.probe
        if proc is None or self.phase=='closed':
            return
        self.probe=None
        proc.deleteLater()
        if code or exit_status!=C.QProcess.NormalExit:
            self.failure(bytes(proc.readAllStandardError()).decode('utf8','replace').strip() or '版本检查进程意外退出')
            return
        try:
            self.feed=json.loads(bytes(proc.readAllStandardOutput()))
            self.offer=update.program_offer(self.feed,self.version)
            latest=self.feed['tag'].removeprefix('wuwa-v')
            newer=update.semver(latest)>update.semver(self.version)
            if update.selected_source(self.root)!=self.source.currentData():
                update.save_source(self.root,self.source.currentData())
        except (ValueError,KeyError,TypeError,OSError) as exc:
            self.failure(str(exc))
            return
        self.bar.setRange(0,100)
        self.bar.setValue(100)
        self.record('发布签名有效 · 最新稳定版本 v'+latest)
        self.status.setText('版本检查完成')
        if newer:
            portable=(self.root/update.MANIFEST).is_file() and (self.root/'runtime/python/pythonw.exe').is_file()
            self.phase='available' if self.offer and portable else 'release'
            self.title.setText('发现新版本  v'+latest)
            if self.phase=='available':
                self.note.setText('当前 v'+self.version+'。下载程序更新后将自动打开新版工作台。')
                self.primary.setText('立即更新')
            else:
                self.note.setText('此安装需从发布页下载适用版本。你也可以继续使用当前工作台。')
                self.primary.setText('打开发布页')
            self.primary.show()
            self.primary.setFocus()
        else:
            self.phase='current'
            self.title.setText('工作台已就绪')
            self.note.setText('所选更新源暂无更高版本。'+('正在进入工作台。' if not self.log.isVisible() else ''))
            if not self.log.isVisible() and not self.source.view().isVisible():
                self.advance.start(900)

    def failure(self,reason):
        self.source.setEnabled(True)
        self.phase='error'
        self.title.setText('暂时无法完成更新')
        self.note.setText('可以重试当前更新源，或进入工作台导入网盘更新包。')
        self.bar.setRange(0,100)
        self.bar.setValue(0)
        self.percent.clear()
        self.status.setText('更新未完成 · 详情中可查看原因')
        self.record(reason)
        self.primary.setText('重试 '+self.source.currentText())
        self.primary.show()
        self.enter.setEnabled(True)

    def act(self):
        if self.phase=='error':
            self.check()
        elif self.phase=='release':
            url=update.release_page(self.source.currentData(), self.feed['tag'])
            if not G.QDesktopServices.openUrl(C.QUrl(url)):
                self.failure('无法打开系统浏览器 · '+url)
        elif self.phase=='available':
            self.phase='installing'
            self.title.setText('正在准备更新')
            self.note.setText('更新包通过签名和文件校验后，将交给安装程序。')
            self.primary.hide()
            self.enter.setEnabled(False)
            source=self.source.currentData()
            self.source.setEnabled(False)
            self.busy()
            self.progress('正在获取程序更新包')
            def prepare(progress):
                archive=update.download(self.root,self.feed,self.offer,progress,source=source)
                return update.stage_program(self.root,archive,progress)
            self.archive=None
            self.install_error=''
            worker=Operation(prepare,self)
            self.worker=worker
            worker.message.connect(self.progress)
            worker.result.connect(self.prepared)
            worker.failed.connect(self.prepare_failed)
            worker.finished.connect(self.install_finished)
            worker.start()

    @C.Slot(str)
    def progress(self,text):
        self.status.setText(text)
        if match:=re.search(r'(\d+)%$',text):
            self.bar.setRange(0,100)
            self.bar.setValue(int(match[1]))
            self.status.setText(text[:match.start()].rstrip(' ·'))
            self.percent.setText(match[0])
        else:
            self.busy()
            self.record(text)

    @C.Slot(object)
    def prepared(self,path):
        self.archive=path

    @C.Slot(str)
    def prepare_failed(self,message):
        self.install_error=message

    @C.Slot()
    def install_finished(self):
        self.worker.deleteLater()
        self.worker=None
        if self.install_error or self.archive is None:
            self.failure(self.install_error or '未取得已验证的程序更新包')
            return
        try:
            launch_apply(self.root,self.archive)
        except OSError as exc:
            self.failure(str(exc))
            return
        self.reject()

    def done(self,result):
        if self.worker is not None:
            return
        self.phase='closed'
        self.advance.stop()
        self.cancel_probe()
        super().done(result)


def enter_workbench(root):
    dialog=StartupUpdate(root)
    return dialog.exec()==W.QDialog.Accepted
