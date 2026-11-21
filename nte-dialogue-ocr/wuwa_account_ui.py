"""Shared game-account selector for the library and the daily queue."""
import sqlite3

from PySide6 import QtCore as C, QtWidgets as W

from wuwa_daily_plan import config
from wuwa_progress import MissingProgress


class GameAccountButton(W.QPushButton):
    changed = C.Signal()
    problem = C.Signal(str)

    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.setAccessibleName('切换游戏账号')
        self.setToolTip('选择工作台和后台配音使用的鸣潮账号')
        self.clicked.connect(lambda: self.choose())
        self.sync()

    def sync(self):
        progress = self.catalog.progress
        self.setText('账号 · '+progress.label(progress.account) if progress.account else '选择游戏账号')

    def ensure(self, *, allow_missing=False):
        return self.choose(only_if_needed=True, allow_missing=allow_missing)

    def detect_single(self):
        try:
            install = config(self.catalog)['install_root']
            labels = self.catalog.progress.snapshot(install)['labels']
            current = self.catalog.progress.account
            if current:
                if current in labels and self.catalog.meta('account_label:'+current) != labels[current]:
                    with self.catalog.db:
                        self.catalog.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('account_label:'+current, labels[current]))
                self.sync()
            elif len(labels) == 1:
                self.catalog.progress.refresh(install)
                self.sync()
                self.changed.emit()
        except MissingProgress:
            self.sync()
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            self.problem.emit(str(exc))

    def choose(self, *, only_if_needed=False, allow_missing=False):
        c = self.catalog
        try:
            install = config(c)['install_root']
            try:
                labels = c.progress.snapshot(install)['labels']
            except MissingProgress:
                if only_if_needed and allow_missing:
                    return True
                raise
            if not labels:
                raise ValueError('尚无游戏账号记录。请先登录鸣潮并打开任务列表。')
            current = c.progress.account
            if only_if_needed and current in labels:
                return True
            if only_if_needed and not current and len(labels)==1:
                selected = next(iter(labels))
            else:
                menu = W.QMenu(self)
                actions = {}
                for key, label in sorted(labels.items(), key=lambda item:item[1]):
                    alias = c.meta('account_alias:'+key)
                    action = menu.addAction(f'{alias}（{label}）' if alias else label)
                    action.setCheckable(True)
                    action.setChecked(key == current)
                    actions[action] = key
                rename = None
                if current in labels:
                    menu.addSeparator()
                    rename = menu.addAction('设置当前账号备注…')
                action = menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
                if action is None:
                    return False
                if action == rename:
                    alias, ok = W.QInputDialog.getText(self,'游戏账号备注',labels[current],
                                                       text=c.meta('account_alias:'+current))
                    if ok:
                        with c.db:
                            c.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                                         ('account_alias:'+current,alias.strip()[:32]))
                        self.sync()
                        self.changed.emit()
                    return False
                selected = actions[action]
            c.progress.refresh(install, account=selected)
            self.sync()
            if current != selected:
                self.changed.emit()
            return True
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            self.problem.emit(str(exc))
            return False
