"""Task library: derive both views from the same set of playable local recordings."""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import json
from threading import Event
from time import monotonic

from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from dialogue_generation import BatchGenerator, file_digest
from wuwa_catalog import Catalog, resolve_text
from wuwa_breeze import strip_asides
from wuwa_log_generation import existing_audio
from wuwa_surface import AccentButton
from wuwa_voice_pack import installed_previews, load_pack, pack_matches
import wuwa_quest_download as quest_download
import wuwa_update as update
from wuwa_update_ui import Operation
from wuwa_account_ui import GameAccountButton


@dataclass
class Quest:
    name: str
    ids: list = field(default_factory=list)
    ready: int = 0
    evidence: str = ""

    @property
    def complete(self):
        return bool(self.ids) and self.ready == len(self.ids)


def playable(catalog, config):
    exports, _, _ = existing_audio(catalog, config)
    audio = {clip.catalog_id: clip for clip in exports.values()}
    packed = {}
    for (state, number), (entry, clip) in load_pack(catalog.root, allow_missing_audio=True).items():
        rows = catalog.db.execute('SELECT * FROM lines WHERE state_id=? AND line_id=?', (state, number)).fetchall()
        if not rows or (len(rows) == 1 and rows[0]['id'] == clip.catalog_id
                        and rows[0]['fingerprint'] == entry['fingerprint'] and rows[0]['voice_status'] != 'voiced'):
            packed[clip.catalog_id] = entry
        if clip.path.is_file() and pack_matches(catalog, rows, entry, clip) and file_digest(clip.path) == clip.sha256:
            audio[clip.catalog_id] = clip
    return audio, packed


def read_library(root):
    catalog = Catalog(root)
    try:
        config = json.loads((root / 'config.json').read_text(encoding='utf8'))
        audio, packed = playable(catalog, config)
        from wuwa_daily_plan import initialize
        from wuwa_daily_scan import matched_lines
        from wuwa_quest_names import repair,title
        initialize(catalog)
        repair(catalog)
        account=catalog.progress.account
        discovered={scope:(name,reason) for scope,name,reason in catalog.db.execute(
            'SELECT scope,name,evidence FROM daily_discovered WHERE account=?',(account,))}
        manifest=root/'sources/local-inventory.json'
        verified=matched_lines(json.loads(manifest.read_text(encoding='utf8')), catalog, discovered) if discovered and manifest.is_file() else {}
        quests = {}
        for scope, name, identity, text in catalog.db.execute(
            """SELECT s.id,s.name,l.id,l.raw_text FROM scopes s JOIN scope_lines sl ON sl.scope_id=s.id
               JOIN lines l ON l.id=sl.line_id WHERE s.kind='quest' AND l.voice_status!='voiced'
               ORDER BY s.name,s.id,l.flow_id,l.state_id,l.branch,CAST(l.line_id AS INTEGER),l.id"""
        ):
            if scope in discovered and scope in verified and identity not in verified[scope]:
                continue
            if not any(ch.isalnum() for ch in strip_asides(text)):
                continue
            if scope not in quests:
                quests[scope] = Quest(title(scope[6:],name))
            quests[scope].ids.append(identity)
        for identity, entry in packed.items():
            scope = entry['quest']
            quest = quests.setdefault(scope, Quest(entry['quest_name']))
            if identity not in quest.ids:
                quest.ids.append(identity)
        for scope,(name,reason) in discovered.items():
            q=quests.setdefault(scope,Quest(title(scope[6:],name)))
            q.evidence=reason
        for quest in quests.values():
            quest.ready = sum(identity in audio for identity in quest.ids)
        return quests, audio, packed
    finally:
        catalog.close()


class IndexReader(C.QThread):
    loaded = C.Signal(object)
    failed = C.Signal(str)

    def __init__(self, root, parent):
        super().__init__(parent)
        self.root = root

    def run(self):
        try:
            self.loaded.emit((read_library(self.root), installed_previews(self.root), quest_download.build_id(self.root)))
        except Exception as exc:
            self.failed.emit(str(exc))


