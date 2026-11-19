"""One durable synthesis queue shared by live dialogue and voice changes."""
import hashlib
import json
from pathlib import Path
import queue
import shutil
import threading
from uuid import uuid4

from dialogue_dubbing import VoiceRuntime, voice_code_path, voice_paths
from dialogue_generation import BatchGenerator, GenerationProject, select_voice, _publish_export, timestamp
from prepared_playback import PreparedLine
from wuwa_catalog import Catalog, resolve_text
from wuwa_voices import local_voice_signature
import wuwa_breeze as breeze


def projects(root):
    paths = [p for folder in ('generation-projects','live-generation-projects')
             for p in (root/folder).glob('*/project.json')]
    for path in sorted(paths,key=lambda p:p.stat().st_mtime_ns,reverse=True):
        try:
            project=GenerationProject.load(path)
            if project.game_id=='wuwa':
                yield project
        except (ValueError, OSError, KeyError):
            continue


def prepared_line(project, row):
    path=project.audio_path(row)
    if row.status=='done' and path:
        return PreparedLine(row.id,row.text,row.speaker,row.audio['voice'],path,row.audio['sha256'],
                            not row.voice.startswith('system:'),row.source_id,row.voice,row.audio.get('cloud_voice',''))
    return None


def existing_audio(catalog,config,only=None):
    """Tie WAVs to exact catalog identity, current text, reference and binding."""
    if catalog.cloud_only():
        voice_code_path(config['dubbing'])
    else:
        voice_paths(config['dubbing'])
    audio, runtime, signatures, used, held = {}, {}, {}, set(), []
    version=catalog.local_version()
    for project in projects(catalog.root):
        for row in project.rows:
            key=row.source_id,row.voice
            if only is not None and key!=only:
                continue
            if key in audio or not row.audio or row.review_reason():
                continue
            source=catalog.db.execute('SELECT * FROM lines WHERE id=?',(row.source_id,)).fetchone()
            if not source or not catalog.voice_basis(source,version,allow_branch=catalog.branch_rule_enabled()):
                continue
            binding=catalog.binding(source['speaker_id'])
            if not binding or not binding['transcript_reviewed'] or binding['voice']!=row.voice:
                continue
            body,missing=resolve_text(source['raw_text'],config.get('nickname','漂泊者'),config.get('traveler_gender','female'))
            if (missing or body!=row.text or source['speaker']!=row.speaker or
                    source['fingerprint']!=row.evidence.get('fingerprint')):
                continue
            try:
                line=prepared_line(project,row)
                if not line:
                    continue
                breeze.check_audio(line.path)
                if line.cloud_voice:
                    from wuwa_cast_review import original_problem
                    if original_problem(catalog,source['speaker_id'],line.cloud_voice):
                        continue
                    direction=breeze.line_direction(catalog,row.source_id,line.cloud_voice)
                    spoken=row.audio.get("tts_text", breeze.strip_asides(row.text))
                    if (line.cloud_voice!=breeze.pairing(catalog,source['speaker_id'],row.voice)
                            or row.audio['cache_key']!=breeze.recipe(row.text,line.cloud_voice,direction,spoken=spoken)):
                        continue
                    audio[key]=line
                    used.add(str(project.directory))
                    continue
                if row.voice not in signatures:
                    mode='system' if row.voice.startswith('system:') else 'character'
                    if mode not in runtime:
                        runtime[mode]=VoiceRuntime(dict(config['dubbing'],catalog_root=str(catalog.root),mode=mode))
                    profile,_=select_voice(runtime[mode],row)
                    signatures[row.voice]=local_voice_signature(runtime[mode],profile)
                if signatures[row.voice]!=row.audio.get('local_voice_signature'):
                    continue
                audio[key]=line
                used.add(str(project.directory))
            except (OSError,ValueError,KeyError) as exc:
                held.append(dict(reason=str(exc),project=str(project.directory)))
    return audio,sorted(used),held


