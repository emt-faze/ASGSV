import unittest
import tempfile
import time
from pathlib import Path
import numpy as np
from frame_gate import glyph_mask,FrameStability,SubtitleChangeGuard


class WuwaCaptionSwitchTests(unittest.TestCase):
    def images(self):
        first=np.zeros((40,180,3),np.uint8)
        first[12:23,60:70]=[218,231,235]
        first[12:23,80:90]=[218,231,235]
        second=np.zeros_like(first)
        second[12:23,15:25]=[218,231,235]
        second[12:23,35:45]=[218,231,235]
        second[12:23,105:115]=[218,231,235]
        return first,second

    def test_warm_white_captions_have_ink_and_page_change_invalidates_cache(self):
        first,second=self.images()
        self.assertEqual(int(glyph_mask(first).sum()),0)
        self.assertGreater(int(glyph_mask(first,style='wuwa').sum()),20)
        gate=FrameStability(style='wuwa')
        for at in (0,.2,.4,.6):
            gate.ready(first,at)
        gate.commit()
        self.assertTrue(gate.ready(first,.8))
        self.assertTrue(gate.unchanged)
        for at in (1.,1.2,1.4,1.6):
            ready=gate.ready(second,at)
        self.assertTrue(ready)
        self.assertFalse(gate.unchanged)

    def test_empty_ink_masks_never_prove_cached_text_still_visible(self):
        first,second=self.images()
        gate=FrameStability()  # Original strict mask deliberately misses both.
        for at in (0,.2,.4,.6):
            gate.ready(first,at)
        gate.commit()
        for at in (1.,1.2,1.4,1.6):
            gate.ready(second,at)
        self.assertFalse(gate.unchanged)

    def test_warm_caption_disappearance_stops_old_audio(self):
        first,second=self.images()
        guard=SubtitleChangeGuard(style='wuwa')
        observation=dict(body='first caption',body_confidence=.99,captured_monotonic=0,
            body_lines=[dict(bbox=[[55,10],[95,10],[95,25],[55,25]])])
        guard.arm(first,observation,(0,0))
        self.assertIsNotNone(guard.reference)
        self.assertFalse(guard.changed(first,.2))
        self.assertTrue(guard.changed(second,.4))

    def test_complete_text_mode_rejects_prefix_and_requires_two_captures(self):
        from dialogue_generation import GenerationProject
        from prepared_playback import PreparedLibrary,PreparedPlayback
        from test_prepared_playback import completed_line,Player
        with tempfile.TemporaryDirectory() as folder:
            project=GenerationProject.create(Path(folder))
            row,path=completed_line(project,'这是一句需要完整匹配的台词。')
            player=Player()
            now=[100.]
            controller=PreparedPlayback(PreparedLibrary(project),player,clock=lambda:now[0],allow_prefix=False)
            def observe(text,stamp):
                now[0]=stamp
                controller.observe(dict(body=text,speaker=row.speaker,body_confidence=.99,speaker_confidence=.99,
                                        captured_monotonic=stamp,render_stable=False))
            try:
                observe('这是一句需要',100.)
                observe('这是一句需要',100.2)
                self.assertEqual(player.played,[])
                observe(row.text,100.4)
                self.assertEqual(controller._count,1)
                observe(row.text,100.4)
                self.assertEqual(controller._count,1)
                observe(row.text,100.6)
                end=time.monotonic()+3
                while not player.played and time.monotonic()<end:
                    time.sleep(.01)
                self.assertEqual(player.played,[path])
                observe(row.text,100.8)
                self.assertEqual(player.played,[path])
            finally:
                controller.close(3)

    def test_capture_gap_does_not_replay_consumed_line_but_next_line_still_plays(self):
        from dialogue_generation import GenerationProject
        from prepared_playback import PreparedLibrary,PreparedPlayback
        from test_prepared_playback import completed_line,Player
        with tempfile.TemporaryDirectory() as folder:
            project=GenerationProject.create(Path(folder))
            first,first_path=completed_line(project,'这是已经开始播放的第一句。')
            second,second_path=completed_line(project,'这是切换之后的第二句。')
            player=Player()
            now=[100.]
            controller=PreparedPlayback(PreparedLibrary(project),player,clock=lambda:now[0],
                allow_prefix=False,preserve_on_unavailable=True)
            def confirm(row):
                for _ in range(2):
                    now[0]+=.2
                    controller.observe(dict(body=row.text,speaker=row.speaker,body_confidence=.99,
                        speaker_confidence=.99,captured_monotonic=now[0]))
            def wait_plays(count):
                end=time.monotonic()+2
                while len(player.played)<count and time.monotonic()<end:
                    time.sleep(.01)
                self.assertEqual(len(player.played),count)
            try:
                confirm(first)
                wait_plays(1)
                controller.invalidate(unavailable=True)
                confirm(first)
                time.sleep(.1)
                self.assertEqual(player.played,[first_path])
                confirm(second)
                wait_plays(2)
                self.assertEqual(player.played,[first_path,second_path])
            finally:
                controller.close(3)


if __name__=='__main__':
    unittest.main()
