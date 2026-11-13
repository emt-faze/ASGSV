"""Recognize an observed in-game dialogue layout, never authorize clicks."""
from pathlib import Path
import cv2
import numpy as np

from game_profiles import WUWA_ROOT

ICON_AREAS = ((.814,.029,.86,.09), (.913,.029,.958,.09))


def icon_mask(frame, area, client_size=None, origin=(0,0)):
    width,height=client_size or frame.shape[1::-1]
    left,top,right,bottom=area
    x,y=origin
    x0,y0,x1,y1=round(left*width)-x,round(top*height)-y,round(right*width)-x,round(bottom*height)-y
    if not (0<=x0<x1<=frame.shape[1] and 0<=y0<y1<=frame.shape[0]):
        return None
    patch=frame[y0:y1,x0:x1]
    patch=cv2.resize(patch,(96,96),interpolation=cv2.INTER_AREA)
    low=patch.min(axis=2)
    return ((low>210)&(patch.max(axis=2).astype(int)-low<35)).astype(np.uint8)*255


def record_known_dialogue(frame, root=WUWA_ROOT):
    directory=Path(root)/'scene-templates'
    directory.mkdir(parents=True,exist_ok=True)
    for index,area in enumerate(ICON_AREAS):
        okay,data=cv2.imencode('.png',icon_mask(frame,area))
        if not okay:
            raise ValueError('无法保存对话界面样本')
        data.tofile(str(directory/f'dialogue-control-{index}.png'))


def dialogue_layout(frame, root=WUWA_ROOT):
    from wuwa_ocr import speaker_ink
    height,width=frame.shape[:2]
    name=frame[round(.735*height):round(.79*height),round(.46*width):round(.54*width)]
    if not name.size or speaker_ink(name).mean()<.025:
        return False
    return dialogue_controls(frame,root)


def dialogue_controls(frame, root=WUWA_ROOT, client_size=None, origin=(0,0)):
    for index,area in enumerate(ICON_AREAS):
        path=Path(root)/'scene-templates'/f'dialogue-control-{index}.png'
        if not path.exists():
            return False
        template=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
        if template is None or np.std(template)<5:
            return False
        mask=icon_mask(frame,area,client_size,origin)
        if mask is None:
            return False
        # A one-pixel resampling difference must not masquerade as scene exit.
        mask=cv2.GaussianBlur(mask,(3,3),.7)
        template=cv2.GaussianBlur(template,(3,3),.7)
        score=float(cv2.matchTemplate(mask,template,cv2.TM_CCOEFF_NORMED)[0,0])
        if not np.isfinite(score) or score<.94:
            return False
    return True
