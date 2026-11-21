"""Exercise the shared task-library and scheduled casting/credit paths."""
from contextlib import closing
import hashlib
import json
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6 import QtCore as C, QtWidgets as W

import test_wuwa_daily_plan as daily_tests
from wuwa_audition import audition, confirm_take, own_line, pending
from wuwa_catalog import Catalog
from wuwa_daily_plan import enqueue, run, save_meta, today_units
from quest_library import QuestLibrary, choose_cloud_voice, generate_missing, playable, read_library
from test_wuwa_breeze import wav
from wuwa_daily_cast import voice_for


class AuditionTests(unittest.TestCase):
    def setUp(self):
        fixture = daily_tests.DailyTests('test_daily_cast_finishes_before_tts_and_directs_both_voices')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.enable_two_speakers()
        self.f = fixture
        self.c, self.scope = fixture.c, fixture.scope
        self.selection = self.c.progress.selection
        self.stop = Event()

    def uncertain(self, *args):
        result = self.f.cast_reply(*args)
        if args[2] == '新角色':
            result['confidence'] = .58
        return result

    def test_one_hold_does_not_block_other_quest_and_confirmation_reuses_trial(self):
        other = 'quest:12345678'
        rows = {r['id']:r['fingerprint'] for r in self.c.lines([self.scope])}
        with self.c.db:
            self.c.db.execute("INSERT INTO scopes VALUES(?, '邻近支线','','quest')",(other,))
            self.c.db.execute('DELETE FROM scope_lines WHERE scope_id=? AND line_id=?',(self.scope,self.f.ids[0]))
            self.c.db.execute('INSERT INTO scope_lines VALUES(?,?)',(other,self.f.ids[0]))
        enqueue(self.c,self.selection[0],[(other,'邻近支线')])
        approved = {self.scope:{self.f.ids[1]:rows[self.f.ids[1]]}, other:{self.f.ids[0]:rows[self.f.ids[0]]}}
        active = (self.selection[0],[(self.scope,'示例','进行中'),(other,'邻近支线','进行中')])
        with patch('wuwa_daily_plan.scan',return_value=active), patch('wuwa_daily_plan.matched_lines',return_value=approved), patch('wuwa_daily_cast.infer',side_effect=self.uncertain) as model:
            self.assertIn('补齐 1 句',run(self.c),dict(self.c.db.execute('SELECT scope,status FROM daily_plan')))
            self.assertIn('补齐 0 句',run(self.c))
            self.assertEqual(model.call_count,2)
            self.assertEqual(self.f.fetch.call_count,1)
            self.assertIsNone(self.c.binding('11'))
            reason = pending(self.c,'11')
            self.assertEqual((reason['scope'],reason['candidate']),(self.scope,'voc_second'))
            statuses = dict(self.c.db.execute('SELECT scope,status FROM daily_plan'))
            self.assertIn('试听确认',statuses[self.scope])
            self.assertIn('1/1',statuses[other])
            with closing(Catalog(self.c.root)) as reopened:
                self.assertIsNotNone(pending(reopened,'11'))
            trial = own_line(self.c,self.scope,'11',preferred=self.f.ids[0])
            self.assertEqual(trial.identity,self.f.ids[1])
            first = audition(self.c,trial,'voc_second',self.selection,self.stop)
            second = audition(self.c,trial,'voc_demo',self.selection,self.stop)
            self.assertIsNone(self.c.binding('11'))
            self.assertNotEqual(first.path,second.path)
            cost = today_units(self.c)
            self.assertTrue(audition(self.c,trial,'voc_demo',self.selection,self.stop).reused)
            self.assertEqual(today_units(self.c),cost)
            confirm_take(self.c,second,self.selection,'')
            self.assertIsNone(pending(self.c,'11'))
            self.assertEqual(self.c.binding('11')['voice'],'breeze:voc_demo')
            with patch('wuwa_breeze.api_key', return_value=''):
                self.assertIn('补齐 1 句',run(self.c))
            self.assertEqual(self.f.fetch.call_count,3)
            self.assertEqual(model.call_count,2)
            self.assertEqual(today_units(self.c),cost)
            with closing(Catalog(self.c.root)) as reopened:
                self.assertEqual(reopened.binding('11')['voice'],'breeze:voc_demo')
                cfg=json.loads((reopened.root/'config.json').read_bytes())
                self.assertEqual(playable(reopened,cfg)[0][trial.identity].sha256,second.sha)

    def test_foreground_batch_pauses_only_uncertain_actor(self):
        with patch('wuwa_daily_cast.infer',side_effect=self.uncertain):
            result = generate_missing(self.c.root,self.scope,self.f.ids,self.stop,lambda _:None,
                                      selection=self.selection)
        self.assertIn('完成 1 句',result)
        self.assertIn('试听确认',result)
        self.assertIsNotNone(pending(self.c,'11'))
        self.assertEqual(self.f.fetch.call_count,1)

    def test_budget_and_account_change_during_listening(self):
        trial = own_line(self.c,self.scope,'11')
        first = audition(self.c,trial,'voc_second',self.selection,self.stop)
        save_meta(self.c,breeze_daily_credits='400')
        with self.c.db:
            balance = 4000-today_units(self.c)
            self.c.db.execute("INSERT INTO breeze_calls VALUES('other-account',date('now','localtime'),?,'ready','')",(balance,))
        with self.assertRaisesRegex(ValueError,'预算'):
            audition(self.c,trial,'voc_demo',self.selection,self.stop)
        self.assertTrue(audition(self.c,trial,'voc_second',self.selection,self.stop).reused)
        save_meta(self.c,account_revision='switched')
        with self.assertRaisesRegex(ValueError,'账号'):
            confirm_take(self.c,first,self.selection,'')
        self.assertEqual(self.f.fetch.call_count,1)
        self.assertIsNone(self.c.binding('11'))
        self.assertEqual(today_units(self.c),4000)

    def test_changed_binding_or_line_cannot_confirm_stale_sample(self):
        trial = own_line(self.c,self.scope,'11')
        take = audition(self.c,trial,'voc_second',self.selection,self.stop)
        choose_cloud_voice(self.c,'11','voc_demo')
        with self.assertRaisesRegex(ValueError,'换声'):
            confirm_take(self.c,take,self.selection,'')
        with self.c.db:
            self.c.db.execute("UPDATE lines SET fingerprint='reindexed' WHERE id=?",(trial.identity,))
        with self.assertRaisesRegex(ValueError,'台词'):
            confirm_take(self.c,take,self.selection,'breeze:voc_demo')
        self.assertEqual(self.c.binding('11')['voice'],'breeze:voc_demo')

    def test_approved_original_voice_cannot_be_recast_as_generic(self):
        with self.c.db:
            self.c.db.execute("INSERT INTO original_voice_roles VALUES('11',NULL)")
        with self.assertRaisesRegex(ValueError,'原声'):
            audition(self.c,own_line(self.c,self.scope,'11'),'voc_demo',self.selection,self.stop)
        self.f.fetch.assert_not_called()

    def test_installed_same_voice_is_adopted_without_key_or_budget(self):
        trial = own_line(self.c,self.scope,'11')
        body = wav()
        sha = hashlib.sha256(body).hexdigest()
        path = self.c.root/'previously-exported.wav'
        path.write_bytes(body)
        clip = SimpleNamespace(binding_voice='breeze:voc_second',text=trial.text,path=path,sha256=sha)
        entry = dict(id=trial.identity,speaker_id=trial.sid,fingerprint=trial.fingerprint)
        installed = {self.scope:(1,{trial.identity:(entry,clip)})}
        save_meta(self.c,breeze_daily_credits='0')
        with patch('wuwa_voice_pack.installed_previews',return_value=installed), patch('wuwa_breeze.api_key',return_value=''):
            first = audition(self.c,trial,'voc_second',self.selection,self.stop)
            self.assertTrue(first.reused)
            self.assertEqual(first.sha,sha)
            first.path.unlink()
            again = audition(self.c,trial,'voc_second',self.selection,self.stop)
            self.assertEqual(again.path.read_bytes(),body)
            self.assertEqual(today_units(self.c),0)
            self.assertEqual(self.c.db.execute('SELECT count(*) FROM breeze_calls').fetchone()[0],1)
        self.f.fetch.assert_not_called()

    def test_human_choice_survives_a_later_uncertain_analysis(self):
        trial = own_line(self.c,self.scope,'11')
        take = audition(self.c,trial,'voc_second',self.selection,self.stop)
        confirm_take(self.c,take,self.selection,'')
        members = self.c.lines([self.scope])
        row = next(r for r in members if r['speaker_id']=='11')
        with patch('wuwa_daily_cast.infer',side_effect=self.uncertain):
            self.assertEqual(voice_for(self.c,self.scope,row,members,day='next-day'),'voc_second')
        self.assertIsNone(pending(self.c,'11'))

    def test_task_library_opens_only_waiting_actor_and_cancel_keeps_the_hold(self):
        app = W.QApplication.instance() or W.QApplication([])
        with patch('wuwa_daily_cast.infer',side_effect=self.uncertain):
            self.assertIn('补齐 1 句',run(self.c))
        ui = QuestLibrary(self.c)
        self.addCleanup(ui.shutdown)
        ui.loaded(read_library(self.c.root))
        self.assertEqual(ui.audition_button.text(),'待确认试音（1）')
        opened = []
        def inspect_dialog():
            dialog = app.activeModalWidget()
            opened.append((dialog.trial.sid,dialog.trial.identity,dialog.selected_voice(),dialog.apply.isEnabled()))
            dialog.reject()
        C.QTimer.singleShot(0,inspect_dialog)
        ui.audition_button.click()
        self.assertEqual(opened,[('11',self.f.ids[1],'voc_second',False)])
        self.assertIsNotNone(pending(self.c,'11'))
        self.assertIsNone(self.c.binding('11'))
        self.assertEqual(self.f.fetch.call_count,1)
