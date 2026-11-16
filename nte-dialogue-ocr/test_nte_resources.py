import json
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets as W
from nte_catalog import build_catalog, catalog_path, resource_stamp
from nte_install import GAME_DIRECTORY, discover_installation, launcher_hints, normalize_game_root
from nte_resources import catalog_status, prepare_catalog, update_catalog


ENTRIES = [dict(namespace='ST_Dialogue_CityMark_Chat', key='SD_NPC_09204_002', text='很荣幸为您服务。')]


def make_game(root):
    client = root / GAME_DIRECTORY
    executable = client / 'Binaries/Win64/HTGame.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'fixture')
    paks = client / 'Content/Paks'
    paks.mkdir(parents=True)
    (paks / 'content.pak').write_bytes(b'fixture resource')
    return root.resolve()


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.game = make_game(self.base / '游戏 安装位置')
        self.root = self.base / 'app'
        self.root.mkdir()
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(dict(nte_hybrid=dict(game_root=str(self.game)), calibrated=True)), 'utf-8')
        self.catalog = self.root / 'nte-data/catalog.sqlite'

    def tearDown(self):
        self.temp.cleanup()

    def test_install_directory_client_subdirectory_and_executable_are_normalized(self):
        for path in (self.game, self.game / 'Client', self.game / GAME_DIRECTORY,
                     self.game / GAME_DIRECTORY / 'Binaries/Win64/HTGame.exe'):
            with self.subTest(path=path):
                self.assertEqual(normalize_game_root(str(path)), self.game)

    def test_launcher_without_downloaded_client_and_wrong_folder_are_rejected(self):
        launcher = self.base / 'launcher/NTEGame.exe'
        launcher.parent.mkdir()
        launcher.touch()
        self.assertIsNone(normalize_game_root(launcher))
        self.assertIsNone(normalize_game_root(''))
        with self.assertRaisesRegex(ValueError, '完整'):
            discover_installation({}, game_root=launcher.parent)

    def test_saved_path_is_validated_before_use_and_bad_path_triggers_discovery(self):
        with patch('nte_install.installation_hints', return_value=[(self.game, '安装记录')]) as hints:
            found = discover_installation(dict(nte_hybrid=dict(game_root='Z:/missing')))
            self.assertEqual(found.root, self.game)
            hints.assert_called_once()
        with patch('nte_install.installation_hints') as hints:
            found = discover_installation(dict(nte_hybrid=dict(game_root=str(self.game))))
            hints.assert_not_called()
            self.assertEqual(found.root, self.game)

    def test_multiple_installations_need_explicit_selection_and_duplicates_collapse(self):
        second = make_game(self.base / '第二份安装')
        with patch('nte_install.installation_hints', return_value=[(self.game, 'A'), (second, 'B')]):
            with self.assertRaisesRegex(ValueError, '多个'):
                discover_installation({})
        with patch('nte_install.installation_hints', return_value=[(self.game, 'A'), (self.game/'Client', 'B')]):
            self.assertEqual(discover_installation({}).root, self.game)

    def test_missing_installation_has_actionable_message(self):
        with patch('nte_install.installation_hints', return_value=[]):
            with self.assertRaisesRegex(ValueError, '选择目录'):
                discover_installation({})

    def test_separate_launcher_can_read_only_installation_path_fields(self):
        launcher = self.base / 'launcher'
        settings = launcher / 'UserData/Config/Config.ini'
        settings.parent.mkdir(parents=True)
        settings.write_text('[Game]\ninstallPath='+str(self.game)+'\n[User]\nAccount=private\n', 'utf-8')
        self.assertEqual(list(launcher_hints(launcher/'NTEGame.exe')), [self.game])

    def test_registry_executable_command_and_icon_can_locate_game(self):
        executable = self.game / 'uninst.exe'
        for value in (f'"{executable}" /S', f'"{executable}",0'):
            self.assertEqual(normalize_game_root(value), self.game)

    def test_patch_packages_invalidate_resource_version(self):
        initial = resource_stamp(self.game)
        patch = self.game / GAME_DIRECTORY / 'Content/PatchPaks/update.pak'
        patch.parent.mkdir()
        patch.write_bytes(b'patch')
        self.assertNotEqual(resource_stamp(self.game), initial)

    def extract_fixture(self, game_root, output, progress):
        build_catalog(ENTRIES, output, dict(resource_stamp=resource_stamp(game_root)))
        return len(ENTRIES)

    def test_missing_catalog_extracts_then_current_catalog_is_reused(self):
        with patch('nte_resources.update_catalog', side_effect=self.extract_fixture) as extract:
            result = prepare_catalog(self.config)
            self.assertTrue(result['updated'])
            result = prepare_catalog(self.config)
            self.assertFalse(result['updated'])
            extract.assert_called_once()
        self.assertEqual(result['dialogue'], 1)
        config = json.loads(self.config.read_text('utf-8'))
        self.assertTrue(config['calibrated'])
        self.assertEqual(catalog_path(self.root,config), self.catalog)

    def test_failed_extraction_preserves_catalog_and_configuration(self):
        build_catalog(ENTRIES,self.catalog,dict(resource_stamp='old'))
        before = self.catalog.read_bytes(),self.config.read_bytes()
        with patch('nte_resources.update_catalog',side_effect=ValueError('fixture failure')):
            with self.assertRaisesRegex(ValueError,'fixture failure'):
                prepare_catalog(self.config)
        self.assertEqual(before, (self.catalog.read_bytes(),self.config.read_bytes()))

    def test_force_refresh_and_shared_custom_path(self):
        custom = self.root / 'custom/catalog.sqlite'
        with patch('nte_resources.update_catalog',side_effect=self.extract_fixture) as extract:
            prepare_catalog(self.config,custom)
            prepare_catalog(self.config,force=True)
            self.assertEqual(extract.call_count,2)
        self.assertEqual(catalog_path(self.root),custom)
        self.assertFalse(self.catalog.exists())

    def test_active_live_lease_prevents_catalog_replacement(self):
        self.catalog.parent.mkdir()
        lease = QtCore.QLockFile(str(self.catalog.parent/'live.lock'))
        self.assertTrue(lease.tryLock(0))
        try:
            with self.assertRaisesRegex(ValueError, '停止异环实时配音'):
                update_catalog(self.game,self.catalog)
        finally:
            lease.unlock()

    def test_legacy_organizer_copy_migrates_to_common_catalog(self):
        from nte_organizer import bootstrap
        organizer = self.root/'nte_organizer'
        organizer.mkdir()
        (organizer/'settings.json').write_text(json.dumps(dict(catalog_path=str(organizer/'data/catalog.sqlite'))),'utf-8')
        with patch.object(bootstrap,'ROOT',organizer),patch.object(bootstrap,'ASGSV',self.root):
            self.assertEqual(bootstrap.load_settings()['catalog_path'],str(self.catalog))

    def test_existing_cli_keeps_single_json_entry_count_response(self):
        from nte_resources import main
        output = io.StringIO()
        with patch('sys.argv',['nte_resources.py','--game-root',str(self.game)]), \
                patch('nte_resources.update_catalog',return_value=123), redirect_stdout(output):
            self.assertEqual(main(),0)
        self.assertEqual(json.loads(output.getvalue()),dict(entries=123))


class ExtractionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = W.QApplication.instance() or W.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        from nte_organizer.organizer_ui import OrganizerWindow
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = self.root/'catalog.sqlite'
        self.options = dict(catalog_path=str(self.catalog),player_name='玩家',gender=None,
                            log_directory=str(self.root),last_project='')
        self.window = OrganizerWindow(self.options,self.root/'organizer')

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_update(self):
        deadline = time.monotonic()+15
        while self.window.busy() and time.monotonic()<deadline:
            self.app.processEvents()
            time.sleep(.02)
        self.assertFalse(self.window.busy())

    def test_empty_catalog_page_can_open_and_be_populated_through_extraction(self):
        self.assertEqual(self.window.store.stats()['resources'],0)
        module_root = str(Path(__file__).resolve().parent)
        script = '\n'.join([
            'import sys,json',
            f'sys.path.insert(0,{module_root!r})',
            'from nte_catalog import build_catalog',
            f'build_catalog({ENTRIES!r},{str(self.catalog)!r},{{}})',
            "print(json.dumps({'progress':'正在整理中文台词'}),flush=True)",
            f"print(json.dumps({{'result':dict(game_root={str(self.root)!r},source='fixture',catalog_path={str(self.catalog)!r},entries=1,dialogue=1,updated=True)}}),flush=True)",
        ])
        (self.root/'nte_resources.py').write_text(script,'utf-8')
        with patch('nte_organizer.organizer_ui.ASGSV',self.root):
            self.window.extract_button.click()
            self.wait_update()
        self.assertEqual(self.window.store.stats()['resources'],1)
        self.assertIn('提取完成',self.window.status.text())
        self.assertEqual(self.window.game_directory.text(),str(self.root))

    def test_extraction_error_and_cancel_leave_browsing_available(self):
        script = "import json,sys\nprint(json.dumps({'error':'游戏客户端未下载完成'}),flush=True)\nsys.exit(1)\n"
        (self.root/'nte_resources.py').write_text(script,'utf-8')
        with patch('nte_organizer.organizer_ui.ASGSV',self.root):
            self.window.update_catalog()
            self.wait_update()
            self.assertIn('未下载完成',self.window.status.text())
            (self.root/'nte_resources.py').write_text('import time\ntime.sleep(10)\n','utf-8')
            self.window.update_catalog()
            self.window.cancel_job()
            self.wait_update()
        self.assertIn('已取消',self.window.status.text())
        self.assertTrue(self.window.body.isEnabled())
        self.assertEqual(self.window.store.stats()['resources'],0)


if __name__ == '__main__':
    unittest.main()
