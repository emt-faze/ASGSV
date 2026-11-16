import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.download_nte_reader import prepare, valid


class ReaderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.destination = self.root/'reader'
        self.archive = self.root/'reader.zip'
        data = b'verified resource-reader fixture'
        with zipfile.ZipFile(self.archive,'w') as archive:
            archive.writestr('assemblies/reader.dll',data)
        self.manifest = dict(url='https://example.invalid/reader.zip',bytes=self.archive.stat().st_size,
            sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            files=[dict(path='assemblies/reader.dll',bytes=len(data),sha256=hashlib.sha256(data).hexdigest())])

    def tearDown(self):
        self.temporary.cleanup()

    def test_install_preserves_manifest_for_subsequent_verification(self):
        self.assertTrue(prepare(self.manifest,self.destination,archive=self.archive))
        self.assertTrue(valid(self.destination,self.manifest))
        self.assertEqual(json.loads((self.destination/'bundle.json').read_text()),self.manifest)
        def no_network(*args,**kwargs):
            raise AssertionError('A verified reader must be reused')
        self.assertFalse(prepare(self.manifest,self.destination,opener=no_network))

    def test_bad_download_preserves_previous_runtime(self):
        self.destination.mkdir()
        previous = self.destination/'previous.dll'
        previous.write_bytes(b'previous runtime')
        self.archive.write_bytes(b'broken download')
        with self.assertRaisesRegex(ValueError,'checksum'):
            prepare(self.manifest,self.destination,archive=self.archive)
        self.assertEqual(previous.read_bytes(),b'previous runtime')
        self.assertEqual(list(self.root.glob('.nte-reader-*')),[])

    def test_archive_content_must_match_reviewed_manifest(self):
        with zipfile.ZipFile(self.archive,'a') as archive:
            archive.writestr('../unexpected.txt',b'unexpected')
        self.manifest.update(bytes=self.archive.stat().st_size,
            sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError,'contents differ'):
            prepare(self.manifest,self.destination,archive=self.archive)
        self.assertFalse((self.root/'unexpected.txt').exists())

    def test_manifest_rejects_parent_traversal(self):
        self.manifest['files'][0]['path'] = '../outside.dll'
        with self.assertRaisesRegex(ValueError,'manifest path'):
            prepare(self.manifest,self.destination,archive=self.archive)
        self.assertFalse((self.root/'outside.dll').exists())


if __name__ == '__main__':
    unittest.main()
