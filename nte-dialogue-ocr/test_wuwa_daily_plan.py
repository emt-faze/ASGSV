from contextlib import ExitStack
from datetime import datetime
import json
import unittest
from unittest.mock import patch

from PySide6 import QtCore as C, QtWidgets as W
import test_quest_library
from test_wuwa_breeze import wav
from wuwa_daily_cast import voice_for,endpoint
from wuwa_daily_plan import initialize,enqueue,run,save_meta,due_slot,today_units
from wuwa_daily_scan import reduce_events,matched_lines
from wuwa_daily_ui import DailyDialog
from quest_library import playable
import wuwa_breeze as breeze


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_quest_library.LibraryTests('test_partial_complete_retry_and_voice_change')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.c=self.fixture.catalog
        self.scope=self.fixture.scope
        self.ids=self.fixture.ids
        self.c.set_branch_rule(True)
        initialize(self.c)
        enqueue(self.c,'test',[(self.scope,'示例')]*2)
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.mock('wuwa_daily_plan.scan',('test',[(self.scope,'示例','进行中')]))
        self.rows=self.c.lines([self.scope])
        self.mock('wuwa_daily_plan.matched_lines',{self.scope:{r['id']:r['fingerprint'] for r in self.rows if r['id'] in self.ids}})
        self.mock('wuwa_breeze.api_key','testing')
        self.fetch=self.mock('wuwa_breeze.fetch_wav',wav())

    def mock(self,target,value):
        return self.stack.enter_context(patch(target,return_value=value))

    def test_standalone_export_retry_and_shared_budget(self):
        self.assertIn('补齐 2 句',run(self.c))
        self.assertEqual(self.fetch.call_count,2)
        cost=today_units(self.c)
        cfg=json.loads((self.c.root/'config.json').read_text())
        self.assertTrue(set(self.ids)<=playable(self.c,cfg)[0].keys())
        self.assertIn('补齐 0 句',run(self.c))
        self.assertEqual(self.fetch.call_count,2)
        self.assertEqual(today_units(self.c),cost)
        self.assertEqual(self.c.db.execute('select count(*) from daily_plan').fetchone()[0],1)

    def test_exclusions_and_held_reservation(self):
        with patch('wuwa_daily_plan.scan',return_value=('other',[])):
            self.assertIn('计划为空',run(self.c))
        with patch('wuwa_daily_plan.scan',return_value=('test',[])):
            self.assertIn('补齐 0',run(self.c))
        self.fetch.assert_not_called()
        text='测试'
        key=breeze.recipe(text,'voc_demo')
        with self.c.db:
            self.c.db.execute("INSERT INTO breeze_calls VALUES(?,date('now','localtime'),3999,'submitted',NULL)",(key,))
        save_meta(self.c,breeze_daily_credits='9999')
        self.assertIn('补齐 0',run(self.c))
        with self.assertRaisesRegex(ValueError,'预算'):
            breeze.cached_wav(self.c,text+'二','voc_demo',lambda:True,max_daily_credits=400)
        self.fetch.assert_not_called()
        self.assertEqual(today_units(self.c),3999)

    def test_cast_uses_context_and_preserves_user_binding(self):
        row=next(r for r in self.rows if r['speaker_id']=='10')
        save_meta(self.c,daily_cast_enabled='1',daily_cast_endpoint='https://model.example/v1',daily_cast_model='demo')
        with self.c.db:
            self.c.db.execute("delete from breeze_cast where speaker_id='10'")
        evidence=row['raw_text']
        profile=dict(gender='female',age='young',personality='干脆直率',voice_id='voc_second',evidence=evidence,confidence=.92)
        with patch('wuwa_cast_context.quest_suggestion',return_value=(None,None,[])), patch('wuwa_daily_cast.infer',return_value=profile) as model:
            self.assertEqual(voice_for(self.c,self.scope,row,self.rows),'voc_second')
            self.assertEqual(voice_for(self.c,self.scope,row,self.rows),'voc_second')
            self.assertEqual(model.call_count,1)
            self.assertIn(evidence,str(model.call_args))
            with self.c.db:
                self.c.db.execute("DELETE FROM breeze_cast WHERE speaker_id='10'")
                self.c.db.execute("DELETE FROM daily_profiles")
            model.return_value=dict(profile,evidence='并不存在的引文')
            for _ in range(2):
                with self.assertRaisesRegex(ValueError,'证据不足'):
                    voice_for(self.c,self.scope,row,self.rows)
            self.assertEqual(model.call_count,2)
        with self.assertRaises(ValueError):
            endpoint('https://u:secret@host/v1')

    def test_evidence_lifecycle_and_catchup(self):
        active,excluded={},set()
        reduce_events(['[Quest] 上线下发进行中的任务 [任务id: 7]','[Quest] 下发可接任务 [任务id: 7]',
                       '[Quest] 上线下发进行中的任务 [任务id: 2]','[Quest] 任务状态更新 [任务Id: 2][StateId: Finish]',
                       '[Quest] 行为树ChildQuest节点状态更新 [任务id: 7][ChildQuest子节点状态: 4-完成]'],active,excluded)
        self.assertEqual(active,{'7':'游戏记录 · 可接取'})
        reduce_events(['[Quest] 任务状态更新 [任务Id: 7][StateId: Progress]'],active,excluded)
        self.assertEqual(active['7'],'游戏记录 · 进行中')
        reduce_events(['[Login] LoginProcedure-LoginRequest-登录成功'],active,excluded)
        self.assertFalse(active)
        now=datetime(2026,11,19,7)
        slot=due_slot(now,'09:00','2026-11-16T08:00:00','')
        self.assertEqual(slot,'2026-11-18T09:00:00')
        self.assertIsNone(due_slot(now,'09:00','',slot))
        self.assertIsNone(due_slot(now,'09:00','2026-11-19T06:00:00',''))
        self.assertEqual(matched_lines({'install_root':'X:/game','mounts':[]}),{})

    def test_dialog_scan_select_remove(self):
        app=W.QApplication.instance() or W.QApplication([])
        with patch('wuwa_daily_ui.manage',return_value={'registered':False}), patch('wuwa_daily_ui.scan',return_value=('test',[(self.scope,'演示任务','进行中')])):
            dialog=DailyDialog(self.c)
            def wait():
                if dialog.worker:
                    dialog.worker.wait(3000)
                    app.processEvents()
            wait()
            dialog.detect_button.click()
            wait()
            self.assertEqual(dialog.results.rowCount(),1)
            dialog.queue.selectRow(0)
            dialog.remove.click()
            dialog.results.item(0,0).setCheckState(C.Qt.Checked)
            dialog.add.click()
            self.assertEqual(dialog.queue.rowCount(),1)
            self.assertEqual(dialog.tabs.currentIndex(),1)
            dialog.close()

