import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from dialogue_generation import GenerationProject, BatchGenerator, file_digest, atomic_text
from game_profiles import wuwa_window_kind, safe_output, wuwa_row_reason
from prepared_playback import PreparedLibrary, PreparedPlayback
from wuwa_catalog import Catalog, classify_audio, resolve_text, talk_items, speaker_names
from wuwa_launch import classify_screen
from wuwa_resources import COMMIT, SOURCE, mount_entries, overlay_order, inventory
from test_dialogue_generation import Runtime, Client
from test_prepared_playback import completed_line, Player


class PureWuwaTests(unittest.TestCase):
    def test_speaker_name_uses_localized_name_when_key_is_missing(self):
        names=speaker_names([{'Id':700223,'NameStringKey':-1,'Name':5273}], {'5273':'思澜'}, {})
        self.assertEqual(names['700223'],'思澜')

    def test_wuwa_yellow_ink_is_not_nte_orange(self):
        import numpy as np
        from wuwa_ocr import speaker_ink
        colors=np.array([[[120,230,238],[255,255,255],[10,120,255]]],dtype=np.uint8)
        self.assertEqual(speaker_ink(colors).tolist(), [[True,False,False]])

    def test_scene_mask_uses_client_coordinates_on_cropped_capture(self):
        import numpy as np
        from wuwa_scene import icon_mask
        frame=np.zeros((1080,1920,3),dtype=np.uint8)
        frame[45:65,1600:1620]=255
        area=(.814,.029,.86,.09)
        mask=icon_mask(frame,area)
        cropped=frame[20:1000,300:1850]
        self.assertTrue(np.array_equal(mask,icon_mask(cropped,area,(1920,1080),(300,20))))
        self.assertIsNone(icon_mask(cropped,area,(1920,1080),(1500,1000)))

    def test_scene_does_not_accept_missing_templates(self):
        import numpy as np
        from wuwa_scene import dialogue_controls
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(dialogue_controls(np.zeros((1080,1920,3),dtype=np.uint8),directory))

    def test_game_executable_requires_full_path_and_class(self):
        root = r'F:\Wuthering Waves'
        exe = root + r'\Wuthering Waves Game\Client\Binaries\Win64\Client-Win64-Shipping.exe'
        self.assertEqual(wuwa_window_kind(exe, 'UnrealWindow', root), 'game')
        self.assertIsNone(wuwa_window_kind(exe.replace('Wuthering Waves Game', 'Another Game'), 'UnrealWindow', root))
        self.assertIsNone(wuwa_window_kind(exe, 'OtherWindow', root))
        self.assertIsNone(wuwa_window_kind(exe, 'UnrealWindow', ''))

    def test_output_must_not_be_inside_installation(self):
        with self.assertRaises(ValueError):
            safe_output(r'F:\Wuthering Waves\export\x.json')

    def test_mounts_priority_not_lexicographic(self):
        entries = mount_entries('::Mount::\nResource/3.7.9/a,21,h,h\nResource/3.7.17/b,10,h,h\n')
        self.assertEqual([e['priority'] for e in overlay_order(entries)], [10, 21])

    def test_manifest_deletion_and_ties(self):
        entries = mount_entries('::Mount::\na,9\nb,9\nc,8\n::Del::\na\n')
        self.assertEqual([e['path'] for e in overlay_order(entries)], ['c', 'b'])

    def test_manifest_rejects_traversal(self):
        with self.assertRaises(ValueError):
            mount_entries('::Mount::\n../outside,3')

    def test_empty_audio_field_never_proves_unvoiced(self):
        self.assertEqual(classify_audio({'PlotLineKey': ''}, {})[0], 'unknown')
        self.assertEqual(classify_audio({'PlotLineKey': 'missing'}, {})[0], 'unknown')
        self.assertEqual(classify_audio({'PlotLineKey': 'x'}, {'x': {'FileName': 'vo_x'}})[0], 'voiced')

    def test_option_text_is_excluded_nested_dialogue_retained(self):
        nested = {'Name': 'ShowTalk', 'Params': {'TalkItems': [{'Id': 1, 'TidTalk': 'branch', 'Type': 'Talk'}]}}
        actions = [{'Name': 'ShowTalk', 'Params': {'TalkItems': [
            {'Id': 1, 'TidTalk': 'main', 'Type': 'Talk', 'Options': [{'TidTalkOption': 'choice', 'Actions': [nested]}]},
            {'Id': 2, 'TidTalk': 'excluded', 'Type': 'Option'},
            {'Id': 3, 'TidTalk': 'narration', 'Type': 'AvgNarration'}]}}]
        items = list(talk_items(actions))
        self.assertEqual({item['TidTalk'] for _, item in items}, {'main', 'branch', 'narration'})
        self.assertEqual(len({branch for branch, _ in items}), 3)

    def test_only_known_dynamic_variables_resolve(self):
        self.assertEqual(resolve_text('你好，{PlayerName}。', '测试')[0], '你好，测试。')
        self.assertTrue(resolve_text('你好，{UnknownGender}。')[1])
        self.assertEqual(resolve_text('<color=#fff>你好</color>')[0], '你好')
        self.assertTrue(resolve_text('<unknown>x</unknown>')[1])

    def test_launch_blocked_pages_preempt_button(self):
        def box(text, x=800, y=800, confidence=.99):
            return [[[x-10,y-5],[x+10,y-5],[x+10,y+5],[x-10,y+5]], text, confidence]
        ready = box('开始游戏')
        self.assertEqual(classify_screen([ready], (1000, 1000), True)['state'], 'launcher_ready')
        for text in ('验证码', '扫码登录', '更新游戏', '选择服务器'):
            self.assertEqual(classify_screen([ready, box(text)], (1000, 1000), True)['state'], 'takeover')
        self.assertEqual(classify_screen([box('开始游戏', confidence=.91)], (1000, 1000), True)['state'], 'unknown')
        self.assertEqual(classify_screen([box('开始游戏', x=100)], (1000, 1000), True)['state'], 'unknown')
        self.assertEqual(classify_screen([box('点击进入游戏', x=500)], (1000, 1000))['state'], 'entry_ready')


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install = self.root / 'game-read-only'
        client = self.install / 'Wuthering Waves Game/Client'
        exe = client / 'Binaries/Win64/Client-Win64-Shipping.exe'
        exe.parent.mkdir(parents=True)
        exe.touch()
        mount = client / 'Saved/Resources/3.7.0/Mount/MountResource.txt'
        mount.parent.mkdir(parents=True)
        mount.write_text('::Mount::\nResource/3.7.17/test,21,h,h\n', encoding='utf-8')
        self.mount = mount
        self.data = self.root / 'data'
        inventory(self.install, self.data)
        self.config = dict(install_root=str(self.install), nickname='测试', traveler_gender='female', allow_unknown_progress=True)
        atomic_text(self.data / 'config.json', json.dumps(self.config))
        self.catalog = Catalog(self.data)
        self.folder = self.root / 'source'
        self.folder.mkdir()
        self.tables = dict(
            text=[{'Id':'a','Content':'你好，{PlayerName}。'}, {'Id':'b','Content':'原声测试。'},
                  {'Id':'c','Content':'这是另一条待核对白。'}, {'Id':'name','Content':'测试角色'}, {'Id':'questname','Content':'测试任务'}],
            speaker_text=[{'Id':1,'Content':'name'}], speakers=[{'Id':10,'NameStringKey':1}],
            audio=[{'Id':'vo','FileName':'vo_1'}],
            flow=[{'Id':'test_1','States':[1]}],
            states=[{'StateKey':'test_1_1','Actions': json.dumps([{'Name':'ShowTalk','Params':{'TalkItems':[
                {'Id':1,'Type':'Talk','WhoId':10,'TidTalk':'a','PlotLineKey':''},
                {'Id':2,'Type':'Talk','WhoId':10,'TidTalk':'b','PlotLineKey':'vo'},
                {'Id':3,'Type':'Talk','WhoId':10,'TidTalk':'c','PlotLineKey':''}]}}])}],
            quests=[{'QuestId':100,'Data':{'TidName':'questname','RegionId':3}}],
            quest_nodes=[{'Key':'100_1','Data':{'FlowListName':'test','FlowId':1,'StateId':1}}])
        self.write_tables()
        self.catalog.import_public(self.folder)
        self.scope = ['quest:100']
        self.catalog.set_binding('10','测试角色','system:default',True)

    def write_tables(self):
        source = dict(SOURCE, files={})
        for key, value in self.tables.items():
            path = self.folder / (key + '.json')
            atomic_text(path, json.dumps(value, ensure_ascii=False))
            source['files'][key] = {'sha256':file_digest(path)}
        atomic_text(self.folder / 'source.json', json.dumps(source))

    def tearDown(self):
        self.catalog.close()
        self.temp.cleanup()

    def approve(self, text_id='a'):
        row = next(r for r in self.catalog.lines(self.scope) if r['text_id'] == text_id)
        self.catalog.review([row['id']], 'unvoiced', '人工测试证据：当前版本实机无原配音')
        return row['id']

    def project(self):
        self.approve()
        return self.catalog.prepare_project(self.scope, self.config)[0]

    def test_public_import_link_and_unknown_gate(self):
        lines = self.catalog.lines(self.scope)
        self.assertEqual(len(lines), 3)
        self.assertEqual({r['speaker'] for r in lines}, {'测试角色'})
        self.assertEqual({r['voice_status'] for r in lines}, {'unknown','voiced'})
        with self.assertRaises(ValueError):
            self.catalog.prepare_project(self.scope, self.config)

    def test_confirmation_scoped_to_version_and_keeps_branch(self):
        project = self.project()
        self.assertEqual(project.game_id, 'wuwa')
        self.assertEqual(project.rows[0].text, '你好，测试。')
        self.assertIn('branch', project.rows[0].evidence)
        self.catalog.validate_project(project)
        self.mount.write_text('::Mount::\nResource/3.7.18/test,22,h,h\n', encoding='utf-8')
        inventory(self.install, self.data)
        with self.assertRaises(ValueError):
            self.catalog.validate_project(project)

    def test_import_diff_preserves_review_only_for_unchanged_content(self):
        identity = self.approve()
        report = self.catalog.import_public(self.folder)
        self.assertEqual(report['unchanged'],3)
        self.assertEqual(self.catalog.db.execute('SELECT voice_status FROM lines WHERE id=?',(identity,)).fetchone()[0], 'unvoiced')
        self.tables['text'][0]['Content'] = '新的正文'
        self.write_tables()
        report = self.catalog.import_public(self.folder)
        self.assertEqual(report['changed'],1)
        self.assertEqual(self.catalog.db.execute('SELECT voice_status FROM lines WHERE id=?',(identity,)).fetchone()[0], 'unknown')

    def test_source_checksum_blocks_modified_download(self):
        atomic_text(self.folder / 'text.json', '[]')
        with self.assertRaises(ValueError):
            self.catalog.import_public(self.folder)

    def test_cancelled_import_rolls_back_whole_transaction(self):
        before = [dict(r) for r in self.catalog.lines(self.scope)]
        stop = threading.Event()
        stop.set()
        with self.assertRaises(InterruptedError):
            self.catalog.import_public(self.folder, stop)
        self.assertEqual(before, [dict(r) for r in self.catalog.lines(self.scope)])

    def test_legacy_project_is_nte_and_cross_game_import_rejected(self):
        project = GenerationProject.create(self.root / 'legacy')
        data = json.loads((project.directory/'project.json').read_text(encoding='utf-8'))
        data.pop('game_id')
        atomic_text(project.directory/'project.json', json.dumps(data))
        self.assertEqual(GenerationProject.load(project.directory).game_id,'nte')
        record = dict(game_id='wuwa',dialogue_id='1',body='不能导入异环',revision=1)
        with self.assertRaises(ValueError):
            project.receive_record(record, self.root)
        with self.assertRaises(ValueError):
            BatchGenerator(self.project(), {})

    def test_reference_transcript_requires_review(self):
        self.approve()
        self.catalog.set_binding('10','测试角色','character:角色',False)
        with self.assertRaises(ValueError):
            self.catalog.prepare_project(self.scope, self.config)

    def test_text_edit_cannot_bypass_voice_status_gate(self):
        project = self.project()
        row = project.rows[0]
        row.edit(text='任意新的台词')
        row.confirmed = True
        self.assertTrue(row.review_reason())
        self.assertTrue(wuwa_row_reason(row))

    def test_catalog_to_generation_resume_fake_backend(self):
        project = self.project()
        client = Client()
        runtime = Runtime(self.root, client)
        project.mode = 'system'
        options = dict(game_id='wuwa', catalog_root=str(self.data))
        runner = lambda: BatchGenerator(project, options, runtime_factory=lambda _: runtime)
        first = runner().run()
        self.assertEqual(first['completed'], 1)
        second = runner().run()
        self.assertEqual(second['resumed'],1)
        self.assertEqual(len(client.calls),1)
        loaded = GenerationProject.load(project.directory)
        self.assertEqual(loaded.game_id,'wuwa')
        library = PreparedLibrary(loaded)
        self.assertEqual(len(library.lines),1)
        self.assertIsNotNone(library.match('你好测试', '测试角色')[0])
        self.assertIsNone(library.match('原声测试', '测试角色')[0])

    def test_known_voiced_collision_prevents_playback(self):
        project = self.project()
        row = project.rows[0]
        from test_dialogue_generation import write_wav
        path = project.directory / 'audio/a.wav'
        write_wav(path)
        row.status='done'
        row.audio=dict(file='audio/a.wav',sha256=file_digest(path),input_key=row.input_key(project.mode),voice='默认')
        guards_path = project.directory / 'playback-guards.json'
        guards = json.loads(guards_path.read_text(encoding='utf-8'))
        guards['lines'].append(dict(id='other',text=row.text,speaker=row.speaker,voice_status='voiced'))
        atomic_text(guards_path,json.dumps(guards))
        library = PreparedLibrary(project)
        self.assertIsNone(library.resolve(row.text,row.speaker)[0])
        self.assertIsNone(library.resolve(row.text[:5],row.speaker,early=True)[0])

    def test_project_rejects_missing_or_cross_game_guard(self):
        project = self.project()
        atomic_text(project.directory/'playback-guards.json',json.dumps({'game_id':'nte','lines':[]}))
        with self.assertRaises(ValueError):
            PreparedLibrary(project)

    def test_unprepared_known_duplicate_waits_for_role(self):
        project = self.project()
        from test_dialogue_generation import write_wav
        row = project.rows[0]
        path=project.directory/'audio/a.wav'
        write_wav(path)
        row.status='done'
        row.audio=dict(file='audio/a.wav',sha256=file_digest(path),input_key=row.input_key(project.mode),voice='默认')
        path=project.directory/'playback-guards.json'
        guards=json.loads(path.read_text(encoding='utf-8'))
        guards['lines'].append(dict(id='other',text=row.text,speaker='另一个角色',voice_status='unknown'))
        atomic_text(path,json.dumps(guards))
        library=PreparedLibrary(project)
        self.assertIsNone(library.match(row.text)[0])
        self.assertIsNotNone(library.match(row.text,row.speaker)[0])

    def test_capture_import_is_unknown_and_survives_public_refresh(self):
        path=self.data/'records/session/dialogue.jsonl'
        atomic_text(path,json.dumps(dict(game_id='wuwa',dialogue_id='local-1',revision=1,
                                        speaker='本机NPC',body='本机新台词')))
        self.assertEqual(self.catalog.import_capture(path,'本机任务'),1)
        capture=self.catalog.db.execute("SELECT id FROM scopes WHERE kind='capture'").fetchone()[0]
        self.assertEqual(self.catalog.lines([capture])[0]['voice_status'],'unknown')
        self.catalog.import_public(self.folder)
        self.assertEqual(len(self.catalog.lines([capture])),1)

    def test_capture_import_rejects_cross_game_and_external_path(self):
        path=self.data/'records/session/dialogue.jsonl'
        atomic_text(path,json.dumps(dict(game_id='nte',dialogue_id='local-1',revision=1,body='其他游戏')))
        with self.assertRaises(ValueError):
            self.catalog.import_capture(path,'任务')
        with self.assertRaises(ValueError):
            self.catalog.import_capture(self.root/'outside.jsonl','任务')

    def test_local_voice_parameter_change_invalidates_signature(self):
        from dataclasses import replace
        from system_voices import preset_profile
        from wuwa_voices import local_voice_signature
        runtime=Runtime(self.root,Client())
        voice=preset_profile('default')
        self.assertNotEqual(local_voice_signature(runtime,voice),local_voice_signature(runtime,replace(voice,speed=1.2)))

    def test_unrelated_scope_change_invalidates_exclusion_index(self):
        project=self.project()
        self.tables['text'][1]['Content']='新的有原声台词'
        self.write_tables()
        self.catalog.import_public(self.folder)
        with self.assertRaises(ValueError):
            self.catalog.validate_project(project)

    def test_mixed_generation_uses_system_for_npc_without_starting_clone(self):
        from dataclasses import replace
        project=self.project()
        created=[]
        def factory(options):
            runtime=Runtime(self.root,Client())
            runtime.system_mode=options['mode']=='system'
            if not runtime.system_mode:
                runtime.config=replace(runtime.config,tts_backend='qwen3-tts')
            created.append(runtime)
            return runtime
        result=BatchGenerator(project,dict(game_id='wuwa',catalog_root=str(self.data)),runtime_factory=factory).run()
        self.assertEqual(result['completed'],1)
        self.assertEqual(sum(len(r.prepared) for r in created if not r.system_mode),0)
        self.assertEqual(sum(len(r.client.calls) for r in created if r.system_mode),1)


if __name__ == '__main__':
    unittest.main()
