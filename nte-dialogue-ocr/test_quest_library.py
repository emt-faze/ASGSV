import json
import threading
import unittest
from unittest.mock import patch

from PySide6 import QtWidgets as W
import test_wuwa
from test_wuwa_breeze import wav
from quest_library import QuestLibrary, choose_cloud_voice, generate_missing, read_library


class LibraryTests(unittest.TestCase):
    def setUp(self):
        fixture = test_wuwa.CatalogTests('test_public_import_link_and_unknown_gate')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.root = fixture.data
        self.catalog = fixture.catalog
        self.scope = fixture.scope[0]
        self.ids = [fixture.approve(key) for key in ('a', 'c')]
        config = dict(fixture.config, dubbing=dict(game_id='wuwa', mode='character'))
        (self.root / 'config.json').write_text(json.dumps(config), encoding='utf8')
        with self.catalog.db:
            self.catalog.db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)', [
                ('wuwa_tts_provider', 'breezeblue'), ('breeze_daily_credits', '40'),
                ('require_quest_cast_review', '1')])
            self.catalog.db.executemany('INSERT INTO breeze_presets VALUES(?,?,?,?)', [
                ('voc_demo','示例男声','male','young'), ('voc_second','示例女声','female','young')])
        choose_cloud_voice(self.catalog, '10', 'voc_demo')

    def test_partial_complete_retry_and_voice_change(self):
        def generate(ids):
            return generate_missing(self.root, self.scope, ids, threading.Event(), lambda text: None)
        with patch('wuwa_breeze.api_key', return_value='test'), patch('wuwa_breeze.fetch_wav', return_value=wav()) as fetch:
            self.assertIn('完成 1 句', generate(self.ids[:1]))
            partial = read_library(self.root)[0][self.scope]
            self.assertEqual((partial.ready, len(partial.ids), partial.complete), (1, 2, False))
            self.assertIn('完成 1 句', generate(self.ids))
            self.assertTrue(read_library(self.root)[0][self.scope].complete)
            self.assertIn('无需', generate(self.ids))
            self.assertEqual(fetch.call_count, 2)
            choose_cloud_voice(self.catalog, '10', 'voc_second')
            self.assertEqual(read_library(self.root)[0][self.scope].ready, 0)

    def test_unknown_game_progress_keeps_existing_batch_policy(self):
        path = self.root / 'config.json'
        config = json.loads(path.read_text(encoding='utf8'))
        config['allow_unknown_progress'] = False
        path.write_text(json.dumps(config), encoding='utf8')
        with patch('wuwa_breeze.fetch_wav') as fetch:
            result = generate_missing(self.root, self.scope, self.ids, threading.Event(), lambda _: None)
            self.assertIn('完成 0 句', result)
            fetch.assert_not_called()

    def test_segment_search_and_missing_selection(self):
        app = W.QApplication.instance() or W.QApplication([])
        widget = QuestLibrary(self.catalog)
        self.addCleanup(widget.shutdown)
        result = read_library(self.root)
        widget.loaded(result)
        self.assertEqual([widget.tasks.horizontalHeaderItem(i).text() for i in range(3)], ['任务名称','句数','状态'])
        self.assertEqual([widget.lines.horizontalHeaderItem(i).text() for i in range(3)], ['角色','台词正文','音频状态'])
        self.assertIn('你好，测试。', [widget.lines.item(i,1).text() for i in range(2)])
        seen = []
        widget.generate_requested.connect(lambda scope, ids: seen.append((scope, ids)))
        widget.generate.click()
        self.assertEqual(seen, [(self.scope,self.ids)])
        widget.pills[1].click()
        self.assertEqual(widget.tasks.rowCount(), 0)
        widget.focus_scope(self.scope)
        self.assertEqual(widget.scope, self.scope)
        widget.search.setText('没有这个名字')
        self.assertFalse(widget.preview.isEnabled())
        self.assertFalse(widget.generate.isEnabled())
        widget.close()
