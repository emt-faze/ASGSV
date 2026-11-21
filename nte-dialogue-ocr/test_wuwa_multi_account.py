import json
import sqlite3
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

from PySide6 import QtCore as C, QtWidgets as W, QtTest

from quest_library import QuestLibrary, generate_missing, read_library
import test_quest_library
from test_wuwa_breeze import wav
from test_wuwa_progress import cache_value
from wuwa_catalog import Catalog
from wuwa_daily_plan import initialize, record_scan, enqueue, run, today_units
from wuwa_daily_scan import scan
from wuwa_daily_ui import DailyDialog
from wuwa_progress import account_key, cache_path
from wuwa_voice_pack import mount_signature


class MultiAccountTests(unittest.TestCase):
    def setUp(self):
        f = test_quest_library.LibraryTests('test_partial_complete_retry_and_voice_change')
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.c, self.root, self.ids = f.catalog, f.root, f.ids
        self.cfg = json.loads((self.root/'config.json').read_text(encoding='utf8'))
        self.install = self.cfg['install_root']
        self.a, self.b = map(account_key, ('100001234','200001234'))
        path=cache_path(self.install)
        path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE LocalStorage(key TEXT PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO LocalStorage VALUES(?,?)',('UserFinishedQuests',json.dumps(
                cache_value([('100001234',[200]),('200001234',[100])]))))
        folder=path.parent.parent/'Logs'
        folder.mkdir()
        (folder/'Client.log').write_text('\n'.join(['Log file open',
            '[Quest] 下发可接任务 [任务id: 999]',
            '[Login] 身份 [uId: 100001234]', '[Login] LoginProcedure-LoginRequest-登录成功',
            '[Quest] 上线下发进行中的任务 [任务id: 100]',
            '[Login] 身份 [uId: 200001234]', '[Login] LoginProcedure-LoginRequest-登录成功',
            '[Quest] 上线下发进行中的任务 [任务id: 200]',
            '[Login] LoginProcedure-LoginRequest-登录成功','[Quest] 下发可接任务 [任务id: 999]', '']),encoding='utf8')
        self.c.set_branch_rule(True)
        with self.c.db:
            self.c.db.executemany('INSERT INTO scopes VALUES(?,?,?,?)',[
                ('quest:200','另一个任务','','quest'),('quest:999','身份未知的记录','','quest')])
            self.c.db.executemany('INSERT INTO scope_lines VALUES(?,?)',[('quest:200',i) for i in self.ids])
            self.c.db.execute("UPDATE meta SET value='400' WHERE key='breeze_daily_credits'")
            self.c.db.executemany('INSERT INTO quest_cast_reviews VALUES(?,?)',
                                  [(scope,'{"10":"male"}') for scope in ('quest:100','quest:200')])
        verified={i['id']:i['fingerprint'] for i in self.c.lines(['quest:100']) if i['id'] in self.ids}
        inventory=json.loads((self.root/'sources/local-inventory.json').read_text(encoding='utf8'))
        manifest={'mounts':mount_signature(inventory),'quests':{q:verified for q in ('quest:100','quest:200','quest:999')}}
        self.enterContext(patch('wuwa_daily_scan.verified_index',return_value=manifest))
        self.enterContext(patch('wuwa_daily_plan.matched_lines',return_value=manifest['quests']))
        initialize(self.c)

    def select_and_scan(self, account, scope):
        self.c.progress.refresh(self.install,account)
        who,items=scan(self.c,self.cfg)
        self.assertEqual((who,[s for s,_,_ in items]),(account,[scope]))
        record_scan(self.c,who,items,self.c.progress.selection)
        enqueue(self.c,who,[(s,n) for s,n,_ in items])

    def test_switch_reopen_and_reject_stale_scan_even_after_switch_back(self):
        with self.assertRaisesRegex(ValueError,'选择游戏账号'):
            scan(self.c,self.cfg)
        self.select_and_scan(self.a,'quest:100')
        previous=self.c.progress.selection
        self.select_and_scan(self.b,'quest:200')
        with self.assertRaisesRegex(ValueError,'已完成'):
            generate_missing(self.root,'quest:100',self.ids,threading.Event(),lambda m:None)
        library=read_library(self.root)[0]
        self.assertTrue(library['quest:200'].evidence)
        self.assertFalse(library['quest:100'].evidence)
        with closing(Catalog(self.root)) as reopened:
            self.assertEqual(reopened.progress.account,self.b)
        self.select_and_scan(self.a,'quest:100')
        with self.assertRaisesRegex(ValueError,'切换'):
            record_scan(self.c,self.a,[],previous)
        self.assertEqual(self.c.db.execute('SELECT COUNT(*) FROM daily_plan').fetchone()[0],2)

    def test_switch_during_cloud_request_saves_one_clip_and_stops_old_queue(self):
        self.select_and_scan(self.b,'quest:200')
        self.select_and_scan(self.a,'quest:100')
        calls=[]
        def receive(*args):
            calls.append(args[0])
            if len(calls)==1:
                with closing(Catalog(self.root)) as ui:
                    ui.progress.refresh(self.install,self.b)
            return wav()
        with patch('wuwa_breeze.api_key',return_value='test'),patch('wuwa_breeze.fetch_wav',side_effect=receive):
            with self.assertRaisesRegex(ValueError,'切换'):
                run(self.c)
            self.assertEqual(len(calls),1)
            first_cost=today_units(self.c)
            self.assertGreater(first_cost,0)
            self.assertIn('补齐 1 句',run(self.c))
            self.assertEqual(len(calls),2)
            second_cost=today_units(self.c)
            self.c.progress.refresh(self.install,self.a)
            self.assertIn('补齐 0 句',run(self.c))
            self.assertEqual(today_units(self.c),second_cost)
            self.assertEqual(len(calls),2)
            self.assertEqual(self.c.db.execute("SELECT count(*) FROM breeze_calls WHERE state='ready'").fetchone()[0],2)

    def test_old_account_queue_survives_rotation_of_its_logs(self):
        self.select_and_scan(self.a,'quest:100')
        logs=cache_path(self.install).parent.parent/'Logs/Client.log'
        logs.write_text('Log file open\n[Login] [uId: 200001234]\n[Login] LoginProcedure-LoginRequest-登录成功\n'
                        '[Quest] 下发可接任务 [任务id: 200]\n',encoding='utf8')
        who,items=scan(self.c,self.cfg)
        self.assertEqual((who,[(s,r) for s,_,r in items]),(self.a,[('quest:100','上次扫描 · 游戏记录 · 进行中')]))
        self.c.progress.refresh(self.install,self.b)
        self.assertEqual([s for s,_,_ in scan(self.c,self.cfg)[1]],['quest:200'])

    def test_shared_limit_after_manual_switch(self):
        import wuwa_breeze as breeze
        self.select_and_scan(self.a,'quest:100')
        with self.c.db:
            self.c.db.execute("INSERT INTO breeze_calls VALUES('prior',date('now','localtime'),3998,'submitted',NULL)")
        with patch('wuwa_breeze.api_key',return_value='test'),patch('wuwa_breeze.fetch_wav',return_value=wav()) as post:
            original,hit=breeze.cached_wav(self.c,'一','voc_demo',lambda:True)
            self.assertFalse(hit)
            self.select_and_scan(self.b,'quest:200')
            reused,hit=breeze.cached_wav(self.c,'一','voc_demo',lambda:True)
            self.assertTrue(hit)
            self.assertEqual(original,reused)
            with self.assertRaisesRegex(ValueError,'预算'):
                breeze.cached_wav(self.c,'二','voc_demo',lambda:True)
            self.assertEqual((today_units(self.c),post.call_count),(4000,1))

    def test_real_qt_account_menu_syncs_both_views(self):
        app=W.QApplication.instance() or W.QApplication([])
        self.select_and_scan(self.a,'quest:100')
        self.select_and_scan(self.b,'quest:200')
        library=QuestLibrary(self.c)
        self.addCleanup(library.shutdown)
        with patch('wuwa_daily_ui.manage',return_value={'registered':False}):
            dialog=DailyDialog(self.c)
            if dialog.worker:
                dialog.worker.wait(5000)
                app.processEvents()
            label=self.c.progress.label(self.a)
            def click_account():
                menu=app.activePopupWidget()
                for action in menu.actions():
                    if action.text()==label:
                        QtTest.QTest.mouseClick(menu,C.Qt.LeftButton,pos=menu.actionGeometry(action).center())
                        return
                menu.close()
            C.QTimer.singleShot(10,click_account)
            dialog.account_button.click()
            self.assertEqual(self.c.progress.account,self.a)
            self.assertEqual(dialog.queue.item(0,0).data(C.Qt.UserRole),'quest:100')
            library.check_catalog()
            self.assertIn(label,library.account_button.text())
            dialog.close()
            library.close()
