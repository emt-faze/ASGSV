"""Review evidence may help fresh playback without relaxing saved-record rules."""
import unittest

import numpy as np

from ocr_core import DialogueOCR
from test_ocr_core import FakeEngine, detection


class ReviewEvidenceTests(unittest.TestCase):
    def core(self, *, first=.88343, reviewed=.95164, review_text='嗯哼？'):
        fast=FakeEngine([[detection('嗯哼？',first)], [detection('另一句。',.99)]])
        medium=FakeEngine([[detection(review_text,reviewed)]])
        fast.model_id='small'; medium.model_id='medium'
        return DialogueOCR([0,0,1,1],engine=fast,review_engine=medium)

    def test_independent_agreement_is_used_only_by_next_unchanged_captured_frame(self):
        core=self.core()
        frame=np.zeros((60,180,3),np.uint8)
        first=core.process_frame(frame,now=1,captured_at=1)
        self.assertLess(first['observation']['body_confidence'],.9)
        reviewed=core.verify(first['event']['dialogue_id'])
        self.assertEqual(reviewed['status'],'needs_review')  # Durable OCR quality remains strict.
        self.assertEqual(reviewed['body_confidence'],.88343)
        fresh=core.process_frame(frame,now=2,captured_at=2,reuse_body=True)
        self.assertGreaterEqual(fresh['observation']['body_confidence'],.9)
        self.assertEqual(fresh['observation']['body_evidence_model'],'medium')
        self.assertTrue(fresh['observation']['body_review_agreement'])
        self.assertEqual(fresh['observation']['captured_monotonic'],2)
        self.assertIsNone(fresh['event'])
        self.assertEqual(core._sequence,1)
        self.assertEqual(len(core.engine.calls),1)

    def test_changed_pixels_must_be_read_again_instead_of_reusing_reviewed_audio_text(self):
        core=self.core();frame=np.zeros((60,180,3),np.uint8)
        first=core.process_frame(frame,now=1,captured_at=1)
        core.verify(first['event']['dialogue_id'])
        changed=core.process_frame(frame+10,now=2,captured_at=2,reuse_body=False)
        self.assertEqual(changed['observation']['body'],'另一句。')
        self.assertEqual(changed['observation']['body_evidence_model'],'small')
        self.assertFalse(changed['observation']['body_review_agreement'])

    def test_disagreement_weak_review_or_very_weak_first_read_cannot_upgrade(self):
        for values in (dict(review_text='嗯啊？'),dict(reviewed=.89),dict(first=.7)):
            with self.subTest(values=values):
                core=self.core(**values);frame=np.zeros((60,180,3),np.uint8)
                first=core.process_frame(frame,now=1,captured_at=1)
                core.verify(first['event']['dialogue_id'])
                fresh=core.process_frame(frame,now=2,captured_at=2,reuse_body=True)
                self.assertLess(fresh['observation']['body_confidence'],.9)

    def test_late_review_cannot_replace_a_new_pages_cache(self):
        core=self.core();frame=np.zeros((60,180,3),np.uint8)
        first=core.process_frame(frame,now=1,captured_at=1)
        core.process_frame(frame,now=2,captured_at=2)
        core.verify(first['event']['dialogue_id'])
        fresh=core.process_frame(frame,now=3,captured_at=3,reuse_body=True)
        self.assertEqual(fresh['observation']['body'],'另一句。')

    def test_clipping_and_coverage_warnings_block_review_reuse(self):
        for flag in ('source_clipped','coverage_warning'):
            with self.subTest(flag=flag):
                core=self.core();frame=np.zeros((60,180,3),np.uint8)
                first=core.process_frame(frame,now=1,captured_at=1)
                core._records[first['event']['dialogue_id']][flag]=True
                core.verify(first['event']['dialogue_id'])
                fresh=core.process_frame(frame,now=2,captured_at=2,reuse_body=True)
                self.assertLess(fresh['observation']['body_confidence'],.9)


if __name__=='__main__':
    unittest.main(verbosity=2)
