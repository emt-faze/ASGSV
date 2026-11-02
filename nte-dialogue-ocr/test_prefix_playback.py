"""Five-character routing, growing subtitle continuity and partial-record isolation."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import cv2
import numpy as np

from dialogue_generation import GenerationProject
from prepared_playback import PreparedLibrary, PreparedPlayback, prefix_text
from frame_gate import SubtitleChangeGuard
from prefix_preview import PrefixPreview
from ocr_core import DialogueOCR
from test_ocr_core import FakeEngine, detection
from test_prepared_playback import Player, completed_line


class PrefixLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project=GenerationProject.create(Path(self.temp.name),mode='character')

    def test_punctuation_counts_and_typography_is_normalized_without_word_changes(self):
        self.assertEqual(prefix_text('以前……「平行线」')[:5], '以前……"')
        self.assertEqual(prefix_text('以前...... “平行线”'),prefix_text('以前……「平行线」'))
        self.assertEqual(prefix_text('ＡＢ，ＣＤ'),prefix_text('AB,CD'))
        self.assertNotEqual(prefix_text('不要开门。'),prefix_text('要开门。'))
        self.assertNotEqual(prefix_text('奖励10。'),prefix_text('奖励11。'))
        self.assertNotEqual(prefix_text('要去吗？'),prefix_text('要去吗。'))

    def test_unique_prefix_accepts_growth_but_rejects_conflicting_readable_suffix(self):
        row,_=completed_line(self.project,'我今天想去街机厅玩。')
        lib=PreparedLibrary(self.project)
        for text in ('我今天想去','我今天想去街机厅'):
            line,_,kind=lib.resolve(text,'伊洛伊',early=True)
            self.assertEqual(line.row_id,row.id)
            self.assertEqual(kind,'prefix')
        self.assertEqual(lib.resolve('我今天想去买点东西。','伊洛伊',early=True)[2],'conflict')
        self.assertIsNone(lib.resolve('我今天想去','其他人',early=True)[0])

    def test_collisions_cannot_be_resolved_by_role_or_more_than_five_partial_characters(self):
        first,_=completed_line(self.project,'我今天想去街机厅玩。')
        completed_line(self.project,'我今天想去买点东西。',speaker='明音凛')
        lib=PreparedLibrary(self.project)
        self.assertIsNone(lib.resolve('我今天想去街机厅','伊洛伊',early=True)[0])
        self.assertIsNone(lib.resolve(first.text,'伊洛伊',early=True)[0])
        self.assertEqual(lib.resolve(first.text,'伊洛伊')[0].row_id,first.id)

    def test_known_but_not_generated_same_prefix_blocks_early_uniqueness(self):
        completed_line(self.project,'我今天想去街机厅玩。')
        self.project.add_dialogue('我今天想去买点东西。')
        lib=PreparedLibrary(self.project)
        self.assertIsNone(lib.resolve('我今天想去',early=True)[0])

    def test_ungenerated_other_character_with_identical_text_still_requires_full_match(self):
        row,_=completed_line(self.project,'我今天想去街机厅玩。')
        self.project.add_dialogue(row.text,'明音凛','character:明音凛')
        lib=PreparedLibrary(self.project)
        self.assertIsNone(lib.resolve('我今天想去','伊洛伊',early=True)[0])
        self.assertEqual(lib.resolve(row.text,'伊洛伊')[0].row_id,row.id)

    def test_short_sentence_needs_complete_observation_and_pure_punctuation_never_routes(self):
        short,_=completed_line(self.project,'嗯哼？')
        lib=PreparedLibrary(self.project)
        self.assertIsNone(lib.resolve(short.text,early=True)[0])
        self.assertEqual(lib.resolve(short.text)[0].row_id,short.id)
        self.assertIsNone(lib.resolve('………………',early=True)[0])


class PrefixPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.project=GenerationProject.create(Path(self.temp.name),mode='character')
        self.row,self.path=completed_line(self.project,'我今天想去街机厅玩。')
        self.other,self.other_path=completed_line(self.project,'下午好，准备好了吗？',sample=2000)
        self.player,self.events=Player(),[]
        self.now=100.
        self.controller=PreparedPlayback(PreparedLibrary(self.project),self.player,self.events.append,clock=lambda:self.now)

    def tearDown(self):
        self.player.release.set()
        self.assertTrue(self.controller.close(2))
        self.temp.cleanup()

    def observe(self,text,**changes):
        self.now+=.2
        event=dict(body=text,speaker='伊洛伊',body_confidence=.99,speaker_confidence=.99,
                   captured_monotonic=self.now,prefix_probe=True)
        event.update(changes)
        self.controller.observe(event)
        return event

    def wait(self,condition):
        deadline=time.monotonic()+2
        while not condition() and time.monotonic()<deadline:
            time.sleep(.005)
        self.assertTrue(condition())

    def start(self):
        self.observe('我今天想去')
        self.assertEqual(self.player.played,[])
        self.observe('我今天想去街机')
        self.wait(lambda:len(self.player.played)==1)

    def test_stable_prefix_starts_early_and_growth_full_check_and_completion_never_replay(self):
        self.start()
        self.observe('我今天想去街机厅')
        self.observe(self.row.text,prefix_probe=False,render_stable=True)
        self.assertEqual(self.player.cancelled,[])
        self.assertEqual(len(self.player.played),1)
        self.assertTrue(any(e['state']=='verified' for e in self.events))
        self.player.release.set()
        self.wait(lambda:self.player.active==0)
        self.observe(self.row.text,prefix_probe=False,render_stable=True)
        self.assertEqual(len(self.player.played),1)

    def test_visual_confirmation_can_avoid_second_ocr_pass_but_duplicate_frames_cannot(self):
        first=self.observe('我今天想去')
        self.controller.observe(dict(first,prefix_stable=True))
        self.assertEqual(self.player.played,[])
        self.observe('我今天想去',prefix_stable=True)
        self.wait(lambda:len(self.player.played)==1)

    def test_conflicting_suffix_stops_and_prefix_alone_cannot_retrigger_until_full_check(self):
        self.start()
        self.observe('我今天想去买点东西。')
        self.wait(lambda:self.player.active==0)
        self.observe('我今天想去',prefix_stable=True)
        self.assertEqual(len(self.player.played),1)
        self.observe(self.row.text,prefix_probe=False,render_stable=True)
        self.wait(lambda:len(self.player.played)==2)

    def test_uncertain_growing_tail_does_not_cut_valid_prefix_audio(self):
        self.start()
        self.observe('我今天想去街机',body_confidence=.7,coverage_warning=True)
        self.assertEqual(self.player.cancelled,[])
        self.observe(self.row.text,prefix_probe=False,render_stable=True)
        self.assertEqual(len(self.player.played),1)

    def test_lower_score_on_same_body_after_full_confirmation_does_not_interrupt(self):
        self.observe(self.row.text,prefix_probe=False,render_stable=True)
        self.wait(lambda:self.player.active==1)
        self.observe(self.row.text,prefix_probe=False,render_stable=True,body_confidence=.89034)
        self.assertEqual(self.player.cancelled,[])
        self.assertEqual(self.player.active,1)
        self.observe(self.row.text,prefix_probe=False,render_stable=True)
        self.assertEqual(len(self.player.played),1)
        self.controller.invalidate(captured_at=self.now+.1)
        self.wait(lambda:self.player.active==0)

    def test_role_conflict_stops_prefix_playback(self):
        self.start()
        self.observe('我今天想去街机',speaker='明音凛')
        self.wait(lambda:self.player.active==0)

    def test_skip_suppresses_growth_and_explicit_replay_still_works(self):
        self.start()
        self.controller.skip()
        self.wait(lambda:self.player.active==0)
        self.observe('我今天想去街机厅',prefix_stable=True)
        self.observe(self.row.text,prefix_probe=False,render_stable=True)
        self.assertEqual(len(self.player.played),1)
        self.controller.replay()
        self.wait(lambda:len(self.player.played)==2)

    def test_pause_and_next_page_keep_single_audio_owner(self):
        self.start()
        self.controller.pause()
        self.wait(lambda:self.player.active==0)
        self.observe(self.other.text,prefix_probe=False,render_stable=True)
        self.controller.pause(False)
        self.wait(lambda:len(self.player.played)==2)
        self.assertEqual(self.player.played[-1],self.other_path)
        self.assertEqual(self.player.max_active,1)

    def test_actual_visual_change_and_blank_cancel_and_reject_late_prefix(self):
        self.start()
        old=self.observe('我今天想去街机厅')
        self.controller.invalidate(captured_at=self.now+.1)
        self.wait(lambda:self.player.active==0)
        self.controller.observe(dict(old,prefix_stable=True))
        self.assertEqual(len(self.player.played),1)
        self.observe('……')
        self.observe('我今天想去',prefix_stable=True)
        self.wait(lambda:len(self.player.played)==2)


class PrefixPixelTests(unittest.TestCase):
    def picture(self,text='HELLO',x=100,y=55):
        image=np.full((100,700,3),30,np.uint8)
        cv2.putText(image,text,(x,y),cv2.FONT_HERSHEY_SIMPLEX,1.,(255,255,255),2,cv2.LINE_AA)
        return image

    def observation(self):
        return dict(body='HELLO',body_confidence=.99,prefix_probe=True,captured_monotonic=1.,
                    body_lines=[dict(bbox=[[98,30],[200,30],[200,60],[98,60]])])

    def test_typing_additions_and_centered_translation_do_not_count_as_page_change(self):
        guard=SubtitleChangeGuard()
        guard.arm(self.picture(),self.observation(),(0,0))
        for at,image in [(1.2,self.picture('HELLO WORLD')),(1.4,self.picture('HELLO WORLD',x=250)),
                         (1.6,self.picture('HELLO WORLD',x=220,y=75))]:
            self.assertTrue(guard.growth_continues(image))
            self.assertFalse(guard.changed(image,at))

    def test_changed_word_and_blank_still_invalidate(self):
        for image in (self.picture('WORLD'),np.full((100,700,3),30,np.uint8)):
            guard=SubtitleChangeGuard()
            guard.arm(self.picture(),self.observation(),(0,0))
            self.assertFalse(guard.growth_continues(image))
            changed=guard.changed(image,1.2) or guard.changed(image,1.4)
            self.assertTrue(changed)

    def test_full_confirmation_upgrades_growth_guard_and_early_updates_cannot_downgrade_it(self):
        guard=SubtitleChangeGuard()
        guard.arm(self.picture(),self.observation(),(0,0))
        full=dict(self.observation(),prefix_probe=False,captured_monotonic=1.2)
        guard.arm(self.picture(),full,(0,0))
        self.assertFalse(guard.allow_growth)
        guard.arm(self.picture(),dict(self.observation(),captured_monotonic=1.4),(0,0))
        self.assertFalse(guard.allow_growth)
        guard.arm(self.picture(),dict(self.observation(),body='HELLO W',captured_monotonic=1.6),(0,0))
        self.assertFalse(guard.allow_growth)

    def test_new_letters_entering_detector_padding_do_not_interrupt_typewriter(self):
        guard=SubtitleChangeGuard()
        observation=dict(self.observation(),body_lines=[dict(bbox=[[98,30],[215,30],[215,60],[98,60]])])
        guard.arm(self.picture(),observation,(0,0))
        self.assertTrue(guard.growth_continues(self.picture('HELLOWORLD')))
        self.assertFalse(guard.changed(self.picture('HELLOWORLD'),1.2))
        self.assertFalse(guard.changed(self.picture('HELLOWORLD'),1.4))

    def test_body_persistence_cannot_confirm_an_old_speaker_after_name_changed(self):
        reader=PrefixPreview()
        reader.pending=self.observation()
        reader.guard.arm(self.picture(),reader.pending,(0,0))
        reader.speaker_ink=np.zeros((20,40),bool)
        reader.speaker_ink[5:15,10:30]=True
        changed=np.roll(reader.speaker_ink,5,axis=1)
        self.assertIsNone(reader.confirm(self.picture('HELLO WORLD'),1.2,changed))
        self.assertTrue(reader.confirm(self.picture('HELLO WORLD'),1.4,reader.speaker_ink)['prefix_stable'])

    def test_preview_does_not_write_dialogue_or_enqueue_review_or_change_cache(self):
        core=DialogueOCR([0,0,1,1],engine=FakeEngine([[detection('我今天想去')],[detection('我今天想去街机厅。')]]))
        image=np.zeros((100,700,3),np.uint8)
        initial=deepcopy(core.__dict__)
        preview=core.process_frame(image,now=1,captured_at=1,preview=True)
        self.assertIsNone(preview['event'])
        self.assertTrue(preview['observation']['prefix_probe'])
        for key in ('_sequence','_records','_pending','_last_body','_last_id','_last_now','_body_cache','_stable_speaker'):
            self.assertEqual(getattr(core,key),initial[key],key)
        final=core.process_frame(image,now=2,captured_at=2)
        self.assertEqual(final['event']['event_id'],1)
        self.assertEqual(final['event']['body'],'我今天想去街机厅。')


class PrefixLoopTests(unittest.TestCase):
    def test_early_observations_precede_full_records_and_stop_suppresses_late_preview(self):
        import app
        from capture_session import FramePacket
        for cancel_during_ocr in (False,True):
            with self.subTest(cancel_during_ocr=cancel_during_ocr), tempfile.TemporaryDirectory() as directory:
                stop=threading.Event()
                early,records=[],[]
                pixel=PrefixPixelTests()
                images=[pixel.picture(),pixel.picture(),*[pixel.picture('HELLO WORLD') for _ in range(5)]]
                for image in images[:2]: image[0,0]=0
                for image in images[2:]: image[0,0]=1
                began=time.monotonic()
                class Detector:
                    def __call__(self,image,**kwargs):
                        if cancel_during_ocr: stop.set()
                        full=bool(image[0,0,0])
                        return [[[[98,30],[340 if full else 200,30],[340 if full else 200,60],[98,60]],
                                 'HELLO WORLD' if full else 'HELLO',.99]],None
                core=DialogueOCR([0,0,1,1],engine=Detector())
                class Capture:
                    backend_name='test'
                    status_text='test'
                    def __init__(self,*args,**kwargs): self.index=0;self.packet=None
                    def start(self): return self
                    def ready_for_ocr(self): return True
                    def read(self,after_sequence=0):
                        if self.index==len(images): stop.set();return None
                        self.packet=FramePacket(self.index+1,began+.2*self.index,images[self.index],(700,100),(0,0),(0,0,700,100),0)
                        self.index+=1
                        return self.packet
                    def peek(self): return self.packet
                    def stop(self): pass
                    def metrics(self): return {}
                def saved(record):
                    records.append(record)
                    stop.set()
                config=dict(calibrated=True,body_roi=[0,0,1,1],render_stable_seconds=.45)
                with patch('app.CaptureSession',Capture),patch('ocr_engines.make_ocr',return_value=core):
                    app._run_loop(SimpleNamespace(is_launcher=False),config,stop,lambda e:None,
                                  on_prefix_observation=early.append,on_record=saved,records_root=directory)
                if cancel_during_ocr:
                    self.assertEqual(early,[])
                    self.assertEqual(records,[])
                else:
                    self.assertEqual(early[0]['body'],'HELLO')
                    self.assertTrue(any(e.get('prefix_stable') for e in early))
                    self.assertEqual(len(records),1)
                    self.assertEqual(records[0]['body'],'HELLO WORLD')
                    self.assertEqual(core._sequence,1)


if __name__=='__main__':
    unittest.main(verbosity=2)