def choose_cloud_voice(catalog, sid, voice_id):
    """An explicit speaker choice also updates that speaker's existing task cast entries."""
    from wuwa_cast_review import original_problem
    with catalog.db:
        original = catalog.db.execute('SELECT profile FROM original_reference_cast WHERE speaker_id=?', (sid,)).fetchone()
        problem = original_problem(catalog, sid, voice_id)
        if original or problem:
            raise ValueError(problem or '此角色沿用原声参考 · ' + original[0])
        preset = catalog.db.execute('SELECT gender FROM breeze_presets WHERE id=?', (voice_id,)).fetchone()
        if not preset:
            raise ValueError('此音色已从云端预设库移除')
        plans = catalog.db.execute("""SELECT DISTINCT r.quest_id,r.roles FROM quest_cast_reviews r
            JOIN scope_lines s ON s.scope_id=r.quest_id JOIN lines l ON l.id=s.line_id
            WHERE l.speaker_id=?""", (sid,)).fetchall()
        for quest, raw in plans:
            roles = json.loads(raw)
            if sid in roles and roles[sid] != 'original':
                roles[sid] = preset[0] if preset[0] in ('male', 'female') else 'any'
                catalog.db.execute('UPDATE quest_cast_reviews SET roles=? WHERE quest_id=?',
                                   (json.dumps(roles, sort_keys=True), quest))
        catalog.db.execute('INSERT OR REPLACE INTO breeze_cast VALUES(?,?)', (sid, voice_id))


def prepare_cast(catalog, scope, rows):
    """Record the automatic cast of a newly selected task, retaining existing role choices."""
    from wuwa_cast_review import reviewed_roles
    if (not rows or not catalog.cloud_only() or catalog.meta('require_quest_cast_review') != '1'
            or reviewed_roles(catalog, scope) is not None):
        return
    from wuwa_cast_context import quest_suggestion
    members = catalog.lines([scope])
    roles = {}
    for row in members:
        sid = row['speaker_id']
        if sid in roles:
            continue
        if catalog.db.execute('SELECT 1 FROM original_voice_roles WHERE speaker_id=?', (sid,)).fetchone():
            roles[sid] = 'original'
        else:
            assigned = catalog.db.execute('SELECT p.gender FROM breeze_cast c JOIN breeze_presets p ON p.id=c.voice_id WHERE c.speaker_id=?', (sid,)).fetchone()
            gender = assigned[0] if assigned else quest_suggestion(members, sid, row['speaker'])[0]
            roles[sid] = gender if gender in ('male', 'female') else 'any'
    with catalog.db:
        added = catalog.db.execute('INSERT OR IGNORE INTO quest_cast_reviews VALUES(?,?)',
                                   (scope, json.dumps(roles, sort_keys=True))).rowcount
        if added:
            catalog.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('cast_source:' + scope, 'automatic'))


