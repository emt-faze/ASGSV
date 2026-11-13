import json
import shutil
import unittest

import test_wuwa
from wuwa_catalog import ordinary_branch
from wuwa_resources import COMMIT


class BranchPolicyTests(unittest.TestCase):
    def test_classification_excludes_roles_activity_and_unclassified(self):
        data=dict(Type=2,DistributeType='System',FinishActions=[{'Params':{'QuestType':'Branch'}}])
        self.assertTrue(ordinary_branch(data))
        for change in (dict(Type=3),dict(Type=100),dict(DistributeType='Activity'),
                       dict(ActivityId=5),dict(FinishActions=[])):
            self.assertFalse(ordinary_branch(dict(data,**change)))

    def test_live_project_and_cache_use_rule_without_changing_reviews(self):
        fixture=test_wuwa.CatalogTests('test_public_import_link_and_unknown_gate')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.tables['quests'][0]['Data'].update(Type=2,DistributeType='System',
            FinishActions=[{'Params':{'QuestType':'Branch'}}])
        fixture.write_tables()
        shutil.copytree(fixture.folder,fixture.data/'sources'/COMMIT)
        config=fixture.config
        fixture.catalog.set_branch_rule(True)
        (fixture.data/'config.json').write_text(json.dumps(config),encoding='utf-8')
        catalog=fixture.catalog
        a,b,c=catalog.lines(fixture.scope)
        version=catalog.local_version()
        self.assertFalse(catalog.voice_basis(a,version))
        self.assertTrue(catalog.voice_basis(a,version,allow_branch=True))
        self.assertFalse(catalog.voice_basis(b,version,allow_branch=True))
        project,_=catalog.prepare_project(fixture.scope,config,{a['id']},live_line=True)
        catalog.validate_project(project)
        self.assertEqual(a['voice_status'],'unknown')
        catalog.review([c['id']],'unknown','人工复查有冲突，需要保留待核对')
        c=catalog.db.execute('SELECT * FROM lines WHERE id=?',(c['id'],)).fetchone()
        self.assertFalse(catalog.voice_basis(c,version,allow_branch=True))
        with catalog.db:
            catalog.db.execute('INSERT INTO scope_lines VALUES(?,?)',('quest:999',a['id']))
        self.assertFalse(catalog.voice_basis(a,version,allow_branch=True))
        with self.assertRaises(ValueError):
            catalog.validate_project(project)
