"""Read visible text before whole-page stability, without saving partial dialogue."""
from copy import deepcopy
import math
import time
import numpy as np

from frame_gate import SubtitleChangeGuard, glyph_mask


class PrefixPreview:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_read = -math.inf
        self.pending = None
        self.confirmed = False
        self.guard = SubtitleChangeGuard()
        self.speaker_ink = None

    @staticmethod
    def speaker_signature(engine, packet):
        if engine.speaker_roi is None:
            return None
        image, _ = engine.region_image(packet.image, engine.speaker_roi, packet.client_size, packet.origin)
        if not engine.centered_body:
            return glyph_mask(image)
        blue, green, red = image.astype(np.int16).transpose(2, 0, 1)
        # Follow the same name-colored ink as the OCR name filter. Background
        # scenery cannot establish that an old speaker name is still present.
        return (red > 190) & (green > 100) & (red-green > 15) & (green-blue > 15)

    def confirm(self, image, captured, speaker_ink=None):
        if self.pending is None or captured-self.pending['captured_monotonic'] < .15:
            return None
        if self.speaker_ink is not None:
            if speaker_ink is None or speaker_ink.shape != self.speaker_ink.shape:
                return None
            if np.count_nonzero(speaker_ink ^ self.speaker_ink) >= max(8, round(self.speaker_ink.sum()*.025)):
                return None
        if captured-self.pending['captured_monotonic']>2.5 or not self.guard.growth_continues(image):
            return None
        observed=deepcopy(self.pending)
        observed.update(prefix_stable=True, prefix_confirmed_at=captured)
        # Keep the source timestamp: translated glyphs confirm the prefix, not
        # a newly read full sentence or a new speaker identity.
        self.confirmed=True
        return observed

    def read(self, engine, packet, image, offset, capture):
        if not self.confirmed:
            confirmed=self.confirm(image,packet.captured_at,self.speaker_signature(engine,packet))
            if confirmed:
                # This is a distinct captured frame supporting the same prefix.
                confirmed['recognized_capture']=confirmed['captured_monotonic']
                confirmed['captured_monotonic']=packet.captured_at
                return confirmed
        if packet.captured_at-self.last_read < (.45 if self.confirmed else .18):
            return None
        self.last_read=packet.captured_at
        result=engine.process_frame(packet.image,now=time.monotonic(),client_size=packet.client_size,
                                    origin=packet.origin,captured_at=packet.captured_at,preview=True)
        observation=result.get('observation')
        if observation is None:
            return None
        self.pending=deepcopy(observation)
        self.speaker_ink=self.speaker_signature(engine,packet)
        self.confirmed=False
        self.guard=SubtitleChangeGuard()
        self.guard.arm(image,observation,offset)
        latest=capture.peek()
        if latest is not None and latest.sequence>packet.sequence and latest.client_size==packet.client_size:
            fresh,_=engine.region_image(latest.image,engine.body_roi,latest.client_size,latest.origin)
            confirmed=self.confirm(fresh,latest.captured_at,self.speaker_signature(engine,latest))
            if confirmed:
                # The current capture confirms persistence while OCR was busy.
                observation=confirmed
        return observation
