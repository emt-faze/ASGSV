"""One capture owner: automatic attachment, screen evidence and prepared playback."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import threading
import time

import cv2
import numpy as np
from PySide6 import QtCore

from dialogue_generation import GenerationProject, atomic_text
from prepared_playback import PreparedLibrary, PreparedPlayback, text_key


def automatic_regions(size, config):
    width, height = size
    reference = config.get('reference_size', [1920, 1080])
    same_aspect = abs(width / height / (reference[0] / reference[1]) - 1) < .015
    if config.get('calibrated') and same_aspect and config.get('body_roi') and config.get('speaker_roi'):
        return list(config['body_roi']), list(config['speaker_roi']), 'saved'
    # Centered 16:9 content in a wider viewport. Confirm from actual glyphs later.
    ratio = min(1., (16 / 9) / (width / height))
    def box(left, top, right, bottom):
        return [.5 + (left - .5) * ratio, top, .5 + (right - .5) * ratio, bottom]
    return box(.14, .80, .86, .94), box(.36, .72, .64, .80), 'preset'


def detect_regions(frame, detections, controls=False):
    """Derive normalized regions from centered body text and yellow name ink."""
    from wuwa_ocr import speaker_ink
    height, width = frame.shape[:2]
    names, bodies = [], []
    top_control = False
    for points, raw, confidence in detections:
        points = np.asarray(points, dtype=float)
        if points.shape != (4, 2) or not np.isfinite(points).all() or float(confidence) < .90:
            continue
        left, top = points.min(axis=0)
        right, bottom = points.max(axis=0)
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            continue
        center = (left + right) / (2 * width)
        label = text_key(raw)
        if top < .14 * height and left > .65 * width and label in {'自动播放', '自动', '跳过', '回顾'}:
            top_control = True
        if not .32 <= center <= .68:
            continue
        patch = frame[int(top):int(bottom), int(left):int(right)]
        if not patch.size:
            continue
        yellow = float(speaker_ink(patch).mean())
        if .69 * height <= top < .81 * height and 1 <= len(label) <= 20 and yellow > .04:
            names.append((left, top, right, bottom))
        if .79 * height <= top and bottom < .955 * height and len(label) >= 2 and yellow < .04:
            # White subtitle ink, not arbitrary colored HUD labels.
            low = patch.min(axis=2)
            white = (low > 175) & (patch.max(axis=2).astype(int) - low < 45)
            if float(white.mean()) > .035:
                bodies.append((left, top, right, bottom))
    if not (controls or top_control) or len(names) != 1 or not bodies:
        return None
    name = names[0]
    body_top = min(b[1] for b in bodies) / height
    if name[3] / height >= body_top:
        return None
    # Keep room for future multi-line captions and longer speaker names.
    body = [.12, max(.78, body_top - .015), .88, .945]
    speaker = [max(.30, name[0]/width-.055), max(.68, name[1]/height-.012),
               min(.70, name[2]/width+.055), min(body_top-.007, name[3]/height+.012)]
    return body, speaker


class TaskEvidence:
    """Exact visible evidence; pending/history alone never identifies a current quest."""
    def __init__(self, db):
        self.db = db
        self.names = defaultdict(list)
        for row in db.execute("SELECT id,name FROM scopes WHERE kind='quest'"):
            if len(text_key(row[1])) >= 2 and not str(row[1]).isdigit():
                self.names[text_key(row[1])].append(dict(id=row[0], name=row[1]))
        self.previous = None
        self.last_seen = 0.
        self.candidate = None
        self.confirmations = 0
        self.sequence = -1

    def screen(self, detections, size, sequence, now):
        width, height = size
        matches, hints, headings, visible_headings = {}, [], {}, {}
        labels = []
        for points, raw, confidence in detections:
            points = np.asarray(points,dtype=float)
            if points.shape==(4,2) and np.isfinite(points).all() and float(confidence)>=.92:
                x,y=points.mean(axis=0)/[width,height]
                labels.append((text_key(raw),x,y))
        task_menu = any(key=='任务' and x<.20 and y<.12 for key,x,y in labels)
        for points, raw, confidence in detections:
            if float(confidence) < .92:
                continue
            points = np.asarray(points, dtype=float)
            if points.shape != (4, 2) or not np.isfinite(points).all():
                continue
            x, y = points.mean(axis=0) / [width, height]
            if not (0 <= x <= 1 and .10 <= y < .90):
                continue
            label = str(raw).strip()
            key = text_key(label)
            if label.startswith('关于'):
                hints.append(label)
                continue  # An interaction option is not an active quest title.
            # HUD tracker is on the left; right-hand task details are candidates
            # only when no second catalog title is visible.
            if x < .45 or (.48 < x < .92 and y < .45):
                for item in self.names.get(key, []):
                    matches[item['id']] = item
            if task_menu and .45<x<.90 and .10<y<.19:
                if 2<=len(key)<=45:
                    visible_headings[key]=label
                for item in self.names.get(key, []):
                    headings[item['id']]=item
        if task_menu:
            matches=headings  # Visible list entries are not the selected task.
            if not matches and len(visible_headings)==1:
                key,label=next(iter(visible_headings.items()))
                matches['screen:'+key]=dict(id='',name=label,unindexed=True)
        basis='任务列表中选中的任务；不等于已追踪' if task_menu else '画面任务标题连续确认'
        if len(matches) == 1:
            candidate = next(iter(matches.values()))
            if sequence != self.sequence:
                identity=(candidate['id'],candidate['name'])
                self.confirmations = self.confirmations + 1 if self.candidate == identity else 1
                self.candidate, self.sequence = identity, sequence
            if self.confirmations >= 2:
                self.previous = dict(candidate, basis=basis+('；本地台词库暂无对应条目' if candidate.get('unindexed') else ''),
                                     state='confirmed', context='selected' if task_menu else 'tracked')
                self.last_seen = now
                return dict(self.previous, visible=True, hints=hints)
            return dict(state='candidate', name=candidate['name'], basis='等待下一帧确认', hints=hints)
        self.candidate, self.confirmations = None, 0
        if len(matches) > 1:
            return dict(state='ambiguous', name='画面存在多个任务', basis='、'.join(x['name'] for x in matches.values()), hints=hints)
        if self.previous and now - self.last_seen <= 15:
            return dict(self.previous, state='last_seen', visible=False, basis='上次识别；当前画面未显示标题', hints=hints)
        return dict(state='unknown', name='尚未确认当前任务', basis='等待任务标题或可唯一归属的对白', hints=hints)

    def dialogue(self, body, speaker, now):
        if len(text_key(body)) < 6 or not speaker:
            return None
        # Ignore punctuation typography only; never fuzzy-match or drop words.
        identities=[r[0] for r in self.db.execute('SELECT id,raw_text FROM lines WHERE speaker=?',(speaker,))
                    if text_key(r[1])==text_key(body)]
        if not identities:
            return None
        slots=','.join('?' for _ in identities)
        rows = self.db.execute(f"""SELECT DISTINCT s.id,s.name FROM lines l
            JOIN scope_lines sl ON sl.line_id=l.id JOIN scopes s ON s.id=sl.scope_id
            WHERE l.id IN ({slots}) AND s.kind='quest'""", identities).fetchall()
        if len(rows) == 1:
            self.previous = dict(id=rows[0][0], name=rows[0][1], basis='当前对白与角色精确匹配到任务', state='confirmed')
            self.last_seen = now
            return dict(self.previous, visible=True)
        return None


class PreparedBank(PreparedLibrary):
    def __init__(self, libraries):
        self.game_id, self.aliases = 'wuwa', {}
        self.lines, self.guards, self.excluded = [], [], 0
        self.by_text, self.by_prefix, self.known_prefixes = {}, {}, {}
        self.known_texts = set()
        seen = set()
        for library in libraries:
            if library.game_id != 'wuwa':
                raise ValueError('不能混入另一游戏的音频')
            self.aliases.update(library.aliases)
            self.guards.extend(library.guards)
            self.known_texts.update(library.known_texts)
            self.excluded += library.excluded
            for key, values in library.known_prefixes.items():
                self.known_prefixes.setdefault(key, set()).update(values)
            for line in library.lines:
                # Latest project is loaded first; preserve one selected export
                # for the same source row, and retain genuinely distinct roles.
                if line.row_id in seen:
                    continue
                seen.add(line.row_id)
                self.lines.append(line)
                self.by_text.setdefault(text_key(line.text), []).append(line)
                from prepared_playback import prefix_text
                canonical = prefix_text(line.text)
                if len(canonical) >= 5:
                    self.by_prefix.setdefault(canonical[:5], []).append(line)


def load_prepared(root, config, catalog):
    from generator_playback import playback_settings
    from wuwa_voices import validate_audio_voices
    settings, aliases = playback_settings(config['dubbing'])
    libraries, projects, held = [], [], []
    task_lines, quality_items = defaultdict(set), {}
    paths = sorted((root/'generation-projects').glob('*/project.json'), key=lambda p:p.stat().st_mtime_ns, reverse=True)
    preferred = root/'generation-projects/last-project.txt'
    if preferred.exists():
        target = Path(preferred.read_text(encoding='utf-8').strip())/'project.json'
        if target in paths:
            paths.remove(target)
            paths.insert(0, target)
    for path in paths:
        try:
            project = GenerationProject.load(path)
            if project.game_id != 'wuwa':
                continue
            catalog.validate_project(project)
            # Honor the user's current binding, including the selected B voice.
            for row in project.rows:
                binding = catalog.binding(row.evidence.get('speaker_id', ''))
                if binding and row.voice != binding['voice']:
                    row.status = 'pending'  # In-memory exclusion only.
            validate_audio_voices(project, config['dubbing'])
            library = PreparedLibrary(project, aliases)
            if library.lines:
                libraries.append(library)
                projects.append(str(project.directory))
                selection=json.loads((project.directory/'selection.json').read_text(encoding='utf-8'))
                for scope in selection.get('scopes',[]):
                    task_lines[scope].update(line.row_id for line in library.lines)
                from wuwa_workflow import project_quality
                checks=project_quality(project)
                for line in library.lines:
                    quality_items.setdefault(line.row_id,checks.get(line.row_id,'unchecked'))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            held.append(dict(project=str(path.parent), reason=str(exc)))
    bank=PreparedBank(libraries)
    bank.task_counts={k:len(v) for k,v in task_lines.items()}
    bank.review_count=sum(v!='pass' for v in quality_items.values())
    return bank, settings, projects, held


class LiveWorker(QtCore.QThread):
    message = QtCore.Signal(object)

    def __init__(self, root, parent=None, *, play_audio=True):
        super().__init__(parent)
        self.root = Path(root)
        self.stop_event = threading.Event()
        self.capture = self.controller = None
        self.play_audio = play_audio
        self.run_id = str(time.time_ns())
        self.frames = 0
        self.last_scan = -float('inf')
        self.last_dialogue = False
        self.verified_layout = False
        self.geometry = None
        self.watch = None

    def emit(self, **event):
        self.message.emit(dict(event, run_id=self.run_id, at=time.monotonic()))

    def cancel(self):
        self.stop_event.set()
        if self.controller:
            self.controller.stop()

    def save_setup(self, updates):
        # Patch only capture preferences; never overwrite voice configuration.
        path = self.root/'config.json'
        current = json.loads(path.read_text(encoding='utf-8'))
        current.update(updates)
        atomic_text(path, json.dumps(current, ensure_ascii=False, indent=2))

    def on_capture(self, capture):
        from generator_ocr import PlaybackFrameWatch
        self.capture = capture
        self.watch = PlaybackFrameWatch(capture, self.config, self.capture_event)
        self.watch.start()

    def capture_event(self, event):
        if 'error' in event:
            self.emit(error=event['error'],status=event['error'])
            self.cancel()
            return
        controller = self.controller
        if controller:
            if event.get('capture_state') in {'unavailable', 'blank'}:
                controller.invalidate(unavailable=True)
            if 'frame_changed' in event:
                controller.invalidate(captured_at=event['frame_changed'])

    def observed(self, observation):
        observation = dict(observation, game_id='wuwa')
        self.emit(observation=observation)
        if float(observation.get('body_confidence') or 0) >= .9 and float(observation.get('speaker_confidence') or 0) >= .9:
            task = self.tasks.dialogue(observation.get('body',''), observation.get('speaker',''), time.monotonic())
            if task:
                self.emit(task=task)
        if self.controller:
            self.controller.observe(observation)

    def full_frame(self, packet, engine):
        import win32gui
        from wuwa_scene import dialogue_controls
        now = time.monotonic()
        self.frames += 1
        size = tuple(packet.client_size)
        if self.geometry != size:
            self.geometry = size
            body, speaker, source = automatic_regions(size, self.config)
            self.config.update(body_roi=body, speaker_roi=speaker, reference_size=list(size))
            engine.body_roi, engine.speaker_roi = body, speaker
            self.verified_layout = False
            self.last_scan = -float('inf')
            self.emit(layout=dict(body=body, speaker=speaker, state='已适配窗口，等待对话确认', size=size))
            self.save_setup(dict(body_roi=body, speaker_roi=speaker, reference_size=list(size),
                                 auto_calibration_state='awaiting_dialogue'))
        controls = dialogue_controls(packet.image, self.root)
        if (not controls or not self.verified_layout) and now - self.last_scan >= (2.0 if controls else 1.2):
            self.last_scan = now
            scale = min(1., 1600 / max(size))
            frame = cv2.resize(packet.image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            detections, _ = engine.engine(frame, use_cls=False)
            task = self.tasks.screen(detections, frame.shape[1::-1], packet.sequence, now)
            self.emit(task=task, screen_text=[str(r[1]) for r in detections if float(r[2]) >= .9 and
                      np.asarray(r[0]).mean(axis=0)[1] < .90*frame.shape[0]])
            detected = detect_regions(frame, detections, controls)
            self.last_dialogue = bool(detected or controls)
            if detected and not self.verified_layout:
                body, speaker = detected
                self.config.update(body_roi=body, speaker_roi=speaker, calibrated=True)
                engine.body_roi, engine.speaker_roi = body, speaker
                self.verified_layout = True
                self.save_setup(dict(body_roi=body, speaker_roi=speaker, reference_size=list(size),
                                     calibrated=True, auto_calibration_state='verified_on_dialogue'))
                self.emit(layout=dict(body=body, speaker=speaker, size=size, state='字幕与角色名已自动定位'))
        in_dialogue = controls or (self.last_dialogue and now-self.last_scan < 2.5)
        self.emit(scene='dialogue' if in_dialogue else 'world_or_menu',
                  foreground=win32gui.GetForegroundWindow() == self.window.hwnd)
        return in_dialogue

    def run(self):
        from app import run_loop
        from capture_windows import list_game_windows
        from wuwa_catalog import Catalog
        from wuwa_resources import inventory
        from generator_playback import playback_settings
        from playback_metrics import PlaybackMetrics
        from dialogue_dubbing import voice_paths
        self.config = json.loads((self.root/'config.json').read_text(encoding='utf-8'))
        self.config.update(game_id='wuwa', capture_backend='wgc', wgc_background=True,live_fresh_text=True)
        catalog = None
        self.metrics = PlaybackMetrics(self.root/'live-sessions'/self.run_id)
        try:
            self.emit(status='正在检查本地音频与游戏目录…')
            inventory(self.config['install_root'], self.root)
            catalog = Catalog(self.root)
            self.tasks = TaskEvidence(catalog.db)
            library, settings, projects, held = load_prepared(self.root, self.config, catalog)
            self.emit(library=dict(lines=len(library.lines), projects=projects, held=held,
                                  task_counts=library.task_counts,review_count=library.review_count))
            player = None
            if self.play_audio:
                try:
                    import sounddevice as sd
                    from wuwa_dubber.audio.player import AudioPlayer
                    sd.check_output_settings(device=settings.output_device, channels=1, samplerate=24000)
                    player = AudioPlayer(settings.output_device, volume=settings.output_volume, latency='low')
                    self.emit(audio_device=sd.query_devices(settings.output_device, 'output')['name'])
                except Exception as exc:
                    self.emit(audio_error='输出设备不可用，识别继续：'+str(exc))
            self.save_setup(dict(capture_backend='wgc', wgc_background=True))
            while not self.stop_event.is_set():
                windows = [w for w in list_game_windows(game_id='wuwa', install_root=self.config['install_root']) if not w.is_launcher]
                if len(windows) != 1:
                    self.emit(status='等待游戏窗口；打开鸣潮后会自动连接' if not windows else '检测到多个游戏窗口，等待只保留一个', connected=False)
                    self.stop_event.wait(1)
                    continue
                self.window = windows[0]
                self.tasks = TaskEvidence(catalog.db)
                body, speaker, source = automatic_regions((self.window.width,self.window.height), self.config)
                self.config.update(body_roi=body, speaker_roi=speaker, reference_size=[self.window.width,self.window.height], calibrated=True)
                self.geometry = None
                self.emit(connected=True, window=dict(title=self.window.title, size=[self.window.width,self.window.height]),
                          status='正在连接窗口采集与加载本地识别模型…')
                if player and library.lines:
                    self.controller = PreparedPlayback(library, player, self.playback_event,
                        allow_prefix=False,preserve_on_unavailable=True)
                try:
                    run_loop(self.window, self.config, self.stop_event, lambda e:self.emit(**e),
                        records_root=self.root, on_capture=self.on_capture, on_full_frame=self.full_frame,
                        on_observation=self.observed,
                        on_state=lambda s:self.capture_event(dict(capture_state=s)),
                        on_frame_change=lambda t:self.capture_event(dict(frame_changed=t)),
                        on_visual_observation=lambda *a:self.watch.arm(*a) if self.watch else None)
                except Exception as exc:
                    if not self.stop_event.is_set():
                        self.emit(status='连接中断，将自动重连：'+str(exc), connected=False, error=str(exc))
                finally:
                    if self.watch:
                        self.watch.close()
                        self.watch = None
                    if self.controller:
                        if not self.controller.close(5):
                            raise RuntimeError('旧播放器尚未结束，已停止重连')
                        self.controller = None
                    self.capture = None
                self.stop_event.wait(2)
        except Exception as exc:
            self.emit(error=str(exc), status='未能启动：'+str(exc))
        finally:
            if catalog:
                catalog.close()
            self.metrics.save_summary()
            self.emit(status='已停止', connected=False)

    def playback_event(self, event):
        self.metrics.add(event)
        self.emit(playback=event)
