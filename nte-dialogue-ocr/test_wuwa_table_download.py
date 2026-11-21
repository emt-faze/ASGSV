import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import wuwa_resources as resources


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.payload = bytes(range(256)) * 1024
        self.prefix = 13312
        self.folder = self.root / 'sources' / resources.COMMIT
        self.folder.mkdir(parents=True)
        self.cache = self.folder / 'flow.json'
        self.part = self.cache.with_suffix('.part')
        self.requests = []
        self.progress = []
        self.behaviors = []
        self.unblock = threading.Event()
        self.stop = threading.Event()
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                header = self.headers.get('Range')
                test.requests.append(header)
                behavior = test.behaviors.pop(0)
                start = int(header[6:-1]) if header else 0
                self.send_response(206 if behavior in ('range', 'misaligned') else 200)
                if behavior in ('range', 'misaligned'):
                    reported = start + int(behavior == 'misaligned')
                    self.send_header('Content-Range', f'bytes {reported}-{len(test.payload)-1}/{len(test.payload)}')
                else:
                    start = 0
                self.send_header('Content-Length', str(len(test.payload) - start))
                self.end_headers()
                if behavior == 'timeout':
                    self.wfile.write(test.payload[:test.prefix])
                    self.wfile.flush()
                    test.unblock.wait(3)
                elif behavior == 'corrupt':
                    self.wfile.write(bytes(len(test.payload)))
                else:
                    self.wfile.write(test.payload[start:])

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.unblock.set)
        addr = f'http://127.0.0.1:{self.server.server_port}'
        digest = hashlib.sha256(self.payload).hexdigest()
        for target, value in [('TABLES', {'flow': 'flow.json'}),
                              ('PINNED_FILES', {'flow': (len(self.payload), 'pinned-blob', digest)}),
                              ('_sources', lambda *args: [('primary', addr+'/primary'), ('backup', addr+'/backup')])]:
            self.enterContext(patch.object(resources, target, value))
        def local_open(req, timeout):
            return urlopen(req, timeout=.25 if req.full_url.endswith('/primary') else 3)
        self.enterContext(patch.object(resources.urllib.request, 'urlopen', side_effect=local_open))

    def download(self):
        return resources.download_tables(self.root, self.stop, self.progress.append)

    def test_read_timeout_resumes_on_other_source_and_cache_works_offline(self):
        self.behaviors = ['timeout', 'range']
        self.download()
        self.assertEqual(self.requests, [None, f'bytes={self.prefix}-'])
        self.assertTrue(any('超时' in p for p in self.progress))
        self.assertEqual(self.cache.read_bytes(), self.payload)
        self.assertTrue((self.folder/'source.json').is_file())
        with patch.object(resources.urllib.request, 'urlopen', side_effect=AssertionError('unexpected network')):
            self.download()

    def test_source_ignoring_range_does_not_append_duplicate_prefix(self):
        self.part.write_bytes(self.payload[:self.prefix])
        self.behaviors = ['misaligned', 'ignore_range']
        self.download()
        self.assertEqual(self.cache.read_bytes(), self.payload)
        self.assertEqual(self.requests, [f'bytes={self.prefix}-'] * 2)

    def test_corrupt_resume_restarts_clean(self):
        self.part.write_bytes(b'x' * self.prefix)
        self.behaviors = ['range', 'ignore_range']
        self.download()
        self.assertEqual(self.requests, [f'bytes={self.prefix}-', None])
        self.assertEqual(self.cache.read_bytes(), self.payload)

    def test_cancel_after_interruption_preserves_partial_and_previous_manifest(self):
        self.cache.write_bytes(b'previous completed table')
        manifest = self.folder / 'source.json'
        manifest.write_bytes(b'previous manifest')
        self.behaviors = ['timeout']
        def cancel(message):
            if '超时' in message:
                self.stop.set()
        with self.assertRaises(InterruptedError):
            resources.download_tables(self.root, self.stop, cancel)
        self.assertEqual(self.part.read_bytes(), self.payload[:self.prefix])
        self.assertEqual(self.cache.read_bytes(), b'previous completed table')
        self.assertEqual(manifest.read_bytes(), b'previous manifest')
        self.stop.clear()
        self.behaviors = ['range']
        self.download()
        self.assertEqual(self.cache.read_bytes(), self.payload)

    def test_failed_download_does_not_publish_manifest(self):
        self.behaviors = ['corrupt'] * 4
        with self.assertRaisesRegex(ValueError, 'flow.*校验不匹配.*续传'):
            self.download()
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.part.exists())
        self.assertFalse((self.folder/'source.json').exists())
