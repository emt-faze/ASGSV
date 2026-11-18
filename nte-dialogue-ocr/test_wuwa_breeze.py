import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import wave

import numpy as np
import test_wuwa
import wuwa_breeze as breeze
from wuwa_catalog import Catalog
from wuwa_log_generation import GenerationService, existing_audio


def wav(silent=False):
    data=io.BytesIO()
    samples=np.sin(np.arange(12000)*.1)*(0 if silent else 12000)
    with wave.open(data,'wb') as writer:
        writer.setparams((1,2,24000,0,'NONE','not compressed'))
        writer.writeframes(samples.astype('<i2').tobytes())
    return data.getvalue()


@unittest.skipUnless(sys.platform == 'win32', 'Windows Credential Manager')
class CredentialTests(unittest.TestCase):
    def test_save_and_read_with_installed_pywin32(self):
        import win32cred
        target = 'ASGSV/test/' + uuid4().hex
        with patch.object(breeze, 'CREDENTIAL', target):
            breeze.save_key('  bb-test-key_123  ')
            self.addCleanup(win32cred.CredDelete, target, win32cred.CRED_TYPE_GENERIC)
            self.assertEqual(breeze.api_key(), 'bb-test-key_123')


class CostTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.c=Catalog(Path(tmp.name))
        self.addCleanup(self.c.close)
        self.c.db.execute("INSERT INTO meta VALUES('breeze_daily_credits','2')")
        self.c.db.commit()
        key=patch.object(breeze,'api_key',return_value='test-only')
        key.start()
        self.addCleanup(key.stop)

    def test_pronunciation_is_literal_single_pass_and_part_of_the_request_key(self):
        path = self.c.root / 'config.json'
        def dictionary(terms):
            path.write_text(json.dumps({'tts_pronunciations': terms}), encoding='utf-8')
        dictionary({'声': '升', '声骸': '孩', '孩': '海', '.': '点'})
        text = '声骸.声（*小声*）'
        with patch.object(breeze, 'build_opener') as opener:
            post = opener.return_value.open
            response = post.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = wav()
            first, hit = breeze.cached_wav(self.c, text, 'voice', lambda: True)
            self.assertFalse(hit)
            self.assertEqual(json.loads(post.call_args.args[0].data)['text'], '孩点升')
            dictionary({})
            second, hit = breeze.cached_wav(self.c, text, 'voice', lambda: True)
            self.assertFalse(hit)
            self.assertNotEqual(first, second)
            self.assertEqual(json.loads(post.call_args.args[0].data)['text'], '声骸.声')
            self.assertTrue(breeze.cached_wav(self.c, text, 'voice', lambda: True)[1])
            self.assertEqual(post.call_count, 2)

    def test_one_paid_call_then_verified_disk_reuse_even_at_limit(self):
        with patch.object(breeze,'fetch_wav',return_value=wav()) as post:
            for hit in (False,True):
                path,cached=breeze.cached_wav(self.c,'一二三四五六七八九十','voice',lambda:True)
                self.assertEqual(hit,cached)
            with self.assertRaisesRegex(ValueError,'预算'):
                breeze.cached_wav(self.c,'新','voice',lambda:True)
            self.assertEqual(post.call_count,1)
            path.write_bytes(b'broken')
            with self.assertRaisesRegex(ValueError,'重复扣额'):
                breeze.cached_wav(self.c,'一二三四五六七八九十','voice',lambda:True)
            self.assertEqual(post.call_count,1)

    def test_page_changed_just_before_post(self):
        checks=iter([True,False])
        with patch.object(breeze,'fetch_wav') as post,self.assertRaisesRegex(ValueError,'台词已切换'):
            breeze.cached_wav(self.c,'你好','voice',lambda:next(checks))
        post.assert_not_called()
        self.assertEqual(self.c.db.execute('select count(*) from breeze_calls').fetchone()[0],0)

    def test_other_worker_cannot_repeat_or_overspend_inflight_request(self):
        started, release = threading.Event(), threading.Event()
        root = self.c.root
        errors = []

        def slow_response(*args):
            started.set()
            if not release.wait(5):
                raise TimeoutError('response not released')
            return wav()

        def generate():
            catalog = Catalog(root)
            try:
                breeze.cached_wav(catalog, '一二三四五六七八九十', 'voice', lambda: True)
            except Exception as exc:
                errors.append(exc)
            finally:
                catalog.close()

        with patch.object(breeze, 'fetch_wav', side_effect=slow_response) as post:
            worker = threading.Thread(target=generate)
            worker.start()
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaisesRegex(ValueError, '重复扣额'):
                    breeze.cached_wav(self.c, '一二三四五六七八九十', 'voice', lambda: True)
                with self.assertRaisesRegex(ValueError, '预算'):
                    breeze.cached_wav(self.c, '新', 'voice', lambda: True)
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(post.call_count, 1)

    def test_timeout_and_silent_response_never_trigger_second_charge(self):
        for text,side_effect in [('一',TimeoutError()),('二',lambda *args:wav(True))]:
            with patch.object(breeze,'fetch_wav',side_effect=side_effect) as post:
                with self.assertRaises((TimeoutError,breeze.AudioQualityError)):
                    breeze.cached_wav(self.c,text,'voice',lambda:True)
                with self.assertRaisesRegex(ValueError,'重复扣额'):
                    breeze.cached_wav(self.c,text,'voice',lambda:True)
                self.assertEqual(post.call_count,1)


