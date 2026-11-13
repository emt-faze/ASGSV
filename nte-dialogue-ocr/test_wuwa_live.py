import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import threading
import time

import numpy as np
from PySide6 import QtWidgets
from wuwa_live import automatic_regions,detect_regions,TaskEvidence,PreparedBank
from wuwa_live_ui import LivePanel


def detection(text,x,y,score=.99,w=150,h=24):
    return [[[x-w/2,y-h/2],[x+w/2,y-h/2],[x+w/2,y+h/2],[x-w/2,y+h/2]],text,score]


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE scopes(id,name,kind); CREATE TABLE lines(id,speaker,raw_text);
            CREATE TABLE scope_lines(scope_id,line_id);''')
        self.db.executemany('INSERT INTO scopes VALUES(?,?,?)',
            [('quest:1','任务甲','quest'),('quest:2','任务乙','quest'),('quest:3','任务丙','quest')])
        self.tasks=TaskEvidence(self.db)

    def test_task_menu_uses_right_heading_not_other_list_entries(self):
        rows=[detection('任务',70,50),detection('任务甲',270,250),detection('任务乙',270,450),
              detection('任务丙',270,600),detection('任务乙',680,140)]
        first=self.tasks.screen(rows,(1200,900),1,1)
        self.assertEqual(first['state'],'candidate')
        result=self.tasks.screen(rows,(1200,900),2,2)
        self.assertEqual(result['id'],'quest:2')
        self.assertEqual(result['context'],'selected')
        self.assertIn('不等于已追踪',result['basis'])

    def test_duplicate_frame_low_confidence_and_interaction_do_not_confirm(self):
        rows=[detection('任务甲',260,280)]
        for _ in range(3):
            self.assertEqual(self.tasks.screen(rows,(1200,900),1,1)['state'],'candidate')
        self.assertEqual(self.tasks.screen([detection('任务甲',260,280,.7)],(1200,900),2,2)['state'],'unknown')
        value=self.tasks.screen([detection('关于任务甲',800,500)],(1200,900),3,3)
        self.assertEqual(value['state'],'unknown')
        self.assertEqual(value['hints'],['关于任务甲'])

    def test_new_task_title_is_visible_without_inventing_a_catalog_binding(self):
        rows=[detection('任务',70,50),detection('新的游戏任务',680,140)]
        self.tasks.screen(rows,(1200,900),1,1)
        value=self.tasks.screen(rows,(1200,900),2,2)
        self.assertEqual(value['name'],'新的游戏任务')
        self.assertEqual(value['state'],'confirmed')
        self.assertEqual(value['id'],'')
        self.assertTrue(value['unindexed'])
        changed=[detection('任务',70,50),detection('另一个新任务',680,140)]
        self.assertEqual(self.tasks.screen(changed,(1200,900),3,3)['state'],'candidate')

    def test_multiple_hud_titles_remain_ambiguous_and_previous_expires(self):
        rows=[detection('任务甲',260,280)]
        self.tasks.screen(rows,(1200,900),1,1)
        self.tasks.screen(rows,(1200,900),2,2)
        result=self.tasks.screen(rows+[detection('任务乙',260,400)],(1200,900),3,3)
        self.assertEqual(result['state'],'ambiguous')
        self.assertEqual(self.tasks.screen([],(1200,900),4,4)['state'],'last_seen')
        self.assertEqual(self.tasks.screen([],(1200,900),5,19)['state'],'unknown')

    def test_dialogue_requires_exact_role_text_and_unique_task(self):
        self.db.execute('INSERT INTO lines VALUES(?,?,?)',('line','NPC','这是一句完整的测试对白。'))
        self.db.execute('INSERT INTO scope_lines VALUES(?,?)',('quest:1','line'))
        self.assertIsNone(self.tasks.dialogue('这是一句完整的测试对白。','Other',1))
        self.assertIsNone(self.tasks.dialogue('这是一句','NPC',1))
        self.assertEqual(self.tasks.dialogue('这是一句完整的测试对白。','NPC',1)['id'],'quest:1')
        self.db.execute('INSERT INTO scope_lines VALUES(?,?)',('quest:2','line'))
        self.assertIsNone(self.tasks.dialogue('这是一句完整的测试对白。','NPC',1))


class LayoutTests(unittest.TestCase):
    def test_reuse_same_aspect_and_reset_changed_aspect(self):
        config=dict(reference_size=[3840,2160],calibrated=True,body_roi=[.1,.8,.9,.94],speaker_roi=[.4,.7,.6,.8])
        body,speaker,kind=automatic_regions((1920,1080),config)
        self.assertEqual(kind,'saved')
        self.assertEqual(body,config['body_roi'])
        self.assertEqual(automatic_regions((3440,1440),config)[2],'preset')

    def test_dialogue_requires_controls_yellow_name_and_centered_white_body(self):
        frame=np.zeros((1000,1600,3),np.uint8)
        frame[738:762,725:875]=[120,230,238]
        frame[838:862,500:1100]=255
        rows=[detection('角色',800,750),detection('这是一句对白',800,850,w=600)]
        self.assertIsNone(detect_regions(frame,rows,False))
        regions=detect_regions(frame,rows,True)
        self.assertIsNotNone(regions)
        self.assertLess(regions[1][3],regions[0][1])
        frame[738:762,725:875]=255
        self.assertIsNone(detect_regions(frame,rows,True))

    def test_empty_bank_never_matches_and_other_game_rejected(self):
        bank=PreparedBank([])
        self.assertIsNone(bank.match('hello','NPC')[0])
        with self.assertRaises(ValueError):
            PreparedBank([SimpleNamespace(game_id='nte')])

    def test_full_frame_monitor_runs_before_geometry_and_never_reads_menu_as_dialogue(self):
        from app import _run_loop
        from capture_session import FramePacket
        with tempfile.TemporaryDirectory() as folder:
            engine=SimpleNamespace(session_id='test-live-frame',pending_ids=[],pause=Mock(),
                min_confidence=.9,ocr_profile='test',candidate_model='test',review_model='test',process_frame=Mock())
            packet=FramePacket(1,time.monotonic(),np.ones((200,200,3),dtype=np.uint8)*100,(200,200),(0,0),(0,0,200,200),1)
            capture=Mock()
            capture.ready_for_ocr.return_value=True
            capture.read.side_effect=lambda sequence:packet if sequence==0 else None
            capture.metrics.return_value={}
            config=dict(game_id='wuwa',calibrated=True,body_roi=[.1,.8,.9,.95],reference_size=[1920,1080],wgc_background=True)
            observed=Mock()
            frame_hook=Mock(return_value=False)
            with patch('app.CaptureSession',return_value=SimpleNamespace(start=lambda:capture)) as start,\
                 patch('ocr_engines.make_ocr',return_value=engine):
                _run_loop(SimpleNamespace(game_id='wuwa',is_launcher=False),config,threading.Event(),lambda e:None,
                    seconds=.02,records_root=Path(folder),on_full_frame=frame_hook,on_observation=observed)
            self.assertIsNone(start.call_args.kwargs['rois'])
            self.assertFalse(start.call_args.kwargs['require_foreground'])
            frame_hook.assert_called_once_with(packet,engine)
            engine.process_frame.assert_not_called()
            observed.assert_not_called()
            capture.stop.assert_called_once()


class LiveUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'config.json').write_text(json.dumps(dict(dubbing={'voice':'B'},other=123)),encoding='utf-8')
        self.panel=LivePanel(self.root,auto_start=False)
        self.panel.active_run='current'
        self.addCleanup(self.panel.close)

    def test_stale_session_events_do_not_change_task_or_play_count(self):
        original=self.panel.task.text()
        self.panel.receive(dict(run_id='old',task=dict(state='confirmed',name='Wrong',basis='old'),
                                playback=dict(state='playing',message='old')))
        self.assertEqual(self.panel.task.text(),original)
        self.assertEqual(self.panel.played,0)

    def test_auto_setting_preserves_voice_and_other_settings(self):
        self.panel.save_auto(False)
        config=json.loads((self.root/'config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['dubbing'],{'voice':'B'})
        self.assertEqual(config['other'],123)
        self.assertFalse(config['live_auto_start'])

    def test_current_task_is_invalidated_when_log_session_exits(self):
        self.panel.receive(dict(run_id='current',task=dict(state='confirmed',id='quest:1',name='任务甲',basis='log')))
        self.panel.receive(dict(run_id='current',clear_line=True,status='游戏已断开'))
        self.assertFalse(self.panel.open_task.isEnabled())
        self.assertEqual(self.panel.task_id,'')
        self.assertEqual(self.panel.task.text(),'等待剧情')

    def test_task_only_becomes_navigable_after_confirmation(self):
        self.panel.receive(dict(run_id='current',task=dict(state='candidate',name='任务甲',basis='next frame')))
        self.assertFalse(self.panel.open_task.isEnabled())
        self.panel.receive(dict(run_id='current',task=dict(state='confirmed',id='quest:1',name='任务甲',basis='selected',context='selected')))
        self.assertTrue(self.panel.open_task.isEnabled())
        self.assertEqual(self.panel.task_caption.text(),'当前选中的任务')


if __name__=='__main__':
    unittest.main()
