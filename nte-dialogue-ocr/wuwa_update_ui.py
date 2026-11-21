"""Update center shared by the workbench and the first-upgrade assistant."""
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from PySide6 import QtCore as C, QtWidgets as W

# The isolated .17 interpreter does not put the bootstrap's directory on sys.path.
sys.path.insert(0,str(Path(__file__).resolve().parent))
import wuwa_update as update


class Operation(C.QThread):
    message=C.Signal(str)
    failed=C.Signal(str)
    result=C.Signal(object)

    def __init__(self,fn,parent):
        super().__init__(parent)
        self.fn=fn

    def run(self):
        try:
            self.result.emit(self.fn(self.message.emit))
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__} · {exc}')


class UpdateCenter(W.QDialog):
    def __init__(self,root,parent=None):
        super().__init__(parent)
        self.root=root.resolve()
        self.host=parent
        self.feed=None
        self.thread=None
        self.outcome=None
        self.setWindowTitle('ASGSV · 更新中心')
        self.resize(550,330)
        layout=W.QVBoxLayout(self)
        layout.addWidget(W.QLabel('当前工作台  v'+update.portable_version(root)))
        self.list=W.QListWidget()
        self.list.setAccessibleName('可用台词索引和任务配音包')
        layout.addWidget(self.list)
        self.status=W.QLabel('程序升级后会重新打开工作台。台词索引与任务配音包可在运行中安装。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row=W.QHBoxLayout()
        layout.addLayout(row)
        self.buttons=[]
        for title,fn in [('检查资源',self.check),('更新选中资源',self.resource),
                         ('一键在线升级',self.upgrade),('导入更新包…',self.local)]:
            b=W.QPushButton(title)
            b.clicked.connect(fn)
            row.addWidget(b)
            self.buttons.append(b)
        self.buttons[1].setEnabled(False)
        self.list.itemSelectionChanged.connect(self.selection)

    def selection(self):
        item=self.list.currentItem()
        self.buttons[1].setEnabled(bool(item and item.data(C.Qt.UserRole) and self.thread is None))

    def start(self,fn):
        if self.thread is not None:
            return
        for b in self.buttons:
            b.setEnabled(False)
        self.status.setText('正在处理…')
        self.outcome=None
        worker=Operation(fn,self)
        self.thread=worker
        worker.message.connect(self.status.setText)
        worker.failed.connect(self.status.setText)
        worker.result.connect(self.received)
        worker.finished.connect(self.on_operation_finished)
        worker.start()

    @C.Slot(object)
    def received(self,result):
        self.outcome=result

    @C.Slot()
    def on_operation_finished(self):
        worker,self.thread=self.thread,None
        worker.deleteLater()
        for b in self.buttons:
            b.setEnabled(True)
        self.selection()
        if self.outcome is None:
            return
        action,value=self.outcome
        if action=='feed':
            self.feed,rows=value
            self.list.clear()
            for entry,state in rows:
                item=W.QListWidgetItem(f'{entry["name"]}  ·  r{entry["revision"]}  ·  {state}')
                if state=='可更新':
                    item.setData(C.Qt.UserRole,entry)
                self.list.addItem(item)
            self.status.setText('选择需要的资源。已安装的任务录音可继续使用。')
        elif action=='program':
            if self.host:
                self.host.queue_program_update(value)
            else:
                launch_apply(self.root,value)
            self.accept()
        else:
            self.status.setText(value)
            if action=='content':
                if self.host:
                    self.host.refresh_all()
                self.list.clear()
                self.status.setText(value+'。点「检查资源」可查看最新状态。')

    def check(self):
        def work(progress):
            progress('正在读取官方签名清单…')
            feed=update.read_feed()
            ver=update.semver(update.portable_version(self.root))
            rows=[]
            for e in feed['packages']:
                if e['kind']=='app':
                    continue
                if ver<update.semver(e['minimum']):
                    status='请先升级程序'
                else:
                    current=update.revision(self.root/'game-data/wuwa',e)
                    status='已安装' if current>=e['revision'] else '可更新'
                rows.append((e,status))
            return 'feed',(feed,rows)
        self.start(work)

    def upgrade(self):
        def work(progress):
            progress('检查程序版本…')
            feed=update.read_feed()
            version=update.portable_version(self.root)
            apps=[e for e in feed['packages'] if e['kind']=='app']
            candidates=[e for e in apps if e['base']==version]
            if not candidates:
                if any(update.semver(e['version'])>update.semver(version) for e in apps):
                    raise ValueError('此版本需要先应用对应的过渡更新包，请查看发布说明')
                return 'status','工作台已是最新版本 · v'+version
            chosen=max(candidates,key=lambda e:update.semver(e['version']))
            return 'program',update.stage_program(self.root,update.download(self.root,feed,chosen,progress),progress)
        self.start(work)

    def resource(self):
        item=self.list.currentItem()
        if item and (entry:=item.data(C.Qt.UserRole)):
            self.start(lambda progress:('content',update.install_content(
                self.root,update.download(self.root,self.feed,entry,progress),progress)))

    def local(self):
        file,_=W.QFileDialog.getOpenFileName(self,'选择 ASGSV 签名更新包','','更新包 (*.zip)')
        if file:
            self.import_file(Path(file))

    def import_file(self,path):
        def work(progress):
            with ZipFile(path) as z:
                kind=update.inspect_zip(z)['kind']
            if kind=='app':
                return 'program',update.stage_program(self.root,path,progress)
            return 'content',update.install_content(self.root,path,progress)
        self.start(work)

    def reject(self):
        if self.thread is None:
            super().reject()
        else:
            self.status.setText('正在处理更新，请等待操作完成。')


def launch_apply(root,archive):
    python=root/'runtime/python/pythonw.exe'
    subprocess.Popen([str(python),'-I','-X','utf8',str(Path(__file__).resolve()),
                      '--apply',str(root),str(archive)],cwd=root,close_fds=True)


def main():
    app=W.QApplication(sys.argv[:1])
    if sys.argv[1:2]!=['--apply']:
        dialog=UpdateCenter(Path(__file__).resolve().parents[1])
        dialog.exec()
        return 0
    root=Path(sys.argv[2]).resolve()
    dialog=W.QProgressDialog('正在等待工作台和每日任务安全退出…',None,0,0)
    dialog.setWindowTitle('ASGSV · 程序升级')
    dialog.setWindowFlag(C.Qt.WindowCloseButtonHint,False)
    dialog.setMinimumDuration(0)
    worker=Operation(lambda progress:update.apply_program(root,Path(sys.argv[3]),progress),dialog)
    status={}
    worker.message.connect(dialog.setLabelText)
    worker.result.connect(lambda msg:status.update(ok=msg))
    worker.failed.connect(lambda msg:status.update(error=msg))
    def finished():
        dialog.close()
        msg=status.get('ok') or status.get('error') or '更新进程意外结束'
        update.atomic(root/'.asgsv-update/last-result.txt',msg.encode('utf8'))
        if 'ok' not in status:
            W.QMessageBox.critical(None,'程序升级未完成',msg+'\n可重新打开 ASGSV，或使用原更新包重试。')
        app.quit()
    worker.finished.connect(finished)
    app.setQuitOnLastWindowClosed(False)
    dialog.show()
    worker.start()
    return app.exec()


if __name__=='__main__':
    raise SystemExit(main())
