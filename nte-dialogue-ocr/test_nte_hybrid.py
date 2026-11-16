import base64
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from nte_catalog import NteCatalog, NteLine, build_catalog
from nte_log_stream import KEYS, LogEvent, NteLogTail, decode_line, parse_event
from nte_audio import NteAudio, HybridPlayback
from nte_live import NteLiveSession, OcrSchedule
from test_dialogue_generation import Client, Runtime, write_wav
from test_prepared_playback import Player, completed_line
from dialogue_generation import GenerationProject, file_digest
from prepared_playback import PreparedLine


def encrypted(text):
    data = (text + '|SPLIT|').encode('utf-8')
    count = 16 - len(data) % 16
    data += bytes([count]) * count
    encryptor = Cipher(algorithms.AES(KEYS[0]), modes.ECB()).encryptor()
    return base64.b64encode(encryptor.update(data) + encryptor.finalize()) + b'\n'


def entry(key, text, namespace='ST_Dialogue_CityMark_Chat'):
    return dict(namespace=namespace, key=key, text=text)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'catalog.sqlite'
        self.entries = [entry('NPC_09204_Name', '丽妮丝', 'ST_ActorName'),
                        entry('NPC_023_Name', '明音凛', 'ST_ActorName'),
                        entry('SD_S0008_NPC_09204_002', '很荣幸为您服务。'),
                        entry('SD_S0008_NPC_09204_004', '「雷加利亚」是一款有着上百年历史的汽车品牌，在高端跑车和机车领域均有建树。'),
                        entry('WLSD_WJ1062_Player_001', '下午好，明音。')]
        build_catalog(self.entries, self.path, {})
        self.catalog = NteCatalog(self.path, player_name='测试鉴定师')

    def tearDown(self):
        self.catalog.close()
        self.temp.cleanup()

    def rebuild(self, extra):
        self.catalog.close()
        build_catalog(self.entries + extra, self.path, {})
        self.catalog = NteCatalog(self.path, player_name='测试鉴定师')

    def test_two_observed_silent_pages_map_to_original_ids(self):
        for item in self.entries[2:4]:
            line, _ = self.catalog.match(item['text'], '丽妮丝')
            self.assertEqual(line.text_key, item['key'])
            self.assertEqual(line.speaker, '丽妮丝')
            self.assertEqual(line.identity, item['namespace'] + '/' + item['key'])

    def test_player_name_and_log_event_prefix(self):
        line = self.catalog.by_key('Play_Vo_WLSD_WJ1062_Player_001')
        self.assertEqual(line.speaker, '测试鉴定师')
        self.assertEqual(self.catalog.match(line.text, '鉴定师')[0], line)

    def test_ambiguous_full_text_and_prefix_wait(self):
        self.rebuild([entry('SD_NPC_09204_999', '很荣幸为您服务。请稍等。')])
        self.assertIsNone(self.catalog.match('很荣幸为您服务。', '丽妮丝')[0])
        self.assertIsNone(self.catalog.match('「雷加利亚」', '丽妮丝', prefix=True)[0])
        self.assertIsNotNone(self.catalog.match('「雷加利亚」是一款有着', '丽妮丝', prefix=True)[0])

    def test_same_text_different_role_needs_role(self):
        self.rebuild([entry('OTHER_NPC_023_001', '很荣幸为您服务。')])
        self.assertIsNone(self.catalog.match('很荣幸为您服务。')[0])
        self.assertEqual(self.catalog.match('很荣幸为您服务。', '明音凛')[0].speaker_key, 'NPC023')

    def test_duplicate_keys_across_namespaces_do_not_guess(self):
        self.rebuild([entry('SD_S0008_NPC_09204_002', '另一句', 'ST_MainDialogue')])
        self.assertIsNone(self.catalog.by_key('SD_S0008_NPC_09204_002'))

    def test_markup_and_player_placeholder_match_visible_text(self):
        self.rebuild([entry('SD_NPC_09204_900', '<purple>你好，{PlayerName}。</>'),
                      entry('SD_NPC_09204_901', '{Gender}|gender(哥哥,姐姐)真厉害。')])
        self.assertEqual(self.catalog.match('你好，测试鉴定师。', '丽妮丝')[0].text, '你好，测试鉴定师。')
        self.assertEqual(self.catalog.match('姐姐真厉害。', '丽妮丝')[0].text, '姐姐真厉害。')
        self.assertIsNone(self.catalog.by_key('SD_NPC_09204_901'))

    def test_ui_and_unexpanded_values_are_excluded(self):
        self.rebuild([entry('MENU', '菜单按钮', 'ST_MainUI'),
                      entry('COST_NPC_09204_003', '需要{Pukacost}金币')])
        self.assertIsNone(self.catalog.match('菜单按钮')[0])
        self.assertIsNone(self.catalog.by_key('COST_NPC_09204_003'))

    def test_failed_catalog_build_keeps_previous_database(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            build_catalog([], self.path, {})
        self.assertEqual(self.path.read_bytes(), before)


class LogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'HT.log'
        self.now = 1800000000.

    def tearDown(self):
        self.temp.cleanup()

    def line(self, text='[VoiceMsg] ShowCurMsg: type=0, MsgID=TEST_001, hasVoice=0', age=0):
        stamp = datetime.fromtimestamp(self.now - age, timezone.utc).strftime('[%Y.%m.%d-%H.%M.%S.000][+0000]')
        return encrypted(stamp + '[ 92]LogHTGame: Warning: ' + text)

    def test_actual_event_forms_and_end_audio_is_not_page_end(self):
        event = parse_event('[DlgVoice] InnerPlayVoice: event=Play_Vo_WLSD_NPC023_WJ1062_001, CurPlayingID=7')
        self.assertEqual((event.kind, event.key, event.native_voice), ('line', 'WLSD_NPC023_WJ1062_001', True))
        self.assertIsNone(parse_event('[DlgVoice] OnAkCallback EndOfEvent: CurPlayingID=7'))
        self.assertEqual(parse_event('UUI_Dialogue::GetDlgSeqObj LevelSequenceActor = nullptr').kind, 'page')
        self.assertEqual(parse_event('WillInteract RoleID 123 InteractIndex 0').kind, 'wake')

    def test_bom_pkcs7_and_game_tab_padding(self):
        self.assertEqual(decode_line(b'\xef\xbb\xbf' + encrypted('Log file open, sample')), ['Log file open, sample'])
        value = b'Log file open|SPLIT|'
        value += b'\t' * (16 - len(value) % 16)
        enc = Cipher(algorithms.AES(KEYS[0]), modes.ECB()).encryptor()
        self.assertEqual(decode_line(base64.b64encode(enc.update(value) + enc.finalize())), ['Log file open'])

    def test_starts_at_end_and_waits_for_complete_append(self):
        self.path.write_bytes(self.line(age=20))
        tail = NteLogTail(self.path, clock=lambda: self.now)
        self.assertEqual(tail.poll(), [])
        data = self.line()
        with self.path.open('ab') as stream:
            stream.write(data[:30])
        self.assertEqual(tail.poll(), [])
        with self.path.open('ab') as stream:
            stream.write(data[30:])
        self.assertEqual([e.key for e in tail.poll()], ['TEST_001'])
        self.assertEqual(tail.poll(), [])

    def test_missing_file_created_later_and_old_events_filtered(self):
        tail = NteLogTail(self.path, clock=lambda: self.now)
        self.assertEqual(tail.poll(), [])
        self.path.write_bytes(self.line(age=100) + self.line())
        self.assertEqual(len(tail.poll()), 1)

    def test_rotation_and_same_file_truncation_regrowth(self):
        self.path.write_bytes(self.line(age=10))
        tail = NteLogTail(self.path, clock=lambda: self.now)
        self.path.rename(self.path.with_suffix('.backup'))
        self.path.write_bytes(self.line())
        self.assertEqual(len(tail.poll()), 1)
        self.now += 1
        self.path.write_bytes(self.line() + self.line())
        self.assertEqual(len(tail.poll()), 2)
        self.assertEqual(tail.resets, 2)

    def test_partial_historical_line_is_discarded_and_bad_lines_recover(self):
        data = self.line(age=100)
        self.path.write_bytes(data[:50])
        tail = NteLogTail(self.path, clock=lambda: self.now)
        with self.path.open('ab') as stream:
            stream.write(data[50:] + b'broken!\n' + self.line())
        self.assertEqual(len(tail.poll()), 1)
        self.assertEqual(tail.errors, 1)


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = Client()
        self.runtime = Runtime(self.root, self.client)
        self.runtime.options = {}
        self.audio = NteAudio(self.root, self.runtime, {})
        self.line = NteLine('ST_Dialogue', 'A', '下午好，准备好了吗？', 'NPC1', '伊洛伊', 'one')

    def tearDown(self):
        self.temp.cleanup()

    def test_first_generation_then_valid_cache_and_stable_link(self):
        first, source, _ = self.audio.resolve(self.line, threading.Event())
        second, cached_source, _ = self.audio.resolve(self.line, threading.Event())
        self.assertEqual((source, cached_source), ('generated', 'cache'))
        self.assertEqual(first.path, second.path)
        link = json.loads(next((self.root / 'nte-data/audio-links').glob('*.json')).read_text('utf-8'))
        self.assertEqual(link['catalog_id'], 'ST_Dialogue/A')

    def test_voice_and_text_changes_invalidate_cached_audio(self):
        first, _, _ = self.audio.resolve(self.line, threading.Event())
        self.runtime.profiles['伊洛伊'] = replace(self.runtime.profiles['伊洛伊'], system_pitch=3)
        changed_voice, source, _ = self.audio.resolve(self.line, threading.Event())
        changed_text, _, _ = self.audio.resolve(replace(self.line, text='我们出发吧。'), threading.Event())
        self.assertEqual(source, 'generated')
        self.assertEqual(len({first.path, changed_voice.path, changed_text.path}), 3)

    def test_preferred_existing_audio_and_project_edits(self):
        project = GenerationProject.create(self.root / 'generation-projects', mode='character')
        row, path = completed_line(project, self.line.text)
        project.save()
        self.audio.preferred = project.directory
        prepared, source, _ = self.audio.resolve(self.line, threading.Event())
        self.assertEqual((prepared.path, source), (path, 'project'))
        row.edit(text='已经改成另一句。')
        project.save()
        self.assertEqual(self.audio.resolve(self.line, threading.Event())[1], 'generated')


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'line.wav'
        write_wav(self.path)
        self.player = Player()
        self.events = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.calls = []
        self.audio = SimpleNamespace(runtime=SimpleNamespace(player=self.player, client=Client()), resolve=self.resolve)
        self.controller = HybridPlayback(self.audio, self.events.append)
        self.first = NteLine('ST_Dialogue', 'A', '第一句测试台词。', 'NPC1', '伊洛伊', 'one')
        self.second = replace(self.first, text_key='B', text='第二句测试台词。', fingerprint='two')

    def resolve(self, line, token):
        self.calls.append(line.identity)
        self.entered.set()
        self.release.wait(3)
        prepared = PreparedLine(line.identity, line.text, line.speaker, 'voice', self.path, file_digest(self.path), False)
        return prepared, 'generated', .01

    def wait_for(self, condition):
        until = time.monotonic() + 3
        while not condition() and time.monotonic() < until:
            time.sleep(.005)
        self.assertTrue(condition())

    def tearDown(self):
        self.release.set()
        self.player.release.set()
        self.controller.close()
        self.temp.cleanup()

    def test_late_generation_cannot_play_old_page(self):
        self.release.clear()
        self.controller.select(self.first, 'subtitle', time.monotonic())
        self.assertTrue(self.entered.wait(1))
        self.controller.select(self.second, 'subtitle', time.monotonic())
        self.release.set()
        self.wait_for(lambda: any(e['state'] == 'playing' for e in self.events))
        self.assertEqual([e['catalog_id'] for e in self.events if e['state'] == 'playing'], [self.second.identity])
        self.assertEqual(self.player.max_active, 1)

    def test_visual_change_stops_playback_and_stale_observation_is_rejected(self):
        self.controller.select(self.first, 'subtitle', 1)
        self.wait_for(lambda: len(self.player.played) == 1)
        self.controller.invalidate(3)
        self.wait_for(lambda: bool(self.player.cancelled))
        self.assertFalse(self.controller.select(self.first, 'subtitle', 2))

    def test_log_priority_and_old_visual_reference_does_not_cancel_new_log_line(self):
        self.controller.select(self.first, 'log', 1)
        self.assertFalse(self.controller.select(self.second, 'subtitle', 2))
        self.controller.select(self.second, 'log', 3)
        self.controller.invalidate(4, expected_id=self.first.identity)
        self.assertEqual(self.controller.current['line'], self.second)

    def test_pause_skip_resume_replay_and_new_page(self):
        self.controller.pause(True)
        self.controller.select(self.first, 'subtitle', 1)
        self.assertEqual(self.calls, [])
        self.controller.pause(False)
        self.wait_for(lambda: len(self.player.played) == 1)
        self.controller.skip()
        self.controller.select(self.first, 'subtitle', 2)
        self.assertEqual(len(self.calls), 1)
        self.controller.replay()
        self.wait_for(lambda: len(self.player.played) == 2)
        self.controller.select(self.second, 'subtitle', 3)
        self.wait_for(lambda: len(self.player.played) == 3)

    def test_native_voice_cancels_pending_synthesis(self):
        self.release.clear()
        self.controller.select(self.first, 'subtitle', 1)
        self.entered.wait(1)
        self.controller.select(self.first, 'log', 2, native=True)
        self.release.set()
        self.wait_for(lambda: self.controller.synthesizing is None)
        self.assertFalse(self.player.played)
        self.assertFalse(self.controller.replay())

    def test_same_line_does_not_repeat_and_metrics_have_source(self):
        self.controller.select(self.first, 'subtitle', time.monotonic())
        self.wait_for(lambda: len(self.player.played) == 1)
        self.controller.select(self.first, 'subtitle', time.monotonic())
        self.assertEqual(len(self.calls), 1)
        playing = next(e for e in self.events if e['state'] == 'playing')
        self.assertEqual(playing['audio_origin'], 'generated')
        self.assertGreaterEqual(playing['latency_ms'], 0)


class ScheduleTests(unittest.TestCase):
    def test_idle_frequency_and_immediate_log_wakeup(self):
        schedule = OcrSchedule()
        self.assertTrue(schedule.ready(10))
        self.assertFalse(schedule.ready(10.2))
        schedule.wake(10.3)
        self.assertTrue(schedule.ready(10.3))
        self.assertTrue(schedule.ready(10.4))
        self.assertTrue(schedule.ready(15))
        self.assertFalse(schedule.ready(15.1))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.session = NteLiveSession(self.temp.name, {}, lambda event: None)
        self.session.controller = Mock(minimum_capture=0, current=None)
        self.session.controller.select.return_value = True
        self.line = NteLine('ST_Dialogue', 'A', '一段测试用的完整台词。', 'NPC1', '伊洛伊', 'one')
        self.session.catalog = Mock()
        self.session.catalog.by_key.return_value = None
        self.session.catalog.match.return_value = (self.line, '')
        self.session.capture = Mock()
        self.session.capture.ready_for_ocr.return_value = True

    def tearDown(self):
        self.temp.cleanup()

    def observation(self, **updates):
        return dict(dict(body=self.line.text, speaker='伊洛伊', body_confidence=.99, speaker_confidence=.99,
                         captured_monotonic=2, render_stable=True), **updates)

    def test_unmapped_native_voice_is_still_suppressed_after_subtitle_lookup(self):
        self.session.log_event(LogEvent('line', 'UNKNOWN_EVENT', True), 1)
        self.session.observe(self.observation())
        self.assertTrue(self.session.controller.select.call_args.kwargs['native'])
        self.session._watch_event(dict(frame_changed=3))
        self.session.observe(self.observation(captured_monotonic=4))
        self.assertFalse(self.session.controller.select.call_args.kwargs['native'])

    def test_old_page_transition_cannot_clear_unseen_new_native_line(self):
        self.session.log_event(LogEvent('line', 'UNKNOWN_EVENT', True), 1)
        self.session._watch_event(dict(frame_changed=1.1))
        self.assertIsNotNone(self.session.native_voice)
        self.session.observe(self.observation())
        self.assertTrue(self.session.controller.select.call_args.kwargs['native'])

    def test_unavailable_capture_tracks_logs_without_starting_audio(self):
        self.session.capture.ready_for_ocr.return_value = False
        self.session.log_event(LogEvent('line', 'UNKNOWN_EVENT', True), 1)
        self.session.controller.select.assert_not_called()
        self.assertIsNotNone(self.session.native_voice)
        self.session.log_event(LogEvent('page'), 2)
        self.assertIsNone(self.session.native_voice)

    def test_invalid_scores_clipping_and_unconfirmed_prefix_are_held(self):
        for updates in ({'body_confidence': float('nan')}, {'body_confidence': None},
                        {'captured_monotonic': float('nan')}, {'source_clipped': True},
                        {'prefix_probe': True, 'prefix_stable': False}):
            self.session.observe(self.observation(**updates))
        self.session.controller.select.assert_not_called()

    def test_idle_ui_text_does_not_keep_ocr_active(self):
        self.session.catalog.match.return_value = (None, 'unmatched')
        self.session.observe(self.observation())
        self.assertEqual(self.session.schedule.active_until, 0)


class LiveUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets as W
        cls.app = W.QApplication.instance() or W.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_normal_start_button_constructs_hybrid_session_and_controls(self):
        from PySide6 import QtCore
        from nte_live_ui import NteLiveDialog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'config.json').write_text(json.dumps(dict(calibrated=True, body_roi=[0,0,1,1], dubbing={})), 'utf-8')
            with patch('wuwa_hotkeys.PlaybackHotkeys') as hotkeys, patch('capture_windows.list_game_windows', return_value=[]):
                hotkeys.return_value.errors = []
                dialog = NteLiveDialog(root)
            try:
                window = SimpleNamespace(is_launcher=False)
                dialog.windows.addItem('异环', window)
                with patch('nte_live_ui.LiveWorker.start') as start:
                    dialog.start.click()
                self.assertIsInstance(dialog.worker.session, NteLiveSession)
                self.assertIs(dialog.worker.window, window)
                start.assert_called_once()
                controller = Mock()
                dialog.worker.session.controller = controller
                dialog.receive(dict(ready=True))
                dialog.pause_button.click()
                controller.pause.assert_called_once_with(True)
                dialog.replay_button.click()
                controller.replay.assert_called_once()
                dialog.skip_button.click()
                controller.skip.assert_called_once()
                dialog.updater = Mock()
                dialog.updater.errorString.return_value = 'test launch failure'
                dialog.catalog_update_error(QtCore.QProcess.FailedToStart)
                self.assertTrue(dialog.start.isEnabled())
            finally:
                dialog.updater = None
                dialog.close()
                dialog.deleteLater()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
