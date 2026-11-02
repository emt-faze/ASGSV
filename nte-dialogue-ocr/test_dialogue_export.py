import copy
import csv
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from dialogue_export import DialogueExportTask, ExportCancelled
from dialogue_generation import GenerationProject
import test_dialogue_generation as fixtures


class DialogueExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = GenerationProject.create(self.root/'projects')
        self.first = self.project.add_dialogue('第一句，含“引号”。\n第二行。', '黑羽')
        self.second = self.project.add_dialogue('尚未核对的台词', '伊洛伊')
        self.second.confirmed = False
        self.third = self.project.add_dialogue('……', '')
        self.first.selected = self.third.selected = False

    def tearDown(self):
        self.temp.cleanup()

    def audio(self, row):
        path = self.project.directory/'audio'/f'{row.id}.wav'
        fixtures.write_wav(path)
        row.status = 'done'
        row.audio = dict(file=path.relative_to(self.project.directory).as_posix(),
                         input_key=row.input_key(self.project.mode), voice='残虹',
                         sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return path

    def run_export(self, format='txt', **kwargs):
        target = self.root/f'台词.{format}'
        result = DialogueExportTask(self.project, target, format, **kwargs).run()
        return target, result

    def csv_rows(self, data):
        return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'), newline='')))

    def test_text_preserves_order_unicode_multiline_and_review_marks(self):
        self.project.save()
        before = {p: p.read_bytes() for p in self.project.directory.iterdir() if p.is_file()}
        rows_before = copy.deepcopy(self.project.rows)
        with patch.object(self.project, 'audio_path', side_effect=AssertionError('TXT needs no audio')):
            path, result = self.run_export()
        self.assertEqual(result['rows'], 3)
        data = path.read_bytes()
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(data.decode('utf-8-sig'),
            '1. 黑羽：第一句，含“引号”。\n第二行。\n\n'
            '2. 伊洛伊：【文字待核对】尚未核对的台词\n\n3. 未指定角色：……\n')
        self.assertEqual(self.project.rows, rows_before)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_selected_uses_checkboxes_and_keeps_original_sequence_number(self):
        path, report = self.run_export(selected_only=True)
        self.assertEqual(report['rows'], 1)
        self.assertEqual(path.read_bytes().decode('utf-8-sig'), '2. 伊洛伊：【文字待核对】尚未核对的台词\n')

    def test_deleted_rows_are_excluded(self):
        self.project.delete_rows([self.second.id])
        path, report = self.run_export()
        self.assertEqual(report['rows'], 2)
        self.assertNotIn('尚未核对的台词', path.read_bytes().decode('utf-8-sig'))

    def test_snapshot_retains_original_text_selection_and_audio_metadata(self):
        path = self.root/'snapshot.txt'
        task = DialogueExportTask(self.project, path, selected_only=True)
        self.second.edit(text='以后修改的台词')
        self.second.selected = False
        self.project.rows.reverse()
        task.run()
        self.assertIn('2. 伊洛伊：【文字待核对】尚未核对的台词', path.read_bytes().decode('utf-8-sig'))

    def test_empty_scope_does_not_replace_existing_export(self):
        for row in self.project.rows:
            row.selected = False
        path = self.root/'previous.txt'
        path.write_bytes(b'keep this')
        with self.assertRaisesRegex(ValueError, '没有可导出'):
            DialogueExportTask(self.project, path, selected_only=True).run()
        self.assertEqual(path.read_bytes(), b'keep this')
        self.assertFalse(list(self.root.glob('.dialogue-export-*')))

    def test_csv_roundtrips_quoted_multiline_text_and_voice(self):
        self.first.text = '他说："你好,朋友"\r\n第二行\n第三行'
        self.audio(self.first)
        path, report = self.run_export('csv')
        self.assertEqual(report['audio'], 1)
        self.assertEqual(report['missing_audio'], 1)
        rows = self.csv_rows(path.read_bytes())
        self.assertEqual(rows[0]['台词'], self.first.text)
        self.assertEqual(rows[0]['角色'], self.first.speaker)
        self.assertEqual(rows[0]['声线'], '残虹')
        self.assertEqual(rows[0]['配音文件'], self.first.audio['file'])
        self.assertEqual(rows[1]['文字核对'], self.second.review_reason())
        self.assertEqual(rows[2]['生成状态'], '无需配音')

    def test_csv_formula_like_text_is_not_interpreted_as_a_formula(self):
        for value in ('=1+1', '+SUM(1,2)', '-1+1', '@SUM(1,2)', '  =1+1', '\tplain', '\rplain', '\nplain'):
            with self.subTest(value=value):
                self.first.text = self.first.speaker = value
                self.first.voice = 'character:'+value
                path, _ = self.run_export('csv')
                row = self.csv_rows(path.read_bytes())[0]
                self.assertEqual(row['角色'], "'"+value)
                self.assertEqual(row['台词'], "'"+value)
                self.assertEqual(row['声线'], "'"+value)

    def test_zip_contains_exact_valid_audio_and_retains_rows_without_audio(self):
        self.first.speaker = '../黑羽\\:*?'
        audio = self.audio(self.first)
        before = audio.read_bytes()
        path, report = self.run_export('zip')
        self.assertEqual((report['rows'], report['audio'], report['missing_audio']), (3, 1, 1))
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            rows = self.csv_rows(archive.read('台词清单.csv'))
            name = rows[0]['配音文件']
            self.assertTrue(name.startswith('audio/0001_'))
            self.assertEqual(len(Path(name).parts), 2)
            self.assertNotIn('..', name)
            self.assertEqual(archive.read(name), before)
            self.assertEqual(rows[1]['配音文件'], '')
            self.assertIn(self.second.text, archive.read('台词.txt').decode('utf-8-sig'))
            self.assertIn('1 条暂无可用配音', archive.read('导出说明.txt').decode('utf-8-sig'))
        self.assertEqual(audio.read_bytes(), before)

    def test_zip_skips_stale_missing_broken_and_hash_mismatched_audio(self):
        for fault in ('stale', 'missing', 'broken', 'hash'):
            with self.subTest(fault=fault):
                audio = self.audio(self.first)
                if fault == 'stale':
                    self.first.audio['input_key'] = 'old input'
                elif fault == 'missing':
                    audio.unlink()
                elif fault == 'broken':
                    audio.write_bytes(b'broken WAV')
                else:
                    self.first.audio['sha256'] = 'wrong digest'
                path, report = self.run_export('zip')
                self.assertEqual(report['audio'], 0)
                with zipfile.ZipFile(path) as archive:
                    self.assertFalse(any(n.endswith('.wav') for n in archive.namelist()))
                    rows = self.csv_rows(archive.read('台词清单.csv'))
                    self.assertIn('音频不可用', rows[0]['生成状态'])
                    self.assertEqual(rows[0]['台词'], self.first.text)

    def test_cancel_and_failed_publish_keep_existing_destination(self):
        for format in ('txt', 'csv', 'zip'):
            with self.subTest(format=format):
                path = self.root/('previous.'+format)
                path.write_bytes(b'previous export')
                task = DialogueExportTask(self.project, path, format)
                task.cancel()
                with self.assertRaises(ExportCancelled):
                    task.run()
                self.assertEqual(path.read_bytes(), b'previous export')
                with patch('dialogue_export.os.replace', side_effect=PermissionError('模拟文件正被占用')):
                    with self.assertRaises(PermissionError):
                        DialogueExportTask(self.project, path, format).run()
                self.assertEqual(path.read_bytes(), b'previous export')
                self.assertFalse(list(self.root.glob('.dialogue-export-*')))

    def test_cancel_during_packaging_removes_partial_export(self):
        self.audio(self.first)
        path = self.root/'previous.zip'
        path.write_bytes(b'previous export')
        task = DialogueExportTask(self.project, path, 'zip')
        task.progress = lambda message: task.cancel() if '正在打包' in message else None
        with self.assertRaises(ExportCancelled):
            task.run()
        self.assertEqual(path.read_bytes(), b'previous export')
        self.assertFalse(list(self.root.glob('.dialogue-export-*')))

    def test_audio_changed_during_packaging_does_not_publish(self):
        audio = self.audio(self.first)
        path = self.root/'changed.zip'
        task = DialogueExportTask(self.project, path, 'zip')
        task.progress = lambda message: audio.write_bytes(b'changed audio') if '正在打包' in message else None
        with self.assertRaisesRegex(ValueError, '配音文件发生变化'):
            task.run()
        self.assertFalse(path.exists())
        self.assertFalse(list(self.root.glob('.dialogue-export-*')))

    def test_original_project_files_and_mismatched_extensions_are_protected(self):
        self.audio(self.first)
        self.project.save()
        for path, format in ((self.project.directory/'manifest.csv', 'csv'),
                             (self.project.directory/'audio/test.zip', 'zip'),
                             (self.root/'wrong.csv', 'txt')):
            with self.subTest(path=path):
                before = path.read_bytes() if path.exists() else None
                with self.assertRaises(ValueError):
                    DialogueExportTask(self.project, path, format).run()
                self.assertEqual(path.read_bytes() if path.exists() else None, before)


if __name__ == '__main__':
    unittest.main()
