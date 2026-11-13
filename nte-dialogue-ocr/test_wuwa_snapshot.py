import unittest
from copy import deepcopy
from wuwa_local_inspect import valid_snapshot


class SnapshotTests(unittest.TestCase):
    def test_missing_changed_and_removed_archives_reject_cached_evidence(self):
        stats = [dict(path='base.pak', bytes=100, mtime_ns=1), dict(path='patch.pak', bytes=200, mtime_ns=2)]
        request = dict(archives=['patch.pak'])
        data = dict(archive_metadata_unchanged=True, manifests=['v1'],
                    archive_stats_before=stats[1:], archive_stats_after=stats[1:])
        self.assertTrue(valid_snapshot(data, request, stats, ['v1']))
        changed = deepcopy(stats)
        changed[1]['mtime_ns'] = 3
        self.assertFalse(valid_snapshot(data, request, changed, ['v1']))
        self.assertFalse(valid_snapshot(data, request, stats[:1], ['v1']))
        self.assertFalse(valid_snapshot(data, request, stats, ['v2']))
        del data['archive_stats_before']
        self.assertFalse(valid_snapshot(data, request, stats, ['v1']))

    def test_changes_during_export_and_missing_files_rejected(self):
        stats = [dict(path='base.pak', missing=True)]
        data = dict(archive_metadata_unchanged=True, manifests=[],
                    archive_stats_before=stats, archive_stats_after=stats)
        self.assertFalse(valid_snapshot(data, dict(archives=['base.pak']), stats, []))


if __name__ == '__main__':
    unittest.main()
