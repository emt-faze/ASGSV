"""WuWa's centered pale-yellow speaker label, separate from NTE's orange ink."""
import numpy as np
from ocr_core import DialogueOCR


def speaker_ink(image):
    blue, green, red = image.astype(np.int16).transpose(2, 0, 1)
    return (red > 180) & (green > 170) & (np.minimum(red, green) - blue > 30) & (np.abs(red - green) < 75)


class WuwaDialogueOCR(DialogueOCR):
    def _recognize_image(self, image, offset=(0, 0), scale=1.0, recover_punctuation=False,
                         centered=False, engine=None, speaker_style=False, allow_partial=False):
        lines, text = super()._recognize_image(image, offset, scale, recover_punctuation,
            centered, engine, False if speaker_style else speaker_style, allow_partial)
        if not speaker_style:
            return lines, text
        accepted = []
        for line in lines:
            points = np.asarray(line['bbox']) - offset
            x0,y0 = np.maximum(0, np.floor(points.min(axis=0)).astype(int))
            x1,y1 = np.minimum([image.shape[1],image.shape[0]],np.ceil(points.max(axis=0)).astype(int))
            patch = image[y0:y1,x0:x1]
            if not patch.size or line['confidence'] < self.min_confidence:
                continue
            if abs((x0+x1)/2-image.shape[1]/2) > image.shape[1]*.18:
                continue
            ink = speaker_ink(patch)
            middle = ink[len(ink)//4:max(len(ink)//4+1,3*len(ink)//4)]
            if ink.mean() >= .04 and middle.mean() >= .04 and 1 <= len(line['text'].strip()) <= 20:
                accepted.append(line)
        if len(accepted) != 1:
            return [], ''
        return accepted, accepted[0]['text'].strip()
