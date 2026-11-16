import base64
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from . import bootstrap
from .organizer_core import Cancelled, Organizer, scan_log
from nte_catalog import build_catalog
from nte_log_stream import KEYS
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from dialogue_generation import GenerationProject


def encrypted(text):
    raw = (text+'|SPLIT|').encode('utf8')
    size = 16-len(raw)%16
    raw += bytes([size])*size
    cipher = Cipher(algorithms.AES(KEYS[0]),modes.ECB()).encryptor()
    return base64.b64encode(cipher.update(raw)+cipher.finalize())+b'\n'


def event(key, number=1, *, native=True):
    prefix = f'[2026.11.13-21.36.{number:02d}.100][+0800][ 92]LogHTGame: Warning: '
    body = f'[DlgVoice] InnerPlayVoice: event=Play_Vo_{key}, CurPlayingID=8' if native else f'[VoiceMsg] ShowCurMsg: type=0, MsgID={key}, hasVoice=0'
    return prefix+body


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = self.root/'catalog.sqlite'
        self.entries = [
            dict(namespace='ST_ActorName',key='NPC_09204_Name',text='丽妮丝'),
            dict(namespace='ST_ActorName',key='NPC_023_Name',text='明音凛'),
            dict(namespace='ST_Dialogue_CityMark_Chat',key='SD_NPC_09204_002',text='很荣幸为您服务。'),
            dict(namespace='ST_Dialogue_CityMark_Chat',key='SD_NPC_09204_004',text='「雷加利亚」是一款有着上百年历史的汽车品牌。'),
            dict(namespace='ST_Dialogue_CityMark_Chat',key='SD_NPC_023_002',text='很荣幸为您服务。'),
            dict(namespace='ST_SideQuest_QQ',key='WLSD_WJ1062_Player_001',text='下午好，明音。'),
            dict(namespace='ST_Dialogue_CityMark_Chat',key='SD_NPC_09204_006',text='<purple>你好，{PlayerName}。</>'),
            dict(namespace='ST_Dialogue_CityMark_Chat',key='SD_NPC_09204_007',text='{Gender}|gender(先生,女士)，欢迎。'),
            dict(namespace='ST_Dialogue_CityMark_Chat',key='SD_NPC_09204_008',text='需要{Pukacost}金币。'),
            dict(namespace='ST_Ui',key='UI_BUTTON',text='退出游戏'),
            dict(namespace='ST_MainDialogue',key='SAME_KEY',text='同编号第一句'),
            dict(namespace='ST_Dialogue_Side',key='SAME_KEY',text='同编号第二句'),
        ]
        build_catalog(self.entries,self.catalog,{})
        self.store = Organizer(self.root/'data',self.catalog,player_name='测试玩家')
        self.first = 'ST_Dialogue_CityMark_Chat/SD_NPC_09204_002'
        self.second = 'ST_Dialogue_CityMark_Chat/SD_NPC_09204_004'

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def log(self, name='HT.log', records=None):
        path = self.root/name
        records = records or [event('SD_NPC_09204_002'),event('SD_NPC_09204_004',2,native=False)]
        path.write_bytes(b'\xef\xbb\xbf'+b''.join(encrypted(text) for text in records))
        return path