def export_cloud(catalog, project, entry, cloud_voice, still_current, stop, lock, *, max_daily_credits=None):
    from wuwa_cast_review import line_problem
    problem = line_problem(catalog,entry.source_id,entry.evidence['speaker_id'],cloud_voice)
    if problem:
        raise ValueError(problem)
    def reviewed_and_current():
        problem = line_problem(catalog,entry.source_id,entry.evidence['speaker_id'],cloud_voice)
        if problem:
            raise ValueError(problem)
        return still_current()
    direction=breeze.line_direction(catalog,entry.source_id,cloud_voice)
    spoken=breeze.spoken_text(entry.text,catalog.root)
    options = {} if max_daily_credits is None else {'max_daily_credits': max_daily_credits}
    cached,hit=breeze.cached_wav(catalog,entry.text,cloud_voice,reviewed_and_current,direction,spoken=spoken,**options)
    folder=project.directory/'audio'
    folder.mkdir(exist_ok=True)
    stage=folder/('.'+uuid4().hex+'.wav')
    try:
        shutil.copyfile(cached,stage)
        label = catalog.db.execute('SELECT name FROM breeze_presets WHERE id=?',(cloud_voice,)).fetchone()
        info=dict(tts_text=spoken,cache_key=breeze.recipe(entry.text,cloud_voice,direction,spoken=spoken),input_key=entry.input_key(project.mode),
            voice='BreezeBlue · '+(label[0] if label else entry.speaker),provider='breezeblue',cloud_voice=cloud_voice,
            cache_hit=hit,generated_at=timestamp())
        _publish_export(project,entry,stage,info,stop,lock)
        entry.status,entry.error='done',''
        project.save()
        return prepared_line(project,entry)
    finally:
        stage.unlink(missing_ok=True)


def export_original_reference(catalog,config,project,entry,stop,lock):
    from wuwa_cast_review import line_problem
    sid=entry.evidence['speaker_id']
    binding=catalog.binding(sid)
    if not binding or not binding['voice'].startswith('character:') or entry.voice!=binding['voice']:
        raise ValueError('角色原声参考已变化')
    problem=line_problem(catalog,entry.source_id,sid,entry.voice)
    if problem:
        raise ValueError(problem)
    runtime=VoiceRuntime(dict(config['dubbing'],mode='character',fallback_to_system=False))
    from wuwa_dubber.models import Utterance
    from wuwa_dubber.speech_cache import SpeechCache
    if runtime.config.tts_backend!='qwen3-tts':
        raise ValueError('需要本地 Qwen3-TTS 原声参考合成')
    profile,_=select_voice(runtime,entry)
    runtime.prepare(stop,lambda message:None,playback=False)
    cache=SpeechCache(runtime.config,runtime.cache_dir,runtime.client)
    from wuwa_pronunciation import tts_text
    utterance=Utterance(tts_text(entry.text,catalog.root),speaker=profile.speaker,source='batch_generation')
    speech=cache.get_or_create(utterance,profile,cancel_event=stop)
    breeze.check_audio(speech.path)
    folder=project.directory/'audio'
    folder.mkdir(exist_ok=True)
    stage=folder/('.'+uuid4().hex+'.wav')
    try:
        shutil.copyfile(speech.path,stage)
        info=dict(cache_key=cache.path_for(utterance,profile).stem,input_key=entry.input_key(project.mode),
                  voice=profile.speaker,provider='local-original-reference',local_voice_signature=local_voice_signature(runtime,profile),
                  cache_hit=speech.cache_hit,generated_at=timestamp())
        _publish_export(project,entry,stage,info,stop,lock)
        entry.status,entry.error='done',''
        project.save()
        return prepared_line(project,entry)
    finally:
        stage.unlink(missing_ok=True)


