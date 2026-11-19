import json
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
import wave
from types import SimpleNamespace

from dialogue_generation import file_digest
from prepared_playback import PreparedLine
from wuwa_log_audio import LogPlayback
from wuwa_log_live import LogWorker
from wuwa_log_stream import GameLog, Plot, Subtitle


def flow(instance='81',number='56'):
    return [f'[Plot] StartFlow [FlowIncId: {instance}][FlowListName: 测试][FlowId: {number}][StateId: 1]',
            f'[Plot] [PlotView] 请求打开界面 [ownerId: {instance}]',
            '[Plot] [PlotView] ViewChange [name: PlotView][isShow: True]']


def display(n):
    return f'[Plot] [FlowShowTalk][Subtitle] 字幕显示 [id: {n}]'


class LogPlaybackTests(unittest.TestCase):
    def test_bootstrap_only_final_subtitle_and_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'Client.log'
            content=['Log file open',*flow(),display(1),display(2)]
            data='\n'.join(content)+'\n'
            p.write_text(data,encoding='utf-8')
            log=GameLog(p,time.time()-10)
            try:
                self.assertEqual(log.poll().number,'2')
                # UE moves old bytes into a backup then truncates the primary.
                backup=p.with_name('Client-backup-local.log')
                timestamp=time.strftime('[%Y.%m.%d-%H.%M.%S:100]')
                backup.write_text('\n'.join(timestamp+l for l in content[1:])+'\n'+
                                  timestamp+'[GameThread]Log file closed, rotated\n',encoding='utf-8')
                p.write_text('Log file open, rotated\n'+timestamp+'[GameThread] tick\n',encoding='utf-8')
                self.assertEqual(log.poll().number,'2')
                with p.open('a',encoding='utf-8') as f:
                    f.write(timestamp+display(3)+'\n')
                self.assertEqual(log.poll().number,'3')
                with p.open('a',encoding='utf-8') as f:
                    f.write('[Plot] [PlotView] ViewChange [name: PlotView][isShow: False]\n')
                self.assertIsNone(log.poll())
            finally:
                log.close()

    def test_startflow_alone_does_not_own_subtitles(self):
        state=Plot()
        for line in flow()+[display(1),flow('82','57')[0],display(2)]:
            state.feed(line)
        self.assertIsNone(state.current)
        for line in flow('82','57')[1:]+[display(2)]:
            state.feed(line)
        self.assertEqual((state.current.flow,state.current.number),('测试_57','2'))

    def test_duplicate_open_for_visible_plot_preserves_subtitle_context(self):
        state = Plot()
        for line in flow():
            state.feed(line)
        duplicate_open = ('[Plot] [PlotView] 请求打开界面 '
                          '[new: PlotView][current: PlotView][ownerId: 81]')
        for line in (duplicate_open, '[Plot] [PlotView] 重复打开', display(1)):
            state.feed(line)
        self.assertIsNotNone(state.current)
        self.assertEqual((state.current.instance, state.current.state, state.current.number),
                         ('81', '测试_56_1', '1'))

        current = state.current
        state.feed(duplicate_open)
        self.assertIs(state.current, current)
        state.feed(display(2))
        self.assertEqual(state.current.number, '2')

        state.feed('[Plot] [PlotView] ViewChange [name: PlotView][isShow: False]')
        state.feed(duplicate_open)
        state.feed(display(3))
        self.assertIsNone(state.current)

    def test_serial_audio_aborts_previous_before_next_start(self):
        with tempfile.TemporaryDirectory() as folder:
            sounds=[]
            for n in ('1','2'):
                path=Path(folder)/(n+'.wav')
                path.write_bytes(n.encode())
                sounds.append(PreparedLine(n,n,'盈叶','B',path,file_digest(path),True))
            starts=[threading.Event(),threading.Event()]
            trace=[]
            class Player:
                def play(self,path,cancel_event,on_started):
                    trace.append(('start',path.stem))
                    on_started()
                    starts[int(path.stem)-1].set()
                    cancel_event.wait(2)
                    trace.append(('end',path.stem))
            output=LogPlayback(Player(),lambda event:None)
            try:
                output.available(sounds[0])
                self.assertTrue(starts[0].wait(1))
                output.clear()
                output.available(sounds[1])
                self.assertTrue(starts[1].wait(1))
                output.available(sounds[1])
                self.assertEqual(trace,[('start','1'),('end','1'),('start','2')])
            finally:
                output.close()

    def test_history_uses_one_output_without_replacing_the_live_line(self):
        with tempfile.TemporaryDirectory() as folder:
            lines = []
            for index in range(3):
                path = Path(folder) / f'{index}.wav'
                with wave.open(str(path), 'wb') as wav:
                    wav.setparams((1, 2, 8000, 0, 'NONE', ''))
                    wav.writeframes(bytes(16000))
                lines.append(PreparedLine(str(index), '相同台词', str(index), 'B', path,
                                          file_digest(path), True, str(index)))
            events = queue.Queue()

            class Player:
                def play(self, path, cancel_event, on_started):
                    on_started()
                    cancel_event.wait(3)

            output = LogPlayback(Player(), events.put)
            self.addCleanup(output.close)

            def expect(state, line, history):
                event = events.get(timeout=2)
                self.assertEqual((event['state'], event['catalog_id'], event['history']),
                                 (state, line.catalog_id, history))
                return event

            first, current, following = lines
            output.available(current)
            start = expect('playing', current, False)
            self.assertEqual(start['duration'], 1)
            self.assertTrue(output.audition(first))
            self.assertEqual(expect('interrupted', current, False)['started_at'], start['started_at'])
            expect('playing', first, True)
            self.assertIs(output.line, current)
            output.clear()
            output.available(following)
            expect('interrupted', first, True)
            expect('playing', following, False)
            output.pause(True)
            self.assertFalse(output.audition(first))
            output.close()

    def test_generation_finishing_after_page_turn_is_saved_only(self):
        worker=LogWorker(Path('.'))
        worker.jobs=SimpleNamespace(finished=queue.SimpleQueue())
        worker.current_row,worker.current_voice='old','voice'
        worker.jobs.finished.put(('old','voice','wav',None))
        worker.controller=SimpleNamespace(available=lambda clip:self.fail('stale audio started'))
        worker.process_jobs(SimpleNamespace(poll=lambda:'new'),'old',None)
        self.assertEqual(worker.audio[('old','voice')],'wav')

