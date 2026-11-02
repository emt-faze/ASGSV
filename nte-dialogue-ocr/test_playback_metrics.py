import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from playback_metrics import PlaybackMetrics, timing_sample, STAGES
from dialogue_generation import GenerationProject
from prepared_playback import PreparedLibrary, PreparedPlayback
from test_prepared_playback import completed_line


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, **values):
        return dict(dict(capture=10., received=10.2, queued=10.4, trigger='automatic', match_kind='prefix'), **values)

    def test_stage_sum_and_invalid_clock_evidence(self):
        sample = timing_sample(self.seed(), 10.45, 10.5)
        self.assertEqual([sample[key] for key, _ in STAGES], [200, 200, 50, 50, 500])
        for bad in (float('nan'), float('inf'), 11., None):
            self.assertIsNone(timing_sample(self.seed(capture=bad), 10.45, 10.5))

    def test_manual_request_does_not_include_time_spent_paused(self):
        sample = timing_sample(self.seed(queued=100., trigger='resume'), 100.05, 100.1)
        self.assertEqual(sample['total_ms'], 100)
        self.assertEqual(sample['recognition_ms'], 0)
        self.assertEqual(sample['origin'], 'manual_request')

    def test_statistics_deduplicate_and_exclude_manual_starts(self):
        metrics = PlaybackMetrics(self.root)
        for number in range(1, 21):
            sample = timing_sample(self.seed(), 10.4, 10.4 + number/1000)
            event = dict(state='playing', generation=number, latency=sample)
            self.assertTrue(metrics.add(event))
            self.assertFalse(metrics.add(event))
        manual = timing_sample(self.seed(trigger='replay'), 10.4, 200.)
        metrics.add(dict(state='playing', generation=21, latency=manual))
        self.assertFalse(metrics.add(dict(state='queued', generation=22, latency=manual)))
        summary = metrics.summary()
        self.assertEqual(summary['automatic_count'], 20)
        self.assertEqual(summary['manual_count'], 1)
        self.assertEqual(summary['stages']['total_ms']['p50'], 410.5)
        self.assertEqual(summary['stages']['total_ms']['p95'], 419)
        self.assertEqual(len(metrics.path.read_text(encoding='utf-8').splitlines()), 21)
        metrics.save_summary()
        self.assertEqual(json.loads(metrics.path.with_suffix('.summary.json').read_text(encoding='utf-8'))['manual_count'], 1)
        modified = metrics.path.with_suffix('.summary.json').stat().st_mtime_ns
        metrics.save_summary()
        self.assertEqual(metrics.path.with_suffix('.summary.json').stat().st_mtime_ns, modified)

    def test_unwritable_statistics_do_not_interrupt_playback_accounting(self):
        blocked = self.root / 'file'
        blocked.write_text('not a directory')
        metrics = PlaybackMetrics(blocked)
        self.assertTrue(metrics.add(dict(state='playing', generation=1, latency=timing_sample(self.seed(), 10.4, 10.5))))
        self.assertEqual(metrics.summary()['automatic_count'], 1)
        self.assertTrue(metrics.save_error)

    def test_controller_freezes_first_candidate_and_counts_actual_start_once(self):
        project = GenerationProject.create(self.root)
        row, _ = completed_line(project, '下午好，准备好了吗？')
        now, events = [11.], []
        class Player:
            def play(self, path, *, cancel_event, on_started):
                now[0] += .1
                on_started()
                on_started()  # A duplicate backend callback must be harmless.
        controller = PreparedPlayback(PreparedLibrary(project), Player(), events.append, clock=lambda: now[0])
        def observe(captured):
            controller.observe(dict(body=row.text, speaker=row.speaker, body_confidence=.99,
                                    speaker_confidence=.99, captured_monotonic=captured))
        def wait_for(count):
            end = time.monotonic()+2
            while len([e for e in events if e['state']=='playing']) < count and time.monotonic() < end:
                time.sleep(.005)
            return [e for e in events if e['state']=='playing']
        try:
            observe(10.8)
            now[0] = 11.3
            observe(11.2)
            starts = wait_for(1)
            self.assertEqual(len(starts), 1)
            sample = starts[0]['latency']
            self.assertEqual(sample['recognition_ms'], 200)
            self.assertEqual(sample['confirmation_ms'], 300)
            self.assertEqual(sample['total_ms'], 600)
            now[0] = 100.
            observe(99.9)
            controller.replay()
            starts = wait_for(2)
            self.assertEqual(starts[-1]['latency']['trigger'], 'replay')
            self.assertEqual(starts[-1]['latency']['total_ms'], 100)
        finally:
            self.assertTrue(controller.close(2))

    def test_cancel_before_output_has_no_successful_latency_sample(self):
        project = GenerationProject.create(self.root)
        row, _ = completed_line(project, '下午好，准备好了吗？')
        entered, events = threading.Event(), []
        class Player:
            def play(self, path, *, cancel_event, on_started):
                entered.set()
                cancel_event.wait(2)
                if not cancel_event.is_set():
                    on_started()
        controller = PreparedPlayback(PreparedLibrary(project), Player(), events.append)
        try:
            controller.observe(dict(body=row.text, body_confidence=.99, render_stable=True,
                                    captured_monotonic=time.monotonic()))
            self.assertTrue(entered.wait(2))
            controller.skip()
        finally:
            controller.close(2)
        self.assertFalse([e for e in events if e['state']=='playing'])


if __name__ == '__main__':
    unittest.main()