def generate_missing(root, scope, ids, stop, progress, on_ready=None, *, selection=None, discovered_only=False):
    """Use the installed batch engine, including its voice, budget and cache checks."""
    from wuwa_resources import inventory
    config = json.loads((root / 'config.json').read_text(encoding='utf8'))
    mounted=inventory(config['install_root'], root)
    catalog = Catalog(root)
    try:
        if selection is not None:
            catalog.progress.require(selection)
        available, _ = playable(catalog, config)
        pending = set(ids).difference(available)
        if not pending or stop.is_set():
            return '当前任务无需新增录音'
        version = catalog.local_version()
        direct, single_lines, eligible = set(), [], []
        if catalog.progress.refresh_for_preparation(config['install_root'], require_progress=discovered_only) is None:
            progress('本机暂无进度记录，按手动选择的任务核对台词与声线')
        if selection is None:
            selection = catalog.progress.selection
        catalog.progress.require(selection)
        if catalog.progress.states(selection[0]).get(scope.removeprefix('quest:'))=='completed':
            raise ValueError('所选账号已完成此任务，已停止配音。')
        from wuwa_daily_plan import initialize
        from wuwa_daily_scan import scan,matched_lines
        initialize(catalog)
        seen=catalog.db.execute('SELECT 1 FROM daily_discovered WHERE account=? AND scope=?',
                                (selection[0],scope)).fetchone()
        if discovered_only and not seen:
            raise ValueError('当前任务不在所选账号的扫描结果中，请重新选择任务。')
        confirmed=matched_lines(mounted,catalog,[scope]).get(scope,{})
        if discovered_only:
            active_account, latest = scan(catalog,config)
            if active_account!=selection[0] or scope not in {s for s,_,_ in latest}:
                raise ValueError('当前账号或任务进度已变化，请重新扫描支线')
            if not confirmed:
                raise ValueError('当前游戏版本的任务指纹尚未覆盖本任务')
        blocked=Counter()
        allowed = catalog.progress.allowed([scope], bool(config.get('allow_unknown_progress', False)))
        for row in catalog.lines([scope]):
            if row['id'] not in pending:
                continue
            known=confirmed.get(row['id'])==row['fingerprint']
            if discovered_only and not known:
                blocked['游戏台词指纹未匹配']+=1
                continue
            if known and catalog.voice_basis(row,version,allow_branch=catalog.branch_rule_enabled()):
                single_lines.append(row)
            elif catalog.voice_basis(row, version):
                if row['id'] not in allowed:
                    blocked['未取得当前账号的未完成证据']+=1
                    continue
                direct.add(row['id'])
            elif catalog.voice_basis(row, version, allow_branch=catalog.branch_rule_enabled()):
                single_lines.append(row)
            else:
                blocked[getattr(catalog,'branch_policy_error','') or '原声或资源版本未核实']+=1
                continue
            eligible.append(row)

        prepare_cast(catalog, scope, eligible)
        done = 0
        problems = []
        def run_project(scopes, selected, single=False):
            nonlocal done
            catalog.progress.require(selection)
            project, _ = catalog.prepare_project(scopes, config, selected, live_line=single)
            lock = C.QLockFile(str(project.directory / 'project.lock'))
            lock.setStaleLockTime(0)
            if not lock.tryLock(0):
                raise ValueError('此配音项目正在其他窗口使用')
            try:
                runner = BatchGenerator(project, dict(config['dubbing'], catalog_root=str(root)),
                                        lambda event: progress(event['message']) if event.get('message') else None,
                                        check_selection=lambda:catalog.progress.require(selection))
                runner.stop_event = stop
                result = runner.run()
                done += result['completed'] + result['resumed']
                from wuwa_log_generation import prepared_line
                for entry in project.rows:
                    clip=prepared_line(project,entry)
                    if clip and on_ready:
                        on_ready(entry.source_id,clip)
                progress(f'已就绪 {done}/{len(pending)} 句')
                problems.extend(r.error for r in project.rows if r.error)
            finally:
                lock.unlock()

        if direct and not stop.is_set():
            try:
                run_project([scope], direct)
            except ValueError as exc:
                problems.append(str(exc))
        # Ordinary side quests use the same verified single-line preparation as the daily queue.
        members=catalog.lines([scope])
        for row in single_lines:
            catalog.progress.require(selection)
            if stop.is_set() or any('预算' in msg or 'API Key' in msg for msg in problems):
                break
            try:
                if catalog.cloud_only() and catalog.meta('daily_cast_enabled')=='1' and not catalog.binding(row['speaker_id']):
                    from wuwa_daily_cast import voice_for
                    voice_for(catalog,scope,row,members)
                run_project(['flow:' + row['flow_id']], {row['id']}, single=True)
            except (OSError, ValueError) as exc:
                problems.append(str(exc))
        message = f'本次完成 {done} 句'
        if stop.is_set():
            return message + ' · 已停止'
        if problems:
            detail = problems[0]
            if '在任务库' in detail or '声线还没有确认' in detail:
                detail = '请点击对白中的角色名选择可用音色'
            return message + ' · ' + detail
        if done < len(pending):
            return message + ' · '+('；'.join(f'{why} {n} 句' for why,n in blocked.items()) or '缺少可用的台词或声线')
        return message
    finally:
        catalog.close()