class CatalogTests(Fixture):
    def test_filters_resolve_player_and_preserve_full_namespace_identity(self):
        rows,total = self.store.browse(dict(speaker='丽妮丝'))
        self.assertEqual(total,5)
        self.assertEqual(len(rows),5)
        player,total = self.store.browse(dict(speaker='测试玩家',keyword='WJ1062'))
        self.assertEqual(total,1)
        self.assertEqual(player[0]['speaker'],'测试玩家')

    def test_default_scope_and_full_resource_scope(self):
        self.assertEqual(self.store.browse(dict(keyword='退出游戏'))[1],0)
        self.assertEqual(self.store.browse(dict(keyword='退出游戏',dialogue_only=False))[1],1)
        self.assertIn('ST_Ui',self.store.groups(False))

    def test_like_metacharacters_and_sql_are_literal(self):
        for keyword in ('%','_',"' OR 1=1 --"):
            self.assertEqual(self.store.browse(dict(keyword=keyword))[1],0 if keyword!='_' else 9)
        self.assertEqual(self.store.stats()['resources'],12)

    def test_markup_name_and_gender_are_explicit(self):
        rows,_ = self.store.browse(dict(keyword='SD_NPC_09204_006'))
        self.assertEqual(rows[0]['text'],'你好，测试玩家。')
        self.assertIn('<purple>',rows[0]['original_text'])
        rows,_ = self.store.browse(dict(keyword='SD_NPC_09204_007'))
        self.assertEqual(rows[0]['issue'],'变量待填写')
        with Organizer(self.root/'other',self.catalog,player_name='测试玩家',gender=1) as female:
            rows,_ = female.browse(dict(keyword='SD_NPC_09204_007'))
            self.assertEqual(rows[0]['text'],'女士，欢迎。')

    def test_paging_has_no_dropped_or_duplicated_rows(self):
        all_rows,total = self.store.browse(all_rows=True)
        paged = []
        for page in range((total+2)//3):
            paged.extend(self.store.browse(page=page,page_size=3)[0])
        self.assertEqual([r['identity'] for r in all_rows],[r['identity'] for r in paged])


class HistoryTests(Fixture):
    def test_encrypted_history_includes_old_records_and_voice_evidence(self):
        result = self.store.import_logs([self.log()])
        self.assertEqual(result['added_events'],2)
        rows,total = self.store.browse(dict(source='history'))
        self.assertEqual(total,2)
        self.assertEqual([r['voice_status'] for r in rows],['voiced','silent'])
        self.assertEqual(len(self.store.provenance('SD_NPC_09204_002')),1)

    def test_reimport_duplicate_copy_and_grown_snapshot_deduplicate_events(self):
        path = self.log()
        self.store.import_logs([path])
        self.assertEqual(self.store.import_logs([path])['added_events'],0)
        copy = self.root/'copy.log'
        copy.write_bytes(path.read_bytes())
        self.assertEqual(self.store.import_logs([copy])['added_events'],0)
        with path.open('ab') as stream:
            stream.write(encrypted(event('WLSD_WJ1062_Player_001',3)))
        self.assertEqual(self.store.import_logs([path])['added_events'],1)
        self.assertEqual(self.store.stats()['events'],3)

    def test_unknown_and_ambiguous_keys_are_kept_for_review(self):
        self.store.import_logs([self.log(records=[event('MISSING'),event('SAME_KEY',2)])])
        self.assertEqual(self.store.browse(dict(source='history'))[1],0)
        unresolved = {r['text_key']:r['candidates'] for r in self.store.unresolved_keys()}
        self.assertEqual(unresolved,{'MISSING':0,'SAME_KEY':2})
        rows,_ = self.store.browse(dict(keyword='SAME_KEY'))
        self.assertTrue(all(r['voice_status']=='unknown' for r in rows))

    def test_generic_events_are_not_promoted_to_sentence_ids(self):
        generic = '[2026.11.13-21.36.03.100][+0800]LogHTGame: UUI_Dialogue::GetDlgSeqObj LevelSequenceActor = nullptr'
        result = self.store.import_logs([self.log(records=[generic])])
        self.assertEqual((result['keys'],result['generic']),(0,1))

    def test_malformed_line_and_partial_tail_do_not_discard_complete_records(self):
        path = self.log()
        with path.open('ab') as stream:
            stream.write(b'bad!\n'+encrypted(event('MISSING',3))[:25])
        result = self.store.import_logs([path])
        self.assertEqual((result['added_events'],result['invalid'],result['partial']),(2,1,1))

    def test_plaintext_and_unspecified_voice_status(self):
        path = self.root/'decoded.txt'
        path.write_text(event('SD_NPC_09204_002',native=False).replace(', hasVoice=0','')+'\n',encoding='utf8')
        self.store.import_logs([path])
        self.assertEqual(self.store.browse(dict(source='history'))[0][0]['voice_status'],'unknown')

    def test_cancelled_import_is_atomic(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            self.store.import_logs([self.log()],stop)
        self.assertEqual(self.store.stats()['events'],0)


class WorkTests(Fixture):
    def test_add_is_idempotent_and_equal_text_keeps_separate_characters(self):
        self.assertEqual(self.store.add(filters=dict(keyword='很荣幸'))['added'],2)
        rows,_ = self.store.work(all_rows=True)
        self.assertEqual({r['speaker'] for r in rows},{'丽妮丝','明音凛'})
        self.assertEqual(self.store.add(filters=dict(keyword='很荣幸'))['added'],0)

    def test_user_edits_survive_reimport_and_restart(self):
        self.store.add([self.first])
        self.store.edit(self.first,speaker='新角色',text='修改后的台词。',note='测试备注')
        self.store.add([self.first])
        with Organizer(self.root/'data',self.catalog) as reopened:
            row = reopened.work()[0][0]
            self.assertEqual((row['speaker'],row['text'],row['note']),('新角色','修改后的台词。','测试备注'))
            self.assertEqual(row['original_text'],'很荣幸为您服务。')

    def test_move_selection_and_remove(self):
        self.store.add([self.first,self.second])
        self.store.move(self.second,-1)
        self.assertEqual(self.store.work()[0][0]['identity'],self.second)
        self.store.select([self.first],False)
        self.assertEqual(self.store.stats()['selected'],1)
        self.store.remove([self.second])
        self.assertEqual(self.store.stats()['work'],1)

    def test_cancelled_add_does_not_publish_rows(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            self.store.add(filters={},stop=stop)
        self.assertEqual(self.store.stats()['work'],0)

    def test_exports_keep_ids_notes_unicode_and_selection(self):
        self.store.add([self.first,self.second])
        self.store.edit(self.first,speaker='丽妮丝',text='第一行\n第二行，“引号”。',note='备注')
        self.store.select([self.second],False)
        for format in ('csv','json','txt'):
            path = self.root/('export.'+format)
            result = self.store.export(path,format)
            self.assertEqual(result['rows'],1)
            self.assertIn('第一行',path.read_text('utf-8-sig'))
        content = json.loads((self.root/'export.json').read_text('utf8'))
        self.assertEqual(content['rows'][0]['identity'],self.first)
        with (self.root/'export.csv').open(encoding='utf-8-sig',newline='') as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[1][1],'第一行\n第二行，“引号”。')

    def test_csv_formula_escaping_and_cancel_preserves_destination(self):
        self.store.add([self.first])
        self.store.edit(self.first,speaker='=1+2',text='@formula',note='+cmd')
        path = self.root/'out.csv'
        self.store.export(path,'csv')
        with path.open(encoding='utf-8-sig',newline='') as stream:
            row = list(csv.reader(stream))[1]
        self.assertEqual(row[0],"'=1+2")
        before = path.read_bytes()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            self.store.export(path,'csv',stop)
        self.assertEqual(path.read_bytes(),before)

    def test_project_loads_in_existing_generator_and_preserves_evidence(self):
        self.store.add([self.first,'ST_Dialogue_CityMark_Chat/SD_NPC_09204_008'])
        result = self.store.create_project(self.root/'projects','测试项目','system')
        project = GenerationProject.load(result['path'])
        self.assertEqual((len(project.rows),result['held']),(2,1))
        self.assertEqual(project.rows[0].evidence['catalog_id'],self.first)
        self.assertEqual(project.rows[0].voice,'system:default')
        self.assertFalse(project.rows[0].review_reason())
        self.assertTrue(project.rows[1].review_reason())

    def test_empty_edited_line_is_held_in_project(self):
        self.store.add([self.first])
        self.store.edit(self.first,speaker='丽妮丝',text='',note='待补')
        result = self.store.create_project(self.root/'projects','空句')
        project = GenerationProject.load(result['path'])
        self.assertEqual(project.rows[0].review_reason(),'台词为空')


class UiTests(Fixture):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets as W
        cls.app = W.QApplication.instance() or W.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        super().setUp()
        from .organizer_ui import OrganizerWindow
        self.options = dict(catalog_path=str(self.catalog),player_name='测试玩家',gender=None,
                            log_directory=str(self.root),last_project='',asgsv_root=str(bootstrap.ASGSV))
        self.window = OrganizerWindow(self.options,self.root/'ui-data')

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        super().tearDown()

    def wait_job(self):
        deadline = time.monotonic()+5
        while self.window.worker and self.window.worker.isRunning() and time.monotonic()<deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.app.processEvents()
        self.assertFalse(self.window.worker and self.window.worker.isRunning())

    def test_search_add_edit_checkbox_and_export_through_controls(self):
        from PySide6 import QtCore
        self.window.keyword.setText('很荣幸')
        self.window.apply_filter()
        self.assertEqual(self.window.models[0].rowCount(),2)
        self.window.add_filtered.click()
        self.wait_job()
        self.window.tabs.setCurrentIndex(1)
        self.assertEqual(self.window.models[1].rowCount(),2)
        self.window.editor_text.setPlainText('编辑后自动保存。')
        self.window.save_editor()
        self.assertEqual(self.window.store.work()[0][0]['text'],'编辑后自动保存。')
        model = self.window.models[1]
        model.setData(model.index(1,0),QtCore.Qt.Unchecked,QtCore.Qt.CheckStateRole)
        self.assertEqual(self.window.store.stats()['selected'],1)
        path = self.root/'ui-export.json'
        with patch('nte_organizer.organizer_ui.W.QFileDialog.getSaveFileName',return_value=(str(path),'')):
            self.window.export('json')
        self.wait_job()
        self.assertEqual(len(json.loads(path.read_text('utf8'))['rows']),1)

    def test_history_import_and_source_filter_use_normal_worker(self):
        self.window.import_logs([self.log()])
        self.wait_job()
        self.window.source.setCurrentIndex(1)
        self.window.apply_filter()
        self.assertEqual(self.window.models[0].rowCount(),2)
        self.assertIn('新增 2',self.window.status.text())

    def test_close_flushes_pending_edits(self):
        self.window.store.add([self.first])
        self.window.refresh(1)
        self.window.tabs.setCurrentIndex(1)
        self.window.editor_note.setText('关闭前写入的备注')
        self.window.save_editor()
        with Organizer(self.root/'ui-data',self.catalog) as reopened:
            self.assertEqual(reopened.work()[0][0]['note'],'关闭前写入的备注')

    def test_compact_work_page_keeps_readable_rows_and_editing_accessible(self):
        self.window.store.add([self.first])
        self.window.tabs.setCurrentIndex(1)
        self.window.show()
        self.window.resize(1100, 680)
        self.app.processEvents()
        self.assertLessEqual(self.window.height(), 680)
        self.assertGreaterEqual(self.window.tables[1].viewport().height(), 114)
        self.assertTrue(self.window.editor_scroll.isVisible())
        self.window.editor_text.setPlainText('分割布局中的编辑仍可保存')
        self.window.save_editor()
        self.assertEqual(self.window.store.work()[0][0]['text'], '分割布局中的编辑仍可保存')

    def test_button_help_includes_filter_scope_and_disabled_controls(self):
        from PySide6 import QtWidgets as W
        for button in self.window.findChildren(W.QPushButton):
            if button.text():
                self.assertTrue(button.toolTip(), button.text())
        self.window.keyword.setText('很荣幸')
        self.window.apply_filter()
        self.assertIn('全部 2 条', self.window.add_filtered.toolTip())
        self.assertIn('所有分页', self.window.add_filtered.toolTip())


if __name__=='__main__':
    unittest.main()