class GenerationService:
    def __init__(self,root,notify):
        self.root=Path(root)
        self.notify=notify
        self.finished=queue.SimpleQueue()
        self.wake=threading.Event()
        self.stopping=threading.Event()
        self.lock=threading.Lock()
        self.runner=None
        self.current=None
        self.activity = ('ready', '合成队列待命')
        self.thread=threading.Thread(target=self.run,name='wuwa-log-generation',daemon=True)
        self.thread.start()

    def set_current(self, key):
        with self.lock:
            self.current=key

    def current_is(self,key):
        with self.lock:
            return not self.stopping.is_set() and self.current==key

    def ensure_voice(self,catalog,row,binding):
        if catalog.cloud_only():
            return binding or breeze.assign_preset(catalog,row)
        origin=catalog.db.execute('SELECT origin FROM binding_origins WHERE speaker_id=?',(row['speaker_id'],)).fetchone()
        if binding and (origin or binding['voice'].startswith('npc:') or
                        (binding['transcript_reviewed'] and binding['voice']!='system:default')):
            return binding
        # Older imports installed system:default for every unmatched NPC.
        # Keep an existing recorded assignment, including an intentional system voice.
        if binding and not origin and binding['transcript_reviewed']:
            if any(r.evidence.get('speaker_id')==row['speaker_id'] and r.audio and r.voice==binding['voice']
                   for project in projects(self.root) for r in project.rows):
                return binding
        config=json.loads((self.root/'config.json').read_text(encoding='utf-8'))
        profiles=voice_paths(config['dubbing'])[1]/'profiles'
        from wuwa_dubber.models import VoiceProfile
        candidates=[]
        preferred=None
        for path in sorted(profiles.glob('*/profile.json')):
            try:
                profile=VoiceProfile.from_dict(json.loads(path.read_text(encoding='utf-8-sig')))
                if not profile.enabled or not profile.prompt_text or not profile.resolve_reference(profiles).is_file():
                    continue
                candidates.append('character:'+profile.speaker)
                if row['speaker'] in [profile.speaker,*profile.aliases]:
                    preferred=candidates[-1]
            except (ValueError,OSError,KeyError):
                continue
        bucket=int(hashlib.sha256(row['speaker_id'].encode()).hexdigest(),16)
        voice=preferred or (candidates[bucket%len(candidates)] if candidates else 'system:default')
        catalog.set_binding(row['speaker_id'],row['speaker'],voice,True,automatic=True)
        self.notify('已为'+row['speaker']+'绑定声线 · '+voice.split(':',1)[1])
        return catalog.binding(row['speaker_id'])

    def request(self,catalog,line,voice):
        with catalog.db:
            catalog.db.execute("""INSERT INTO log_generation VALUES(?,?,0,'')
                ON CONFLICT(line_id,voice) DO UPDATE SET priority=0,error=''""",(line,voice))
        self.wake.set()

    def expand_rebuilds(self,catalog):
        for request in catalog.db.execute('SELECT * FROM voice_rebuilds').fetchall():
            sid,voice=request
            ids={r.source_id for project in projects(self.root) for r in project.rows
                 if r.evidence.get('speaker_id')==sid and r.audio and project.audio_path(r,verify=False)}
            with catalog.db:
                if not catalog.db.execute('DELETE FROM voice_rebuilds WHERE speaker_id=? AND voice=?',(sid,voice)).rowcount:
                    continue
                catalog.db.executemany("INSERT OR IGNORE INTO log_generation VALUES(?,?,1,'')",[(i,voice) for i in ids])
                catalog.db.executemany("UPDATE log_generation SET error='' WHERE line_id=? AND voice=?",[(i,voice) for i in ids])
            self.notify(f'换声重录已排队 · {len(ids)} 句')

    def generate(self,catalog,config,identity,voice,background):
        voice_paths(config['dubbing'])
        row=catalog.db.execute('SELECT * FROM lines WHERE id=?',(identity,)).fetchone()
        if not row:
            raise ValueError('台词库已移除这句')
        if catalog.cloud_only() and background:
            return None
        binding=catalog.binding(row['speaker_id'])
        if not binding or binding['voice']!=voice:
            return None  # Superseded by another binding.
        if not binding['transcript_reviewed']:
            raise ValueError('声线待核对')
        if not catalog.voice_basis(row,catalog.local_version(),allow_branch=catalog.branch_rule_enabled()):
            raise ValueError('原声未知，待核对')
        if not background:
            previous,_,_=existing_audio(catalog,config,only=(identity,voice))
            if (identity,voice) in previous:
                return previous[identity,voice]
        project,_=catalog.prepare_project(['flow:'+row['flow_id']],config,{identity},live_line=True)
        if catalog.cloud_only():
            entry = project.rows[0]
            def still_current():
                active = catalog.binding(row['speaker_id'])
                return self.current_is((identity,voice)) and active and active['voice']==voice
            if not still_current():
                return None
            if voice.startswith('character:'):
                self.activity = ('busy', '本地原声参考合成中')
                self.notify('原声参考合成 · '+binding['label'])
                return export_original_reference(catalog,config,project,entry,self.stopping,self.lock)
            self.activity = ('busy', 'BreezeBlue 合成中')
            self.notify('BreezeBlue · '+binding['label']+' · '+entry.text[:18])
            return export_cloud(catalog,project,entry,voice.partition(':')[2],still_current,self.stopping,self.lock)
        runner=BatchGenerator(project,dict(config['dubbing'],catalog_root=str(self.root)))
        with self.lock:
            if self.stopping.is_set():
                return None
            self.runner=runner
        try:
            self.activity = ('busy', '本地语音合成中')
            self.notify(('后台重录' if background else '正在生成')+' · '+row['speaker']+' · '+row['raw_text'][:18])
            entry=project.rows[0]
            try:
                runner.run(force=background or entry.error.startswith('音质检查：'))
                clip=prepared_line(project,entry)
                if not clip:
                    raise ValueError(entry.error or '生成未完成')
                try:
                    breeze.check_audio(clip.path)
                except breeze.AudioQualityError as exc:
                    entry.status,entry.error='failed','音质检查：'+str(exc)
                    project.save()
                    raise
                return clip
            except Exception as local_error:
                cloud_voice=breeze.pairing(catalog,row['speaker_id'],voice)
                if (background or not cloud_voice or not self.current_is((identity,voice))
                        or int(catalog.meta('breeze_daily_credits','0'))<=0):
                    raise
                catalog.validate_project(project)
                def still_current():
                    active=catalog.binding(row['speaker_id'])
                    return (self.current_is((identity,voice)) and active and active['voice']==voice
                            and active['transcript_reviewed'] and
                            breeze.pairing(catalog,row['speaker_id'],voice)==cloud_voice)
                if not still_current():
                    raise
                self.activity = ('busy', 'BreezeBlue 补录中')
                self.notify('本地合成未通过，尝试已选的 BreezeBlue 声线 · '+row['speaker'])
                try:
                    return export_cloud(catalog, project, entry, cloud_voice, still_current,self.stopping,self.lock)
                except Exception as cloud_error:
                    raise ValueError(f'本地：{local_error}；云端：{cloud_error}') from cloud_error
        finally:
            with self.lock:
                self.runner=None

    def run(self):
        catalog=Catalog(self.root)
        try:
            while not self.stopping.is_set():
                try:
                    self.expand_rebuilds(catalog)
                    job=catalog.db.execute("SELECT * FROM log_generation WHERE error='' ORDER BY priority,rowid LIMIT 1").fetchone()
                    if job is None:
                        self.wake.wait(.5)
                        self.wake.clear()
                        continue
                    identity,voice,priority,_=job
                    try:
                        config=json.loads((self.root/'config.json').read_text(encoding='utf-8'))
                        clip=self.generate(catalog,config,identity,voice,priority>0)
                        self.activity = ('ready', '合成队列待命')
                        if self.stopping.is_set():
                            break
                        with catalog.db:
                            catalog.db.execute('DELETE FROM log_generation WHERE line_id=? AND voice=?',(identity,voice))
                        if clip:
                            self.finished.put((identity,voice,clip,None))
                            self.notify('音频已保存 · '+clip.speaker+' · '+clip.text[:18])
                    except Exception as exc:
                        if self.stopping.is_set():
                            break
                        with catalog.db:
                            catalog.db.execute('UPDATE log_generation SET error=? WHERE line_id=? AND voice=?',(str(exc),identity,voice))
                        self.finished.put((identity,voice,None,str(exc)))
                        self.activity = ('warning', '上次合成暂缓：' + str(exc))
                        self.notify('生成暂缓 · '+str(exc))
                except Exception as exc:
                    self.activity = ('warning', '上次处理队列暂缓：' + str(exc))
                    self.notify('生成队列暂缓 · '+str(exc))
                    self.stopping.wait(1)
        finally:
            catalog.close()

    def close(self):
        self.stopping.set()
        self.wake.set()
        with self.lock:
            if self.runner:
                self.runner.cancel()
        self.thread.join()

