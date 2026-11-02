"""Export a read-only snapshot of dialogue text and existing, verified audio."""
import copy
import csv
import hashlib
import io
import os
from pathlib import Path
import re
import tempfile
import threading
import zipfile


FORMATS = {'txt': '纯文字 TXT', 'csv': '台词表格 CSV（Excel 可打开）', 'zip': '台词与配音 ZIP'}
STATES = {'pending': '待生成', 'generating': '生成中', 'done': '已生成', 'failed': '生成失败',
          'cancelled': '已停止', 'silent': '无需配音'}


class ExportCancelled(Exception):
    pass


def export_rows(project, selected_only=False):
    return [(index, copy.deepcopy(row)) for index, row in enumerate(project.rows, 1)
            if not selected_only or row.selected]


def _cell(value):
    value = str(value)
    # Excel should treat imported dialogue as text, including a formula-like line.
    return "'"+value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


def _voice(row):
    if row.voice == 'auto':
        return row.audio.get('voice') or '按角色自动选择'
    return row.voice.split(':', 1)[-1]


def _text(rows):
    output = []
    for number, row in rows:
        review = '【文字待核对】' if row.review_reason() else ''
        output.append(f'{number}. {row.speaker or "未指定角色"}：{review}{row.text}')
    return '\n\n'.join(output)+'\n'


def _csv(rows, audio_names):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['序号', '角色', '台词', '文字核对', '声线', '生成状态', '配音文件'])
    for number, row in rows:
        state = '无需配音' if row.punctuation_only else STATES.get(row.status, row.status)
        if row.status == 'done' and number not in audio_names and not row.punctuation_only:
            state = '音频不可用，需重新生成'
        writer.writerow([number, _cell(row.speaker), _cell(row.text), row.review_reason() or '已核对',
                         _cell(_voice(row)), state, audio_names.get(number, '')])
    return stream.getvalue()


class DialogueExportTask:
    def __init__(self, project, path, format='txt', *, selected_only=False, progress=lambda message: None):
        if format not in FORMATS:
            raise ValueError('请选择 TXT、CSV 或 ZIP 格式')
        self.project, self.format, self.progress = project, format, progress
        self.rows = export_rows(project, selected_only)
        self.path = Path(path).expanduser().resolve()
        self.stop, self.lock = threading.Event(), threading.RLock()

    def cancel(self):
        with self.lock:
            self.stop.set()

    def check_cancel(self):
        if self.stop.is_set():
            raise ExportCancelled('已取消导出，原文件保持不变')

    def validate_target(self):
        if not self.rows:
            raise ValueError('没有可导出的台词；请添加台词或勾选需要导出的条目')
        project = self.project.directory.resolve()
        if (self.path in {project/name for name in ('project.json', 'manifest.json', 'manifest.csv', 'project.lock')}
                or self.path.is_relative_to(project/'audio')):
            raise ValueError('请选择其他保存位置，不能覆盖项目资料或原有配音')
        if self.path.suffix.lower() != '.'+self.format:
            raise ValueError('文件扩展名与导出格式不一致')

    def run(self):
        self.validate_target()
        self.check_cancel()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.dialogue-export-', suffix='.tmp', dir=self.path.parent)
        os.close(fd)
        staging = Path(name)
        try:
            audio_paths, audio_names = {}, {}
            if self.format != 'txt':
                for number, row in self.rows:
                    self.check_cancel()
                    self.progress(f'正在检查第 {number} 条台词的配音…')
                    path = self.project.audio_path(row)
                    if path:
                        audio_paths[number] = path
                        if self.format == 'zip':
                            speaker = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', row.speaker).strip(' .')[:32] or '未指定角色'
                            audio_names[number] = f'audio/{number:04d}_{speaker}.wav'
                        else:
                            audio_names[number] = row.audio['file']
            text = _text(self.rows).encode('utf-8-sig')
            if self.format == 'txt':
                staging.write_bytes(text)
            elif self.format == 'csv':
                staging.write_bytes(_csv(self.rows, audio_names).encode('utf-8-sig'))
            else:
                with zipfile.ZipFile(staging, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
                    archive.writestr('台词.txt', text)
                    archive.writestr('台词清单.csv', _csv(self.rows, audio_names).encode('utf-8-sig'))
                    for number, row in self.rows:
                        self.check_cancel()
                        if number not in audio_paths:
                            continue
                        self.progress(f'正在打包第 {number} 条配音：{row.speaker or "未指定角色"}')
                        checksum = hashlib.sha256()
                        with audio_paths[number].open('rb') as source, archive.open(audio_names[number], 'w') as target:
                            while chunk := source.read(256*1024):
                                self.check_cancel()
                                checksum.update(chunk)
                                target.write(chunk)
                        if checksum.hexdigest() != row.audio.get('sha256'):
                            raise ValueError('导出期间配音文件发生变化，请停止生成后重新导出')
                    missing = sum(not row.punctuation_only and number not in audio_paths for number, row in self.rows)
                    note = (f'共 {len(self.rows)} 条台词，已包含 {len(audio_paths)} 条配音，{missing} 条暂无可用配音。\n'
                            '序号与原项目列表一致；文字待核对的条目已标注。\n'
                            '配音文件对应关系见“台词清单.csv”；无需重新生成即可播放包内 WAV。\n'
                            '缺少、过期或损坏的配音不打包，文字仍保留。\n')
                    archive.writestr('导出说明.txt', note.encode('utf-8-sig'))
            with self.lock:
                self.check_cancel()
                os.replace(staging, self.path)
            return dict(path=str(self.path), format=self.format, rows=len(self.rows), audio=len(audio_paths),
                        missing_audio=sum(not row.punctuation_only and number not in audio_paths for number, row in self.rows)
                        if self.format != 'txt' else None)
        finally:
            staging.unlink(missing_ok=True)
