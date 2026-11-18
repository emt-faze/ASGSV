import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtWidgets as W
from dialogue_generation import GenerationProject
from nte_home import NteHomePanel
from test_prepared_playback import completed_line
from wuwa_ui import GameHome


class NteHomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = W.QApplication.instance() or W.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'nte'
        self.root.mkdir()
        data = self.base / 'data'
        data.mkdir()
        (data / 'config.json').write_text('{}', encoding='utf-8')
        self.options = {'game_id': 'nte', 'mode': 'system',
            'code_root': str(Path(__file__).resolve().parent.parent / 'wuwa-dubber'),
            'data_root': str(data)}
        config = {'dubbing': self.options, 'launcher_path': ''}
        (self.root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        model_root = self.root / 'assets/ocr-v6'
        model_root.mkdir(parents=True)
        (model_root / 'models.json').write_text('{}', encoding='utf-8')
        self.project = GenerationProject.create(self.root / 'generation-projects', mode='character')
        self.row, self.audio = completed_line(self.project, '测试旧版台词')
        self.project.add_dialogue('尚未生成的台词', '测试角色')
        self.project.save()
        project_file = self.project.directory / 'project.json'
        legacy = json.loads(project_file.read_text(encoding='utf-8'))
        legacy.pop('game_id')
        project_file.write_text(json.dumps(legacy), encoding='utf-8')
        (self.root / 'generation-projects/last-project.txt').write_text(str(self.project.directory), encoding='utf-8')
        self.panel = NteHomePanel(self.root)

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_legacy_project_audio_is_available_and_stale_audio_is_held(self):
        self.assertEqual(self.panel.projects.currentData(), str(self.project.directory))
        self.assertIn('1 条完整配音', self.panel.project_summary.text())
        self.assertTrue(self.panel.play_button.isEnabled())
        self.audio.write_bytes(b'broken wav')
        self.panel.update_project()
        self.assertFalse(self.panel.play_button.isEnabled())

    def test_other_game_projects_are_excluded(self):
        GenerationProject.create(self.root / 'generation-projects', game_id='wuwa')
        self.panel.refresh()
        self.assertEqual(self.panel.projects.count(), 1)

    def test_project_action_uses_selected_project(self):
        requests = []
        self.panel.project_requested.connect(lambda path, action: requests.append((path, action)))
        self.panel.play_button.click()
        self.assertEqual(requests, [(self.project.directory, 'playback')])

    def test_realtime_entry_uses_embedded_dashboard_and_settings_avoid_duplicate_launch(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(self.panel.live,'start_live') as connect:
            self.panel.open_live(dubbing=True)
            connect.assert_called_once()
        self.assertEqual(self.panel.workspace_tabs.currentIndex(),0)
        self.assertEqual(self.panel.live.project_path,self.project.directory)
        with patch('nte_home.subprocess.Popen', return_value=process) as start:
            self.panel.open_live(dubbing=False)
            self.panel.open_live(dubbing=False)
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0][1:], ['-s', str(self.root / 'app.py')])
        self.assertEqual(start.call_args.kwargs['cwd'], self.root)

    def test_library_embeds_organizer_and_routes_projects_inside_workbench(self):
        from nte_catalog import build_catalog
        from nte_organizer.organizer_ui import OrganizerWindow
        catalog = self.base/'catalog.sqlite'
        build_catalog([dict(namespace='ST_MainDialogue',key='SAMPLE',text='测试台词')],catalog,{})
        options = dict(catalog_path=str(catalog),player_name='玩家',gender=None,log_directory=str(self.base),
                       last_project=str(self.project.directory))
        def create(*,parent,project_opener):
            return OrganizerWindow(options,self.base/'organizer',parent,project_opener=project_opener)
        with patch('nte_organizer.organizer_ui.OrganizerWindow',side_effect=create):
            self.panel.workspace_tabs.setCurrentIndex(1)
        self.assertIsNotNone(self.panel.organizer)
        self.assertFalse(self.panel.organizer.isWindow())
        requests = []
        self.panel.project_requested.connect(lambda path,action:requests.append((path,action)))
        with patch('nte_organizer.organizer_ui.subprocess.Popen') as launch:
            self.panel.organizer.open_in_generator()
            launch.assert_not_called()
        self.assertEqual(requests,[(self.project.directory,'prepare')])

    def test_project_open_waits_for_live_session_to_stop(self):
        requests = []
        self.panel.project_requested.connect(lambda path,action:requests.append((path,action)))
        with patch.object(self.panel.live,'running',return_value=True),patch.object(self.panel.live,'stop_live') as stop:
            self.panel.request_project('prepare')
            stop.assert_called_once()
            self.assertFalse(requests)
        self.panel.work_stopped()
        self.assertEqual(requests,[(self.project.directory,'prepare')])

    def test_manual_extraction_waits_for_live_stop_and_auto_check_can_wait(self):
        organizer = Mock()
        organizer.busy.return_value = False
        self.panel.organizer = organizer
        with patch.object(self.panel.live,'running',return_value=True),patch.object(self.panel.live,'stop_live') as stop:
            self.panel.prepare_catalog(None,False)
            stop.assert_not_called()
            self.panel.prepare_catalog('D:/game',True)
            stop.assert_called_once()
            organizer.start_catalog_update.assert_not_called()
        self.panel.work_stopped()
        organizer.start_catalog_update.assert_called_once_with('D:/game',True)

    def test_stop_cancels_pending_extraction_and_busy_prevents_live_start(self):
        self.panel.pending_catalog = (None,False)
        self.panel.stop()
        self.assertIsNone(self.panel.pending_catalog)
        self.panel.organizer_busy_changed(True)
        with patch('nte_live_panel.LiveWorker') as worker:
            self.panel.live.start_live()
            worker.assert_not_called()
        self.panel.organizer_busy_changed(False)
        self.assertTrue(self.panel.live.start.isEnabled())

    def test_explicit_game_and_project_picker_route_to_nte(self):
        wuwa = self.base / 'wuwa'
        wuwa.mkdir()
        (wuwa / 'config.json').write_text(json.dumps({'install_root': '', 'live_auto_start': False,
            'dubbing': dict(self.options, game_id='wuwa')}), encoding='utf-8')
        preference = self.base / 'entry.json'
        preference.write_text('{"game_id":"wuwa"}', encoding='utf-8')
        with patch('wuwa_ui.CODE_ROOT', self.root):
            home = GameHome(root=wuwa, preference=preference, game_id='nte')
            try:
                self.assertEqual(home.game.currentData(), 'nte')
                self.assertIn('异环', home.windowTitle())
                self.assertTrue(home.settings.isEnabled())
                home.request_workspace('wuwa', library=True)
                self.assertTrue(home.nte.isHidden())
                self.assertIs(home.workspace_tabs.currentWidget(), home.task_library)
                home.request_workspace('nte', library=True)
                self.assertTrue(home.wuwa.isHidden())
                self.assertIs(home.nte.workspace_tabs.currentWidget(), home.nte.task_library)
                home.advanced_toggle.setChecked(True)
                self.assertFalse(home.nte.advanced.isHidden())
                self.assertEqual(json.loads(preference.read_text())['game_id'], 'nte')
                chosen = self.project.directory / 'project.json'
                with patch.object(W.QFileDialog, 'getOpenFileName', return_value=(str(chosen), '')):
                    with patch.object(home, 'open_nte') as open_project:
                        home.open_existing()
                        open_project.assert_called_once_with(chosen)
                with patch.object(home.nte, 'open_live') as open_settings:
                    home.open_settings()
                    open_settings.assert_called_once_with(dubbing=False)
            finally:
                home.close()
                home.deleteLater()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