class QuestLibrary(W.QWidget):
    voice_requested = C.Signal(object)
    generate_requested = C.Signal(str, object)
    notice = C.Signal(str)
    line_ready = C.Signal(str,object)
    page_size = 40

    def open_daily_plan(self):
        from wuwa_daily_ui import DailyDialog
        dialog = DailyDialog(self.catalog, self)
        dialog.scan_changed.connect(self.scanned)
        dialog.exec()
        dialog.deleteLater()
        self.reload()

    def __init__(self, catalog, parent=None, *, install_root=None):
        super().__init__(parent)
        self.catalog = catalog
        self.install_root = install_root or Path(__file__).resolve().parents[1]
        self.local_quests = {}
        self.offers = {}
        self.installed = {}
        self.game_build = ''
        self.offer_worker = self.pack_worker = None
        self.offer_source = None
        self.last_check = 0
        self.download_stop = Event()
        self.quests, self.audio, self.packed = {}, {}, {}
        self.rows = []
        self.scope = None
        self.requested_scope = None
        self.page_number = 0
        self.busy = False
        self.reload_pending = False
        self.closing = False
        self.revision=None
        self.catalog_poll=C.QTimer(self)
        self.catalog_poll.setInterval(1800)
        self.catalog_poll.timeout.connect(self.check_catalog)
        self.reader = IndexReader(catalog.root, self)
        self.reader.loaded.connect(self.loaded_view)
        self.reader.failed.connect(self.index_failed)
        self.reader.finished.connect(self.scan_finished)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.player.errorOccurred.connect(lambda _, message: self.notice.emit('试听失败 · ' + message))
        layout = W.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(10)
        filters = W.QHBoxLayout()
        group = W.QButtonGroup(self)
        self.pills = []
        for complete, label in enumerate(('未配音任务', '已配音任务')):
            button = W.QPushButton(label + ' (…)')
            button.setCheckable(True)
            button.setObjectName('questPill')
            button.setCursor(C.Qt.PointingHandCursor)
            group.addButton(button, complete)
            filters.addWidget(button)
            self.pills.append(button)
        self.pills[0].setChecked(True)
        group.idClicked.connect(self.filter_changed)
        filters.addStretch()
        self.daily_plan_button = W.QPushButton('每日配音计划')
        self.daily_plan_button.clicked.connect(self.open_daily_plan)
        self.account_button = GameAccountButton(catalog, self)
        self.account_button.changed.connect(self.account_changed)
        self.account_button.problem.connect(self.set_work_status)
        self.discovered_only=W.QCheckBox('扫描到的支线')
        self.discovered_only.setChecked(bool(catalog.progress.account and (
            catalog.meta('scanned:'+catalog.progress.account) or catalog.meta('daily_scan_account')==catalog.progress.account)))
        self.discovered_only.toggled.connect(self.filter_changed)
        filters.addWidget(self.discovered_only)
        filters.addWidget(self.account_button)
        filters.addWidget(self.daily_plan_button)
        self.search = W.QLineEdit()
        self.search.setPlaceholderText('搜索任务名称')
        self.search.setAccessibleName('搜索任务名称')
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self.filter_changed)
        filters.addWidget(self.search, 1)
        self.prev = AccentButton('', 'previous')
        self.next = AccentButton('', 'next')
        self.page = W.QLabel()
        for btn, label, step in ((self.prev, '上一页', -1), (self.next, '下一页', 1)):
            btn.setAccessibleName(label)
            btn.setToolTip(label)
            btn.clicked.connect(lambda _, n=step: self.turn_page(n))
        for widget in (self.prev, self.page, self.next):
            filters.addWidget(widget)
        layout.addLayout(filters)
        split = W.QSplitter()
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)
        self.tasks = self.table(['任务名称','句数','状态'], 0)
        self.tasks.setColumnWidth(1, 48)
        self.tasks.setColumnWidth(2, 85)
        self.tasks.setMinimumWidth(235)
        split.addWidget(self.tasks)
        right = W.QWidget()
        detail = W.QVBoxLayout(right)
        detail.setContentsMargins(8, 0, 0, 0)
        self.lines = self.table(['角色','台词正文','音频状态'], 1)
        self.lines.setWordWrap(True)
        self.lines.verticalHeader().setMinimumSectionSize(34)
        self.lines.verticalHeader().setSectionResizeMode(W.QHeaderView.ResizeToContents)
        self.lines.setColumnWidth(0, 100)
        self.lines.setColumnWidth(2, 76)
        detail.addWidget(self.lines, 1)
        self.summary = W.QLabel()
        self.summary.setObjectName('subtle')
        detail.addWidget(self.summary)
        package_info = W.QHBoxLayout()
        self.package_status = W.QLabel()
        self.package_status.setWordWrap(True)
        self.package_status.setTextFormat(C.Qt.PlainText)
        package_info.addWidget(self.package_status, 1)
        self.retry_catalog = W.QPushButton('重试配音目录')
        self.retry_catalog.clicked.connect(lambda: self.check_offers(force=True))
        self.retry_catalog.hide()
        package_info.addWidget(self.retry_catalog)
        detail.addLayout(package_info)
        self.catalog_error = ''
        self.work_note=W.QLabel()
        self.work_note.setWordWrap(True)
        self.work_note.hide()
        detail.addWidget(self.work_note)
        self.line_ready.connect(self.mark_ready)
        actions = W.QHBoxLayout()
        self.generate = W.QPushButton('一键生成本任务配音')
        self.generate.setObjectName('primary')
        self.generate.clicked.connect(self.primary_action)
        self.preview = AccentButton('试听当前句', 'play')
        self.preview.clicked.connect(self.preview_current)
        actions.addWidget(self.generate)
        actions.addWidget(self.preview)
        actions.addStretch()
        detail.addLayout(actions)
        split.addWidget(right)
        split.setSizes([370, 650])
        self.tasks.currentCellChanged.connect(self.select_task)
        self.lines.currentCellChanged.connect(self.update_actions)
        self.lines.cellClicked.connect(self.role_clicked)
        role_key = G.QShortcut(G.QKeySequence(C.Qt.Key_Return), self.lines)
        role_key.setContext(C.Qt.WidgetShortcut)
        role_key.activated.connect(lambda: self.role_clicked(self.lines.currentRow(), self.lines.currentColumn()))
        self.setStyleSheet("""
            QPushButton#questPill {background:#192328; color:#ABBDC3; border:1px solid #33464E;
                border-radius:14px; padding:5px 13px;}
            QPushButton#questPill:checked {background:#D8B467; border-color:#E8C880; color:#11181B; font-weight:600;}
            QLabel#questStatus {background:#253237; color:#ADBDC2; border-radius:9px; padding:1px 8px;}
            QLabel#questStatus[ready="true"] {background:#183B35; color:#86DEC1;}
        """)
        self.update_actions()
        self.prev.setEnabled(False)
        self.next.setEnabled(False)

    @staticmethod
    def table(headers, stretch):
        table = W.QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(W.QAbstractItemView.SingleSelection)
        table.setSelectionBehavior(W.QAbstractItemView.SelectRows)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(36)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(stretch, W.QHeaderView.Stretch)
        return table

    def check_catalog(self):
        self.account_button.sync()
        inventory = self.catalog.root / 'sources/local-inventory.json'
        try:
            inventory_stamp = inventory.stat().st_mtime_ns
        except FileNotFoundError:
            inventory_stamp = None
        revision=(self.catalog.progress.selection,self.catalog.meta('daily_scan_revision'),
                  self.catalog.meta('daily_updated'),inventory_stamp)
        if revision!=self.revision:
            self.revision=revision
            self.reload()

    def showEvent(self, event):
        super().showEvent(event)
        self.account_button.detect_single()
        self.check_offers()
        self.catalog_poll.start()
        self.check_catalog()
        self.reload()

    def hideEvent(self, event):
        self.catalog_poll.stop()
        self.player.stop()
        super().hideEvent(event)

    def shutdown(self):
        self.closing = True
        self.catalog_poll.stop()
        self.player.stop()
        self.download_stop.set()
        for worker in (self.offer_worker, self.pack_worker):
            if worker is not None:
                worker.wait()
        self.reader.wait()

    def check_offers(self, force=False):
        source = update.selected_source(self.install_root)
        if self.closing or self.offer_worker is not None:
            return
        if not force and source == self.offer_source and monotonic() - self.last_check < 600:
            return
        self.offer_source = source
        self.last_check = monotonic()
        self.catalog_error = ''
        self.retry_catalog.hide()
        cached = quest_download.catalog_file(self.catalog.root)
        if not self.offers and cached.is_file():
            try:
                self.offers = quest_download.parse_catalog(cached.read_bytes())
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.catalog_error = '缓存目录无效 · ' + str(exc)
        root = self.catalog.root
        worker = Operation(lambda _: quest_download.fetch_catalog(root, source), self)
        self.offer_worker = worker
        worker.result.connect(self.offers_loaded)
        worker.failed.connect(self.offers_failed)
        worker.finished.connect(self.offers_finished)
        self.merge_offers()
        self.render()
        worker.start()

    def offers_loaded(self, offers):
        if not self.closing:
            self.catalog_error = ''
            if offers != self.offers:
                self.offers = offers
                self.merge_offers()
                self.render()

    def offers_failed(self, error):
        if not self.closing:
            self.catalog_error = ('使用上次配音目录' if self.offers else '成品目录暂不可用') + ' · ' + error
            self.retry_catalog.show()

    def offers_finished(self):
        self.offer_worker.deleteLater()
        self.offer_worker = None
        if not self.closing:
            self.update_actions()
            if self.isVisible() and self.offer_source != update.selected_source(self.install_root):
                self.check_offers()

    def merge_offers(self):
        self.quests = dict(self.local_quests)
        for scope, (_, clips) in self.installed.items():
            first = next(iter(clips.values()))[0]
            self.quests.setdefault(scope, Quest(first['quest_name']))
        for scope, offer in self.offers.items():
            self.quests.setdefault(scope, Quest(offer['name']))

    def loaded_view(self, result):
        if self.closing or self.reader_selection != self.catalog.progress.selection:
            self.reload_pending = True
            return
        library, self.installed, self.game_build = result
        self.loaded(library)

    def local_complete(self, scope):
        q = self.quests[scope]
        if q.complete or scope not in self.installed:
            return q.complete
        revision, clips = self.installed[scope]
        offer = self.offers.get(scope)
        if offer and revision == offer['revision']:
            return len(clips) == offer['lines']
        return all(i in clips or i in self.audio for i in q.ids)

    def available_offer(self):
        offer = self.offers.get(self.scope)
        if offer and self.installed.get(self.scope, (0, {}))[0] < offer['revision']:
            return offer
        return None

    def primary_action(self):
        offer = self.available_offer()
        if not offer:
            self.generate_current()
            return
        if self.pack_worker is not None or self.busy:
            return
        scope, root = self.scope, self.catalog.root
        source = update.selected_source(self.install_root)
        worker = Operation(lambda progress: quest_download.install_offer(
            self.install_root, root, offer, source, self.download_stop, progress), self)
        self.pack_worker = worker
        self.download_scope = scope
        self.download_stop.clear()
        self.set_work_status('正在下载 · ' + offer['name'])
        worker.message.connect(self.set_work_status)
        worker.failed.connect(self.set_work_status)
        worker.result.connect(self.pack_installed)
        worker.finished.connect(self.pack_finished)
        self.update_actions()
        worker.start()

    def pack_installed(self, message):
        if self.closing:
            return
        if self.scope == self.download_scope:
            self.requested_scope = self.scope
        self.set_work_status(message + ' · 可选中台词试听')
        self.reload()

    def pack_finished(self):
        self.pack_worker.deleteLater()
        self.pack_worker = None
        if not self.closing:
            self.update_actions()

    def set_work_status(self,text):
        self.work_note.setText(text)
        self.work_note.setVisible(bool(text))

    def scanned(self):
        self.discovered_only.setChecked(True)
        self.reload()

    def account_changed(self):
        self.page_number = 0
        self.set_work_status('当前游戏账号 · '+self.catalog.progress.label(self.catalog.progress.account))
        self.reload()

    def mark_ready(self,identity,clip):
        self.audio[identity]=clip
        for quest in self.quests.values():
            if identity in quest.ids:
                quest.ready=sum(i in self.audio for i in quest.ids)
        for i,row in enumerate(self.rows):
            if row['id']==identity:
                self.lines.item(i,2).setText('已生成')
        self.update_actions()

    def reload(self):
        if self.closing:
            return
        if self.reader.isRunning():
            self.reload_pending = True
        elif self.isVisible():
            self.reader_selection = self.catalog.progress.selection
            self.reader.start()
        self.update_actions()

    def scan_finished(self):
        if self.closing:
            return
        if self.reload_pending:
            self.reload_pending = False
            self.reload()
        else:
            self.update_actions()

    def index_failed(self, message):
        if self.closing:
            return
        self.local_quests, self.audio, self.packed = {}, {}, {}
        self.merge_offers()
        self.render()
        self.set_work_status('本地任务库读取失败 · ' + message)
        self.notice.emit('任务库读取失败 · ' + message)

    def loaded(self, result):
        if self.closing:
            return
        if getattr(self,'reader_selection',self.catalog.progress.selection) != self.catalog.progress.selection:
            self.reload_pending=True
            return
        self.local_quests, self.audio, self.packed = result
        self.merge_offers()
        if self.requested_scope and self.requested_scope not in self.quests:
            self.requested_scope = None
            self.notice.emit('该任务没有可列出的预配音台词')
        if self.requested_scope in self.quests:
            self.focus_scope(self.requested_scope)
        else:
            self.render()

    def focus_scope(self, scope):
        self.requested_scope = scope
        if scope not in self.quests:
            return
        self.requested_scope = None
        self.scope = scope
        if not self.quests[scope].evidence:
            self.discovered_only.setChecked(False)
        self.pills[int(self.local_complete(scope))].setChecked(True)
        with C.QSignalBlocker(self.search):
            self.search.clear()
        self.render(locate=True)

    def filter_changed(self, *_):
        self.page_number = 0
        self.render()

    def turn_page(self, step):
        self.page_number += step
        self.render()

    def render(self, locate=False):
        text = self.search.text().strip().casefold()
        visible = [(sid,q) for sid,q in self.quests.items() if text in q.name.casefold() and (not self.discovered_only.isChecked() or q.evidence)]
        counts = Counter(self.local_complete(sid) for sid,q in visible)
        for i, label in enumerate(('未配音任务','已配音任务')):
            self.pills[i].setText(f'{label} ({counts[bool(i)]})')
        scopes = [scope for scope,quest in visible if self.local_complete(scope) == self.pills[1].isChecked()]
        pages = (len(scopes) + self.page_size - 1) // self.page_size
        if locate and self.scope in scopes:
            self.page_number = scopes.index(self.scope) // self.page_size
        self.page_number = max(0, min(self.page_number, pages - 1))
        self.page.setText(f'{self.page_number + 1 if pages else 0} / {pages}')
        self.prev.setEnabled(self.page_number > 0)
        self.next.setEnabled(self.page_number + 1 < pages)
        start = self.page_number * self.page_size
        page = scopes[start:start+self.page_size]
        with C.QSignalBlocker(self.tasks):
            self.tasks.clearContents()
            self.tasks.setRowCount(len(page))
            for i,sid in enumerate(page):
                q = self.quests[sid]
                name = W.QTableWidgetItem(q.name)
                name.setData(C.Qt.UserRole, sid)
                name.setToolTip(q.name+((" · "+q.evidence) if q.evidence else ""))
                self.tasks.setItem(i,0,name)
                count = len(q.ids) or self.offers.get(sid, {}).get('lines') or len(self.installed.get(sid,(0,{}))[1])
                self.tasks.setItem(i,1,W.QTableWidgetItem(str(count)))
                status = '已配音' if self.local_complete(sid) else '有成品包' if sid in self.offers else '未配音'
                self.tasks.setItem(i,2,W.QTableWidgetItem(''))
                self.tasks.item(i,2).setData(C.Qt.AccessibleTextRole,status)
                container = W.QWidget()
                container.setAttribute(C.Qt.WA_TransparentForMouseEvents)
                row = W.QHBoxLayout(container)
                row.setContentsMargins(4,0,4,0)
                badge = W.QLabel(status)
                badge.setObjectName('questStatus')
                badge.setProperty('ready', self.local_complete(sid))
                row.addWidget(badge,0,C.Qt.AlignLeft | C.Qt.AlignVCenter)
                self.tasks.setCellWidget(i,2,container)
            if page:
                self.tasks.setCurrentCell(page.index(self.scope) if self.scope in page else 0,0)
        self.select_task()

    def select_task(self, *_):
        item = self.tasks.item(self.tasks.currentRow(),0)
        sid = item.data(C.Qt.UserRole) if item else None
        old_row = self.lines.currentRow()
        previous = self.rows[old_row]['id'] if sid == self.scope and 0 <= old_row < len(self.rows) else None
        if sid != self.scope:
            self.player.stop()
        self.scope = sid
        ids = self.quests[sid].ids if sid else []
        indexed = {r['id']:r for r in self.catalog.lines([sid])} if sid else {}
        self.rows = [indexed.get(i) or self.packed[i] for i in ids if i in indexed or i in self.packed]
        self.pack_clips = self.installed.get(sid,(0,{}))[1]
        listed = {r['id'] for r in self.rows}
        self.rows.extend(entry for i,(entry,_) in self.pack_clips.items() if i not in listed)
        cfg = json.loads((self.catalog.root/'config.json').read_text(encoding='utf8'))
        with C.QSignalBlocker(self.lines):
            self.lines.setRowCount(len(self.rows))
            selected = 0
            for i,r in enumerate(self.rows):
                if 'raw_text' in r.keys():
                    body, _ = resolve_text(r['raw_text'],cfg.get('nickname','漂泊者'),cfg.get('traveler_gender','female'))
                else:
                    body = r['text']
                state = '已生成' if r['id'] in self.audio else '成品录音' if r['id'] in self.pack_clips else '未生成'
                for col,text in enumerate((r['speaker'],body,state)):
                    cell = W.QTableWidgetItem(text)
                    cell.setToolTip('切换角色音色 · '+text if col==0 else text)
                    if col==0:
                        cell.setForeground(G.QColor('#D8B467'))
                    self.lines.setItem(i,col,cell)
                if r['id']==previous:
                    selected=i
            if self.rows:
                self.lines.setCurrentCell(selected,1)
        self.update_actions()

    def update_actions(self, *_):
        q = self.quests.get(self.scope)
        row = self.lines.currentRow()
        offer = self.offers.get(self.scope)
        download = self.available_offer()
        clips = self.installed.get(self.scope,(0,{}))[1]
        ready = len(set(clips).union(i for i in q.ids if i in self.audio)) if q else 0
        total = len(set(q.ids).union(clips)) if q else 0
        total = total or (offer['lines'] if offer else 0)
        self.summary.setText(f'共 {total} 句 · 本地就绪 {ready} 句' if q else
                             '正在读取本地录音…' if self.reader.isRunning() else '没有匹配的任务')
        info = []
        if offer:
            info.append(f"成品已配齐 {offer['lines']} 句")
            info.append('当前版本实机验证通过' if self.game_build in offer['tested_builds'] else '当前版本尚无实机验证记录')
        if self.catalog_error:
            info.append(self.catalog_error)
        elif self.offer_worker is not None and not self.offers:
            info.append('正在检查成品配音目录…')
        self.package_status.setText(' · '.join(info))
        self.package_status.setVisible(bool(info))
        fetching = self.pack_worker is not None
        if fetching and self.download_scope == self.scope:
            label = '正在下载配音包…'
        elif download:
            label = f"下载配音包 · {download['size']/1048576:.1f} MiB"
        elif clips and (not q or not q.ids or q.complete):
            label = '配音包已安装'
        else:
            label = '一键生成本任务配音'
        self.generate.setText(label)
        self.generate.setEnabled(bool((download or (q and q.ids and not q.complete))
            and not self.busy and not fetching and not self.reader.isRunning()
            and (self.offer_worker is None or download)))
        self.preview.setEnabled(bool(not self.reader.isRunning() and 0 <= row < len(self.rows)
            and (self.rows[row]['id'] in self.audio or self.rows[row]['id'] in clips)))

    def generate_current(self):
        q = self.quests.get(self.scope)
        scope = self.scope
        if q and self.generate.isEnabled() and self.account_button.ensure(allow_missing=not self.discovered_only.isChecked()):
            self.generate_requested.emit(scope, [i for i in q.ids if i not in self.audio])

    def preview_current(self):
        row = self.lines.currentRow()
        if not 0 <= row < len(self.rows):
            return
        clip = self.audio.get(self.rows[row]['id'])
        if clip is None and self.rows[row]['id'] in self.pack_clips:
            clip = self.pack_clips[self.rows[row]['id']][1]
        if not clip:
            return
        if not clip.path.is_file() or file_digest(clip.path) != clip.sha256:
            self.notice.emit('录音已变化，正在刷新任务库')
            self.reload()
            return
        self.player.stop()
        self.player.setSource(C.QUrl.fromLocalFile(str(clip.path)))
        self.player.play()

    def role_clicked(self, row, column):
        if column==0 and not self.busy and 0 <= row < len(self.rows):
            self.player.stop()
            self.voice_requested.emit(self.rows[row])
