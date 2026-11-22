import base64
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave
from zipfile import ZipFile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import wuwa_community as pack
import wuwa_community_cnb as cnb
from wuwa_voice_pack import active_quest_state, pack_matches
import wuwa_update as update

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'community'))
import publish


class CommunityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.scope = 'quest:101'
        self.names = {self.scope:'验收支线'}
        self.key = Ed25519PrivateKey.generate()
        self.data = io.BytesIO()
        with wave.open(self.data,'wb') as out:
            out.setparams((1,2,16000,0,'NONE','not compressed'))
            out.writeframes(b'\x10\x01' * 1200)
        wav = self.root/'own.wav'
        wav.write_bytes(self.data.getvalue())
        raw = '你好，{PlayerName}。'
        ident = pack.game_digest(['wuwa','state','/0','1','text'])
        fp = pack.game_digest([raw,'8','陶老',''])
        self.ident = ident
        self.rows = {ident:dict(id=ident, state_id='state',branch='/0',line_id='1',text_id='text',
            speaker_id='8',speaker='陶老',raw_text=raw,plot_key='',voice_status='unvoiced',fingerprint=fp)}
        self.reference = {'mounts':[['3.8/Mount/中文.txt','a'*64]],'quests':{self.scope:{ident:fp}}}
        self.clips = {ident:SimpleNamespace(path=wav,sha256=update.digest(wav),text='你好，漂泊者。')}
        self.path = self.root/'source.zip'
        self.public = self.root/'public'
        self.build()

    def build(self):
        return pack.build_submission(self.path,self.scope,self.rows,self.clips,self.key,self.reference,'author')

    def publish(self, issue=5):
        return publish.publish(self.public,self.path,issue,'author',self.reference,self.names)

    def test_signed_scope_checked_before_extraction(self):
        detail = pack.inspect_bundle(self.path,self.reference,self.names)
        self.assertEqual(detail['rows'][0]['text'],'你好，漂泊者。')
        self.assertNotIn('voice', detail['rows'][0])
        with ZipFile(self.path,'a') as z:
            z.writestr('extra.txt','extra')
        with self.assertRaisesRegex(ValueError,'额外文件'):
            pack.inspect_bundle(self.path,self.reference,self.names)
        self.assertFalse(self.public.exists())

    def test_signature_and_fingerprint_both_checked(self):
        with ZipFile(self.path) as z:
            envelope=json.loads(z.read('submission.json'))
            raw=z.read('audio/'+self.clips[self.ident].sha256+'.wav')
        envelope['body']['lines'][0]['speaker']='其他角色'
        for valid_key in (False,True):
            if valid_key:
                envelope['signature']=base64.b64encode(self.key.sign(update.canonical(envelope['body']))).decode()
            with ZipFile(self.path,'w') as z:
                z.writestr('submission.json',update.canonical(envelope))
                z.writestr('audio/'+self.clips[self.ident].sha256+'.wav',raw)
            with self.assertRaises(ValueError):
                pack.inspect_bundle(self.path,self.reference,self.names)

    def test_missing_personalized_and_original_lines_stay_local(self):
        self.clips[self.ident].text='你好，私人昵称。'
        with self.assertRaisesRegex(ValueError,'通用|个性化'):
            self.build()
        self.clips[self.ident].text='你好，漂泊者。'
        self.rows[self.ident]['voice_status']='voiced'
        with self.assertRaisesRegex(ValueError,'原声'):
            self.build()
        self.clips.clear()
        with self.assertRaisesRegex(ValueError,'还缺 1 句'):
            self.build()

    def test_reject_truncated_and_silent_wav(self):
        data=self.data.getvalue()
        for bad in (data[:-50],data[:44]+bytes(len(data)-44)):
            with self.assertRaises(ValueError):
                pack.clean_wav(bad)

    def test_account_version_idempotency_and_withdrawal(self):
        with self.assertRaisesRegex(ValueError,'署名'):
            publish.publish(self.public,self.path,1,'other',self.reference,self.names)
        entry=self.publish()
        self.assertEqual(entry,self.publish(6))
        self.assertEqual(len(cnb.parse_catalog((self.public/'catalog.json').read_bytes())),1)
        publish.withdraw(self.public,5)
        self.assertEqual(cnb.parse_catalog((self.public/'catalog.json').read_bytes()),[])
        self.assertFalse((self.public/'objects'/(entry['sha256']+'.zip')).exists())
        with self.assertRaisesRegex(ValueError,'下架'):
            self.publish(7)

    def test_install_and_restore_official_snapshot_and_voice_policy(self):
        reader=self.root/'work'
        official={self.scope:{'folder':'e'*64,'revision':3}}
        update.atomic(reader/'updates/quests.json',update.canonical(official))
        entry=self.publish()
        with patch.object(pack,'fingerprints',return_value=self.reference):
            pack.install_bundle(reader,self.path,entry)
        self.assertEqual(active_quest_state(reader)[self.scope]['community'],5)
        self.assertEqual(update.quest_state(reader),official)
        row=pack.inspect_bundle(self.path,self.reference,self.names)['rows'][0]
        row['community']=5
        clip=SimpleNamespace(catalog_id=self.ident)
        evidence=dict(id=self.ident,fingerprint=self.rows[self.ident]['fingerprint'],voice_status='unvoiced',reviewed_at='')
        self.assertTrue(pack_matches(None,[evidence],row,clip))
        self.assertFalse(pack_matches(None,[dict(evidence,voice_status='voiced')],row,clip))
        pack.select_version(reader,self.scope)
        self.assertEqual(active_quest_state(reader),official)

    def test_official_updater_rejects_community_packages(self):
        with ZipFile(self.path) as z:
            with self.assertRaises((KeyError,ValueError)):
                update.inspect_zip(z)

    def test_intake_paths_and_storage_hosts_are_bounded(self):
        for value in ('../file','https://other.test/a','a//b'):
            with self.assertRaises(ValueError):
                cnb.file_path(value)
        for value in ('http://asset.cnb.cool/a','https://example.com/','https://u@cnb.cool/file'):
            with self.assertRaises(ValueError):
                cnb.storage_host(value)

    def test_lfs_pointer_is_resolved_without_author_credentials(self):
        class Response(io.BytesIO):
            status = 200
            def peek(self, count):
                return self.getvalue()[self.tell():][:count]
        blob = self.data.getvalue()
        sha = pack.hexdigest(blob)
        size = len(blob)
        entry = dict(sample=sha, sample_size=size)
        pointer = Response(f'version https://git-lfs.github.com/spec/v1\noid sha256:{sha}\nsize {size}\n'.encode())
        batch = Response(json.dumps({'objects':[{'oid':sha,'size':size,'actions':{'download':{'href':'https://asset.cnb.cool/file'}}}]}).encode())
        with patch.object(cnb,'public_file',return_value=pointer), patch.object(cnb,'build_opener') as opener:
            opener.return_value.open.side_effect=[batch,Response(blob)]
            with cnb.media(entry,sample=True) as resolved:
                self.assertEqual(resolved.read(),blob)
            calls=opener.return_value.open.call_args_list
            self.assertTrue(all(call.args[0].get_header('Authorization') is None for call in calls))

    def test_chunked_submission_is_private_and_reassembled_byte_for_byte(self):
        from threading import Event
        chunks, tickets = {}, []
        class Platform:
            def request(inner, path, data):
                if path == cnb.ISSUES+'/asset-groups':
                    name = data['file_assets'][0]['name']
                    asset = dict(path='parts/'+name, upload_url='https://asset.cnb.cool/'+name, asset_link=name)
                    return {'file_upload_urls':[asset]}
                self.assertEqual(path,cnb.ISSUES)
                tickets.append(data)
                return dict(number='17',invisible=True)
        def attachment(req, timeout):
            name=req.full_url.rsplit('/',1)[1]
            if req.get_method()=='PUT':
                self.assertIsNone(req.get_header('Authorization'))
                chunks[name]=req.data
                response=io.BytesIO()
                response.status=200
                return response
            return io.BytesIO(chunks[name])
        archive=self.root/'two-parts'
        archive.write_bytes(b'a'*cnb.PART+b'final')
        with patch.object(cnb,'build_opener') as opens, patch.object(publish,'build_opener') as reads:
            opens.return_value.open.side_effect=reads.return_value.open.side_effect=attachment
            self.assertEqual(cnb.submit(Platform(),archive,Event(),lambda _:None),17)
            self.assertEqual(len(chunks),2)
            self.assertTrue(tickets[0]['invisible'])
            reconstructed=self.root/'readback'
            publish.read_intake(tickets[0],reconstructed,'test')
            self.assertEqual(update.digest(reconstructed),update.digest(archive))

    def test_oversized_incomplete_issue_is_rejected_before_network(self):
        issue=dict(invisible=True,title=cnb.TITLE+'1'*16,body='\x60\x60\x60json\n'+json.dumps(dict(
            format='asgsv-intake-v1',sha256='1'*64,size=cnb.PART+1,files=[]))+'\n\x60\x60\x60')
        with self.assertRaisesRegex(ValueError,'数量'):
            publish.read_intake(issue,self.root/'intake.zip','unused')

