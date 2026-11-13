import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from dialogue_generation import GenerationProject
from wuwa_catalog import Catalog
from wuwa_progress import read_finished_quests


def cache_value(accounts):
    return {'___MetaType___': '___Map___', 'Content': [
        [account, {'___MetaType___': '___Set___', 'Content': ids}]
        for account, ids in accounts]}


class QuestProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install = self.root / 'read-only-game'
        self.path = self.install / 'Wuthering Waves Game/Client/Saved/LocalStorage/LocalStorage.db'
        self.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE LocalStorage(key TEXT PRIMARY KEY,value TEXT)')
        self.write_cache([('test-account', [100, 200])])
        self.catalog = Catalog(self.root / 'tool')
        with self.catalog.db:
            self.catalog.db.executemany('INSERT INTO scopes VALUES(?,?,?,?)', [
                ('quest:100', 'Quest A', '', 'quest'), ('quest:300', 'Quest B', '', 'quest'),
                ('flow:test', 'Flow', '', 'flow')])
            self.catalog.db.executemany('INSERT INTO scope_lines VALUES(?,?)', [
                ('quest:100', 'a'), ('quest:300', 'b'), ('quest:100', 'shared'),
                ('quest:300', 'shared'), ('flow:test', 'a'), ('flow:test', 'b'),
                ('flow:test', 'shared'), ('flow:test', 'unassociated')])

    def tearDown(self):
        self.catalog.close()
        self.temp.cleanup()

    def write_cache(self, accounts):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO LocalStorage VALUES(?,?)',
                       ('UserFinishedQuests', json.dumps(cache_value(accounts))))

    def scan(self):
        return self.catalog.progress.refresh(self.install)

    def test_read_only_exact_key_no_game_changes(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest(), self.path.stat().st_mtime_ns
        report = self.scan()
        after = hashlib.sha256(self.path.read_bytes()).hexdigest(), self.path.stat().st_mtime_ns
        self.assertEqual(before, after)
        self.assertEqual((report['recorded'], report['matched'], report['unmatched']), (2, 1, 1))
        self.assertNotIn('test-account', json.dumps(report))
        self.assertEqual(report['absent_status'], 'unknown')

    def test_absence_never_means_pending(self):
        self.scan()
        states = self.catalog.progress.line_states(['quest:300'])
        self.assertEqual(set(states.values()), {'unknown'})
        self.assertEqual(self.catalog.progress.allowed(['quest:300']), set())
        self.assertEqual(self.catalog.progress.allowed(['quest:300'], True), {'b', 'shared'})

    def test_completed_never_allowed_by_unknown_opt_in(self):
        self.scan()
        self.assertEqual(self.catalog.progress.allowed(['quest:100'], True), set())

    def test_flow_does_not_bypass_completed_quest(self):
        self.scan()
        states = self.catalog.progress.line_states(['flow:test'])
        self.assertEqual(states['a'], 'completed')
        self.assertEqual(states['shared'], 'unknown')
        self.assertEqual(states['unassociated'], 'unknown')
        self.assertNotIn('a', self.catalog.progress.allowed(['flow:test'], True))

    def test_shared_dialogue_retained_for_selected_pending_quest(self):
        self.scan()
        self.catalog.progress.review(['quest:300'], 'pending', 'Current task list review')
        self.assertEqual(self.catalog.progress.allowed(['quest:100', 'quest:300']), {'b', 'shared'})
        self.assertEqual(self.catalog.progress.allowed(['quest:100']), set())

    def test_cache_shrink_retains_positive_history(self):
        self.scan()
        self.write_cache([('test-account', [])])
        self.scan()
        self.assertEqual(self.catalog.progress.states()['100'], 'completed')

    def test_manual_repeatable_override_survives_refresh(self):
        self.scan()
        self.catalog.progress.review(['quest:100'], 'pending', 'Repeatable quest is active in task list')
        self.scan()
        self.assertEqual(self.catalog.progress.states()['100'], 'pending')
        self.assertEqual(self.catalog.db.execute('SELECT count(*) FROM quest_progress_audit').fetchone()[0], 1)

    def test_manual_unknown_remains_held(self):
        self.scan()
        self.catalog.progress.review(['quest:100'], 'unknown', 'Current task state not verified')
        self.scan()
        self.assertEqual(self.catalog.progress.states()['100'], 'unknown')
        self.assertEqual(self.catalog.progress.allowed(['quest:100']), set())

    def test_multiple_profiles_are_never_merged(self):
        self.write_cache([('first', [100]), ('second', [300])])
        with self.assertRaises(ValueError):
            self.scan()
        self.assertEqual(self.catalog.progress.account, '')
        profiles = read_finished_quests(self.install)['profiles']
        first = next(key for key, ids in profiles.items() if '100' in ids)
        self.catalog.progress.refresh(self.install, account=first)
        self.assertEqual(self.catalog.progress.states(), {'100': 'completed'})

    def test_changed_account_does_not_silently_rebind(self):
        self.scan()
        bound = self.catalog.progress.account
        self.write_cache([('different-account', [300])])
        with self.assertRaises(ValueError):
            self.scan()
        self.assertEqual(self.catalog.progress.account, bound)
        self.assertNotIn('300', self.catalog.progress.states())

    def test_malformed_records_never_partially_import(self):
        self.scan()
        for ids in ([True], [-1], ['100']):
            self.write_cache([('test-account', ids)])
            with self.assertRaises(ValueError):
                self.scan()
            self.assertEqual(self.catalog.progress.states()['100'], 'completed')

    def test_unknown_schema_fails_closed(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE LocalStorage SET value=?', ('{"finished":[100]}',))
        with self.assertRaises(ValueError):
            self.scan()
        self.assertEqual(self.catalog.progress.states(), {})

    def test_missing_cache_does_not_create_game_files(self):
        missing = self.root / 'absent-game'
        with self.assertRaises(ValueError):
            read_finished_quests(missing)
        self.assertFalse(missing.exists())


class CatalogProgressIntegrationTests(unittest.TestCase):
    def setUp(self):
        from test_wuwa import CatalogTests
        self.fixture = CatalogTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.catalog = self.fixture.catalog

    def test_unknown_blocks_by_default(self):
        self.fixture.approve()
        config = dict(self.fixture.config, allow_unknown_progress=False)
        with self.assertRaises(ValueError):
            self.catalog.prepare_project(self.fixture.scope, config)
        self.assertFalse((self.fixture.data / 'generation-projects').exists())

    def test_identical_selection_reopens_same_project(self):
        first = self.fixture.project()
        second = self.fixture.project()
        self.assertEqual(first.directory, second.directory)
        self.assertEqual(first.rows[0].id, second.rows[0].id)
        self.assertEqual(len(list((self.fixture.data / 'generation-projects').glob('*/project.json'))), 1)

    def test_changed_voice_creates_new_project(self):
        first = self.fixture.project()
        self.catalog.set_binding('10', 'Test', 'system:young_female', True)
        second = self.fixture.project()
        self.assertNotEqual(first.directory, second.directory)

    def test_generation_rechecks_completion_and_account(self):
        project = self.fixture.project()
        self.catalog.progress.validate_generation(project)
        with self.catalog.db:
            self.catalog.db.execute('INSERT INTO quest_progress VALUES(?,?,?,?,?,?)',
                                    ('', '100', 'completed', 'test', 'test', 'test'))
        with self.assertRaises(ValueError):
            self.catalog.progress.validate_generation(project)
        with self.catalog.db:
            self.catalog.db.execute('DELETE FROM quest_progress')
            self.catalog.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('progress_account', 'other'))
        with self.assertRaises(ValueError):
            self.catalog.progress.validate_generation(project)

    def test_hide_completed_count_agrees_with_rows(self):
        with self.catalog.db:
            self.catalog.db.execute('INSERT INTO quest_progress VALUES(?,?,?,?,?,?)',
                                    ('', '100', 'completed', 'test', 'test', 'test'))
        self.assertEqual(self.catalog.scope_count(kind='quest', hide_completed=True), 0)
        self.assertEqual(self.catalog.scopes(kind='quest', hide_completed=True), [])
        self.assertEqual(self.catalog.scope_count(kind='quest'), 1)
        self.assertEqual(self.catalog.scopes(kind='quest')[0]['progress_status'], 'completed')


if __name__ == '__main__':
    unittest.main()
