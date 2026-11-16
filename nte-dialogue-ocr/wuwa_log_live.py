"""Desktop live dubbing from the game log and the versioned dialogue catalog."""
import json
from pathlib import Path
import threading
import time
import queue
from collections import Counter

import psutil
from PySide6 import QtCore

from generator_playback import playback_settings
from wuwa_catalog import Catalog, resolve_text
from wuwa_log_generation import existing_audio, GenerationService
from wuwa_log_audio import LogPlayback
from wuwa_log_stream import GameLog
from wuwa_voice_pack import load_pack
from wuwa_breeze import pairing


class LogWorker(QtCore.QThread):
    message = QtCore.Signal(object)

    def __init__(self, root, parent=None, *, jobs=None):
        super().__init__(parent)
        self.root = Path(root)
        self.stop_event = threading.Event()
        self.controller = None
        self.game_pid = None
        self.run_id = str(time.time_ns())
        self.report = self.root/'live-sessions'/self.run_id/'log-playback.jsonl'
        self.write_lock = threading.Lock()
        self.current_row = None
        self.current_voice = None
        self.audio = {}
        self.pack = {}
        self.jobs = jobs
        self.owns_jobs = jobs is None

    def emit(self, **event):
        self.message.emit(dict(event, run_id=self.run_id, at=time.monotonic()))

    def record(self, **event):
        with self.write_lock:
            self.report.parent.mkdir(parents=True, exist_ok=True)
            with self.report.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(event, timestamp=time.time(), monotonic=time.monotonic()), ensure_ascii=False)+'\n')

    def playback_event(self, event):
        self.record(**event)
        self.emit(playback=event)

    def cancel(self):
        self.stop_event.set()
        if self.controller:
            self.controller.stop()

    def clear(self, status):
        if self.jobs:
            self.jobs.set_current(None)
        if self.controller:
            self.controller.clear()
        self.current_row = self.current_voice = None
        self.emit(clear_line=True, status=status, audio_message='等待当前台词')

    def apply(self, subtitle, catalog, config, current_check=None):
        self.clear('等待剧情字幕' if subtitle is None else '正在匹配日志台词')
        if subtitle is None:
            return
        rows = catalog.db.execute('SELECT * FROM lines WHERE state_id=? AND line_id=?',
                                  (subtitle.state, subtitle.number)).fetchall()
        self.record(state='subtitle', flow=subtitle.flow, instance=subtitle.instance, number=subtitle.number, matches=len(rows))
        packed = self.pack.get((subtitle.state, subtitle.number))
        if packed:
            entry, clip = packed
            source = rows[0] if len(rows)==1 else None
            matches = not rows or (source is not None and source['id']==clip.catalog_id
                                   and source['fingerprint']==entry['fingerprint']
                                   and source['voice_status']!='voiced'
                                   and not (source['voice_status']=='unknown' and source['reviewed_at']))
            binding = catalog.binding(entry['speaker_id'])
            explicit = catalog.db.execute('SELECT 1 FROM binding_origins WHERE speaker_id=?',
                                          (entry['speaker_id'],)).fetchone()
            if binding and (explicit or catalog.cloud_only() or binding['voice']!='system:default'):
                matches = matches and binding['voice']==clip.binding_voice
            from wuwa_cast_review import reviewed_roles, review_problem
            roles = reviewed_roles(catalog,entry['quest'])
            if roles is not None and entry['speaker_id'] in roles:
                voice = clip.binding_voice.removeprefix('breeze:')
                matches = matches and not review_problem(catalog,entry['quest'],entry['speaker_id'],voice)
            if matches and self.controller:
                if current_check and not current_check():
                    return
                self.emit(observation=dict(body=clip.text,speaker=clip.speaker,subtitle_id=subtitle.number,
                                           flow=subtitle.flow,catalog_id=clip.catalog_id),log_line=subtitle.number,
                          task=dict(id=entry['quest'],name=entry['quest_name'],state='confirmed',basis='任务配音包'),
                          status='已匹配当前台词 · 任务配音包')
                self.controller.available(clip)
                return
        if len(rows)!=1:
            self.emit(status='本句未匹配 · ' + ('台词库缺少该编号' if not rows else '编号对应多句台词'))
            return
        row = rows[0]
        text, missing = resolve_text(row['raw_text'],config.get('nickname','漂泊者'),config.get('traveler_gender','female'))
        quests = catalog.db.execute("""SELECT s.id,s.name FROM scopes s JOIN scope_lines sl ON s.id=sl.scope_id
                                        WHERE sl.line_id=? AND s.kind='quest'""",(row['id'],)).fetchall()
        self.emit(observation=dict(body=text,speaker=row['speaker'],subtitle_id=subtitle.number,
                                   flow=subtitle.flow,catalog_id=row['id']),log_line=subtitle.number,
                  task=dict(id=quests[0]['id'] if len(quests)==1 else '',
                            name=quests[0]['name'] if len(quests)==1 else subtitle.flow,
                            state='confirmed',basis='剧情日志与台词库对应'))
        basis = catalog.voice_basis(row,self.version,allow_branch=catalog.branch_rule_enabled())
        if not basis:
            self.emit(status='已有原声 · 跳过' if row['voice_status']=='voiced' else '原声未知，待核对')
            return
        if missing:
            self.emit(status='本句含未解析变量，暂缓配音')
            return
        binding = catalog.binding(row['speaker_id'])
        if catalog.cloud_only():
            from wuwa_cast_review import line_problem
            voice_id = binding['voice'].removeprefix('breeze:') if binding else None
            problem = line_problem(catalog,row['id'],row['speaker_id'],voice_id)
            if problem:
                self.emit(status=problem)
                return
        if self.jobs:
            binding = self.jobs.ensure_voice(catalog,row,binding)
        if not binding or not binding['transcript_reviewed']:
            self.emit(status='声线待核对')
            return
        self.current_row = row['id']
        self.current_voice = binding['voice']
        key = self.current_row, self.current_voice
        if self.jobs:
            self.jobs.set_current(key)
        if key in self.audio and self.audio[key].cloud_voice and self.audio[key].cloud_voice!=pairing(catalog,row['speaker_id'],key[1]):
            del self.audio[key]
        if key in self.audio and (self.audio[key].text,self.audio[key].speaker)!=(text,row['speaker']):
            del self.audio[key]
        if key not in self.audio:
            cached,_,_=existing_audio(catalog,config,only=key)
            self.audio.update(cached)
        if key in self.audio and self.controller:
            if current_check and not current_check():
                return
            self.emit(status='已匹配当前台词 · 普通支线' if '普通支线规则' in basis else '已匹配当前台词')
            self.controller.available(self.audio[key])
        elif self.jobs:
            self.jobs.request(catalog,*key)
            self.emit(status='正在生成当前句', audio_message='音频就绪后核对当前台词')
        else:
            self.emit(status='当前句暂无有效音频')

    def run(self):
        from capture_windows import list_game_windows
        from wuwa_resources import inventory
        catalog = log = None
        proc = None
        last = object()
        check_process = 0.
        try:
            config=json.loads((self.root/'config.json').read_text(encoding='utf-8'))
            self.emit(status='正在读取本地配音')
            inventory(config['install_root'],self.root)
            catalog=Catalog(self.root)
            self.version=catalog.local_version()
            self.pack=load_pack(self.root)
            settings,_=playback_settings(config['dubbing'])
            self.audio,projects,held=existing_audio(catalog,config)
            counts=Counter(scope[0] for key in self.audio for scope in catalog.db.execute(
                "SELECT scope_id FROM scope_lines WHERE line_id=? AND scope_id LIKE 'quest:%'",(key[0],)))
            local_ids = {key[0] for key in self.audio}
            for entry,clip in self.pack.values():
                if clip.catalog_id not in local_ids:
                    counts[entry['quest']] += 1
            self.emit(library=dict(lines=len(local_ids | {c.catalog_id for _,c in self.pack.values()}),
                                   projects=projects+sorted({r['quest'] for r,_ in self.pack.values()}),held=held,
                                   task_counts=dict(counts),review_count=0))
            from wuwa_dubber.audio.player import AudioPlayer
            self.controller=LogPlayback(AudioPlayer(settings.output_device,volume=settings.output_volume,latency='low'),self.playback_event)
            if self.owns_jobs:
                self.jobs=GenerationService(self.root,lambda msg:self.emit(generation_message=msg))
            stamp=None
            log_path=Path(config['install_root'])/'Wuthering Waves Game/Client/Saved/Logs/Client.log'
            while not self.stop_event.is_set():
                if time.monotonic()>=check_process:
                    check_process=time.monotonic()+.4
                    windows=[w for w in list_game_windows(game_id='wuwa',install_root=config['install_root']) if not w.is_launcher]
                    pid=windows[0].pid if len(windows)==1 else None
                    if proc and (pid!=proc.pid or not proc.is_running()):
                        log.close()
                        proc=log=None
                        self.game_pid=None
                        self.clear('游戏已断开')
                        last=object()
                    if pid and not proc:
                        try:
                            proc=psutil.Process(pid)
                            log=GameLog(log_path,proc.create_time())
                            self.game_pid=pid
                        except psutil.NoSuchProcess:
                            proc=None
                            continue
                        inventory(config['install_root'],self.root)
                        version=catalog.local_version()
                        if version!=self.version:
                            self.version=version
                            self.audio.clear()
                            self.pack=load_pack(self.root)
                        self.emit(log_connection='已连接鸣潮日志')
                    if not proc:
                        self.emit(log_connection='等待鸣潮启动',status='等待鸣潮启动')
                if log:
                    try:
                        subtitle=log.poll()
                        decision=(self.decision(catalog,subtitle),catalog.branch_rule_enabled())
                        if subtitle!=last or decision!=stamp:
                            last=subtitle
                            self.apply(subtitle,catalog,config,
                                       current_check=lambda:proc.is_running() and log.poll()==subtitle)
                            stamp=(self.decision(catalog,subtitle),catalog.branch_rule_enabled())
                        self.process_jobs(log,subtitle,catalog)
                    except (OSError, ValueError) as exc:
                        self.clear(str(exc))
                        log.close()
                        last=object()
                        self.stop_event.wait(.3)
                else:
                    self.process_jobs(None,None,catalog)
                self.stop_event.wait(.035)
        except Exception as exc:
            self.emit(status='日志配音出错 · '+str(exc))
            self.record(state='error',message=str(exc))
        finally:
            if self.jobs:
                self.jobs.set_current(None)
            if self.controller:
                self.controller.close()
            if self.owns_jobs and self.jobs:
                self.jobs.close()
            if log:
                log.close()
            if catalog:
                catalog.close()
            self.controller=None
            self.game_pid=None

    def decision(self,catalog,subtitle):
        if subtitle is None:
            return None
        if catalog.cloud_only():
            return tuple(tuple(row) for row in catalog.db.execute(
                """SELECT l.id,l.fingerprint,l.voice_status,l.review_version,coalesce('character:'||r.profile,'breeze:'||c.voice_id),
                          CASE WHEN c.voice_id IS NULL AND r.profile IS NULL THEN 0 ELSE 1 END,c.voice_id,
                          (SELECT group_concat(q.roles) FROM scope_lines sl JOIN quest_cast_reviews q
                           ON q.quest_id=sl.scope_id WHERE sl.line_id=l.id),
                          (SELECT coalesce(o.cloud_voice_id,'pending') FROM original_voice_roles o WHERE o.speaker_id=l.speaker_id)
                   FROM lines l LEFT JOIN breeze_cast c ON c.speaker_id=l.speaker_id
                   LEFT JOIN original_reference_cast r ON r.speaker_id=l.speaker_id
                   WHERE l.state_id=? AND l.line_id=? ORDER BY l.id""",(subtitle.state,subtitle.number)))
        return tuple(tuple(row) for row in catalog.db.execute(
            """SELECT l.id,l.fingerprint,l.voice_status,l.review_version,b.voice,b.transcript_reviewed,p.voice_id
               FROM lines l LEFT JOIN voice_bindings b ON b.speaker_id=l.speaker_id
               LEFT JOIN breeze_voices p ON p.speaker_id=l.speaker_id AND p.local_voice=b.voice
               WHERE l.state_id=? AND l.line_id=? ORDER BY l.id""",(subtitle.state,subtitle.number)))

    def process_jobs(self,log,subtitle,catalog):
        while True:
            try:
                identity,voice,clip,error=self.jobs.finished.get_nowait()
            except queue.Empty:
                break
            if clip:
                self.audio[identity,voice]=clip
            if (identity,voice)!=(self.current_row,self.current_voice) or not log or not subtitle:
                continue
            if error:
                self.emit(status='生成暂缓 · '+error)
            elif log.poll()==subtitle and self.allowed_now(catalog,subtitle,identity,voice,clip.cloud_voice):
                self.controller.available(clip)
                self.emit(status='当前句音频已就绪')

    def allowed_now(self,catalog,subtitle,identity,voice,cloud_voice=''):
        matches=self.decision(catalog,subtitle)
        if (len(matches)!=1 or matches[0][0]!=identity or matches[0][4:6]!=(voice,1)
                or (cloud_voice and matches[0][6]!=cloud_voice)):
            return False
        row=catalog.db.execute('SELECT * FROM lines WHERE id=?',(identity,)).fetchone()
        if row and catalog.cloud_only():
            from wuwa_cast_review import line_problem
            if line_problem(catalog,identity,row['speaker_id'],cloud_voice or voice):
                return False
        return bool(row and catalog.voice_basis(row,self.version,allow_branch=catalog.branch_rule_enabled()))

