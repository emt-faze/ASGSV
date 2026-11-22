"""Provider budgets, one-line auditions, and fallbacks in isolated temporary catalogs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
import base64
import json
import sys
import unittest
from uuid import uuid4

import wuwa_tencent_tts as tx
import wuwa_free_voices as free
from wuwa_audition import own_line
from wuwa_catalog import Catalog
from wuwa_daily_plan import save_meta, today_units
from wuwa_log_generation import projects
from quest_library import choose_cloud_voice, generate_missing, playable
import test_quest_library
from test_wuwa_breeze import wav


class TencentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.path = Path(self.stack.enter_context(TemporaryDirectory()))
        self.c = self.stack.enter_context(closing(Catalog(self.path/'catalog')))
        self.patch('storage',return_value=self.path/'private')
        self.pair = ('localtestid','localtestkey')
        self.patch('saved_credentials',return_value=self.pair)
        self.patch('save_credentials')
        self.billable = []
        self.service = self.patch('call',side_effect=self.endpoint)
        self.expires = (tx.datetime.now(tx.BEIJING).date()+timedelta(days=10)).isoformat()

    def patch(self,name,**kw):
        return self.stack.enter_context(patch.object(tx,name,**kw))

    def endpoint(self,service,payload,pair):
        if service=='sts':
            return {'AccountId':'10000123'}
        self.billable.append(payload)
        return {'Audio':base64.b64encode(wav()).decode(),'RequestId':'unit-id','SessionId':payload['SessionId']}

    def grant(self,amount=40,tier='super2'):
        return tx.configure(self.c,self.pair,tier,amount,self.expires,True)

    def synth(self,body='同一句',trial=False):
        return tx.synthesize(self.c,body,403013,lambda:True,single_trial=trial)

    def test_quota_and_cached_play_after_exhaustion(self):
        self.grant(3)
        path,hit=self.synth()
        self.assertFalse(hit)
        self.assertTrue(path.is_file())
        self.assertTrue(self.synth()[1])
        with self.assertRaisesRegex(ValueError,'余量不足'):
            self.synth('甲')
        self.assertEqual([p['VoiceType'] for p in self.billable],[403013])
        self.assertEqual(self.grant(100)['remaining'],0)

    def test_mismatched_owner_tier_disabled_and_expired(self):
        with self.assertRaisesRegex(ValueError,'登记'):
            self.synth()
        self.grant(tier='standard')
        with self.assertRaisesRegex(ValueError,'免费资源包'):
            self.synth()
        g=self.grant()
        with patch.object(tx,'account',return_value='x'*64), self.assertRaisesRegex(ValueError,'不同'):
            self.synth()
        with tx.ledger() as db,db:
            db.execute("UPDATE grants SET until_date='2000-01-01' WHERE owner=?",(g['owner'],))
        with self.assertRaisesRegex(ValueError,'到期'):
            self.synth()
        with self.assertRaisesRegex(ValueError,'后付费'):
            tx.configure(self.c,self.pair,'super2',100,self.expires,False)
        self.assertEqual(self.billable,[])

    def test_interactive_permit_is_not_a_background_grant(self):
        tx.enable_trial(self.c,self.pair,'super2',True)
        with self.assertRaisesRegex(ValueError,'未登记'):
            self.synth()
        self.assertFalse(self.synth(trial=True)[1])
        self.assertTrue(self.synth(trial=True)[1])
        with self.assertRaisesRegex(ValueError,'已使用'):
            tx.enable_trial(self.c,self.pair,'super2',True)
        with self.assertRaisesRegex(ValueError,'余量不足'):
            self.synth('另一个句子',True)
        self.assertEqual(len(self.billable),1)

    def test_known_exhaustion_stops_other_lines(self):
        g=self.grant()
        for code in ['UnsupportedOperation.NoFreeAccount','UnsupportedOperation.PkgExhausted']:
            with self.subTest(code=code):
                with tx.ledger() as db,db:
                    db.execute('UPDATE grants SET remaining=20')
                def reject(service,payload,pair):
                    if service=='sts':
                        return self.endpoint(service,payload,pair)
                    raise tx.TencentError(code,'0001')
                self.service.side_effect=reject
                with self.assertRaisesRegex(ValueError,'用尽'):
                    self.synth()
                self.assertEqual(tx.grant_info(g['owner'],'super2')['remaining'],0)
                with self.assertRaisesRegex(ValueError,'余量不足'):
                    self.synth('下一句')

    def test_ambiguous_reply_is_never_retried(self):
        g=self.grant(12)
        def uncertain(service,payload,pair):
            if service=='sts':
                return self.endpoint(service,payload,pair)
            raise TimeoutError('transport')
        self.service.side_effect=uncertain
        with self.assertRaises(TimeoutError):
            self.synth()
        self.service.side_effect=self.endpoint
        with self.assertRaisesRegex(ValueError,'未核实'):
            self.synth()
        self.assertEqual(tx.grant_info(g['owner'],'super2')['remaining'],9)
        self.assertEqual(self.billable,[])

    def test_explicit_denial_allows_manual_retry_without_losing_free_allowance(self):
        g=self.grant(3)
        def denied(service,payload,pair):
            if service=='tts':
                raise tx.TencentError('UnauthorizedOperation','unit-request')
            return self.endpoint(service,payload,pair)
        self.service.side_effect=denied
        with self.assertRaisesRegex(tx.TencentError,'UnauthorizedOperation'):
            self.synth()
        self.assertEqual(tx.grant_info(g['owner'],'super2')['remaining'],3)
        self.service.side_effect=self.endpoint
        self.assertFalse(self.synth()[1])
        self.assertEqual(len(self.billable),1)

    def test_concurrent_workers_cannot_double_submit(self):
        self.grant(3)
        entered, release = Event(),Event()
        def stall(service,payload,pair):
            if service=='tts':
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('test barrier')
            return self.endpoint(service,payload,pair)
        self.service.side_effect=stall
        def run():
            with closing(Catalog(self.c.root)) as second:
                tx.synthesize(second,'同一句',403013,lambda:True)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(run)
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaisesRegex(ValueError,'未核实'):
                    self.synth()
            finally:
                release.set()
            future.result()
        self.assertEqual(len(self.billable),1)
        self.assertEqual(tx.grant_info(self.c.meta('fallback_tencent_owner'),'super2')['remaining'],0)

    def test_signed_target_and_region(self):
        req=tx.signed_request('tts',{'Text':'鲙娘'},self.pair,1700000000)
        self.assertEqual(req.full_url,'https://tts.tencentcloudapi.com/')
        self.assertEqual(req.get_header('X-tc-action'),'TextToVoice')
        self.assertIn('2023-11-14/tts/tc3_request',req.get_header('Authorization'))
        self.assertNotIn(self.pair[1],str(req.headers))


class AcceptedTests(unittest.TestCase):
    def setUp(self):
        f=test_quest_library.LibraryTests('test_partial_complete_retry_and_voice_change')
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.c,self.root,self.scope,self.ids=f.catalog,f.root,f.scope,f.ids
        self.config=json.loads((self.root/'config.json').read_bytes())
        self.select=self.c.progress.selection
        self.trial=own_line(self.c,self.scope,'10',self.ids[0])
        self.wav=self.root/'web.wav'
        self.wav.write_bytes(wav())

    def test_web_import_is_local_and_only_active_after_confirmation(self):
        c=self.c
        with patch('wuwa_breeze.fetch_wav') as primary, patch.object(tx,'call') as secondary:
            take=free.web_trial(c,self.trial,'voc_demo',self.select,self.wav)
            self.assertNotIn(self.trial.identity,playable(c,self.config)[0])
            free.accept(c,self.trial,'voc_demo',self.select,take)
            result=playable(c,self.config)[0]
            self.assertEqual(list(result),[self.trial.identity])
            self.assertEqual(result[self.trial.identity].voice,'TTSMaker · 网页配音')
            choose_cloud_voice(c,'10','voc_second')
            self.assertNotIn(self.trial.identity,playable(c,self.config)[0])
            primary.assert_not_called()
            secondary.assert_not_called()

    def test_approved_tencent_fallback_through_normal_generation_and_replay(self):
        c=self.c
        with patch.object(tx,'synthesize',return_value=(self.wav,False)) as synth:
            take,_=free.tencent_trial(c,self.trial,'voc_demo',403013,self.select,Event())
            self.assertIsNone(free.configured(c,'10','voc_demo'))
            free.accept(c,self.trial,'voc_demo',self.select,take)
            save_meta(c,breeze_daily_credits='0')
            with patch('wuwa_breeze.fetch_wav') as primary:
                self.assertIn('完成 1 句',generate_missing(c.root,self.scope,self.ids,Event(),lambda _:None))
                self.assertIn('无需',generate_missing(c.root,self.scope,self.ids,Event(),lambda _:None))
                self.assertEqual(today_units(c),0)
                self.assertEqual(synth.call_count,2)
                self.assertTrue(synth.call_args_list[0].kwargs['single_trial'])
                self.assertFalse(synth.call_args_list[1].kwargs.get('single_trial',False))
                self.assertEqual({v.voice for v in playable(c,self.config)[0].values()},{'腾讯云 · 云小璇'})
                info=next(row.audio for proj in projects(c.root) for row in proj.rows if row.audio)
                self.assertEqual(info['provider'],'tencent')
                self.assertEqual(info['primary_cloud_voice'],'voc_demo')
                primary.assert_not_called()

    def test_uncertain_primary_never_submits_to_backup(self):
        with patch.object(tx,'synthesize',return_value=(self.wav,False)):
            take,_ = free.tencent_trial(self.c,self.trial,'voc_demo',403013,self.select,Event())
        free.accept(self.c,self.trial,'voc_demo',self.select,take)
        with patch('wuwa_breeze.api_key',return_value='unit-test'), patch(
                'wuwa_breeze.fetch_wav',side_effect=TimeoutError('timeout')) as primary, patch.object(tx,'synthesize') as secondary:
            self.assertIn('完成 0 句',generate_missing(self.root,self.scope,self.ids,Event(),lambda _:None))
            self.assertIn('完成 0 句',generate_missing(self.root,self.scope,self.ids,Event(),lambda _:None))
            self.assertEqual(primary.call_count,1)
            secondary.assert_not_called()

    def test_old_binding_and_protected_original_cannot_accept(self):
        take=free.web_trial(self.c,self.trial,'voc_demo',self.select,self.wav)
        with self.c.db:
            self.c.db.execute("INSERT INTO original_voice_roles VALUES('10','voc_demo')")
        with self.assertRaisesRegex(ValueError,'主音色'):
            free.accept(self.c,self.trial,'voc_demo',self.select,take)


@unittest.skipUnless(sys.platform=='win32','Windows credentials')
class CredentialRoundTrip(unittest.TestCase):
    def test_pair_round_trip(self):
        import win32cred
        name='ASGSV/test/'+uuid4().hex
        with patch.object(tx,'CREDENTIAL',name):
            tx.save_credentials(('test-sid','test-key'))
            self.addCleanup(win32cred.CredDelete,name,win32cred.CRED_TYPE_GENERIC)
            self.assertEqual(tx.saved_credentials(),('test-sid','test-key'))
