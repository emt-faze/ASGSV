import json
import threading
import unittest
from unittest.mock import patch

import test_wuwa_multi_account
from test_wuwa_breeze import wav
from wuwa_progress import account_key, cache_path, read_finished_quests
from wuwa_daily_plan import record_scan, enqueue, run, today_units
from wuwa_daily_scan import scan, matched_lines
from wuwa_resources import inventory, resource_snapshot_current
from quest_library import generate_missing


def login(sdk, role, quest):
    return (f'[Login] 设置SDK登录配置 [uId: {sdk}]\n'
            '[Login] LoginProcedure-LoginRequest-登录成功\n'
            f'[Log][YYZ] 设置当前UID [UID: {role}]\n'
            f'[Quest] 上线下发进行中的任务 [任务id: {quest}]\n')


class CustomerAccountTests(unittest.TestCase):
    def setUp(self):
        f = test_wuwa_multi_account.MultiAccountTests('test_shared_limit_after_manual_switch')
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.c, self.root, self.cfg, self.a, self.b, self.ids = f.c, f.root, f.cfg, f.a, f.b, f.ids
        self.cache = cache_path(f.install)
        self.log = self.cache.parent.parent/'Logs/Client.log'
        self.log.write_text('Log file open\n'+login('100001234','712345678',100)
                            +login('200001234','823456789',200), encoding='utf8')

    def test_sdk_is_not_game_uid_and_pinned_account_survives(self):
        self.c.progress.refresh(self.cfg['install_root'], self.a)
        before = self.c.progress.selection
        labels = self.c.progress.snapshot(self.cfg['install_root'])['labels']
        self.assertIn('5678', labels[self.a])
        self.assertIn('6789', labels[self.b])
        self.assertNotIn('1234', str(labels))
        self.assertEqual(scan(self.c, self.cfg)[1][0][0], 'quest:100')
        self.assertEqual(self.c.progress.selection, before)
        self.log.write_text('Log file open\n',encoding='utf8')
        self.assertIn('5678', self.c.progress.snapshot(self.cfg['install_root'])['labels'][self.a])
        self.assertNotIn('UID 尾号',read_finished_quests(self.cfg['install_root'])['labels'][self.a])

    def test_missing_cache_uses_only_paired_login_and_role(self):
        self.cache.unlink()
        self.c.progress.refresh(self.cfg['install_root'],self.b)
        self.assertEqual(self.c.progress.states(),{})
        self.assertEqual([x[0] for x in scan(self.c,self.cfg)[1]], ['quest:200'])
        with patch('wuwa_breeze.api_key', return_value='test'),patch('wuwa_breeze.fetch_wav',return_value=wav()) as cloud:
            self.assertIn('完成 1 句',generate_missing(self.root,'quest:200',self.ids[:1],threading.Event(),lambda _:None))
            self.assertEqual(cloud.call_count,1)

    def test_incomplete_sdk_switch_cannot_claim_the_previous_roles_quests(self):
        with self.log.open('a',encoding='utf8') as stream:
            stream.write('[Login] 设置SDK登录配置 [uId: 100001234]\n'
                         '[Quest] 上线下发进行中的任务 [任务id: 999]\n')
        self.c.progress.refresh(self.cfg['install_root'],self.a)
        self.assertEqual([s for s, _, _ in scan(self.c,self.cfg)[1]], ['quest:100'])

    def test_two_roles_on_one_sdk_do_not_share_completion_or_a_queue(self):
        self.log.write_text('Log file open\n'+login('100001234','712345678',100)
                            +login('100001234','823456789',200), encoding='utf8')
        snap = self.c.progress.snapshot(self.cfg['install_root'])
        self.assertNotIn(self.a,snap['profiles'])
        role = account_key('role:823456789')
        self.assertEqual(snap['profiles'][role],set())
        self.c.progress.refresh(self.cfg['install_root'],role)
        self.assertEqual([x[0] for x in scan(self.c,self.cfg)[1]], ['quest:200'])

    def test_standalone_game_directory_and_unknown_mount_prepare_matching_lines(self):
        self.cfg['install_root'] = str(self.cache.parents[3])
        self.assertEqual(cache_path(self.cfg['install_root']),self.cache)
        (self.root/'config.json').write_text(json.dumps(self.cfg),encoding='utf8')
        self.c.progress.refresh(self.cfg['install_root'],self.a)
        account, items=scan(self.c,self.cfg)
        record_scan(self.c,account,items,self.c.progress.selection)
        enqueue(self.c,account,[(s,n) for s,n,_ in items])
        inv=inventory(self.cfg['install_root'],self.root)
        ref={r['id']:r['fingerprint'] for r in self.c.lines(['quest:100']) if r['id'] in self.ids}
        ref[self.ids[1]]='changed fingerprint'
        with patch('wuwa_daily_scan.verified_index',return_value={'mounts':[], 'quests':{'quest:100':ref}}), \
             patch('wuwa_daily_plan.matched_lines',side_effect=matched_lines), \
             patch('wuwa_breeze.api_key',return_value='test'),patch('wuwa_breeze.fetch_wav',return_value=wav()) as cloud:
            self.assertIn('补齐 1 句',run(self.c))
            cost=today_units(self.c)
            self.assertIn('补齐 0 句',run(self.c))
            self.assertEqual((cost,cloud.call_count),(today_units(self.c),1))
            self.assertIn('待核实',self.c.db.execute('select status from daily_plan').fetchone()[0])

    def test_base_only_install_accepts_index_and_detects_hot_patch(self):
        client = self.cache.parents[2]
        mount, = (client/'Saved/Resources').glob('*/Mount/MountResource.txt')
        original = mount.read_bytes()
        mount.unlink()
        pak = client/'Content/Paks/game.pak'
        pak.parent.mkdir(parents=True,exist_ok=True)
        pak.write_bytes(b'test base package')
        inv = inventory(self.cfg['install_root'],self.root)
        self.assertTrue(matched_lines(inv,self.c,['quest:100'])['quest:100'])
        self.assertTrue(resource_snapshot_current(inv))
        mount.write_bytes(original)
        self.assertFalse(resource_snapshot_current(inv))