class CooperationTests(unittest.TestCase):
    def test_review_is_quest_scoped_and_originals_reject_generic_voices(self):
        from wuwa_cast_review import line_problem, review_problem, save_review
        fixture=test_wuwa.CatalogTests('test_public_import_link_and_unknown_gate')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        c=fixture.catalog
        line=fixture.approve('a')
        quest=c.db.execute("select scope_id from scope_lines where line_id=? and scope_id like 'quest:%'",(line,)).fetchone()[0]
        sid=c.db.execute('select speaker_id from lines where id=?',(line,)).fetchone()[0]
        with c.db:
            c.db.execute("insert into meta values('require_quest_cast_review','1')")
        self.assertTrue(review_problem(c,quest))
        save_review(c,quest,{sid:'original'})
        self.assertTrue(review_problem(c,'quest:new'))
        self.assertTrue(line_problem(c,line,sid,'voc_generic'))
        with c.db:
            c.db.execute('insert into original_reference_cast values(?,?)',(sid,'原参考'))
        self.assertFalse(line_problem(c,line,sid,'character:原参考'))
        self.assertTrue(line_problem(c,line,sid,'voc_generic'))

    def test_cloud_primary_cast_cache_and_page_turn(self):
        fixture = test_wuwa.CatalogTests('test_public_import_link_and_unknown_gate')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        c = fixture.catalog
        line_id = fixture.approve('a')
        cfg = dict(fixture.config,dubbing=dict(game_id='wuwa',mode='character'))
        (fixture.data/'config.json').write_text(json.dumps(cfg),encoding='utf8')
        with c.db:
            c.db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',[
                ('wuwa_tts_provider','breezeblue'),('breeze_daily_credits','30')])
            c.db.execute("INSERT INTO breeze_presets VALUES('voc_demo','测试声','male','young')")
        row = c.db.execute('SELECT * FROM lines WHERE id=?',(line_id,)).fetchone()
        service = GenerationService(fixture.data,lambda msg:None)
        self.addCleanup(service.close)
        voice = service.ensure_voice(c,row,None)['voice']
        self.assertEqual(voice,'breeze:voc_demo')
        self.assertEqual(c.binding(row['speaker_id'])['voice'],voice)
        def response(*args):
            service.set_current(None)
            return wav()
        with patch('wuwa_log_generation.BatchGenerator') as local, patch.object(breeze,'api_key',return_value='test'), \
                patch.object(breeze,'fetch_wav',side_effect=response) as post:
            self.assertIsNone(service.generate(c,cfg,line_id,voice,False))
            service.set_current((line_id,voice))
            self.assertIsNone(service.generate(c,cfg,line_id,voice,True))
            post.assert_not_called()
            clip = service.generate(c,cfg,line_id,voice,False)
            self.assertTrue(clip.path.is_file())
            self.assertFalse(service.current_is((line_id,voice)))
            service.set_current((line_id,voice))
            self.assertEqual(clip.sha256,service.generate(c,cfg,line_id,voice,False).sha256)
            local.assert_not_called()
            post.assert_called_once()

    def test_live_fallback_and_reload_through_generation_service(self):
        fixture=test_wuwa.CatalogTests('test_public_import_link_and_unknown_gate')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        c=fixture.catalog
        key=fixture.approve('a')
        cfg=dict(fixture.config,dubbing=dict(game_id='wuwa',mode='character'))
        (fixture.data/'config.json').write_text(json.dumps(cfg),encoding='utf8')
        breeze.set_pairing(c,'10','system:default','voc_example')
        c.db.execute("INSERT INTO meta VALUES('breeze_daily_credits','30')")
        c.db.commit()
        service=GenerationService(fixture.data,lambda msg:None)
        self.addCleanup(service.close)
        with patch('wuwa_log_generation.BatchGenerator.run',side_effect=RuntimeError('local timeout')) as local, \
             patch.object(breeze,'api_key',return_value='test-only'), \
             patch.object(breeze,'fetch_wav',return_value=wav()) as post:
            service.set_current(None)
            with self.assertRaisesRegex(RuntimeError,'local timeout'):
                service.generate(c,cfg,key,'system:default',False)
            service.set_current((key,'system:default'))
            with self.assertRaisesRegex(RuntimeError,'local timeout'):
                service.generate(c,cfg,key,'system:default',True)
            post.assert_not_called()
            clip=service.generate(c,cfg,key,'system:default',False)
            self.assertEqual(clip.cloud_voice,'voc_example')
            self.assertEqual(post.call_count,1)
            previous_calls=local.call_count
            again=service.generate(c,cfg,key,'system:default',False)
            self.assertEqual(again.sha256,clip.sha256)
            self.assertEqual((local.call_count,post.call_count),(previous_calls,1))
            breeze.set_pairing(c,'10','system:default','voc_different')
            self.assertFalse(existing_audio(c,cfg)[0])
