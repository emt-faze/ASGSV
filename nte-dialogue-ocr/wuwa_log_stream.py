"""Follow a verified game process's Client.log and reconstruct its visible subtitle."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
import time

from ww_log_decoder.decoder import LOG_MAGIC, decrypt_client_log

# The upstream byte transform is independent of position. Applying its lookup
# table lets us restore a rotated 20 MB log without stalling the output thread.
TABLE = decrypt_client_log(LOG_MAGIC + bytes(range(256)))
STAMP = re.compile(rb'^\[\d{4}\.\d\d\.\d\d-\d\d\.\d\d\.\d\d:')
SUBTITLE = re.compile(r'\[FlowShowTalk\]\[Subtitle\] 字幕显示 \[id: (\d+)\]')
FIELDS = re.compile(r'\[([^:\[\]]+): ([^\]]*)\]')


def format_of(head):
    if head.startswith((b'Log file open', b'[20', b'\xef\xbb\xbfLog file open')):
        return False, 3 if head.startswith(b'\xef\xbb\xbf') else 0
    sample = head[3:].translate(TABLE)
    if sample.startswith(b'Log file open') or STAMP.match(sample) or head.startswith(LOG_MAGIC):
        return True, 3
    return None


def relevant_lines(data):
    for line in data.split(b'\n'):
        if any(tag in line for tag in (b'[Plot]', b'PlotView', b'StartFlow', b'EndFlow', b'FlowShowTalk',
                                       b'Log file open', b'Log file closed', b'LogExit:')):
            yield line.decode('utf-8', 'replace').strip()


@dataclass(frozen=True)
class Subtitle:
    instance: str
    flow: str
    state: str
    number: str
    displayed_at: str = ''


class Plot:
    def __init__(self):
        self.reset()

    def reset(self):
        self.contexts = {}
        self.owner = None
        self.visible = False
        self.current = None

    def feed(self, line):
        # UE writes file-close/open markers when rotating Client.log during a
        # conversation. GameLog already scopes history to the current process.
        if 'LogExit:' in line or '离开关卡' in line:
            self.reset()
            return
        if 'PlotView' in line and '[UiCore]' in line and any(s in line for s in ('界面关闭','关闭界面','界面隐藏')):
            self.reset()
            return
        if '[Plot]' not in line:
            return
        fields = {k.lower(): v for k, v in FIELDS.findall(line)}
        if ' StartFlow ' in line:
            self.current = self.owner = None
            self.visible = False
            if all(fields.get(k) for k in ('flowincid','flowlistname','flowid','stateid')):
                flow = fields['flowlistname'] + '_' + fields['flowid']
                self.contexts[fields['flowincid']] = (flow, flow + '_' + fields['stateid'])
        elif any(s in line for s in ('EndFlow','FinishFlow','结束剧情','剧情结束','退出剧情')):
            self.reset()
        elif self.owner and fields.get('stateid') and self.owner in self.contexts:
            flow,state = self.contexts[self.owner]
            if state != flow+'_'+fields['stateid']:
                self.current = None
                if fields.get('flowincid')==self.owner:
                    self.contexts[self.owner]=(flow,flow+'_'+fields['stateid'])
                else:
                    self.visible=False
        elif '[PlotView]' in line:
            if any(s in line for s in ('关闭','隐藏','isShow: False')):
                self.current = self.owner = None
                self.visible = False
            elif '请求打开界面' in line:
                owner = fields.get('ownerid')
                # The game can request the same view again after ViewChange(True)
                # without another visibility event before the next subtitle.
                if self.visible and owner == self.owner and fields.get('current') == 'PlotView':
                    return
                self.owner = owner
                self.visible = False
                self.current = None
            elif 'ViewChange' in line and fields.get('isshow') == 'True':
                self.visible = self.owner in self.contexts
        elif '[FlowShowTalk][Subtitle]' in line:
            match = SUBTITLE.search(line)
            if match:
                if self.visible and self.owner in self.contexts:
                    if self.current is None or self.current.number != match[1]:
                        self.current = Subtitle(self.owner,*self.contexts[self.owner],match[1],line.partition(']')[0])
                else:
                    self.current = None
            elif '选择选项' in line:
                self.current = None


class GameLog:
    def __init__(self, path, process_start):
        self.path = Path(path)
        self.start = process_start
        self.earliest = time.strftime('%Y.%m.%d-%H.%M.%S', time.localtime(process_start))
        self.stream = None
        self.pending = b''
        self.head = b''
        self.encoded = False
        self.plot = Plot()

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None

    def _restore(self):
        self.close()
        self.plot.reset()
        self.pending = b''
        # Ignore files from an earlier game launch, including the final log
        # renamed by this launch (whose embedded timestamps are still older).
        for path in sorted(self.path.parent.glob('Client-backup*.log'), key=lambda p:p.stat().st_mtime_ns):
            if path.stat().st_mtime < self.start:
                continue
            with path.open('rb') as f:
                head = f.read(160)
                fmt = format_of(head)
                if fmt is None:
                    self.plot.reset()
                    continue
                f.seek(fmt[1])
                data = f.read()
                for line in relevant_lines(data.translate(TABLE) if fmt[0] else data):
                    if line.startswith('[') and line[1:20] >= self.earliest:
                        self.plot.feed(line)
        stream = self.path.open('rb')
        try:
            head = stream.read(160)
            fmt = format_of(head)
            if fmt is None:
                raise ValueError('等待可识别的游戏日志文件头')
            self.head, self.encoded = head, fmt[0]
            stream.seek(fmt[1])
            self.stream = stream
        except BaseException:
            stream.close()
            raise

    def poll(self):
        if self.path.stat().st_mtime < self.start:
            raise ValueError('等待本次游戏启动的新日志')
        if self.stream is None:
            self._restore()
        else:
            with self.path.open('rb') as current:
                same = os.path.samestat(os.fstat(self.stream.fileno()),os.fstat(current.fileno()))
                header = current.read(160)
                if not same or not header.startswith(self.head) or current.seek(0,2) < self.stream.tell():
                    self._restore()
        data = self.stream.read()
        if data:
            combined = self.pending + (data.translate(TABLE) if self.encoded else data)
            complete, sep, self.pending = combined.rpartition(b'\n')
            if sep:
                for line in relevant_lines(complete):
                    if line.startswith('[20') and line[1:20]<self.earliest:
                        continue
                    self.plot.feed(line)
        return self.plot.current

