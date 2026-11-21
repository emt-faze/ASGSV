"""Invariants of signed update packages and reversible program installation."""
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
from urllib.request import Request
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import wuwa_update as update


class ProgramUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.key=Ed25519PrivateKey.generate()
        key=patch.object(update,'PUBLIC_KEY',self.key.public_key().public_bytes_raw().hex())
        key.start()
        self.addCleanup(key.stop)
        (self.root/update.VERSION).parent.mkdir()
        (self.root/update.VERSION).write_bytes(b'0.4.17')
        (self.root/update.MANIFEST).write_bytes(update.canonical(dict(version='0.4.17',files={
            update.VERSION:update.digest(self.root/update.VERSION)})))
        self.config=self.root/'nte-dialogue-ocr/config.json'
        self.config.write_bytes(b'personal settings')

    def package(self,extra=None,tamper=False):
        files={update.VERSION:b'0.4.18',**(extra or {})}
        body=dict(schema=1,kind='app',name='workbench',base='0.4.17',version='0.4.18',
                  files={n:[len(b),hashlib.sha256(b).hexdigest()] for n,b in files.items()})
        signature=self.key.sign(update.canonical(body))
        if tamper:
            body['name']='changed'
        path=self.root/'package.zip'
        with ZipFile(path,'w') as z:
            z.writestr('update.json',json.dumps(dict(body=body,signature=base64.b64encode(signature).decode())))
            for n,b in files.items():
                z.writestr('payload/'+n,b)
        return path

    @contextmanager
    def mirror(self, files):
        paths=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                blob=files.get(self.path)
                if blob is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Length',str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)

            def log_message(self,fmt,*args):
                pass
        with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
            thread=Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                with patch.object(update,'cnb_releases',return_value=f'http://127.0.0.1:{server.server_port}/-/releases/'), \
                     patch.object(update,'urlopen',side_effect=AssertionError('unexpected GitHub request')):
                    yield paths
            finally:
                server.shutdown()
                thread.join()

    def signed_feed(self,entry):
        body=dict(schema=1,tag='wuwa-v0.4.18',packages=[entry])
        return update.canonical(dict(body=body,signature=base64.b64encode(self.key.sign(update.canonical(body))).decode()))

    def test_legacy_mirror_uses_cnb_and_explicit_github_is_retained(self):
        update.atomic(self.root/update.SOURCE_FILE, b'tuna')
        self.assertEqual(update.selected_source(self.root), 'cnb')
        self.assertEqual(list(update.SOURCES), ['cnb','github'])
        update.save_source(self.root, 'github')
        self.assertEqual(update.selected_source(self.root), 'github')

    def test_default_cnb_anonymous_manifest_and_versioned_attachment(self):
        package = self.package().read_bytes()
        entry = dict(kind='app', name='工作台', asset='update.zip', size=len(package),
                     base='0.4.17', version='0.4.18', sha256=hashlib.sha256(package).hexdigest())
        manifest_path = '/-/releases/latest/download/wuwa-updates.json'
        asset_path = '/-/releases/download/wuwa-v0.4.18/update.zip'
        with self.mirror({manifest_path:self.signed_feed(entry), asset_path:package}) as requests:
            self.assertEqual(update.selected_source(self.root), 'cnb')
            feed = update.read_feed()
            archive = update.download(self.root, feed, entry, lambda msg: None)
            self.assertEqual(update.stage_program(self.root, archive).read_bytes(), package)
            self.assertEqual(requests, [manifest_path, asset_path])
        self.assertEqual(update.cnb_releases('group/sub/repo'), 'https://cnb.cool/group/sub/repo/-/releases/')
        with self.assertRaises(ValueError):
            update.cnb_releases('../other')
        for url in (update.FEED, 'http://asset.cnb.cool/a', 'https://cnb.cool.evil.example/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                update.CnbRedirect().redirect_request(Request('https://cnb.cool/'),None,302,'',{},url)

    def test_cnb_bad_signature_does_not_fall_back_to_github(self):
        with self.mirror({'/-/releases/latest/download/wuwa-updates.json':b'{}'}):
            with self.assertRaisesRegex(ValueError,'签名'):
                update.read_feed()
        update.save_source(self.root, 'github')
        self.assertEqual(update.selected_source(self.root), 'github')

    def test_switch_rejects_unavailable_mirror_and_keeps_offline_import(self):
        import time
        from PySide6 import QtWidgets as W
        from wuwa_update_ui import UpdateCenter
        app=W.QApplication.instance() or W.QApplication([])
        update.save_source(self.root, 'github')
        dialog=UpdateCenter(self.root)
        with self.mirror({}):
            dialog.source.setCurrentIndex(dialog.source.findData('cnb'))
            dialog.switch_source()
            deadline=time.monotonic()+5
            while dialog.thread is not None and time.monotonic()<deadline:
                app.processEvents()
                time.sleep(.01)
            self.assertIn('404',dialog.status.text())
            self.assertEqual(dialog.source.currentData(),'cnb')
            self.assertEqual(update.selected_source(self.root),'github')
            self.assertTrue(dialog.buttons[-1].isEnabled())
        dialog.close()

    def test_tampering_and_reserved_personal_paths(self):
        for name in ('../README.md','nte-dialogue-ocr/config.json','game-data/wuwa/dialogue.sqlite',
                     'nte-dialogue-ocr/CON.py','nte-dialogue-ocr/a.py:stream'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                update.stage_program(self.root,self.package({name:b'x'}))
        with self.assertRaisesRegex(ValueError,'签名'):
            update.stage_program(self.root,self.package(tamper=True))
        self.assertEqual(self.config.read_bytes(),b'personal settings')

    def test_failed_start_restores_changes_and_removes_new_program_file(self):
        staged=update.stage_program(self.root,self.package({'nte-dialogue-ocr/added.py':b'new'}))
        manifest=(self.root/update.MANIFEST).read_bytes()
        with patch.object(update,'start_verified',side_effect=RuntimeError('startup failed')):
            with self.assertRaisesRegex(RuntimeError,'startup failed'):
                update.apply_program(self.root,staged)
        self.assertEqual(update.portable_version(self.root),'0.4.17')
        self.assertEqual((self.root/update.MANIFEST).read_bytes(),manifest)
        self.assertFalse((self.root/'nte-dialogue-ocr/added.py').exists())
        self.assertEqual(self.config.read_bytes(),b'personal settings')

    def test_conflicting_local_program_file_is_kept(self):
        staged=update.stage_program(self.root,self.package({'nte-dialogue-ocr/added.py':b'new'}))
        local=self.root/'nte-dialogue-ocr/added.py'
        local.write_bytes(b'user copy')
        with self.assertRaisesRegex(ValueError,'保留本机修改'):
            update.apply_program(self.root,staged)
        self.assertEqual(local.read_bytes(),b'user copy')
        self.assertEqual(update.portable_version(self.root),'0.4.17')

    def test_update_dialog_delivers_thread_completion(self):
        import time
        from PySide6 import QtWidgets as W
        from wuwa_update_ui import UpdateCenter
        app=W.QApplication.instance() or W.QApplication([])
        dialog=UpdateCenter(self.root)
        dialog.start(lambda progress:('status','completed'))
        deadline=time.monotonic()+5
        while dialog.thread is not None and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.01)
        self.assertIsNone(dialog.thread)
        self.assertEqual(dialog.status.text(),'completed')
        dialog.close()

    def test_new_mount_requires_verified_line_and_matching_fingerprint(self):
        from wuwa_voice_pack import _load_folder
        scope='quest:12'
        row=dict(quest=scope,id='line',fingerprint='verified',state='scene',number='1',text='台词',
                 speaker='角色',label='对白',voice='voice',sha256='a'*64)
        (self.root/'pack.json').write_text(json.dumps(dict(format=1,mounts=['old'],lines=[row])))
        index=dict(mounts=['new'],quests={scope:{'line':'verified'}})
        with patch('wuwa_daily_scan.verified_index',return_value=index):
            self.assertEqual(len(_load_folder(self.root,['new'],True)),1)
            self.assertFalse(_load_folder(self.root,['unknown'],True))
            index['quests'][scope]['line']='changed'
            self.assertFalse(_load_folder(self.root,['new'],True))
